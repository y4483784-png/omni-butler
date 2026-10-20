"""Calendar tool: write actions return a confirm card and do not touch the database."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.core.llm import Role, Spoken, complete_text
from app.services.calendar import (
    REMOTE_READ_ERRORS,
    EventDraft,
    _catalog_text,
    _civil_day,
    calendar_now,
    classify_calendar_action,
    draft_expires_at,
    extract_event,
    extract_free_slots,
    extract_list_day,
    rank_one_event,
    upcoming_events,
)
from app.services.calendar_local import free_gaps
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
_UNCLEAR_ACTION = "没有听清要新建、改期、删除、查看还是看空闲，这次没有改动日历。"
_ACTIONS = frozenset({"create", "update", "cancel", "list", "suggest"})


def resolve_action(
    message: str,
    args: dict | None,
    *,
    history: list | None = None,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> str:
    """Use a passed action when it is one of the five. Otherwise ask the model.

    Anything else in ``args`` is ignored. Keywords in the sentence do not decide.
    An empty return means the model did not name one action.
    """
    action = str((args or {}).get("action") or "").strip().lower()
    if action in _ACTIONS:
        return action
    return classify_calendar_action(
        message, history, now=now, timezone_name=timezone_name
    )


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def _clock(value: datetime | None) -> str:
    if value is None:
        return ""
    return f"{value:%Y-%m-%d %H:%M}"


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


def _matched_dict(row) -> dict:
    return {
        "title": getattr(row, "title", "") or "",
        "start_at": getattr(row, "start_at", None),
        "end_at": getattr(row, "end_at", None),
        "participants": getattr(row, "participants", None) or [],
        "location": getattr(row, "location", "") or "",
        "description": getattr(row, "description", "") or "",
    }


def _apply_matched_fields(draft: EventDraft, matched: dict, action: str) -> None:
    """Copy the selected event, then overlay only fields the user asked to change."""
    title = str(matched.get("title") or "").strip()
    participants = _people(matched.get("participants"))
    location = str(matched.get("location") or "")
    description = str(matched.get("description") or "")
    old_start = _as_clock(matched.get("start_at"))
    old_end = _as_clock(matched.get("end_at"))
    if action == "cancel":
        draft.title = title
        draft.participants = participants
        draft.location = location
        draft.description = description
        draft.start_at = old_start
        draft.end_at = old_end
        draft.kind = ""
        return
    if draft.change_title:
        draft.title = (draft.new_title or "").strip()
    else:
        draft.title = title
    if not draft.change_participants:
        draft.participants = participants
    if not draft.change_location:
        draft.location = location
    if not draft.notes_specified:
        draft.description = description
    new_start = draft.start_at if isinstance(draft.start_at, datetime) else old_start
    draft.start_at = new_start
    if isinstance(draft.specified_end, datetime):
        draft.end_at = draft.specified_end
    elif (
        isinstance(new_start, datetime)
        and isinstance(old_start, datetime)
        and isinstance(old_end, datetime)
        and old_end > old_start
        and new_start != old_start
    ):
        draft.end_at = new_start + (old_end - old_start)
    elif isinstance(old_end, datetime):
        draft.end_at = old_end
    else:
        draft.end_at = None
    if not (draft.change_kind and draft.kind in {"meeting", "block"}):
        draft.kind = _infer_kind(participants, location)


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
        return f"请确认删除{name}。还没点确认删除就不会删。"
    if card.get("mode") == "update":
        return f"请确认修改{name}{('，时间 ' + when) if when else ''}。未确认不会写入。"
    if not title or not start:
        return "请在卡片上补全后再确认。未确认不会写入。"
    return f"请确认日程{name}，时间 {when}。未确认不会写入。"


def _cleared(
    steps: list[str],
    answer: str,
    card: dict | None = None,
    *,
    reasoning: str | None = None,
) -> ToolResult:
    return ToolResult(
        ok=True,
        thinking_steps=steps,
        direct_answer=answer,
        answer_reasoning=reasoning,
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


def _planning_advice(now: datetime, zone_name: str, listed: str) -> Spoken:
    """Model notes on the list already shown. A failed call does not invent a schedule."""
    try:
        spoken = complete_text(
            [
                {"role": "system", "content": _advice_prompt(now, zone_name)},
                {"role": "user", "content": listed},
            ],
            role=Role.CHAT,
            raise_on_error=True,
        )
        if not isinstance(spoken, Spoken):
            spoken = Spoken(str(spoken or ""), "", None)
    except Exception:
        return Spoken(_ADVICE_UNAVAILABLE, "", None)
    cleaned = (spoken.content or "").strip()
    if not cleaned:
        return Spoken(_ADVICE_UNAVAILABLE, "", None)
    return Spoken(cleaned, spoken.reasoning, spoken.prompt_tokens)


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


def _row_overlaps(row, start: datetime, end: datetime) -> bool:
    busy_start = getattr(row, "start_at", None)
    busy_end = getattr(row, "end_at", None)
    if not isinstance(busy_start, datetime) or not isinstance(busy_end, datetime):
        return False
    if busy_end <= busy_start or end <= start:
        return False
    return busy_start < end and busy_end > start


def _same_event(row, *, event_id: int | None, external_id: str) -> bool:
    if event_id is not None and getattr(row, "id", None) == event_id:
        return True
    ext = str(getattr(row, "external_id", "") or "").strip()
    return bool(external_id) and bool(ext) and ext == external_id


def _rows_overlapping(rows, start: datetime, end: datetime, *, event_id=None, external_id: str = ""):
    found = []
    for row in rows:
        if _same_event(row, event_id=event_id, external_id=external_id):
            continue
        if _row_overlaps(row, start, end):
            found.append(row)
    return found


def _memory_slots(rows, start: datetime, end: datetime, *, event_id=None, external_id: str = ""):
    duration = end - start if end > start else timedelta(hours=1)
    if duration <= timedelta(0):
        duration = timedelta(hours=1)
    busy = []
    for row in rows:
        if _same_event(row, event_id=event_id, external_id=external_id):
            continue
        busy_start = getattr(row, "start_at", None)
        busy_end = getattr(row, "end_at", None)
        if not isinstance(busy_start, datetime) or not isinstance(busy_end, datetime):
            continue
        if busy_end <= busy_start:
            continue
        busy.append((busy_start, busy_end))
    return free_gaps(busy, start, start + timedelta(days=7), duration, 3)


def _list_text(
    ctx: ToolContext,
    *,
    day: str = "",
    now: datetime | None = None,
    zone_name: str | None = None,
    events=None,
    notice: str = "",
) -> tuple[str, str, dict | None]:
    if now is None:
        try:
            now = calendar_now(ctx.db, ctx.user_id)
        except CalendarTransientError:
            text, card = _saved_rows_when_clock_fails(ctx)
            return text, "", card
    zone = zone_name if zone_name is not None else _zone_name(ctx)
    start, end, header, empty = _list_span(day, now)
    if events is None:
        provider = calendar_provider_for(ctx.db, ctx.user_id)
        events, notice = upcoming_events(
            ctx.db,
            ctx.user_id,
            now,
            provider=provider,
            connected=_writes_remote(ctx),
        )
    rows = [row for row in events if _row_overlaps(row, start, end)]
    shown = list(rows[:20])
    card = _list_card(shown)
    listed = _listed_text(shown, header, empty)
    if notice in _SKIP_ADVICE:
        return notice, "", card
    advice = _planning_advice(now, zone, listed)
    if not shown:
        return f"{empty}\n\n{advice.content}", advice.reasoning, None
    return advice.content, advice.reasoning, card


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


def _suggest_result(
    ctx: ToolContext,
    *,
    now: datetime | None = None,
    zone_name: str | None = None,
    events=None,
    notice: str = "",
) -> tuple[str, dict | None]:
    """List the named day, then let the model pick slots. No second calendar read."""
    if now is None:
        try:
            now = calendar_now(ctx.db, ctx.user_id)
        except CalendarTransientError:
            return _SUGGEST_UNAVAILABLE, None
    zone = zone_name if zone_name is not None else _zone_name(ctx)
    day = extract_list_day(ctx.message, ctx.history, now=now, timezone_name=zone or None)
    start, end, header, empty = _list_span(day, now)
    if events is None:
        provider = calendar_provider_for(ctx.db, ctx.user_id)
        try:
            events, notice = upcoming_events(
                ctx.db,
                ctx.user_id,
                now,
                provider=provider,
                connected=_writes_remote(ctx),
            )
        except REMOTE_READ_ERRORS:
            return _SUGGEST_UNAVAILABLE, None
    if notice in _SKIP_ADVICE:
        return _SUGGEST_UNAVAILABLE, None
    rows = [row for row in events if _row_overlaps(row, start, end)]
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


def _load_upcoming(ctx: ToolContext, now: datetime) -> tuple[list, str]:
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    return upcoming_events(
        ctx.db,
        ctx.user_id,
        now,
        provider=provider,
        connected=_writes_remote(ctx),
    )


def _read_failure_reply(notice: str) -> str:
    """Existing read-failure sentences. They do not say the calendar is empty."""
    if notice == _LIST_TRUNCATED:
        return _LIST_TRUNCATED
    if notice:
        return notice
    return _LIST_LOCAL_ONLY


def _conflict_note(notice: str) -> str:
    if notice == _LIST_TRUNCATED:
        return _LIST_TRUNCATED
    if notice:
        return _GOOGLE_UNCHECKED
    return ""


def _fill_conflicts(
    card: dict,
    rows,
    notice: str,
    start: datetime,
    end: datetime,
    *,
    event_id: int | None = None,
    external_id: str = "",
) -> str:
    """Conflicts and gaps from the read already in hand. No second events.list."""
    remote_note = _conflict_note(notice)
    conflicts = _rows_overlapping(
        rows, start, end, event_id=event_id, external_id=external_id
    )
    if not conflicts:
        return remote_note
    card["conflicts"] = [_conflict_payload(row) for row in conflicts[:5]]
    if remote_note:
        card["suggested_slots"] = []
        return remote_note
    slots = _memory_slots(
        rows, start, end, event_id=event_id, external_id=external_id
    )
    card["suggested_slots"] = _slot_payload(slots)
    return ""


def _create_answer(card: dict, remote_note: str) -> str:
    if remote_note == _LIST_TRUNCATED:
        return _LIST_TRUNCATED
    if not remote_note:
        return _confirm_text(card, full=bool(card.get("conflicts")))
    if card.get("conflicts"):
        return (
            "暂时没读到 Google 日历。这里已有的日程仍然列在卡片上。"
            "确认时会再查；查不到就不会写入。"
        )
    confirm = _confirm_text(card, full=False)
    return f"{remote_note}{confirm}"


def _account_clock(ctx: ToolContext) -> tuple[datetime, str | None]:
    now = calendar_now(ctx.db, ctx.user_id)
    zone_name = None
    if ctx.db is not None and ctx.user_id is not None:
        zone_name = account_timezone_name(ctx.db, ctx.user_id) or None
    return now, zone_name


def _mutate_from_selection(
    ctx: ToolContext,
    steps: list[str],
    action: str,
    draft: EventDraft,
    selected,
    *,
    zone_name: str | None,
    events,
    notice: str,
) -> ToolResult:
    _apply_matched_fields(draft, _matched_dict(selected), action)
    event_id = getattr(selected, "id", None)
    external_id = str(getattr(selected, "external_id", "") or "").strip()
    connected = _writes_remote(ctx)
    if connected and not external_id:
        return _cleared(steps, _NOT_FOUND)
    if not connected and event_id is None:
        return _cleared(steps, _NOT_FOUND)
    card = _base_card(
        mode=action,
        action=action,
        draft=draft,
        event_id=event_id,
        kind=_write_kind(action, draft),
    )
    card["external_id"] = external_id if connected else ""
    if zone_name:
        card["timezone"] = zone_name
    status = str(getattr(selected, "status", "") or "")
    if status:
        card["status"] = status
    remote_note = ""
    if (
        action == "update"
        and isinstance(draft.start_at, datetime)
        and isinstance(draft.end_at, datetime)
    ):
        remote_note = _fill_conflicts(
            card,
            events,
            notice,
            draft.start_at,
            draft.end_at,
            event_id=event_id,
            external_id=external_id if connected else "",
        )
        if card.get("conflicts") and not card.get("suggested_slots") and not remote_note:
            steps.append("该时段已满")
    steps.append("已生成待确认卡片，尚未写入")
    return _cleared(steps, _create_answer(card, remote_note), card)


def execute_calendar(ctx: ToolContext, args: dict | None = None) -> ToolResult:
    steps = [f"第 {ctx.iteration} 轮：解析日程并生成确认卡片"]
    explicit = str((args or {}).get("action") or "").strip().lower()
    passed = explicit if explicit in _ACTIONS else ""
    now: datetime | None = None
    zone_name: str | None = None
    clock_error: CalendarTransientError | None = None
    try:
        now, zone_name = _account_clock(ctx)
    except CalendarTransientError as exc:
        clock_error = exc

    if passed:
        action = passed
    else:
        action = resolve_action(
            ctx.message,
            None,
            history=ctx.history,
            now=now,
            timezone_name=zone_name,
        )
        if not action:
            if clock_error is not None:
                steps.append("暂时连不上 Google 日历")
                return _cleared(steps, str(clock_error))
            return _cleared(steps, _UNCLEAR_ACTION)

    if clock_error is not None:
        if action == "list":
            steps.append("只读查询，不写入")
            text, card = _saved_rows_when_clock_fails(ctx)
            return _cleared(steps, text, card)
        if action == "suggest":
            steps.append("只读空闲建议，不写入")
            return _cleared(steps, _SUGGEST_UNAVAILABLE)
        steps.append("暂时连不上 Google 日历")
        return _cleared(steps, str(clock_error))

    assert now is not None
    events, notice = _load_upcoming(ctx, now)

    if action == "list":
        steps.append("只读查询，不写入")
        day = extract_list_day(
            ctx.message, ctx.history, now=now, timezone_name=zone_name or None
        )
        text, reasoning, card = _list_text(
            ctx, day=day, now=now, zone_name=zone_name or "", events=events, notice=notice
        )
        return _cleared(steps, text, card, reasoning=reasoning)
    if action == "suggest":
        steps.append("只读空闲建议，不写入")
        text, card = _suggest_result(
            ctx, now=now, zone_name=zone_name or "", events=events, notice=notice
        )
        return _cleared(steps, text, card)

    if action in {"update", "cancel"} and notice:
        steps.append("没有读完日程，未生成卡片")
        return _cleared(steps, _read_failure_reply(notice))

    draft = extract_event(
        ctx.message,
        ctx.history,
        now=now,
        timezone_name=zone_name,
        existing_event=action in {"update", "cancel"},
        listed=_catalog_text(events) if action in {"update", "cancel"} else "",
    )
    if action == "create":
        card = _base_card(
            mode="create", action="create", draft=draft, kind=_write_kind("create", draft)
        )
        remote_note = ""
        if isinstance(draft.start_at, datetime) and isinstance(draft.end_at, datetime):
            remote_note = _fill_conflicts(
                card, events, notice, draft.start_at, draft.end_at
            )
            if card.get("conflicts") and not card.get("suggested_slots") and not remote_note:
                steps.append("该时段已满")
        steps.append("已生成待确认卡片，尚未写入")
        return _cleared(steps, _create_answer(card, remote_note), card)

    query = (draft.search_title or draft.title or "").strip()
    selected = rank_one_event(events, query)
    if selected is None:
        # Reply wording for no unique upcoming event is not settled.
        steps.append("没有定位到唯一的未结束日程")
        return _cleared(steps, _NOT_FOUND)
    return _mutate_from_selection(
        ctx,
        steps,
        action,
        draft,
        selected,
        zone_name=zone_name,
        events=events,
        notice=notice,
    )


register(
    ToolSpec(
        name="calendar",
        risk="medium",
        execute=execute_calendar,
        description="Prepare a local calendar change for the user to confirm",
    )
)
