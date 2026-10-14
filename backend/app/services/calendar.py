"""Calendar service helpers for Phase 3 local schedule actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import complete_json
from app.agents.context import clip_history_for_prompt
from app.models.models import CalendarEvent
from app.services.calendar_factory import calendar_provider_for, calendar_writes_remote
from app.services.calendar_google import CalendarReconnectRequired, CalendarRemoteReadError
from app.services.calendar_secrets import CalendarTokenError

REMOTE_READ_ERRORS = (CalendarRemoteReadError, CalendarTokenError)

# Accept Arabic and Chinese numerals ("3点" / "三点" / "三点半") — real users rarely type digits only.
_CALENDAR_TIME = re.compile(
    r"(今天|明天|后天|今晚|明早|明天下午|明天上午|下午|上午|中午)?\s*"
    r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*"
    r"(点|:|：)\s*"
    r"(半|\d{1,2}|[一二三四五六七八九十]{1,3})?"
)
_CALENDAR_HINTS = re.compile(
    r"(日程|安排|会议|开会|约|预定|提醒|日历|周会|月会|面试|晨会|例会|"
    r"复盘会|评审会|一对一|同步会|启动会|站会|对接)"
)
_PARTICIPANTS_PAT = re.compile(r"(参与人|参会人|和)([:：]?\s*)(.+)")
_CANCEL_PAT = re.compile(r"(取消|算了|放弃|不安排了|先不安排)", re.I)
_UPDATE_SPLIT = re.compile(r"(改到|改成|改为|挪到|换到|推迟到|提前到)")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _parse_cn_int(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "半":
        return 30
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _CN_DIGIT.get(s[1], 0)
    if s.endswith("十") and len(s) == 2:
        return _CN_DIGIT.get(s[0], 0) * 10
    if "十" in s and len(s) == 3:
        return _CN_DIGIT.get(s[0], 0) * 10 + _CN_DIGIT.get(s[2], 0)
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    return None


@dataclass
class EventDraft:
    title: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    participants: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    original_request: str = ""
    # Update only: the existing event's time. start_at/end_at are the new slot.
    reference_start_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_at": self.start_at.isoformat() if self.start_at else "",
            "end_at": self.end_at.isoformat() if self.end_at else "",
            "participants": list(self.participants),
            "missing_fields": list(self.missing_fields),
            "original_request": self.original_request,
            "reference_start_at": self.reference_start_at.isoformat() if self.reference_start_at else "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventDraft":
        return cls(
            title=str(data.get("title") or ""),
            start_at=_parse_dt(str(data.get("start_at") or "")),
            end_at=_parse_dt(str(data.get("end_at") or "")),
            participants=[str(x).strip() for x in (data.get("participants") or []) if str(x).strip()],
            missing_fields=[str(x).strip() for x in (data.get("missing_fields") or []) if str(x).strip()],
            original_request=str(data.get("original_request") or ""),
            reference_start_at=_parse_dt(str(data.get("reference_start_at") or "")),
        )


def calendar_hint(message: str, history: list[dict] | None = None) -> bool:
    text = " ".join(
        [message or ""]
        + [str(m.get("content") or "") for m in (history or []) if m.get("role") == "user"]
    )
    return bool(_CALENDAR_HINTS.search(text) and _CALENDAR_TIME.search(text))


def is_calendar_cancel(message: str) -> bool:
    return bool(_CANCEL_PAT.search(message or ""))


def extract_event(
    message: str,
    history: list[dict] | None = None,
    *,
    now: datetime | None = None,
) -> EventDraft:
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你是日程信息提取器。只输出 JSON："
                    '{"title":str,"start_at":str,"end_at":str,"participants":[str],"missing_fields":[str]}。'
                    " start_at/end_at 用无偏移的 ISO 8601；缺失则输出空字符串。"
                    " 参与人可以省略，不要因为没有参与人写入 missing_fields。"
                    " 用户没说结束时间时 end_at 留空，不要把 end_at 放进 missing_fields。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"最近对话：{json.dumps(clip_history_for_prompt(history, max_msgs=8, max_chars=300), ensure_ascii=False)}\n"
                    f"当前消息：{message}"
                ),
            },
        ]
    )
    if not isinstance(parsed, dict):
        draft = EventDraft(original_request=message)
        _supplement_draft_from_message(draft, message, allow_end=True, now=now)
        return _finalize_prefill(draft)

    start_at = _parse_dt(str(parsed.get("start_at") or ""))
    end_at = _parse_dt(str(parsed.get("end_at") or ""))
    participants = [str(x).strip() for x in (parsed.get("participants") or []) if str(x).strip()]
    missing = [str(x).strip() for x in (parsed.get("missing_fields") or []) if str(x).strip()]
    if not start_at:
        missing.append("start_at")
    if not end_at:
        missing.append("end_at")
    draft = EventDraft(
        title=str(parsed.get("title") or "").strip(),
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        missing_fields=list(dict.fromkeys(missing)),
        original_request=message,
    )
    _supplement_draft_from_message(draft, message, allow_end=True, now=now)
    return _finalize_prefill(draft)


DRAFT_TTL = timedelta(minutes=30)


def _finalize_prefill(draft: EventDraft) -> EventDraft:
    """Optional participants. A missing end becomes start + 1 hour when start is known."""
    if draft.start_at is not None and draft.end_at is None:
        draft.end_at = draft.start_at + timedelta(hours=1)
    draft.missing_fields = _normalize_missing_fields(
        title=draft.title,
        start_at=draft.start_at,
        end_at=draft.end_at,
        participants=draft.participants,
        existing=draft.missing_fields,
    )
    return draft


def _parse_dt(raw: str) -> datetime | None:
    s = raw.strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _normalize_missing_fields(
    *,
    title: str,
    start_at: datetime | None,
    end_at: datetime | None,
    participants: list[str],
    existing: list[str],
) -> list[str]:
    missing = [item for item in existing if item not in {"end_at", "participants"}]
    if not title:
        missing.append("title")
    if not start_at:
        missing.append("start_at")
    return list(dict.fromkeys(missing))


def _merge_from_message(draft: EventDraft, message: str) -> None:
    if "participants" in draft.missing_fields:
        parts = _parse_participants(message)
        if parts:
            draft.participants = parts
    if "end_at" in draft.missing_fields and draft.start_at:
        dt = _parse_relative_time(message, base=draft.start_at)
        if dt:
            draft.end_at = dt
    if "start_at" in draft.missing_fields:
        dt = _parse_time_from_message(message)
        if dt:
            draft.start_at = dt


def _parse_participants(message: str) -> list[str]:
    m = _PARTICIPANTS_PAT.search(message or "")
    if not m:
        return []
    raw = m.group(3).strip()
    for stop in ("主题", "开始", "结束", "到 ", "到5", "明天", "今天", "后天"):
        if stop in raw:
            raw = raw.split(stop, 1)[0].strip(" ，。,；;")
    raw = raw.replace("，", ",").replace("、", ",").replace("和", ",")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _supplement_draft_from_message(
    draft: EventDraft,
    message: str,
    *,
    allow_end: bool = True,
    now: datetime | None = None,
) -> None:
    if not draft.title:
        title = _infer_title(message)
        if title:
            draft.title = title
    if draft.start_at is None:
        draft.start_at = _parse_time_from_message(message, now=now)
    if allow_end and draft.end_at is None and draft.start_at is not None:
        if re.search(r"(到|至|结束)", message or ""):
            draft.end_at = _parse_relative_time(message, base=draft.start_at)
    if not draft.participants:
        draft.participants = _parse_participants(message)


def _infer_title(message: str) -> str:
    text = message or ""
    m = re.search(r"(主题|会议主题)(?:是|为|[:：])?\s*([^，。；]+)", text)
    if m:
        return m.group(2).strip()
    for keyword in ("周会", "月会", "面试", "晨会", "例会", "会议"):
        if keyword in text:
            return keyword
    return ""


def _parse_time_from_message(message: str, *, now: datetime | None = None) -> datetime | None:
    m = _CALENDAR_TIME.search(message or "")
    if not m:
        return None
    now = now or datetime.now()
    prefix = (m.group(1) or "").strip()
    hour = _parse_cn_int(m.group(2) or "")
    minute = _parse_cn_int(m.group(4) or "") or 0
    if hour is None:
        return None
    days = 0
    if prefix in {"明天", "明早", "明天下午", "明天上午"}:
        days = 1
    elif prefix == "后天":
        days = 2
    base = (now + timedelta(days=days)).replace(second=0, microsecond=0)
    final_hour = hour
    if prefix in {"下午", "明天下午", "今晚"} and hour < 12:
        final_hour = hour + 12
    return base.replace(hour=final_hour % 24, minute=minute)


def _parse_relative_time(message: str, *, base: datetime | None) -> datetime | None:
    if not base:
        return None
    m = _CALENDAR_TIME.search(message or "")
    if not m:
        return None
    hour = _parse_cn_int(m.group(2) or "")
    minute = _parse_cn_int(m.group(4) or "") or 0
    if hour is None:
        return None
    candidate = base.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    prefix = (m.group(1) or "").strip()
    if prefix in {"明天", "明早", "明天下午", "明天上午"}:
        candidate = candidate + timedelta(days=1)
    elif prefix == "后天":
        candidate = candidate + timedelta(days=2)
    if prefix in {"下午", "明天下午"} and hour < 12:
        candidate = candidate.replace(hour=hour + 12)
    if prefix in {"今晚"} and hour < 12:
        candidate = candidate.replace(hour=min(hour + 12, 23))
    if candidate <= base and prefix == "" and hour < 12:
        candidate = candidate.replace(hour=min(hour + 12, 23))
    if candidate <= base:
        candidate = candidate + timedelta(days=1)
    return candidate


def format_missing_fields(draft: EventDraft) -> str:
    labels = {
        "title": "会议主题",
        "start_at": "开始时间",
        "end_at": "结束时间",
        "participants": "参与人",
    }
    names = [labels[x] for x in draft.missing_fields if x in labels]
    if not names:
        return ""
    text = "、".join(names)
    example = []
    if "end_at" in draft.missing_fields:
        example.append("到 5 点")
    if "participants" in draft.missing_fields:
        example.append("参与人张三、李四")
    if "title" in draft.missing_fields:
        example.append("主题是周会")
    if "start_at" in draft.missing_fields:
        example.append("明天上午 3 点开始")
    suffix = f" 你可以一次补充完整，例如：{'，'.join(example)}。" if example else ""
    return f"还缺{text}。{suffix}".strip()


def check_conflict(db: Session, user_id: int, start_at: datetime, end_at: datetime) -> list[CalendarEvent]:
    """Local active overlaps only. Connected creates use ``conflicts_for_create``."""
    return calendar_provider_for(db, user_id).overlap_rows(user_id, start_at, end_at)


def conflicts_for_create(db: Session, user_id: int, start_at: datetime, end_at: datetime) -> list:
    """Create-window conflicts. Connected users also read primary ``events.list``.

    Raises ``CalendarRemoteReadError`` or ``CalendarTokenError`` when that read
    fails. Callers must not treat the failure as an empty calendar.
    """
    provider = calendar_provider_for(db, user_id)
    if calendar_writes_remote(db, user_id):
        return provider.conflicts_for_create(user_id, start_at, end_at)
    return provider.overlap_rows(user_id, start_at, end_at)


def create_event(
    db: Session,
    *,
    user_id: int,
    title: str,
    start_at: datetime,
    end_at: datetime,
    participants: list[str],
    location: str = "",
    description: str = "",
) -> CalendarEvent:
    provider = calendar_provider_for(db, user_id)
    created = provider.create_event(
        user_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        location=location,
        description=description,
    )
    event = provider.get_row(created.id)
    if event is None:
        raise RuntimeError("calendar event missing after insert")
    return event


def _lookup_window(
    start_at: datetime | None,
    title: str,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Day window when the user named a day, otherwise from ``now`` through year 2100."""
    if start_at is not None:
        day = start_at.replace(hour=0, minute=0, second=0, microsecond=0)
        return day, day + timedelta(days=1)
    if (title or "").strip():
        return now, datetime(2100, 1, 1)
    return None


def _block_disconnected_remote(db: Session, user_id: int, event: CalendarEvent) -> None:
    """Refuse edits that would leave Google on the old time after a disconnect."""
    if (event.external_id or "").strip() and not calendar_writes_remote(db, user_id):
        raise CalendarReconnectRequired()


def conflicts_for_update(
    db: Session,
    user_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_event_id: int | None = None,
) -> list:
    """Overlaps for a time change. Connected users read the primary calendar.

    The edited row is left out by local id and, when it has one, by external id.
    A Google row's stored interval is not a second busy block.
    """
    provider = calendar_provider_for(db, user_id)
    if calendar_writes_remote(db, user_id):
        checker = getattr(provider, "conflicts_for_update", None)
        if checker is not None:
            return checker(
                user_id, start_at, end_at, exclude_event_id=exclude_event_id
            )
    return provider.overlap_rows(
        user_id, start_at, end_at, exclude_event_id=exclude_event_id
    )


def readonly_titles(
    db: Session,
    user_id: int,
    *,
    title: str,
    start_at: datetime | None,
    now: datetime,
) -> list[str]:
    """Titles that exist only on the connected primary calendar. Unconnected users get none."""
    if not (title or "").strip() or not calendar_writes_remote(db, user_id):
        return []
    if start_at is not None:
        window = _lookup_window(start_at, title, start_at)
    else:
        window = _lookup_window(None, title, now)
    if window is None:
        return []
    provider = calendar_provider_for(db, user_id)
    reader = getattr(provider, "readonly_titles", None)
    if reader is None:
        return []
    return reader(user_id, window[0], window[1])


def update_event(
    db: Session,
    event_id: int,
    *,
    user_id: int,
    title: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    participants: list[str] | None = None,
    location: str | None = None,
    description: str | None = None,
) -> CalendarEvent | None:
    provider = calendar_provider_for(db, user_id)
    current = provider.get_row(event_id)
    if current is None or current.user_id != user_id:
        return None
    _block_disconnected_remote(db, user_id, current)
    updated = provider.update_event(
        user_id,
        event_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        location=location,
        description=description,
    )
    if updated is None:
        return None
    return provider.get_row(event_id)


def cancel_event(db: Session, event_id: int, *, user_id: int) -> CalendarEvent | None:
    provider = calendar_provider_for(db, user_id)
    current = provider.get_row(event_id)
    if current is None or current.user_id != user_id:
        return None
    _block_disconnected_remote(db, user_id, current)
    cancelled = provider.cancel_event(user_id, event_id)
    if cancelled is None:
        return None
    return provider.get_row(event_id)


def _card_text(event: Any, name: str) -> str:
    value = getattr(event, name, "")
    return "" if value is None else str(value)


def serialize_event_card(event: CalendarEvent) -> dict[str, Any]:
    raw = getattr(event, "participants", None)
    if isinstance(raw, list):
        participants = [str(item) for item in raw]
    else:
        try:
            parsed = json.loads(raw or "[]")
        except json.JSONDecodeError:
            parsed = []
        participants = parsed if isinstance(parsed, list) else []
    return {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at.isoformat() if event.start_at else "",
        "end_at": event.end_at.isoformat() if event.end_at else "",
        "participants": participants,
        "status": event.status,
        "timezone": _card_text(event, "timezone"),
        "location": _card_text(event, "location"),
        "description": _card_text(event, "description"),
        "external_id": _card_text(event, "external_id"),
        "provider": _card_text(event, "provider"),
    }


def draft_expires_at(now: datetime | None = None) -> str:
    base = (now or datetime.now()).replace(microsecond=0)
    return (base + DRAFT_TTL).isoformat()


def titles_match(query: str, title: str) -> bool:
    needle = (query or "").strip()
    hay = (title or "").strip()
    if not needle or not hay:
        return False
    return needle.casefold() in hay.casefold() or hay.casefold() in needle.casefold()


def calendar_now(db: Session | None = None, user_id: int | None = None) -> datetime:
    """Naive wall clock for ``user_id``.

    Connected accounts use that Google account's IANA zone. Everyone else
    keeps the process-local clock used before a calendar was connected.
    """
    if db is None or user_id is None:
        return datetime.now().replace(microsecond=0)
    zone = calendar_provider_for(db, user_id).get_timezone()
    return datetime.now(zone).replace(tzinfo=None, microsecond=0)


def calendar_local_now(db: Session | None = None, user_id: int | None = None) -> datetime:
    """Thin wrapper. Prefer ``calendar_now(db, user_id)``."""
    return calendar_now(db, user_id)


def _weekday_base(now: datetime, marker: str) -> datetime:
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday + timedelta(days=_WEEKDAY[marker])


def _parse_clause_time(text: str, *, now: datetime) -> datetime | None:
    """One clause's clock, weekday, or both. Weekday names the day even without 点."""
    weekday = re.search(r"周([一二三四五六日天])", text or "")
    day = _weekday_base(now, weekday.group(1)) if weekday else None
    clock_text = re.sub(r"周[一二三四五六日天]", " ", text or "")
    clock = _parse_time_from_message(clock_text, now=now)
    if day is not None and clock is not None:
        return day.replace(hour=clock.hour, minute=clock.minute)
    if clock is not None:
        return clock
    return day


def apply_update_times(draft: EventDraft, message: str, *, now: datetime | None = None) -> EventDraft:
    """Split '把周三的周会改到周四三点' into the event's day and the new slot.

    ``reference_start_at`` is only the day used to find the existing event.
    ``start_at`` / ``end_at`` are the new slot. A message with only a new time
    leaves the reference empty so lookup is by title.
    """
    now = now or datetime.now()
    parts = _UPDATE_SPLIT.split(message or "", maxsplit=1)
    if len(parts) < 3:
        draft.reference_start_at = None
        return draft
    reference_clause, _pivot, target_clause = parts[0], parts[1], parts[2]
    reference = _parse_clause_time(reference_clause, now=now)
    target = _parse_clause_time(target_clause, now=now)
    draft.reference_start_at = reference
    title = _infer_title(reference_clause)
    if title:
        draft.title = title
    if target is not None:
        draft.start_at = target
        draft.end_at = target + timedelta(hours=1)
    return draft


def find_event_candidates(
    db: Session,
    user_id: int,
    *,
    title: str,
    start_at: datetime | None,
    now: datetime | None = None,
) -> list[CalendarEvent]:
    """Active events for update/cancel.

    ``start_at`` is the named day of the existing event, including a past day
    the user actually said. With only a title, keep events that end after
    ``calendar_now`` so last year's rows are not candidates.
    """
    provider = calendar_provider_for(db, user_id)
    if start_at is not None:
        window = _lookup_window(start_at, title, start_at)
    elif (title or "").strip():
        current = now if now is not None else calendar_now(db, user_id)
        window = _lookup_window(None, title, current)
    else:
        return []
    if window is None:
        return []
    rows = provider.overlap_rows(user_id, window[0], window[1])
    if (title or "").strip():
        rows = [row for row in rows if titles_match(title, row.title or "")]
    return rows[:5]


def free_slots_for(
    db: Session,
    user_id: int,
    start_at: datetime,
    end_at: datetime | None,
    *,
    exclude_event_id: int | None = None,
    limit: int = 3,
) -> list[tuple[datetime, datetime]]:
    duration = end_at - start_at if end_at and end_at > start_at else timedelta(hours=1)
    if duration <= timedelta(0):
        duration = timedelta(hours=1)
    return calendar_provider_for(db, user_id).find_free_slots(
        user_id,
        start_at,
        start_at + timedelta(days=7),
        duration,
        limit=limit,
        exclude_event_id=exclude_event_id,
    )


def void_draft_schedule_cards(db: Session, session_id: int) -> int:
    """Mark unconfirmed write cards cancelled. Suggest/info cards are left alone."""
    from app.models.models import Message

    changed = 0
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.role == "assistant")
        .all()
    )
    for row in rows:
        raw = row.schedule_card or ""
        if not raw:
            continue
        try:
            card = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(card, dict):
            continue
        if card.get("phase") == "draft" and card.get("mode") != "suggest":
            card["phase"] = "cancelled"
            row.schedule_card = json.dumps(card, ensure_ascii=False)
            changed += 1
    if changed:
        db.commit()
    return changed


def suggest_next_slot(start_at: datetime, end_at: datetime) -> tuple[datetime, datetime]:
    delta = end_at - start_at
    new_start = end_at
    return new_start, new_start + (delta if delta > timedelta() else timedelta(hours=1))
