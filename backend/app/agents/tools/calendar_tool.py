"""Calendar tool: write actions return a confirm card and do not touch the database."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.services.calendar import (
    REMOTE_READ_ERRORS,
    EventDraft,
    apply_update_times,
    calendar_now,
    check_conflict,
    conflicts_for_create,
    conflicts_for_update,
    draft_expires_at,
    extract_event,
    find_event_candidates,
    free_slots_for,
    readonly_titles,
    titles_match,
    _parse_cn_int,
)
from app.services.calendar_factory import (
    account_timezone_name,
    calendar_provider_for,
    calendar_writes_remote,
)
from app.services.calendar_google import _LIST_LOCAL_ONLY
from app.services.calendar_secrets import CalendarTransientError

_GOOGLE_UNCHECKED = "暂时没读到 Google 日历，确认时会再查；查不到就不会写入。"
_SUGGEST_UNAVAILABLE = "暂时读不到 Google 日历，不能建议空闲时段。"
_NOT_FOUND = "没有找到对应日程。"
_NOT_FOUND_UNREAD = "没有找到对应日程。暂时读不到 Google 日历，以下只是这里保存的日程。"
_READONLY_IN_GOOGLE = "这条日程只在 Google 日历里，请在 Google 日历里改。"

_UPDATE = re.compile(r"(改到|改成|改为|推迟|提前|换到|挪到|延后)")
_CANCEL = re.compile(r"(取消|撤销|删掉)")
_LIST = re.compile(r"(有哪些|有什么安排|有什么会|查一下日程|查日程|我的日程|哪些日程)")
_SUGGEST = re.compile(r"(有空|空闲|什么时候方便)")
_SUGGEST_CLOCK = re.compile(
    r"(今天|明天|后天|今晚|明早)?"
    r"\s*(上午|下午|中午|晚上)?"
    r"\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})"
    r"\s*(?:点|:|：)\s*"
    r"(半|\d{1,2}|[一二三四五六七八九十]{1,3})?"
)


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


def _day_start(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _suggest_window(message: str, now: datetime) -> tuple[datetime, datetime]:
    """Named day uses that local day. Otherwise search from now through 7 days."""
    if "后天" in (message or ""):
        day = _day_start(now + timedelta(days=2))
        return day, day + timedelta(days=1)
    if "明天" in (message or ""):
        day = _day_start(now + timedelta(days=1))
        start = day.replace(hour=12) if "下午" in message else day
        return start, day + timedelta(days=1)
    if "今天" in (message or ""):
        day = _day_start(now)
        start = day.replace(hour=12) if "下午" in message else max(now, day)
        return start, day + timedelta(days=1)
    start = now.replace(second=0, microsecond=0)
    return start, start + timedelta(days=7)


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


def _saved_rows_when_clock_fails(ctx: ToolContext) -> str:
    """Local rows only. A failed account clock is not replaced with the process zone."""
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    local = getattr(provider, "_local", provider)
    rows = local.list_events(ctx.user_id, datetime(2000, 1, 1), datetime(2100, 1, 1))
    if not rows:
        return _LIST_LOCAL_ONLY
    lines = [_list_line(row) for row in rows[:20]]
    return f"{_LIST_LOCAL_ONLY}\n" + "\n".join(lines)


def _list_text(ctx: ToolContext) -> str:
    try:
        now = _day_start(calendar_now(ctx.db, ctx.user_id))
    except CalendarTransientError:
        return _saved_rows_when_clock_fails(ctx)
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    rows = provider.list_events(ctx.user_id, now, now + timedelta(days=7))
    notice = str(getattr(provider, "list_notice", "") or "")
    if not rows:
        body = "" if notice else "接下来 7 天没有日程。"
    else:
        lines = [_list_line(row) for row in rows[:20]]
        body = "接下来 7 天的日程：\n" + "\n".join(lines)
    if notice and body:
        return f"{notice}\n{body}"
    return notice or body


def _named_clock(message: str, now: datetime) -> datetime | None:
    """The hour the sentence names, on the account wall clock. No clock means None."""
    match = _SUGGEST_CLOCK.search(message or "")
    if not match:
        return None
    day_word = match.group(1) or ""
    meridiem = match.group(2) or ""
    hour = _parse_cn_int(match.group(3) or "")
    if hour is None:
        return None
    minute_raw = match.group(4) or ""
    minute = 30 if minute_raw == "半" else (_parse_cn_int(minute_raw) or 0)
    days = {"明天": 1, "明早": 1, "后天": 2}.get(day_word, 0)
    if day_word == "今晚":
        meridiem = meridiem or "晚上"
    base = (now + timedelta(days=days)).replace(second=0, microsecond=0)
    final_hour = hour
    if meridiem in {"下午", "晚上"} and hour < 12:
        final_hour = hour + 12
    elif meridiem == "中午" and hour < 11:
        final_hour = hour + 12
    return base.replace(hour=final_hour % 24, minute=minute)


def _suggest_card(slots: list[tuple[datetime, datetime]]) -> dict:
    return {
        "mode": "suggest",
        "phase": "info",
        "action": "suggest",
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
        "suggested_slots": _slot_payload(slots),
        "candidates": [],
    }


def _slot_lines(slots: list[tuple[datetime, datetime]]) -> list[str]:
    return [f"{begin:%Y-%m-%d %H:%M}–{end_at:%H:%M}" for begin, end_at in slots]


def _suggest_result(ctx: ToolContext) -> tuple[str, dict | None]:
    try:
        now = calendar_now(ctx.db, ctx.user_id)
    except CalendarTransientError:
        return _SUGGEST_UNAVAILABLE, None
    start, end = _suggest_window(ctx.message, now)
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    named = _named_clock(ctx.message, now)
    try:
        if named is not None:
            hour_end = named + timedelta(hours=1)
            named_free = provider.find_free_slots(
                ctx.user_id, named, hour_end, timedelta(hours=1), limit=1
            )
            if named_free and named_free[0] == (named, hour_end):
                slots = [(named, hour_end)]
                return f"这个时间可以：{_slot_lines(slots)[0]}", _suggest_card(slots)
            others = provider.find_free_slots(
                ctx.user_id, start, end, timedelta(hours=1), limit=3
            )
            others = [slot for slot in others if not (slot[0] < hour_end and slot[1] > named)]
            if not others:
                return "这个时间已有安排，这段时间没有足够的空闲时段。", None
            lines = _slot_lines(others)
            return "这个时间已有安排。可以考虑这些空闲时段：\n" + "\n".join(lines), _suggest_card(others)
        span = end - start
        if span > timedelta(0):
            whole = provider.find_free_slots(ctx.user_id, start, end, span, limit=1)
            if whole == [(start, end)]:
                text = f"这段时间都有空：{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"
                return text, _suggest_card([(start, end)])
        slots = provider.find_free_slots(
            ctx.user_id, start, end, timedelta(hours=1), limit=3
        )
    except REMOTE_READ_ERRORS:
        return _SUGGEST_UNAVAILABLE, None
    if not slots:
        return "这段时间没有足够的空闲时段。", None
    lines = _slot_lines(slots)
    return "可以考虑这些空闲时段：\n" + "\n".join(lines), _suggest_card(slots)


def _writes_remote(ctx: ToolContext) -> bool:
    if ctx.db is None or ctx.user_id is None:
        return False
    return calendar_writes_remote(ctx.db, ctx.user_id)


def _confirm_horizon(now: datetime) -> tuple[datetime, datetime]:
    """Same window confirm lists: account wall now through year 2100."""
    return now.replace(microsecond=0), datetime(2100, 1, 1)


def _align_connected_title(
    ctx: ToolContext,
    card: dict,
    draft: EventDraft,
    action: str,
    now: datetime,
    zone_name: str | None,
) -> None:
    """Align a connected card to one live primary event, or leave the extraction.

    One exact ``summary.strip() == title`` stays as extracted. Otherwise exactly
    one ``titles_match`` hit copies that summary, and a cancel also copies its
    wall times. Zero or many soft hits stay on the extraction: connected
    update/cancel has no choose step. A failed read leaves the extraction.
    Confirm still exact-matches and does not use this alignment.
    """
    title = (draft.title or "").strip()
    if not title or not zone_name or ctx.db is None or ctx.user_id is None:
        return
    provider = calendar_provider_for(ctx.db, ctx.user_id)
    reader = getattr(provider, "primary_events_for_title", None)
    if reader is None:
        return
    start, end = _confirm_horizon(now)
    try:
        events = reader(start, end, zone_name)
    except REMOTE_READ_ERRORS:
        return
    if not isinstance(events, list):
        return
    exact = [
        item
        for item in events
        if isinstance(item, dict) and str(item.get("summary") or "").strip() == title
    ]
    if len(exact) == 1:
        return
    soft = [
        item
        for item in events
        if isinstance(item, dict) and titles_match(title, str(item.get("summary") or ""))
    ]
    if len(soft) != 1:
        return
    match = soft[0]
    card["title"] = str(match.get("summary") or "").strip()
    if action != "cancel":
        return
    start_at = match.get("start_at")
    end_at = match.get("end_at")
    if isinstance(start_at, datetime):
        card["start_at"] = _iso(start_at)
    if isinstance(end_at, datetime):
        card["end_at"] = _iso(end_at)


def _remote_change_preview(
    ctx: ToolContext,
    steps: list[str],
    action: str,
    draft: EventDraft,
    *,
    now: datetime,
    zone_name: str | None,
) -> ToolResult:
    """Connected update/cancel. Prefill may align one soft title. Confirm writes."""
    card = _base_card(mode=action, action=action, draft=draft)
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
    _align_connected_title(ctx, card, draft, action, now, zone_name)
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
        return _cleared(steps, _list_text(ctx))
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
    remote = _writes_remote(ctx)
    if action == "update" and not remote:
        apply_update_times(draft, ctx.message, now=now)
    if action == "create":
        card = _base_card(mode="create", action="create", draft=draft)
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
    chosen = EventDraft(
        title=event.title or draft.title,
        start_at=event.start_at,
        end_at=event.end_at,
        participants=draft.participants,
    )
    card = _base_card(mode=action, action=action, draft=chosen, event_id=event.id)
    card["status"] = event.status or "active"
    remote_note = ""
    if action == "update" and draft.start_at:
        card["start_at"] = _iso(draft.start_at)
        card["end_at"] = _iso(draft.end_at or event.end_at)
        start = draft.start_at
        end = draft.end_at or event.end_at
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
