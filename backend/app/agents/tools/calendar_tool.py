"""Calendar tool: write actions return a confirm card and do not touch the database."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.core.llm import complete_text
from app.services.calendar import (
    REMOTE_READ_ERRORS,
    EventDraft,
    _civil_day,
    apply_update_times,
    calendar_now,
    check_conflict,
    conflicts_for_create,
    conflicts_for_update,
    draft_expires_at,
    extract_event,
    extract_free_slots,
    extract_list_day,
    find_event_candidates,
    free_slots_for,
    readonly_titles,
    titles_match,
)
from app.services.calendar_factory import (
    account_timezone_name,
    calendar_provider_for,
    calendar_writes_remote,
)
from app.services.calendar_google import _LIST_LOCAL_ONLY, _LIST_TRUNCATED
from app.services.calendar_secrets import CalendarTransientError

_GOOGLE_UNCHECKED = "暂时没读到 Google 日历，确认时会再查；查不到就不会写入。"
_ADVICE_UNAVAILABLE = "暂时给不出建议"
_SKIP_ADVICE = frozenset({_LIST_LOCAL_ONLY, _LIST_TRUNCATED})
_SUGGEST_UNAVAILABLE = "暂时读不到 Google 日历，不能建议空闲时段。"
_SAFE_SLOT_NOTE = "请以上面的列表为准，已有安排的时段不是空闲。"
_FREE_CLAIM = ("有空", "空闲", "方便", "可以")
_BUSY_ACK = ("重叠", "占用", "已有安排", "不是空", "没空", "忙")
_NOT_FOUND = "没有找到对应日程。"
_NOT_FOUND_UNREAD = "没有找到对应日程。暂时读不到 Google 日历，以下只是这里保存的日程。"
_READONLY_IN_GOOGLE = "这条日程只在 Google 日历里，请在 Google 日历里改。"

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


def _conflict_payload(row) -> dict:
    event_id = getattr(row, "id", None)
    title = getattr(row, "title", "") or ""
    if event_id is None:
        title = "Google 日历上已有安排"
    payload = {
        "id": event_id,
        "title": title,
        "start_at": _iso(getattr(row, "start_at", None)),
        "end_at": _iso(getattr(row, "end_at", None)),
    }
    if event_id is None:
        payload["readonly"] = True
    return payload


def _slot_payload(slots: list[tuple[datetime, datetime]]) -> list[dict]:
    return [{"start_at": _iso(start), "end_at": _iso(end)} for start, end in slots]


def _write_kind(action: str, draft: EventDraft) -> str:
    """Create and update pick meeting or block. Cancel and choose stay blank."""
    if action not in {"create", "update"}:
        return ""
    return "block" if (draft.kind or "").strip().lower() == "block" else "meeting"


def _people(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _infer_kind(participants: list[str], location: str) -> str:
    """A span with nobody and nowhere is a block. Anything else is a meeting."""
    if not participants and not (location or "").strip():
        return "block"
    return "meeting"


def _as_clock(value) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _rescheduled_end(
    draft: EventDraft, old_start: datetime | None, old_end: datetime | None
) -> datetime | None:
    """Model end only when that end still belongs to the model's start.

    A local regex may move the start after extraction. That move is a new
    start, not a request to change the duration, so the matched length wins.
    """
    new_start = draft.start_at
    specified = draft.specified_end
    if (
        isinstance(specified, datetime)
        and isinstance(new_start, datetime)
        and specified > new_start
        and new_start == draft.model_start
    ):
        return specified
    if (
        isinstance(new_start, datetime)
        and isinstance(old_start, datetime)
        and isinstance(old_end, datetime)
        and old_end > old_start
    ):
        return new_start + (old_end - old_start)
    if isinstance(draft.end_at, datetime):
        return draft.end_at
    return old_end


def _apply_matched_fields(draft: EventDraft, matched: dict, action: str) -> None:
    """Copy one matched event onto the draft. Update keeps the model's new start."""
    title = str(matched.get("title") or "").strip()
    if title:
        draft.title = title
    participants = _people(matched.get("participants"))
    location = str(matched.get("location") or "")
    description = str(matched.get("description") or "")
    old_start = _as_clock(matched.get("start_at"))
    old_end = _as_clock(matched.get("end_at"))
    draft.participants = participants
    draft.location = location
    if action == "cancel":
        draft.description = description
        if old_start is not None:
            draft.start_at = old_start
        if old_end is not None:
            draft.end_at = old_end
        return
    draft.kind = _infer_kind(participants, location)
    if not draft.notes_specified:
        draft.description = description
    draft.start_at = draft.start_at or old_start
    draft.end_at = _rescheduled_end(draft, old_start, old_end)


def _base_card(
    *,
    mode: str,
    action: str,
    draft: EventDraft,
    event_id: int | None = None,
    kind: str = "",
) -> dict:
    participants = [] if kind == "block" else list(draft.participants)
    return {
        "mode": mode,
        "phase": "draft",
        "action": action,
        "kind": kind,
        "expires_at": draft_expires_at(),
        "event_id": event_id,
        "title": draft.title or "",
        "start_at": _iso(draft.start_at),
        "end_at": _iso(draft.end_at),
        "participants": participants,
        "location": "" if kind == "block" else (draft.location or ""),
        "description": draft.description or "",
        "timezone": "",
        "provider": "local",
        "external_id": "",
        "status": "",
        "conflicts": [],
        "suggested_slots": [],
        "candidates": [],
        "items": [],
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


def _day_start(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _list_span(day: str, now: datetime) -> tuple[datetime, datetime, str, str]:
    """Model-named civil day, or the same 7-day window a blank list day uses."""
    named = _civil_day(day)
    if named:
        start = datetime.fromisoformat(named)
        return start, start + timedelta(days=1), f"{named} 的日程：", "这一天没有日程。"
    start = _day_start(now)
    return start, start + timedelta(days=7), "接下来 7 天的日程：", "接下来 7 天没有日程。"


def _item_payload(row) -> dict:
    return {
        "title": row.title or "（无标题）",
        "start_at": _iso(getattr(row, "start_at", None)),
        "end_at": _iso(getattr(row, "end_at", None)),
        "all_day": bool(getattr(row, "all_day", False)),
    }


def _list_card(rows, slots: list[tuple[datetime, datetime]] | None = None) -> dict | None:
    """Read-only rows. No rows and no slots means no card."""
    items = [_item_payload(row) for row in rows[:20]]
    suggested = _slot_payload(slots or [])
    if not items and not suggested:
        return None
    return {
        "mode": "list",
        "phase": "info",
        "action": "list",
        "kind": "",
        "expires_at": "",
        "event_id": None,
        "title": "",
        "start_at": "",
        "end_at": "",
        "participants": [],
        "location": "",
        "description": "",
        "timezone": "",
        "provider": "local",
        "external_id": "",
        "status": "",
        "conflicts": [],
        "suggested_slots": suggested,
        "candidates": [],
        "items": items,
    }


def _list_line(row) -> str:
    title = row.title or "（无标题）"
    suffix = "（来自 Google）" if getattr(row, "readonly", False) else ""
    if getattr(row, "all_day", False):
        return f"{_all_day_label(row.start_at, row.end_at)} {title}{suffix}".strip()
    if row.start_at is None:
        return f"{title}{suffix}"
    return f"{row.start_at:%Y-%m-%d %H:%M} {title}{suffix}"


def _all_day_label(start: datetime | None, end: datetime | None) -> str:
    """All-day display. ``end.date`` is exclusive, and the clock is not 00:00."""
    if start is None:
        return "全天"
    start_day = start.date()
    if end is None:
        return f"{start_day.isoformat()} 全天"
    end_day = end.date()
    last = end_day - timedelta(days=1) if end_day > start_day else start_day
    if last == start_day:
        return f"{start_day.isoformat()} 全天"
    return f"{start_day.isoformat()}–{last.isoformat()} 全天"


def _saved_rows_when_clock_fails(ctx: ToolContext) -> tuple[str, dict | None]:
    """Local rows only. A failed account clock is not replaced with the process zone."""
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    local = getattr(provider, "_local", provider)
    rows = local.list_events(ctx.user_id, datetime(2000, 1, 1), datetime(2100, 1, 1))
    return _LIST_LOCAL_ONLY, _list_card(rows)


def _advice_prompt(now: datetime, zone_name: str) -> str:
    day = now.date().isoformat()
    if zone_name:
        today = f"今天是 {day}，账户时区是 {zone_name}。"
    else:
        today = f"今天是 {day}。"
    return (
        "你在帮用户看已经列出的日程。只写几句中文建议。"
        f" {today}"
        " 列出的时间是账户墙钟。"
        " 看密度、缓冲、整段空闲是否适合专注、是否过载该挪走某件。"
        " 不要逐段填空，不要把每个空档都填上，不要创建日程，不要输出卡片。"
    )


def _planning_advice(now: datetime, zone_name: str, listed: str) -> str:
    """Model notes on the list already shown. A failed call does not invent a schedule."""
    try:
        text = complete_text(
            [
                {"role": "system", "content": _advice_prompt(now, zone_name)},
                {"role": "user", "content": listed},
            ],
            raise_on_error=True,
        )
    except Exception:
        return _ADVICE_UNAVAILABLE
    cleaned = (text or "").strip()
    return cleaned or _ADVICE_UNAVAILABLE


def _zone_name(ctx: ToolContext) -> str:
    if ctx.db is None or ctx.user_id is None:
        return ""
    try:
        return account_timezone_name(ctx.db, ctx.user_id) or ""
    except REMOTE_READ_ERRORS:
        return ""


def _listed_text(rows, header: str, empty: str) -> str:
    if not rows:
        return empty
    lines = [_list_line(row) for row in rows[:20]]
    return header + "\n" + "\n".join(lines)


def _list_text(
    ctx: ToolContext,
    *,
    day: str = "",
    now: datetime | None = None,
    zone_name: str | None = None,
) -> tuple[str, dict | None]:
    if now is None:
        try:
            now = calendar_now(ctx.db, ctx.user_id)
        except CalendarTransientError:
            return _saved_rows_when_clock_fails(ctx)
    zone = zone_name if zone_name is not None else _zone_name(ctx)
    start, end, header, empty = _list_span(day, now)
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    rows = provider.list_events(ctx.user_id, start, end)
    notice = str(getattr(provider, "list_notice", "") or "")
    shown = list(rows[:20])
    card = _list_card(shown)
    listed = _listed_text(shown, header, empty)
    if notice in _SKIP_ADVICE:
        return notice, card
    advice = _planning_advice(now, zone, listed)
    if not shown:
        return f"{empty}\n\n{advice}", None
    return advice, card


def _overlaps_listed(start: datetime, end: datetime, rows) -> bool:
    """True when the slot cuts a listed event. Touching endpoints do not."""
    if end <= start:
        return False
    for row in rows:
        busy_start = getattr(row, "start_at", None)
        busy_end = getattr(row, "end_at", None)
        if not isinstance(busy_start, datetime) or not isinstance(busy_end, datetime):
            continue
        if busy_end <= busy_start:
            continue
        if start < busy_end and end > busy_start:
            return True
    return False


def _without_busy_overlap(slots, rows):
    """Drop slots that overlap a listed event. Clock hour is not a reason to drop."""
    return [pair for pair in slots if not _overlaps_listed(pair[0], pair[1], rows)]


def _replace_unreliable_free_note(note: str, removed: int) -> str:
    """Replace a note that can still call a filtered-out busy slot free.

    A note that already admits the overlap stays. No clock is parsed from it.
    """
    text = (note or "").strip()
    if removed <= 0 or not text:
        return text
    claims_free = any(token in text for token in _FREE_CLAIM)
    admits_busy = any(token in text for token in _BUSY_ACK)
    if claims_free and not admits_busy:
        return _SAFE_SLOT_NOTE
    return text


def _suggest_result(ctx: ToolContext) -> tuple[str, dict | None]:
    """List the named day, then let the model pick slots. No gap algorithm."""
    try:
        now = calendar_now(ctx.db, ctx.user_id)
    except CalendarTransientError:
        return _SUGGEST_UNAVAILABLE, None
    zone = _zone_name(ctx)
    day = extract_list_day(ctx.message, ctx.history, now=now, timezone_name=zone or None)
    start, end, header, empty = _list_span(day, now)
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    try:
        rows = provider.list_events(ctx.user_id, start, end)
    except REMOTE_READ_ERRORS:
        return _SUGGEST_UNAVAILABLE, None
    notice = str(getattr(provider, "list_notice", "") or "")
    if notice in _SKIP_ADVICE:
        return _SUGGEST_UNAVAILABLE, None
    shown = list(rows[:20])
    listed = _listed_text(shown, header, empty)
    picked = extract_free_slots(
        ctx.message,
        ctx.history,
        now=now,
        timezone_name=zone or None,
        listed=listed,
    )
    if picked is None:
        return _ADVICE_UNAVAILABLE, _list_card(shown)
    slots, note = picked
    kept = _without_busy_overlap(slots, shown)
    note = _replace_unreliable_free_note(note, len(slots) - len(kept))
    card = _list_card(shown, kept)
    if note:
        return note, card
    if kept:
        return "可以考虑下面这些时段。", card
    return _ADVICE_UNAVAILABLE, card


def _writes_remote(ctx: ToolContext) -> bool:
    if ctx.db is None or ctx.user_id is None:
        return False
    return calendar_writes_remote(ctx.db, ctx.user_id)


def _confirm_horizon(now: datetime) -> tuple[datetime, datetime]:
    """Same window confirm lists: account wall now through year 2100."""
    return now.replace(microsecond=0), datetime(2100, 1, 1)


def _align_connected_title(
    ctx: ToolContext,
    draft: EventDraft,
    action: str,
    now: datetime,
    zone_name: str | None,
) -> str:
    """Copy one live primary event onto a connected update or cancel draft.

    One exact summary, or else exactly one soft title, supplies kind, times,
    notes, participants, and location. The return value is that event's Google
    id, or empty when nothing unique matched. Update keeps the model's new
    start. Its end and notes change only when extraction says the user asked.
    Cancel uses the event's wall times. Zero or many soft hits, or a failed
    read, leave the extraction and return no id.
    """
    title = (draft.title or "").strip()
    if not title or not zone_name or ctx.db is None or ctx.user_id is None:
        return ""
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    reader = getattr(provider, "primary_events_for_title", None)
    if reader is None:
        return ""
    start, end = _confirm_horizon(now)
    try:
        events = reader(start, end, zone_name)
    except REMOTE_READ_ERRORS:
        return ""
    if not isinstance(events, list):
        return ""
    exact = [
        item
        for item in events
        if isinstance(item, dict) and str(item.get("summary") or "").strip() == title
    ]
    if len(exact) == 1:
        match = exact[0]
    else:
        soft = [
            item
            for item in events
            if isinstance(item, dict) and titles_match(title, str(item.get("summary") or ""))
        ]
        if len(soft) != 1:
            return ""
        match = soft[0]
    _apply_matched_fields(
        draft,
        {
            "title": match.get("summary"),
            "start_at": match.get("start_at"),
            "end_at": match.get("end_at"),
            "participants": match.get("participants") or [],
            "location": match.get("location") or "",
            "description": match.get("description") or "",
        },
        action,
    )
    return str(match.get("id") or "").strip()


def _remote_change_preview(
    ctx: ToolContext,
    steps: list[str],
    action: str,
    draft: EventDraft,
    *,
    now: datetime,
    zone_name: str | None,
) -> ToolResult:
    """Connected update/cancel. Prefill copies one matched event. Confirm writes."""
    matched_id = _align_connected_title(ctx, draft, action, now, zone_name)
    card = _base_card(mode=action, action=action, draft=draft, kind=_write_kind(action, draft))
    if matched_id:
        card["external_id"] = matched_id
    if zone_name:
        card["timezone"] = zone_name
    remote_note = ""
    if action == "update" and draft.start_at and draft.end_at:
        conflicts, remote_note = _update_preview_conflicts(
            ctx, draft.start_at, draft.end_at, None
        )
        if conflicts:
            card["conflicts"] = [_conflict_payload(row) for row in conflicts[:5]]
            if remote_note:
                card["suggested_slots"] = []
            else:
                slots, slot_note = _preview_slots(ctx, draft.start_at, draft.end_at)
                remote_note = remote_note or slot_note
                card["suggested_slots"] = [] if remote_note else _slot_payload(slots)
    steps.append("已生成待确认卡片，尚未写入")
    return _cleared(steps, _create_answer(card, remote_note), card)


def _update_preview_conflicts(
    ctx: ToolContext, start: datetime, end: datetime, event_id: int | None
) -> tuple[list, str]:
    """Merged busy time for a connected time change. A failed read keeps local conflicts only."""
    if not calendar_writes_remote(ctx.db, ctx.user_id):
        rows = [
            row
            for row in check_conflict(ctx.db, ctx.user_id, start, end)
            if getattr(row, "id", None) != event_id
        ]
        return rows, ""
    try:
        return (
            conflicts_for_update(
                ctx.db, ctx.user_id, start, end, exclude_event_id=event_id
            ),
            "",
        )
    except REMOTE_READ_ERRORS:
        rows = [
            row
            for row in check_conflict(ctx.db, ctx.user_id, start, end)
            if getattr(row, "id", None) != event_id
        ]
        return rows, _GOOGLE_UNCHECKED


def _readonly_reply(
    ctx: ToolContext, draft: EventDraft, search_at: datetime | None, now: datetime
) -> str | None:
    """When nothing local matches, a Google-only title is not an editable card."""
    if not calendar_writes_remote(ctx.db, ctx.user_id) or not (draft.title or "").strip():
        return None
    try:
        titles = readonly_titles(
            ctx.db, ctx.user_id, title=draft.title, start_at=search_at, now=now
        )
    except REMOTE_READ_ERRORS:
        return _NOT_FOUND_UNREAD
    if any(titles_match(draft.title, item) for item in titles):
        return _READONLY_IN_GOOGLE
    return None


def _create_conflicts(ctx: ToolContext, start: datetime, end: datetime) -> tuple[list, str]:
    """Local conflicts, plus primary-calendar busy time when Google is connected.

    A failed read still returns local conflicts and a note. It does not recommend slots.
    """
    if not calendar_writes_remote(ctx.db, ctx.user_id):
        return check_conflict(ctx.db, ctx.user_id, start, end), ""
    try:
        return conflicts_for_create(ctx.db, ctx.user_id, start, end), ""
    except REMOTE_READ_ERRORS:
        return check_conflict(ctx.db, ctx.user_id, start, end), _GOOGLE_UNCHECKED


def _preview_slots(
    ctx: ToolContext,
    start: datetime,
    end: datetime,
    *,
    exclude_event_id: int | None = None,
) -> tuple[list[tuple[datetime, datetime]], str]:
    try:
        return (
            free_slots_for(ctx.db, ctx.user_id, start, end, exclude_event_id=exclude_event_id),
            "",
        )
    except REMOTE_READ_ERRORS:
        return [], _GOOGLE_UNCHECKED


def _create_answer(card: dict, remote_note: str) -> str:
    if not remote_note:
        return _confirm_text(card, full=bool(card.get("conflicts")))
    if card.get("conflicts"):
        return (
            "暂时没读到 Google 日历。这里已有的日程仍然列在卡片上。"
            "确认时会再查；查不到就不会写入。"
        )
    confirm = _confirm_text(card, full=False)
    return f"{remote_note}{confirm}"


def execute_calendar(ctx: ToolContext, args: dict | None = None) -> ToolResult:
    steps = [f"第 {ctx.iteration} 轮：解析日程并生成确认卡片"]
    action = resolve_action(ctx.message, args or {})
    if action == "list":
        steps.append("只读查询，不写入")
        try:
            now = calendar_now(ctx.db, ctx.user_id)
        except CalendarTransientError:
            text, card = _saved_rows_when_clock_fails(ctx)
            return _cleared(steps, text, card)
        zone_name = _zone_name(ctx)
        day = extract_list_day(ctx.message, ctx.history, now=now, timezone_name=zone_name or None)
        text, card = _list_text(ctx, day=day, now=now, zone_name=zone_name)
        return _cleared(steps, text, card)
    if action == "suggest":
        steps.append("只读空闲建议，不写入")
        text, card = _suggest_result(ctx)
        return _cleared(steps, text, card)

    try:
        now = calendar_now(ctx.db, ctx.user_id)
        zone_name = None
        if ctx.db is not None and ctx.user_id is not None:
            zone_name = account_timezone_name(ctx.db, ctx.user_id)
    except CalendarTransientError as exc:
        steps.append("暂时连不上 Google 日历")
        return _cleared(steps, str(exc))
    draft = extract_event(
        ctx.message,
        ctx.history,
        now=now,
        timezone_name=zone_name,
        existing_event=action in {"update", "cancel"},
    )
    if action == "create" and draft.requested_action == "list":
        steps.append("只读查询，不写入")
        text, card = _list_text(ctx, day=draft.list_day, now=now, zone_name=zone_name or "")
        return _cleared(steps, text, card)
    remote = _writes_remote(ctx)
    if action == "update" and not remote:
        apply_update_times(draft, ctx.message, now=now)
    if action == "create":
        card = _base_card(
            mode="create", action="create", draft=draft, kind=_write_kind("create", draft)
        )
        remote_note = ""
        if draft.start_at and draft.end_at:
            conflicts, remote_note = _create_conflicts(ctx, draft.start_at, draft.end_at)
            if conflicts:
                card["conflicts"] = [_conflict_payload(row) for row in conflicts[:5]]
                if remote_note:
                    card["suggested_slots"] = []
                else:
                    slots, slot_note = _preview_slots(ctx, draft.start_at, draft.end_at)
                    remote_note = remote_note or slot_note
                    card["suggested_slots"] = [] if remote_note else _slot_payload(slots)
                if not card["suggested_slots"] and not remote_note:
                    steps.append("该时段已满")
        steps.append("已生成待确认卡片，尚未写入")
        return _cleared(steps, _create_answer(card, remote_note), card)

    if remote and action in {"update", "cancel"}:
        return _remote_change_preview(
            ctx, steps, action, draft, now=now, zone_name=zone_name
        )

    # Unconnected update/cancel still confirm against a local row.
    # Update searches the named original day, not the new slot. No original day
    # means title-only among events that have not already ended.
    search_at = draft.reference_start_at if action == "update" else draft.start_at
    matches = find_event_candidates(
        ctx.db, ctx.user_id, title=draft.title, start_at=search_at, now=now
    )
    if not matches:
        steps.append("没有定位到已有日程")
        readonly = _readonly_reply(ctx, draft, search_at, now)
        if readonly is not None:
            return _cleared(steps, readonly)
        return _cleared(steps, _NOT_FOUND)
    if len(matches) > 1:
        card = _base_card(mode="choose", action=action, draft=draft)
        card["candidates"] = [_candidate_payload(row) for row in matches]
        card["title"] = ""
        card["start_at"] = ""
        card["end_at"] = ""
        steps.append("多条日程，等待在卡片上选择")
        return _cleared(steps, _confirm_text(card), card)

    event = matches[0]
    _apply_matched_fields(
        draft,
        {
            "title": event.title,
            "start_at": event.start_at,
            "end_at": event.end_at,
            "participants": event.participants,
            "location": getattr(event, "location", "") or "",
            "description": getattr(event, "description", "") or "",
        },
        action,
    )
    card = _base_card(
        mode=action,
        action=action,
        draft=draft,
        event_id=event.id,
        kind=_write_kind(action, draft),
    )
    card["status"] = event.status or "active"
    remote_note = ""
    if action == "update" and draft.start_at and draft.end_at:
        start = draft.start_at
        end = draft.end_at
        if start and end:
            conflicts, remote_note = _update_preview_conflicts(ctx, start, end, event.id)
            if conflicts:
                card["conflicts"] = [_conflict_payload(row) for row in conflicts[:5]]
                if remote_note:
                    card["suggested_slots"] = []
                else:
                    slots, slot_note = _preview_slots(ctx, start, end, exclude_event_id=event.id)
                    remote_note = remote_note or slot_note
                    card["suggested_slots"] = [] if remote_note else _slot_payload(slots)
    steps.append("已生成待确认卡片，尚未写入")
    return _cleared(steps, _create_answer(card, remote_note), card)


register(
    ToolSpec(
        name="calendar",
        risk="medium",
        execute=execute_calendar,
        description="Prepare a local calendar change for the user to confirm",
    )
)
