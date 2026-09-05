"""Logical-turn sliding window helpers for ContextManager."""

from __future__ import annotations

from typing import Any


def to_logical_turns(history: list[dict]) -> list[list[dict]]:
    """Split history into user-led turns (user + optional assistant).

    Leading orphan assistant messages are dropped (start_on_user).
    Extra keys (e.g. ``id``) are preserved for summary coverage tracking.
    """
    turns: list[list[dict]] = []
    current: list[dict] | None = None
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        msg = dict(raw)
        msg["role"] = role
        msg["content"] = str(raw.get("content") or "")
        if role == "user":
            if current:
                turns.append(current)
            current = [msg]
        elif current is not None:
            current.append(msg)
        # else: orphan assistant before first user — drop
    if current:
        turns.append(current)
    return turns


def flatten_turns(turns: list[list[dict]]) -> list[dict]:
    out: list[dict] = []
    for turn in turns:
        out.extend(turn)
    return out


def recent_turns(
    turns: list[list[dict]],
    k: int,
) -> tuple[list[list[dict]], list[list[dict]]]:
    """Return (kept recent k turns, overflow older turns)."""
    n = max(0, int(k))
    if n <= 0:
        return [], list(turns)
    if len(turns) <= n:
        return list(turns), []
    return list(turns[-n:]), list(turns[:-n])


def clip_history_for_prompt(
    history: list[dict] | None,
    *,
    max_msgs: int = 8,
    max_chars: int = 300,
) -> list[dict[str, Any]]:
    """Trim secondary prompts (calendar extract, etc.) by message count and chars."""
    msgs = [
        m
        for m in (history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    limit = max(0, int(max_msgs))
    if limit and len(msgs) > limit:
        msgs = msgs[-limit:]
    out: list[dict[str, Any]] = []
    cap = max(0, int(max_chars))
    for m in msgs:
        content = str(m.get("content") or "")
        if cap and len(content) > cap:
            content = content[:cap]
        out.append({"role": m.get("role"), "content": content})
    return out
