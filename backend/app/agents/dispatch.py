from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.agents.context import ContextBundle
from app.agents.router import Intent, plan_tools

ToolName = Literal["kb", "web", "calendar", "sandbox"]


def sse_intent(
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


@dataclass(frozen=True)
class TurnAssignment:
    next_tool: ToolName | None
    pending_tools: tuple[ToolName, ...]
    needs_freshness: bool
    needs_kb: bool
    needs_web: bool
    needs_calendar: bool
    needs_sandbox: bool
    forced: bool
    intent: Intent

    @classmethod
    def from_plan(cls, plan: dict, *, forced: bool) -> TurnAssignment:
        queue = list(plan["pending_tools"])
        next_tool = queue[0] if queue else None
        tail = tuple(queue[1:]) if queue else ()
        needs_kb = bool(plan["needs_kb"])
        needs_web = bool(plan["needs_web"])
        needs_calendar = bool(plan.get("needs_calendar"))
        needs_sandbox = bool(plan.get("needs_sandbox"))
        return cls(
            next_tool=next_tool,
            pending_tools=tail,
            needs_freshness=plan["needs_freshness"],
            needs_kb=needs_kb,
            needs_web=needs_web,
            needs_calendar=needs_calendar,
            needs_sandbox=needs_sandbox,
            forced=forced,
            intent=sse_intent(
                forced=forced,
                evidence=[],
                pending_had_kb=needs_kb,
                pending_had_web=needs_web,
                pending_had_calendar=needs_calendar,
                pending_had_sandbox=needs_sandbox,
            ),
        )


def assign_turn(
    message: str,
    history: list[dict],
    *,
    context: dict | None = None,
    use_kb: bool = False,
    has_kb_docs: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
) -> TurnAssignment:
    ctx = context if isinstance(context, dict) else ContextBundle.from_history(history).to_state()
    router_hist = list(ctx.get("router_history") or history or [])
    context_line = str(ctx.get("context_line") or "")
    planned = plan_tools(
        message,
        router_hist,
        has_kb_docs=has_kb_docs,
        forced_kb=use_kb,
        has_tabular_docs=has_tabular_docs,
        document_ids=document_ids or None,
        context_line=context_line,
    )
    return TurnAssignment.from_plan(planned, forced=bool(use_kb))


class TalkReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relation: Literal["supplement", "redirect", "new", "cancel", "ask"]
    target_generation: int | None = None
    web: Literal["allow", "forbid"] = "allow"
    ask: str = ""


@dataclass(frozen=True)
class OpenJob:
    generation: int
    kind: str
    status: str
    goal: str
    web_constraint: str


def _ask(text: str = "") -> TalkReply:
    return TalkReply(relation="ask", target_generation=None, web="allow", ask=text or "")


def _resolve(parsed: TalkReply, jobs: list[OpenJob]) -> TalkReply:
    web = "forbid" if parsed.web == "forbid" else "allow"
    if parsed.relation == "new":
        return TalkReply(relation="new", target_generation=None, web=web, ask="")
    if parsed.relation == "ask":
        return TalkReply(relation="ask", target_generation=None, web="allow", ask=parsed.ask or "")
    open_rows = [job for job in jobs if job.kind == "job"]
    by_generation = {job.generation: job for job in open_rows}
    target = parsed.target_generation
    if target in by_generation:
        chosen = target
    elif target is None and len(open_rows) == 1:
        chosen = open_rows[0].generation
    else:
        return _ask(parsed.ask or "")
    return TalkReply(relation=parsed.relation, target_generation=chosen, web=web, ask="")


def classify_message(message: str, jobs: list[OpenJob]) -> TalkReply:
    """Ask the router which open job this sentence is about.

    The production path does not read a fixture and does not scan the sentence
    for cancel, stop, forbid, or search words. A bad reply asks and changes nothing.
    """
    from app.core.llm import Role, complete_json_object

    listed = [
        {
            "generation": job.generation,
            "kind": job.kind,
            "status": job.status,
            "goal": (job.goal or "")[:120],
            "web_constraint": job.web_constraint,
        }
        for job in jobs
    ]
    try:
        parsed = complete_json_object(
            [
                {
                    "role": "system",
                    "content": (
                        "Return one JSON object with relation, target_generation, web, and ask. "
                        "relation is supplement, redirect, new, cancel, or ask. "
                        "target_generation is an integer from open_jobs or null. "
                        "web is allow or forbid. "
                        "ask is a short question when relation is ask, otherwise empty."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "open_jobs": listed},
                        ensure_ascii=False,
                    ),
                },
            ],
            TalkReply,
            role=Role.ROUTER,
        )
    except Exception:
        return _ask()
    if not isinstance(parsed, TalkReply):
        return _ask()
    return _resolve(parsed, jobs)
