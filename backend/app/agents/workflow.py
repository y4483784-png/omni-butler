from __future__ import annotations

from dataclasses import dataclass
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
from app.agents.harness.effects import decide_replay
from app.agents.harness.execution import execution_denial
from app.agents.harness.types import ToolContext, ToolResult, UnknownWrite
from app.agents.harness.verify import verify_state
from app.agents.context import ContextBundle, compose_answer_messages
from app.agents.dispatch import TurnAssignment, sse_intent
from app.agents.router import (
    Intent,
    classify_intent,
)
from app.core.prompts import (
    answer_rules,
    clip_text,
    max_pool_chars,
    no_evidence_block,
)
from app.core.generation_stop import GenerationStopped, is_stopped
from app.core.llm import Role, Spoken, answer_pair, complete_text
from app.services.calendar_factory import calendar_provider_for
from app.services.memory import load_memory_prompt
from app.services.schedule_confirm import CONFIRM_DETAIL, schedule_card_needs_claim

ToolName = Literal["kb", "web", "calendar", "sandbox"]

_MAX_ITERATIONS = 4


@dataclass(frozen=True)
class LoopStep:
    tool: str
    user_id: int
    task_id: int | None
    args: dict[str, Any]


@dataclass(frozen=True)
class Halted:
    pass


@dataclass(frozen=True)
class Denied:
    tool: str
    reason: str


@dataclass(frozen=True)
class ReadDone:
    tool: str


@dataclass(frozen=True)
class ConfirmDraft:
    sentence: str
    card: dict[str, Any]


@dataclass(frozen=True)
class UnsettledUnknown:
    report: UnknownWrite


@dataclass(frozen=True)
class Reconciled:
    external_id: str
    found_event_id: str | None
    sentence: str


@dataclass(frozen=True)
class Refused:
    sentence: str


StepOutcome = Halted | Denied | ReadDone | ConfirmDraft | UnsettledUnknown | Reconciled | Refused


@dataclass(frozen=True)
class RunRead:
    step: LoopStep


@dataclass(frozen=True)
class RunConfirm:
    step: LoopStep


@dataclass(frozen=True)
class Stopped:
    pass


@dataclass(frozen=True)
class Blocked:
    prior: StepOutcome


@dataclass(frozen=True)
class NotAllowed:
    tool: str
    reason: str


Admission = RunRead | RunConfirm | Stopped | Blocked | NotAllowed

_CLOSED = (ConfirmDraft, Reconciled, Refused, Denied, UnsettledUnknown, Halted)


class WorkflowState(TypedDict, total=False):
    history: list[dict]
    message: str
    use_kb: bool
    has_kb_docs: bool
    has_tabular_docs: bool
    document_ids: list[int] | None
    user_id: int
    task_id: int | None
    generation: int | None
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
    answer_reasoning: str | None
    pending_calendar: dict | None
    artifact: dict | None
    analysis_ir: dict | None
    analysis_summary: dict | None
    analysis_asked_ids: list[str]
    analysis_uncomputable: bool
    sandbox_replanned: bool
    sandbox_feedback: str
    step_outcome: StepOutcome


class WorkflowResult(TypedDict):
    intent: Intent
    forced: bool
    citations: list[dict]
    thinking_steps: list[str]
    llm_messages: list[dict]
    schedule_card: dict | None
    direct_answer: str | None
    answer_reasoning: str | None
    pending_calendar: dict | None
    artifact: dict | None
    analysis_summary: dict | None
    context_stats: dict | None


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


def _cutoff() -> dict[str, Any]:
    return {"next_tool": None, "pending_tools": [], "need_more": False}


def _halted_result() -> WorkflowResult:
    return {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [],
        "schedule_card": None,
        "direct_answer": None,
        "answer_reasoning": None,
        "pending_calendar": None,
        "artifact": None,
        "analysis_summary": None,
        "context_stats": None,
    }


def admit(
    step: LoopStep,
    prior: StepOutcome | None,
    *,
    db: object,
) -> Admission:
    stopped = is_stopped()
    reason = execution_denial(
        db,
        user_id=step.user_id,
        task_id=step.task_id,
        tool=step.tool,
    )
    if stopped:
        return Stopped()
    if isinstance(prior, (Reconciled, Refused, ConfirmDraft, Denied, Halted, UnsettledUnknown)):
        return Blocked(prior)
    if reason:
        return NotAllowed(step.tool, reason)
    if step.tool == "calendar":
        return RunConfirm(step)
    return RunRead(step)


def classify_tool_result(
    admission: RunConfirm | RunRead,
    result: ToolResult,
) -> StepOutcome:
    if result.unknown_write is not None:
        return UnsettledUnknown(result.unknown_write)
    card = result.schedule_card
    if (
        isinstance(admission, RunConfirm)
        and isinstance(card, dict)
        and schedule_card_needs_claim(card)
    ):
        return ConfirmDraft(sentence=result.direct_answer or "", card=card)
    return ReadDone(admission.step.tool)


def settle_unknown(
    report: UnknownWrite,
    *,
    db: Session,
    user_id: int,
) -> Reconciled | Refused:
    choice = decide_replay(
        report.effect,
        attempt=report.attempt,
        external_id=report.external_id,
        prior_external_id=report.prior_external_id,
    )
    sentence = CONFIRM_DETAIL["unknown"]
    if choice != "reconcile":
        return Refused(sentence)
    provider = calendar_provider_for(db, user_id)
    lookup = getattr(provider, "lookup_inserted_event", None)
    item = lookup(report.external_id) if lookup is not None else None
    found = None
    if isinstance(item, dict) and str(item.get("id") or "").strip():
        found = str(item["id"]).strip()
    return Reconciled(report.external_id, found, sentence)


def project_reply(outcome: StepOutcome, drafted: dict[str, Any]) -> dict[str, Any]:
    if isinstance(outcome, ConfirmDraft):
        drafted["direct_answer"] = outcome.sentence
        drafted["llm_messages"] = []
        drafted["schedule_card"] = outcome.card
        return drafted
    if isinstance(outcome, (Reconciled, Refused)):
        drafted["direct_answer"] = outcome.sentence
        drafted["llm_messages"] = []
        drafted["schedule_card"] = None
        return drafted
    if isinstance(outcome, Denied):
        drafted["direct_answer"] = outcome.reason
        drafted["llm_messages"] = []
        drafted["schedule_card"] = None
        return drafted
    if isinstance(outcome, UnsettledUnknown):
        drafted["direct_answer"] = CONFIRM_DETAIL["unknown"]
        drafted["llm_messages"] = []
        drafted["schedule_card"] = None
        return drafted
    if isinstance(outcome, Halted):
        drafted["direct_answer"] = None
        drafted["llm_messages"] = []
        drafted["schedule_card"] = None
        return drafted
    card = drafted.get("schedule_card")
    if isinstance(card, dict) and schedule_card_needs_claim(card):
        drafted["schedule_card"] = None
    if drafted.get("direct_answer"):
        drafted["llm_messages"] = []
    return drafted


def run_agent_workflow(
    db: Session,
    *,
    assignment: TurnAssignment,
    message: str,
    history: list[dict],
    use_kb: bool = False,
    has_kb_docs: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
    user_id: int = 1,
    pending_calendar: dict | None = None,
    context: dict | None = None,
    task_id: int | None = None,
    generation: int | None = None,
) -> WorkflowResult:
    if is_stopped():
        return _halted_result()
    ctx_state = (
        context if isinstance(context, dict) else ContextBundle.from_history(history).to_state()
    )

    def retrieve_node(state: WorkflowState) -> dict[str, Any]:
        tool = state.get("next_tool")
        ctx = state.get("context") if isinstance(state.get("context"), dict) else ctx_state
        hist = list(ctx.get("tool_history") or state.get("history") or [])
        msg = state.get("message") or ""
        steps = list(state.get("thinking_steps") or [])
        it = int(state.get("iteration") or 1)
        evidence = list(state.get("evidence") or [])

        if is_stopped():
            return {**_cutoff(), "step_outcome": Halted()}
        if not tool:
            steps.append(f"第 {it} 轮：无可用工具，跳过")
            return {"thinking_steps": steps, "evidence": _reindex(evidence)}

        # Extra args reserved for governance checks (e.g. deny sandbox network escapes).
        tool_args: dict[str, Any] = {}
        if tool == "sandbox":
            tool_args = {"network": "none", "read_only": True}
            if state.get("sandbox_feedback"):
                tool_args["feedback"] = state.get("sandbox_feedback")
            if state.get("analysis_ir"):
                tool_args["prior_ir"] = state.get("analysis_ir")

        step = LoopStep(
            tool=tool,
            user_id=state.get("user_id") or 1,
            task_id=state.get("task_id"),
            args=tool_args,
        )
        admission = admit(step, state.get("step_outcome"), db=db)
        if isinstance(admission, Stopped):
            return {**_cutoff(), "step_outcome": Halted()}
        if isinstance(admission, Blocked):
            return {"next_tool": None, "need_more": False, "step_outcome": admission.prior}
        if isinstance(admission, NotAllowed):
            return {
                "next_tool": None,
                "need_more": False,
                "direct_answer": admission.reason,
                "schedule_card": None,
                "step_outcome": Denied(step.tool, admission.reason),
            }

        tool_ctx = ToolContext(
            db=db,
            message=msg,
            history=hist,
            user_id=step.user_id,
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
            task_id=state.get("task_id"),
            generation=state.get("generation"),
        )
        result = gateway.invoke(step.tool, tool_ctx, step.args)
        if is_stopped():
            return {**_cutoff(), "step_outcome": Halted()}
        outcome = classify_tool_result(admission, result)
        prior_steps = list(steps)
        update = result.as_state_update(base_evidence=evidence)
        update["thinking_steps"] = prior_steps + list(update.get("thinking_steps") or [])
        update["step_outcome"] = outcome
        if isinstance(outcome, UnsettledUnknown):
            update["schedule_card"] = None
            update["direct_answer"] = None
            update["answer_reasoning"] = None
        if isinstance(outcome, ConfirmDraft):
            update["schedule_card"] = outcome.card
            update["direct_answer"] = outcome.sentence
        if isinstance(outcome, ReadDone):
            card = update.get("schedule_card")
            if isinstance(card, dict) and schedule_card_needs_claim(card):
                update["schedule_card"] = None
        if result.denied and "calendar_reply" not in update:
            update.setdefault("need_more", False)
        return update

    def reflect_node(state: WorkflowState) -> dict[str, Any]:
        if is_stopped():
            return {**_cutoff(), "step_outcome": Halted()}
        outcome = state.get("step_outcome")
        if isinstance(outcome, UnsettledUnknown):
            settled = settle_unknown(
                outcome.report,
                db=db,
                user_id=state.get("user_id") or 1,
            )
            steps = list(state.get("thinking_steps") or [])
            steps.append("写入结果未知，改为核对，不再写入")
            return {
                "next_tool": None,
                "pending_tools": [],
                "need_more": False,
                "schedule_card": None,
                "direct_answer": settled.sentence,
                "answer_reasoning": None,
                "step_outcome": settled,
                "thinking_steps": steps,
            }
        if isinstance(outcome, (ConfirmDraft, Denied, Halted, Reconciled, Refused)):
            return {"next_tool": None, "need_more": False, "step_outcome": outcome}
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
        if is_stopped():
            return {**_cutoff(), "step_outcome": Halted()}
        outcome = state.get("step_outcome")
        if isinstance(outcome, _CLOSED):
            ctx = state.get("context") if isinstance(state.get("context"), dict) else ctx_state
            evidence = list(state.get("evidence") or [])
            shell = {
                "intent": sse_intent(
                    forced=bool(state.get("forced")),
                    evidence=evidence,
                    pending_had_kb=bool(state.get("needs_kb")) or bool(state.get("forced")),
                    pending_had_web=bool(state.get("needs_web")),
                    pending_had_calendar=bool(state.get("needs_calendar")),
                    pending_had_sandbox=bool(state.get("needs_sandbox")),
                ),
                "thinking_steps": list(state.get("thinking_steps") or []),
                "artifact": state.get("artifact"),
                "context_stats": dict(ctx.get("stats") or {}),
                "answer_reasoning": state.get("answer_reasoning"),
                "pending_calendar": state.get("pending_calendar"),
                "citations": [],
                "llm_messages": [],
                "direct_answer": state.get("direct_answer"),
                "schedule_card": state.get("schedule_card"),
            }
            return project_reply(outcome, shell)
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
            state = {**state, **answer_pair(canned=direct)}
        intent = sse_intent(
            forced=bool(state.get("forced")),
            evidence=evidence,
            pending_had_kb=bool(state.get("needs_kb")) or bool(state.get("forced")),
            pending_had_web=bool(state.get("needs_web")),
            pending_had_calendar=bool(state.get("needs_calendar")),
            pending_had_sandbox=bool(state.get("needs_sandbox")),
        )
        included: list[dict] = []
        llm_messages: list[dict] = []
        compose_stats: dict = dict(ctx.get("stats") or {})
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
                canned = answer_pair(canned=direct)
                return project_reply(
                    outcome or ReadDone(tool=""),
                    {
                        "intent": intent,
                        "citations": [],
                        "llm_messages": [],
                        "thinking_steps": thinking,
                        "schedule_card": state.get("schedule_card"),
                        "direct_answer": canned["direct_answer"],
                        "answer_reasoning": canned["answer_reasoning"],
                        "pending_calendar": state.get("pending_calendar"),
                        "artifact": state.get("artifact"),
                        "context_stats": compose_stats,
                    },
                )
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
            compose_stats = dict(bundle.stats or {})
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
            compose_stats = dict(bundle.stats or {})

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
            # Infra failures must surface (eval: infra_error; SSE: the existing
            # error event). A silent "" was scored as an ordinary empty answer.
            draft = complete_text(llm_messages, role=Role.CHAT, raise_on_error=True)
            if is_stopped():
                return _cutoff()
            if not isinstance(draft, Spoken):
                draft = Spoken(str(draft or ""), "", None)
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
            if not isinstance(final, Spoken):
                final = Spoken(str(final or ""), draft.reasoning, None)
            thinking.extend(grounding_thinking_steps(critique, repaired=repaired))
            paired = answer_pair(spoken=final)
            direct = paired["direct_answer"]
            state = {**state, **paired}
            llm_messages = []

        return project_reply(
            outcome or ReadDone(tool=""),
            {
                "intent": intent,
                "citations": citations,
                "llm_messages": llm_messages,
                "thinking_steps": thinking,
                "schedule_card": state.get("schedule_card"),
                "direct_answer": direct,
                "answer_reasoning": state.get("answer_reasoning"),
                "pending_calendar": state.get("pending_calendar"),
                "artifact": state.get("artifact"),
                "context_stats": compose_stats,
            },
        )

    def after_plan(state: WorkflowState) -> Literal["retrieve_node", "answer_node"]:
        return "retrieve_node" if state.get("next_tool") else "answer_node"

    def after_reflect(state: WorkflowState) -> Literal["retrieve_node", "answer_node"]:
        return "retrieve_node" if state.get("need_more") and state.get("next_tool") else "answer_node"

    g = StateGraph(WorkflowState)
    g.add_node("retrieve_node", retrieve_node)
    g.add_node("reflect_node", reflect_node)
    g.add_node("answer_node", answer_node)
    g.add_conditional_edges(
        START,
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

    try:
        out = g.compile().invoke(
            {
                "history": history,
                "message": message,
                "use_kb": use_kb,
                "has_kb_docs": has_kb_docs,
                "has_tabular_docs": has_tabular_docs,
                "document_ids": document_ids,
                "user_id": user_id,
                "task_id": task_id,
                "generation": generation,
                "pending_calendar": pending_calendar,
                "context": ctx_state,
                "next_tool": assignment.next_tool,
                "pending_tools": list(assignment.pending_tools),
                "needs_freshness": assignment.needs_freshness,
                "needs_kb": assignment.needs_kb,
                "needs_web": assignment.needs_web,
                "needs_calendar": assignment.needs_calendar,
                "needs_sandbox": assignment.needs_sandbox,
                "forced": assignment.forced,
                "intent": assignment.intent,
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
                "answer_reasoning": None,
                "artifact": None,
            }
        )
    except GenerationStopped:
        return _halted_result()
    if is_stopped():
        return _halted_result()
    return {
        "intent": out.get("intent") or "chat",
        "forced": bool(out.get("forced")),
        "citations": out.get("citations") or [],
        "thinking_steps": out.get("thinking_steps") or [],
        "llm_messages": out.get("llm_messages") or [],
        "schedule_card": out.get("schedule_card"),
        "direct_answer": out.get("direct_answer"),
        "answer_reasoning": out.get("answer_reasoning"),
        "pending_calendar": out.get("pending_calendar"),
        "artifact": out.get("artifact"),
        "analysis_summary": out.get("analysis_summary"),
        "context_stats": out.get("context_stats") if isinstance(out.get("context_stats"), dict) else None,
    }


async def route_intent(
    messages: list[dict],
) -> Literal["chat", "rag", "tool", "web_search", "calendar", "data_analysis"]:
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    return classify_intent(last, messages, has_kb_docs=True, has_tabular_docs=False)
