"""
Agent workflow — plan → run_one_tool → reflect → answer.

Scheme B: one tool per round; results merge into a shared evidence pool.
Tools today: kb | web | calendar | sandbox.
SSE intent label: chat | rag | web_search | calendar | data_analysis (display only).
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.harness.critique import (
    ground_and_repair_answer,
    grounding_thinking_steps,
    should_apply_sandbox_number_gate,
    should_ground_answer,
)
from app.agents.harness import gateway
from app.agents.harness.types import ToolContext
from app.agents.harness.verify import verify_state
from app.agents.context import ContextBundle, compose_answer_messages
from app.agents.router import (
    Intent,
    classify_intent,
    plan_tools,
)
from app.core.prompts import (
    answer_rules,
    clip_text,
    max_pool_chars,
    no_evidence_block,
)
from app.core.llm import complete_text
from app.services.memory import load_memory_prompt

ToolName = Literal["kb", "web", "calendar", "sandbox"]

_MAX_ITERATIONS = 4


class WorkflowState(TypedDict, total=False):
    history: list[dict]
    message: str
    use_kb: bool
    has_kb_docs: bool
    has_tabular_docs: bool
    document_ids: list[int] | None
    user_id: int
    context: dict
    intent: Intent
    forced: bool
    citations: list[dict]
    thinking_steps: list[str]
    llm_messages: list[dict]
    needs_freshness: bool
    needs_kb: bool
    needs_web: bool
    needs_calendar: bool
    needs_sandbox: bool
    pending_tools: list[str]
    next_tool: str | None
    iteration: int
    evidence: list[dict]
    need_more: bool
    kb_retried: bool
    web_retried: bool
    schedule_card: dict | None
    calendar_reply: str | None
    direct_answer: str | None
    pending_calendar: dict | None
    artifact: dict | None
    analysis_ir: dict | None
    analysis_summary: dict | None
    analysis_asked_ids: list[str]
    analysis_uncomputable: bool
    sandbox_replanned: bool
    sandbox_feedback: str


class WorkflowResult(TypedDict):
    intent: Intent
    forced: bool
    citations: list[dict]
    thinking_steps: list[str]
    llm_messages: list[dict]
    schedule_card: dict | None
    direct_answer: str | None
    pending_calendar: dict | None
    artifact: dict | None
    analysis_summary: dict | None


def _sse_intent(
    *,
    forced: bool,
    evidence: list[dict],
    pending_had_kb: bool,
    pending_had_web: bool,
    pending_had_calendar: bool,
    pending_had_sandbox: bool = False,
) -> Intent:
    if forced:
        return "rag"
    has_kb = any(e.get("source_type") == "kb" for e in evidence)
    has_web = any(e.get("source_type") == "web" for e in evidence)
    has_calendar = any(e.get("source_type") == "calendar" for e in evidence)
    has_sandbox = any(e.get("source_type") == "sandbox" for e in evidence)
    if has_sandbox:
        return "data_analysis"
    if has_kb:
        return "rag"
    if has_calendar:
        return "calendar"
    if has_web:
        return "web_search"
    if pending_had_sandbox:
        return "data_analysis"
    if pending_had_kb:
        return "rag"
    if pending_had_calendar:
        return "calendar"
    if pending_had_web:
        return "web_search"
    return "chat"


def build_pool_prompt(
    evidence: list[dict],
    *,
    max_chars: int | None = None,
) -> tuple[str, list[dict]]:
    lines = [answer_rules(), "", "【证据池】"]
    if not evidence:
        lines.append(no_evidence_block())
        return "\n".join(lines).strip(), []

    used = 0
    budget = int(max_chars) if max_chars is not None else max_pool_chars()
    omitted = 0
    included: list[dict] = []
    display_idx = 0

    for e in evidence:
        if e.get("source_type") == "kb":
            meta = " · ".join(
                x
                for x in (
                    e.get("heading") or "",
                    f"第{e['page']}页" if e.get("page") is not None else "",
                )
                if x
            )
            body = clip_text(str(e.get("content") or e.get("snippet") or ""))
        elif e.get("source_type") == "web":
            parts = []
            if e.get("url"):
                parts.append(f"链接：{e['url']}")
            parts.append(clip_text(str(e.get("content") or e.get("snippet") or "")))
            body = "\n".join(parts)
        else:
            body = clip_text(str(e.get("content") or e.get("snippet") or ""))

        display_idx += 1
        if e.get("source_type") == "kb":
            meta = " · ".join(
                x
                for x in (
                    e.get("heading") or "",
                    f"第{e['page']}页" if e.get("page") is not None else "",
                )
                if x
            )
            header = f"[{display_idx}] 知识库 · {e.get('filename')}{f'（{meta}）' if meta else ''}"
        elif e.get("source_type") == "web":
            date_s = f" | {e['publish_date']}" if e.get("publish_date") else ""
            header = f"[{display_idx}] 联网 · {e.get('title')}（{e.get('filename')}{date_s}）"
        elif e.get("source_type") == "sandbox":
            header = f"[{display_idx}] 数据分析 · {e.get('title') or e.get('filename') or '沙箱结果'}"
        else:
            header = f"[{display_idx}] 日程 · {e.get('title') or e.get('filename') or '日程操作'}"

        block = f"{header}\n{body}\n"
        if used + len(block) > budget and used > 0:
            omitted += 1
            display_idx -= 1
            continue

        entry = dict(e)
        entry["index"] = display_idx
        included.append(entry)
        lines.append(header)
        lines.append(body)
        lines.append("")
        used += len(block)

    if omitted:
        lines.append(f"（其余 {omitted} 条证据因长度限制略去）")
    return "\n".join(lines).strip(), included


def _pool_prompt(evidence: list[dict]) -> str:
    text, _ = build_pool_prompt(evidence)
    return text


def _reindex(evidence: list[dict]) -> list[dict]:
    for i, e in enumerate(evidence, start=1):
        e["index"] = i
    return evidence


def run_agent_workflow(
    db: Session,
    *,
    message: str,
    history: list[dict],
    use_kb: bool = False,
    has_kb_docs: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
    user_id: int = 1,
    pending_calendar: dict | None = None,
    context: dict | None = None,
) -> WorkflowResult:
    ctx_state = (
        context if isinstance(context, dict) else ContextBundle.from_history(history).to_state()
    )

    def plan_node(state: WorkflowState) -> dict[str, Any]:
        if state.get("pending_calendar"):
            return {
                "forced": False,
                "needs_freshness": False,
                "needs_kb": False,
                "needs_web": False,
                "needs_calendar": True,
                "needs_sandbox": False,
                "pending_tools": [],
                "next_tool": "calendar",
                "iteration": 1,
                "evidence": [],
                "thinking_steps": [],
                "citations": [],
                "need_more": False,
                "kb_retried": False,
                "web_retried": False,
                "schedule_card": None,
                "calendar_reply": None,
                "direct_answer": None,
                "artifact": None,
                "intent": "calendar",
            }
        ctx = state.get("context") if isinstance(state.get("context"), dict) else ctx_state
        router_hist = list(ctx.get("router_history") or state.get("history") or [])
        planned = plan_tools(
            state.get("message") or "",
            router_hist,
            has_kb_docs=bool(state.get("has_kb_docs")),
            forced_kb=bool(state.get("use_kb")),
            has_tabular_docs=bool(state.get("has_tabular_docs")),
            document_ids=state.get("document_ids") or None,
            context_line=str(ctx.get("context_line") or ""),
        )
        queue = list(planned["pending_tools"])
        next_tool = queue[0] if queue else None
        pending = queue[1:] if queue else []
        return {
            "forced": bool(state.get("use_kb")),
            "needs_freshness": planned["needs_freshness"],
            "needs_kb": bool(planned["needs_kb"]),
            "needs_web": bool(planned["needs_web"]),
            "needs_calendar": bool(planned.get("needs_calendar")),
            "needs_sandbox": bool(planned.get("needs_sandbox")),
            "pending_tools": pending,
            "next_tool": next_tool,
            "iteration": 1,
            "evidence": [],
            "thinking_steps": [],
            "citations": [],
            "need_more": False,
            "kb_retried": False,
            "web_retried": False,
            "schedule_card": None,
            "calendar_reply": None,
            "direct_answer": None,
            "artifact": None,
            "intent": _sse_intent(
                forced=bool(state.get("use_kb")),
                evidence=[],
                pending_had_kb=bool(planned["needs_kb"]),
                pending_had_web=bool(planned["needs_web"]),
                pending_had_calendar=bool(planned.get("needs_calendar")),
                pending_had_sandbox=bool(planned.get("needs_sandbox")),
            ),
        }

    def retrieve_node(state: WorkflowState) -> dict[str, Any]:
        tool = state.get("next_tool")
        ctx = state.get("context") if isinstance(state.get("context"), dict) else ctx_state
        hist = list(ctx.get("tool_history") or state.get("history") or [])
        msg = state.get("message") or ""
        steps = list(state.get("thinking_steps") or [])
        it = int(state.get("iteration") or 1)
        evidence = list(state.get("evidence") or [])

        if not tool:
            steps.append(f"第 {it} 轮：无可用工具，跳过")
            return {"thinking_steps": steps, "evidence": _reindex(evidence)}

        tool_ctx = ToolContext(
            db=db,
            message=msg,
            history=hist,
            user_id=state.get("user_id") or 1,
            document_ids=state.get("document_ids") or None,
            iteration=it,
            pending_calendar=state.get("pending_calendar"),
            needs_freshness=bool(state.get("needs_freshness")),
            forced_kb=bool(state.get("forced") or state.get("use_kb")),
            needs_kb=bool(state.get("needs_kb")),
            needs_web=bool(state.get("needs_web")),
            needs_calendar=bool(state.get("needs_calendar")),
            needs_sandbox=bool(state.get("needs_sandbox")),
            summary=str(ctx.get("summary_block") or ""),
            working_state=None,
        )
        # Extra args reserved for governance checks (e.g. deny sandbox network escapes).
        tool_args: dict[str, Any] = {}
        if tool == "sandbox":
            tool_args = {"network": "none", "read_only": True}
            if state.get("sandbox_feedback"):
                tool_args["feedback"] = state.get("sandbox_feedback")
            if state.get("analysis_ir"):
                tool_args["prior_ir"] = state.get("analysis_ir")

        result = gateway.invoke(tool, tool_ctx, tool_args)
        prior_steps = list(steps)
        update = result.as_state_update(base_evidence=evidence)
        update["thinking_steps"] = prior_steps + list(update.get("thinking_steps") or [])
        if result.denied and "calendar_reply" not in update:
            update.setdefault("need_more", False)
        return update

    def reflect_node(state: WorkflowState) -> dict[str, Any]:
        steps = list(state.get("thinking_steps") or [])
        pending = list(state.get("pending_tools") or [])
        it = int(state.get("iteration") or 1)
        last = state.get("next_tool")

        if state.get("direct_answer"):
            steps.append("结果已就绪，直接返回")
            return {"next_tool": None, "thinking_steps": steps, "need_more": False}

        # Prefer finishing the planned queue before retries / verify repairs.
        if pending and it < _MAX_ITERATIONS:
            nxt = pending[0]
            steps.append(f"继续下一工具：{nxt}")
            return {
                "next_tool": nxt,
                "pending_tools": pending[1:],
                "iteration": it + 1,
                "thinking_steps": steps,
                "need_more": True,
            }

        decision = verify_state(
            {
                **state,
                "max_iterations": _MAX_ITERATIONS,
                "pending_tools": pending,
            }
        )
        if decision.ok:
            if last == "sandbox":
                steps.append("数据分析完成，正在生成回答")
            else:
                steps.append(f"证据已足够（{decision.reason}），正在生成回答")
            return {"next_tool": None, "thinking_steps": steps, "need_more": False}

        if decision.next_tool and it < _MAX_ITERATIONS:
            steps.append(decision.reason or f"校验未通过，补调 {decision.next_tool}")
            out: dict[str, Any] = {
                "next_tool": decision.next_tool,
                "pending_tools": [],
                "iteration": it + 1,
                "thinking_steps": steps,
                "need_more": True,
            }
            if decision.mark_web_retried:
                out["web_retried"] = True
            if decision.mark_kb_retried:
                out["kb_retried"] = True
            if decision.mark_sandbox_replanned:
                out["sandbox_replanned"] = True
                if decision.sandbox_feedback:
                    out["sandbox_feedback"] = decision.sandbox_feedback
            return out

        steps.append(f"校验未完全满足（{decision.reason}），仍生成回答")
        return {
            "next_tool": None,
            "thinking_steps": steps,
            "need_more": False,
        }

    def answer_node(state: WorkflowState) -> dict[str, Any]:
        ctx = state.get("context") if isinstance(state.get("context"), dict) else ctx_state
        hist = list(state.get("history") or ctx.get("answer_history") or [])
        msg = state.get("message") or ""
        evidence = list(state.get("evidence") or [])
        asked_kb = bool(
            state.get("forced") or state.get("use_kb") or state.get("needs_kb")
        )
        has_kb = any(e.get("source_type") == "kb" for e in evidence)
        has_web = any(e.get("source_type") == "web" for e in evidence)
        has_sandbox = any(e.get("source_type") == "sandbox" for e in evidence)
        direct = state.get("direct_answer")
        if (
            asked_kb
            and not has_kb
            and not has_web
            and not has_sandbox
            and not direct
        ):
            from app.core.messages import EMPTY_KB_MESSAGE

            direct = EMPTY_KB_MESSAGE
        intent = _sse_intent(
            forced=bool(state.get("forced")),
            evidence=evidence,
            pending_had_kb=bool(state.get("needs_kb")) or bool(state.get("forced")),
            pending_had_web=bool(state.get("needs_web")),
            pending_had_calendar=bool(state.get("needs_calendar")),
            pending_had_sandbox=bool(state.get("needs_sandbox")),
        )
        included: list[dict] = []
        llm_messages: list[dict] = []
        if direct:
            llm_messages = []
        elif evidence or intent != "chat":
            if state.get("analysis_uncomputable") and any(
                e.get("source_type") == "sandbox" for e in evidence
            ):
                missing = []
                summary = state.get("analysis_summary") if isinstance(state.get("analysis_summary"), dict) else {}
                for item in summary.get("missing") or []:
                    if isinstance(item, dict):
                        missing.append(str(item.get("reason") or item.get("asked") or "未提及"))
                reason = "；".join(missing[:3]) if missing else "证据中未计算该指标"
                direct = f"证据中未提及或无法计算：{reason}。请确认表格是否包含所需列，或换一种问法。"
                thinking = list(state.get("thinking_steps") or [])
                thinking.append("沙箱标明指标不可算，直接拒答")
                return {
                    "intent": intent,
                    "citations": [],
                    "llm_messages": [],
                    "thinking_steps": thinking,
                    "schedule_card": state.get("schedule_card"),
                    "direct_answer": direct,
                    "pending_calendar": state.get("pending_calendar"),
                    "artifact": state.get("artifact"),
                }
            pool_text, included = build_pool_prompt(evidence)
            sandbox_note = ""
            if any(e.get("source_type") == "sandbox" for e in evidence):
                sandbox_note = (
                    "【数据分析说明】沙箱已在隔离环境中对上传表格**全量**计算。"
                    "数值必须以证据中的 ===SUMMARY=== / ===SUMMARY_JSON=== 的 metrics 为准；"
                    "禁止根据 df.head() 或样例行估算，勿写「基于样本数据」。"
                    "只回答用户所问的指标；若 missing 非空则说明未提及，不要用相邻汇总顶替。"
                    "比率可用小数或百分数等价表述。图表见 Artifact 面板。"
                )
            memory_block = str(ctx.get("memory_block") or "") or load_memory_prompt(
                db, state.get("user_id") or 1
            )
            bundle = ContextBundle(
                answer_history=hist,
                summary_block=str(ctx.get("summary_block") or ""),
                working_block=str(ctx.get("working_block") or ""),
                memory_block=memory_block,
                stats=dict(ctx.get("stats") or {}),
            )

            def _rebuild(n: int) -> tuple[str, list[dict]]:
                return build_pool_prompt(evidence, max_chars=n)

            llm_messages, pool_text, rebuilt = compose_answer_messages(
                bundle=bundle,
                hist=hist,
                message=msg,
                pool_text=pool_text,
                memory_block=memory_block,
                rebuild_pool=_rebuild,
                sandbox_note=sandbox_note,
                has_evidence_system=True,
            )
            if rebuilt:
                included = rebuilt
        else:
            memory_block = str(ctx.get("memory_block") or "") or load_memory_prompt(
                db, state.get("user_id") or 1
            )
            bundle = ContextBundle(
                answer_history=hist,
                summary_block=str(ctx.get("summary_block") or ""),
                working_block=str(ctx.get("working_block") or ""),
                memory_block=memory_block,
                stats=dict(ctx.get("stats") or {}),
            )
            llm_messages, _, _ = compose_answer_messages(
                bundle=bundle,
                hist=hist,
                message=msg,
                pool_text="",
                memory_block=memory_block,
                rebuild_pool=None,
                sandbox_note="",
                has_evidence_system=False,
            )

        citations = [
            {
                "index": e["index"],
                "filename": e.get("filename"),
                "title": e.get("title"),
                "snippet": e.get("snippet"),
                "url": (e.get("url") or "").strip() or None,
                "source_type": e.get("source_type"),
            }
            for e in included
            if e.get("source_type") not in ("calendar", "sandbox")
        ]

        thinking = list(state.get("thinking_steps") or [])
        if (
            not direct
            and llm_messages
            and should_ground_answer(evidence=evidence, direct_answer=direct)
        ):
            thinking.append("正在根据证据核对结论")
            draft = complete_text(llm_messages)
            sandbox_gate = should_apply_sandbox_number_gate(
                evidence=included or evidence,
                needs_sandbox=bool(state.get("needs_sandbox")),
            )
            final, critique, repaired = ground_and_repair_answer(
                question=msg,
                draft=draft,
                evidence=evidence,
                included=included,
                messages=llm_messages,
                sandbox_gate=sandbox_gate,
            )
            thinking.extend(grounding_thinking_steps(critique, repaired=repaired))
            direct = final
            llm_messages = []

        return {
            "intent": intent,
            "citations": citations,
            "llm_messages": llm_messages,
            "thinking_steps": thinking,
            "schedule_card": state.get("schedule_card"),
            "direct_answer": direct,
            "pending_calendar": state.get("pending_calendar"),
            "artifact": state.get("artifact"),
        }

    def after_plan(state: WorkflowState) -> Literal["retrieve_node", "answer_node"]:
        return "retrieve_node" if state.get("next_tool") else "answer_node"

    def after_reflect(state: WorkflowState) -> Literal["retrieve_node", "answer_node"]:
        return "retrieve_node" if state.get("need_more") and state.get("next_tool") else "answer_node"

    g = StateGraph(WorkflowState)
    g.add_node("plan_node", plan_node)
    g.add_node("retrieve_node", retrieve_node)
    g.add_node("reflect_node", reflect_node)
    g.add_node("answer_node", answer_node)
    g.add_edge(START, "plan_node")
    g.add_conditional_edges(
        "plan_node",
        after_plan,
        {"retrieve_node": "retrieve_node", "answer_node": "answer_node"},
    )
    g.add_edge("retrieve_node", "reflect_node")
    g.add_conditional_edges(
        "reflect_node",
        after_reflect,
        {"retrieve_node": "retrieve_node", "answer_node": "answer_node"},
    )
    g.add_edge("answer_node", END)

    out = g.compile().invoke(
        {
            "history": history,
            "message": message,
            "use_kb": use_kb,
            "has_kb_docs": has_kb_docs,
            "has_tabular_docs": has_tabular_docs,
            "document_ids": document_ids,
            "user_id": user_id,
            "pending_calendar": pending_calendar,
            "context": ctx_state,
        }
    )
    return {
        "intent": out.get("intent") or "chat",
        "forced": bool(out.get("forced")),
        "citations": out.get("citations") or [],
        "thinking_steps": out.get("thinking_steps") or [],
        "llm_messages": out.get("llm_messages") or [],
        "schedule_card": out.get("schedule_card"),
        "direct_answer": out.get("direct_answer"),
        "pending_calendar": out.get("pending_calendar"),
        "artifact": out.get("artifact"),
        "analysis_summary": out.get("analysis_summary"),
    }


async def route_intent(
    messages: list[dict],
) -> Literal["chat", "rag", "tool", "web_search", "calendar", "data_analysis"]:
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    return classify_intent(last, messages, has_kb_docs=True, has_tabular_docs=False)
