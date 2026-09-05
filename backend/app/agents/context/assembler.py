"""Assemble virtual context into router/answer/tool views and answer messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.context.budget import TokenBudget, estimate_messages_tokens
from app.agents.context.window import flatten_turns, recent_turns, to_logical_turns
from app.agents.context.working_state import (
    format_context_line,
    format_working_block,
    load_working_state,
)
from app.core.config import settings
from app.core.prompts import answer_rules, no_evidence_block
from app.services.memory import inject_system_memory


@dataclass
class ContextBundle:
    answer_history: list[dict] = field(default_factory=list)
    router_history: list[dict] = field(default_factory=list)
    tool_history: list[dict] = field(default_factory=list)
    summary_block: str = ""
    working_block: str = ""
    context_line: str = ""
    memory_block: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def to_state(self) -> dict[str, Any]:
        return {
            "answer_history": list(self.answer_history),
            "router_history": list(self.router_history),
            "tool_history": list(self.tool_history),
            "summary_block": self.summary_block or "",
            "working_block": self.working_block or "",
            "context_line": self.context_line or "",
            "memory_block": self.memory_block or "",
            "stats": dict(self.stats or {}),
        }

    @classmethod
    def from_history(cls, history: list[dict] | None) -> ContextBundle:
        """Compat path when workflow is invoked without a Session/context."""
        hist = list(history or [])
        if not settings.context_manager_enabled:
            limit = max(1, int(settings.max_context_turns)) * 2
            clipped = hist[-limit:] if len(hist) > limit else hist
            return cls(
                answer_history=clipped,
                router_history=clipped[-4:],
                tool_history=clipped[-8:],
                stats={"tokens": 0, "level": "safe", "dropped_turns": 0, "total_turns": 0},
            )
        turns = to_logical_turns(hist)
        kept, overflow = recent_turns(turns, int(settings.max_context_turns))
        answer = flatten_turns(kept)
        router_kept, _ = recent_turns(turns, int(settings.context_router_turns))
        tool_kept, _ = recent_turns(turns, int(settings.context_tool_turns))
        return cls(
            answer_history=answer,
            router_history=flatten_turns(router_kept),
            tool_history=flatten_turns(tool_kept),
            stats={
                "tokens": estimate_messages_tokens(answer),
                "level": "safe",
                "dropped_turns": len(overflow),
                "total_turns": len(turns),
            },
        )


def _legacy_window(history: list[dict]) -> ContextBundle:
    limit = max(1, int(settings.max_context_turns)) * 2
    clipped = history[-limit:] if len(history) > limit else list(history)
    return ContextBundle(
        answer_history=clipped,
        router_history=clipped[-4:],
        tool_history=clipped[-8:],
        stats={
            "tokens": estimate_messages_tokens(clipped),
            "level": "safe",
            "dropped_turns": max(0, (len(history) - len(clipped) + 1) // 2),
            "total_turns": 0,
        },
    )


def build_context(
    *,
    session: Any = None,
    history: list[dict] | None = None,
    message: str = "",
    pending_calendar: dict | None = None,
    memory_block: str = "",
) -> ContextBundle:
    hist = list(history or [])
    if not settings.context_manager_enabled:
        return _legacy_window(hist)

    turns = to_logical_turns(hist)
    kept, overflow = recent_turns(turns, int(settings.max_context_turns))
    answer_history = flatten_turns(kept)
    router_kept, _ = recent_turns(turns, int(settings.context_router_turns))
    tool_kept, _ = recent_turns(turns, int(settings.context_tool_turns))
    router_history = flatten_turns(router_kept)
    tool_history = flatten_turns(tool_kept)

    summary_raw = ""
    ws: dict = {}
    if session is not None:
        summary_raw = str(getattr(session, "context_summary", "") or "").strip()
        ws = load_working_state(session)

    summary_block = ""
    if summary_raw:
        summary_block = (
            "【会话摘要】以下为更早对话的压缩工作状态，请据此延续，"
            "不要重复询问已确认信息：\n"
            + summary_raw
        )
    elif overflow:
        summary_block = f"（另有 {len(overflow)} 轮更早对话未展示）"

    working_block = format_working_block(pending_calendar, ws)
    context_line = format_context_line(pending_calendar, ws)

    budget = TokenBudget.from_settings()
    preview = []
    if memory_block:
        preview.append({"role": "system", "content": memory_block})
    if summary_block or working_block:
        preview.append(
            {
                "role": "system",
                "content": "\n\n".join(x for x in (working_block, summary_block) if x),
            }
        )
    preview.extend(answer_history)
    if message:
        preview.append({"role": "user", "content": message})
    used = estimate_messages_tokens(preview)

    return ContextBundle(
        answer_history=answer_history,
        router_history=router_history,
        tool_history=tool_history,
        summary_block=summary_block,
        working_block=working_block,
        context_line=context_line,
        memory_block=memory_block or "",
        stats={
            "tokens": used,
            "level": budget.level(used),
            "dropped_turns": len(overflow),
            "total_turns": len(turns),
        },
    )


def _clip_memory(block: str, max_chars: int) -> str:
    text = (block or "").strip()
    if not text or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars)]


def compose_answer_messages(
    *,
    bundle: ContextBundle | None = None,
    hist: list[dict] | None = None,
    message: str,
    pool_text: str,
    memory_block: str = "",
    rebuild_pool: Callable[[int], tuple[str, list[dict]]] | None = None,
    sandbox_note: str = "",
    has_evidence_system: bool = True,
) -> tuple[list[dict], str, list[dict]]:
    """Build answer llm_messages under token watermarks.

    Returns (messages, pool_text_used, included_evidence_rows).
    included_evidence_rows is empty when rebuild_pool is None.
    """
    b = bundle or ContextBundle(answer_history=list(hist or []))
    history = list(hist if hist is not None else b.answer_history)
    mem = (memory_block or b.memory_block or "").strip()
    working = (b.working_block or "").strip()
    summary = (b.summary_block or "").strip()
    pool = pool_text or ""
    included: list[dict] = []

    plain = (
        not has_evidence_system
        and not working
        and not summary
        and not mem
        and not (sandbox_note or "").strip()
    )
    if plain:
        messages = [*history, {"role": "user", "content": message}]
        budget = TokenBudget.from_settings()
        used = estimate_messages_tokens(messages)
        level = budget.level(used)
        # Still apply emergency trim on plain chat if over budget.
        if level in ("compact", "emergency") and history:
            turns = to_logical_turns(history)
            keep_n = 1 if level == "emergency" else max(1, len(turns) // 2)
            history = flatten_turns(turns[-keep_n:])
            messages = [*history, {"role": "user", "content": message}]
        return messages, pool, included

    def build(pool_body: str, hist_msgs: list[dict], mem_block: str) -> list[dict]:
        parts = []
        if (sandbox_note or "").strip():
            parts.append(sandbox_note.strip())
        if pool_body:
            parts.append(pool_body)
        if working:
            parts.append(working)
        if summary:
            parts.append(summary)
        system = "\n\n".join(parts)
        msgs: list[dict] = [{"role": "system", "content": system}, *hist_msgs, {"role": "user", "content": message}]
        if mem_block:
            msgs = inject_system_memory(msgs, mem_block)
        return msgs

    if not pool.strip() and has_evidence_system:
        pool = "\n".join([answer_rules(), "", "【证据池】", no_evidence_block()]).strip()

    messages = build(pool, history, mem)
    budget = TokenBudget.from_settings()
    used = estimate_messages_tokens(messages)
    level = budget.level(used)

    if level == "safe":
        return messages, pool, included

    # warn: shrink evidence pool + memory
    if rebuild_pool is not None:
        warn_chars = int(settings.context_pool_chars_warn)
        pool, included = rebuild_pool(warn_chars)
        mem = _clip_memory(mem, 400)
        messages = build(pool, history, mem)
        used = estimate_messages_tokens(messages)
        level = budget.level(used)
        if level == "safe" or level == "warn":
            # warn is acceptable after pool shrink
            if level == "warn" and budget.level(used) != "compact":
                return messages, pool, included

    used = estimate_messages_tokens(messages)
    level = budget.level(used)
    if level in ("compact", "emergency"):
        turns = to_logical_turns(history)
        while turns and budget.level(estimate_messages_tokens(build(pool, flatten_turns(turns), mem))) in (
            "compact",
            "emergency",
        ):
            if len(turns) <= 1:
                break
            turns = turns[1:]  # drop oldest turn
        history = flatten_turns(turns)
        messages = build(pool, history, mem)
        used = estimate_messages_tokens(messages)
        level = budget.level(used)

    if level == "emergency":
        if rebuild_pool is not None:
            pool, included = rebuild_pool(int(settings.context_pool_chars_emergency))
        turns = to_logical_turns(history)
        history = flatten_turns(turns[-1:]) if turns else []
        mem = _clip_memory(mem, 200)
        messages = build(pool, history, mem)

    return messages, pool, included
