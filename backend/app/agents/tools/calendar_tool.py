"""Calendar tool: write actions return a confirm card and do not touch the database."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.services.calendar import (
    EventDraft,
    check_conflict,
    draft_expires_at,
    extract_event,
    find_event_candidates,
    free_slots_for,
)
from app.services.calendar_local import LocalCalendarProvider

_UPDATE = re.compile(r"(改到|改成|改为|推迟|提前|换到|挪到|延后)")
_CANCEL = re.compile(r"(取消|撤销|删掉)")
_LIST = re.compile(r"(有哪些|有什么安排|有什么会|查一下日程|查日程|我的日程|哪些日程)")
_SUGGEST = re.compile(r"(有空|空闲|什么时候方便)")


def resolve_action(message: str, args: dict | None) -> str:
    action = str((args or {}).get("action") or "").strip().lower()
    if action == "query":
        action = "list"
    if action in {"create", "update", "cancel", "list", "suggest"}:
        return action
    text = message or ""
    if _SUGGEST.search(text):
        return "suggest"
    if _LIST.search(text):
        return "list"
    if _CANCEL.search(text):
        return "cancel"
    if _UPDATE.search(text):
        return "update"
    return "create"


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def _clock(value: datetime | None) -> str:
    if value is None:
        return ""
    return f"{value:%Y-%m-%d %H:%M}"


def _candidate_payload(row) -> dict:
    return {
        "id": row.id,
        "title": row.title or "",
        "start_at": _iso(row.start_at),
        "end_at": _iso(row.end_at),
    }


def _slot_payload(slots: list[tuple[datetime, datetime]]) -> list[dict]:
    return [{"start_at": _iso(start), "end_at": _iso(end)} for start, end in slots]


def _base_card(*, mode: str, action: str, draft: EventDraft, event_id: int | None = None) -> dict:
    return {
        "mode": mode,
        "phase": "draft",
        "action": action,
        "expires_at": draft_expires_at(),
        "event_id": event_id,
        "title": draft.title or "",
        "start_at": _iso(draft.start_at),
        "end_at": _iso(draft.end_at),
        "participants": list(draft.participants),
        "location": "",
        "description": "",
        "timezone": "",
        "provider": "local",
        "external_id": "",
        "status": "",
        "conflicts": [],
        "suggested_slots": [],
        "candidates": [],
    }


def _confirm_text(card: dict, *, full: bool = False) -> str:
    title = card.get("title") or ""
    start = card.get("start_at") or ""
    end = card.get("end_at") or ""
    if card.get("mode") == "choose":
        return "找到多条日程，请在卡片上选择。未确认不会写入。"
    if full and card.get("conflicts"):
        return "该时段已有日程。请在卡片上改时间或选用建议空隙，确认后才会写入。"
    when = ""
    if start:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end) if end else None
            when = _clock(start_dt)
            if end_dt:
                when = f"{when}–{end_dt:%H:%M}"
        except ValueError:
            when = start
    name = f"「{title}」" if title else "这条日程"
    if card.get("mode") == "cancel":
        return f"请确认撤销{name}{('，时间 ' + when) if when else ''}。未确认不会写入。"
    if card.get("mode") == "update":
        return f"请确认修改{name}{('，时间 ' + when) if when else ''}。未确认不会写入。"
    if not title or not start:
        return "请在卡片上补全后再确认。未确认不会写入。"
    return f"请确认日程{name}，时间 {when}。未确认不会写入。"


def _cleared(steps: list[str], answer: str, card: dict | None = None) -> ToolResult:
    return ToolResult(
        ok=True,
        thinking_steps=steps,
        direct_answer=answer,
        schedule_card=card,
        pending_calendar=None,
        update_pending_calendar=True,
        need_more=False,
        risk="medium",
    )


def _list_text(ctx: ToolContext) -> str:
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = LocalCalendarProvider(ctx.db).list_events(ctx.user_id, now, now + timedelta(days=7))
    if not rows:
        return "接下来 7 天没有日程。"
    lines = []
    for row in rows[:20]:
        if row.start_at is None:
            lines.append(row.title)
        else:
            lines.append(f"{row.start_at:%Y-%m-%d %H:%M} {row.title}")
    return "接下来 7 天的日程：\n" + "\n".join(lines)


def _suggest_text(ctx: ToolContext) -> str:
    start = datetime.now().replace(second=0, microsecond=0)
    slots = LocalCalendarProvider(ctx.db).find_free_slots(
        ctx.user_id, start, start + timedelta(days=7), timedelta(hours=1), limit=3
    )
    if not slots:
        return "接下来 7 天没有足够的空闲时段。"
    lines = [f"{begin:%Y-%m-%d %H:%M}–{end:%H:%M}" for begin, end in slots]
    return "接下来可以考虑这些空闲时段：\n" + "\n".join(lines)


def execute_calendar(ctx: ToolContext, args: dict | None = None) -> ToolResult:
    steps = [f"第 {ctx.iteration} 轮：解析日程并生成确认卡片"]
    action = resolve_action(ctx.message, args or {})
    if action == "list":
        steps.append("只读查询，不写入")
        return _cleared(steps, _list_text(ctx))
    if action == "suggest":
        steps.append("只读空闲建议，不写入")
        return _cleared(steps, _suggest_text(ctx))

    draft = extract_event(ctx.message, ctx.history)
    if action == "create":
        card = _base_card(mode="create", action="create", draft=draft)
        if draft.start_at and draft.end_at:
            conflicts = check_conflict(ctx.db, ctx.user_id, draft.start_at, draft.end_at)
            if conflicts:
                card["conflicts"] = [_candidate_payload(row) for row in conflicts[:5]]
                card["suggested_slots"] = _slot_payload(
                    free_slots_for(ctx.db, ctx.user_id, draft.start_at, draft.end_at)
                )
                if not card["suggested_slots"]:
                    steps.append("该时段已满")
        steps.append("已生成待确认卡片，尚未写入")
        return _cleared(steps, _confirm_text(card, full=bool(card["conflicts"])), card)

    matches = find_event_candidates(ctx.db, ctx.user_id, title=draft.title, start_at=draft.start_at)
    if not matches:
        steps.append("没有定位到已有日程")
        return _cleared(steps, "没有找到对应日程。")
    if len(matches) > 1:
        card = _base_card(mode="choose", action=action, draft=draft)
        card["candidates"] = [_candidate_payload(row) for row in matches]
        card["title"] = ""
        card["start_at"] = ""
        card["end_at"] = ""
        steps.append("多条日程，等待在卡片上选择")
        return _cleared(steps, _confirm_text(card), card)

    event = matches[0]
    chosen = EventDraft(
        title=event.title or draft.title,
        start_at=event.start_at,
        end_at=event.end_at,
        participants=draft.participants,
    )
    card = _base_card(mode=action, action=action, draft=chosen, event_id=event.id)
    card["status"] = event.status or "active"
    if action == "update" and draft.start_at:
        card["start_at"] = _iso(draft.start_at)
        card["end_at"] = _iso(draft.end_at or event.end_at)
        start = draft.start_at
        end = draft.end_at or event.end_at
        if start and end:
            conflicts = [
                row
                for row in check_conflict(ctx.db, ctx.user_id, start, end)
                if row.id != event.id
            ]
            if conflicts:
                card["conflicts"] = [_candidate_payload(row) for row in conflicts[:5]]
                card["suggested_slots"] = _slot_payload(
                    free_slots_for(ctx.db, ctx.user_id, start, end, exclude_event_id=event.id)
                )
    steps.append("已生成待确认卡片，尚未写入")
    return _cleared(steps, _confirm_text(card, full=bool(card.get("conflicts"))), card)


register(
    ToolSpec(
        name="calendar",
        risk="medium",
        execute=execute_calendar,
        description="Prepare a local calendar change for the user to confirm",
    )
)
