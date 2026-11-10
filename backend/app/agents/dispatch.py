"""The assignment written before the work graph runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
