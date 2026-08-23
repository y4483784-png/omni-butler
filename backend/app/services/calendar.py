"""Calendar service helpers for Phase 3 local schedule actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import complete_json
from app.models.models import CalendarEvent

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_at": self.start_at.isoformat() if self.start_at else "",
            "end_at": self.end_at.isoformat() if self.end_at else "",
            "participants": list(self.participants),
            "missing_fields": list(self.missing_fields),
            "original_request": self.original_request,
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
        )


def calendar_hint(message: str, history: list[dict] | None = None) -> bool:
    text = " ".join(
        [message or ""]
        + [str(m.get("content") or "") for m in (history or []) if m.get("role") == "user"]
    )
    return bool(_CALENDAR_HINTS.search(text) and _CALENDAR_TIME.search(text))


def is_calendar_cancel(message: str) -> bool:
    return bool(_CANCEL_PAT.search(message or ""))


def extract_event(message: str, history: list[dict] | None = None) -> EventDraft:
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你是日程信息提取器。只输出 JSON："
                    '{"title":str,"start_at":str,"end_at":str,"participants":[str],"missing_fields":[str]}。'
                    ' start_at/end_at 用 ISO 8601；缺失则输出空字符串。'
                    ' 若用户未说结束时间，missing_fields 至少包含 "end_at"。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"最近对话：{json.dumps(history or [], ensure_ascii=False)}\n"
                    f"当前消息：{message}"
                ),
            },
        ]
    )
    if not isinstance(parsed, dict):
        draft = EventDraft(original_request=message)
        _supplement_draft_from_message(draft, message, allow_end=False)
        draft.missing_fields = _normalize_missing_fields(
            title=draft.title,
            start_at=draft.start_at,
            end_at=draft.end_at,
            participants=draft.participants,
            existing=[],
        )
        return draft

    start_at = _parse_dt(str(parsed.get("start_at") or ""))
    end_at = _parse_dt(str(parsed.get("end_at") or ""))
    participants = [str(x).strip() for x in (parsed.get("participants") or []) if str(x).strip()]
    missing = [str(x).strip() for x in (parsed.get("missing_fields") or []) if str(x).strip()]
    if not start_at:
        missing.append("start_at")
    if not end_at:
        missing.append("end_at")
    draft = EventDraft(
        title=str(parsed.get("title") or "").strip() or "未命名日程",
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        missing_fields=_normalize_missing_fields(
            title=str(parsed.get("title") or "").strip() or "未命名日程",
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            existing=list(dict.fromkeys(missing)),
        ),
        original_request=message,
    )
    _supplement_draft_from_message(draft, message, allow_end=False)
    draft.missing_fields = _normalize_missing_fields(
        title=draft.title,
        start_at=draft.start_at,
        end_at=draft.end_at,
        participants=draft.participants,
        existing=draft.missing_fields,
    )
    return draft


def fill_pending_calendar(pending: EventDraft, message: str, history: list[dict] | None = None) -> EventDraft:
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你是日程补槽器。已有一个未完成日程草稿，请只补充缺失字段。"
                    " 允许用户一条消息同时补多个字段。只输出 JSON："
                    '{"title":str,"start_at":str,"end_at":str,"participants":[str],"filled_fields":[str]}。'
                    " 未补到的字段输出空字符串或空数组；不要重写已确认字段。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"已有草稿：{json.dumps(pending.to_dict(), ensure_ascii=False)}\n"
                    f"最近对话：{json.dumps(history or [], ensure_ascii=False)}\n"
                    f"当前补充：{message}"
                ),
            },
        ]
    )
    next_draft = EventDraft.from_dict(pending.to_dict())
    next_draft.original_request = pending.original_request or message
    if next_draft.original_request:
        _supplement_draft_from_message(next_draft, next_draft.original_request, allow_end=False)

    if isinstance(parsed, dict):
        title = str(parsed.get("title") or "").strip()
        start_at = _parse_dt(str(parsed.get("start_at") or ""))
        end_at = _parse_dt(str(parsed.get("end_at") or ""))
        participants = [str(x).strip() for x in (parsed.get("participants") or []) if str(x).strip()]
        if title and ("title" in pending.missing_fields or title != pending.title):
            next_draft.title = title
        if start_at is not None:
            next_draft.start_at = start_at
        if end_at is not None:
            next_draft.end_at = end_at
        if participants:
            next_draft.participants = participants

    _supplement_draft_from_message(next_draft, message)
    _merge_from_message(next_draft, message)
    next_draft.missing_fields = _normalize_missing_fields(
        title=next_draft.title,
        start_at=next_draft.start_at,
        end_at=next_draft.end_at,
        participants=next_draft.participants,
        existing=[],
    )
    return next_draft


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
    missing = list(existing)
    if not title:
        missing.append("title")
    if not start_at:
        missing.append("start_at")
    if not end_at:
        missing.append("end_at")
    if not participants:
        missing.append("participants")
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


def _supplement_draft_from_message(draft: EventDraft, message: str, *, allow_end: bool = True) -> None:
    if not draft.title:
        title = _infer_title(message)
        if title:
            draft.title = title
    if draft.start_at is None:
        draft.start_at = _parse_time_from_message(message)
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


def _parse_time_from_message(message: str) -> datetime | None:
    m = _CALENDAR_TIME.search(message or "")
    if not m:
        return None
    now = datetime.now()
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
    rows = (
        db.query(CalendarEvent)
        .filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.status == "active",
            CalendarEvent.start_at < end_at,
            CalendarEvent.end_at > start_at,
        )
        .order_by(CalendarEvent.start_at.asc())
        .all()
    )
    return rows


def create_event(
    db: Session, *, user_id: int, title: str, start_at: datetime, end_at: datetime, participants: list[str]
) -> CalendarEvent:
    event = CalendarEvent(
        user_id=user_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=json.dumps(participants, ensure_ascii=False),
        status="active",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def update_event(
    db: Session,
    event_id: int,
    *,
    title: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    participants: list[str] | None = None,
) -> CalendarEvent | None:
    event = db.get(CalendarEvent, event_id)
    if not event:
        return None
    if title is not None:
        event.title = title
    if start_at is not None:
        event.start_at = start_at
    if end_at is not None:
        event.end_at = end_at
    if participants is not None:
        event.participants = json.dumps(participants, ensure_ascii=False)
    db.commit()
    db.refresh(event)
    return event


def cancel_event(db: Session, event_id: int) -> CalendarEvent | None:
    event = db.get(CalendarEvent, event_id)
    if not event:
        return None
    event.status = "cancelled"
    db.commit()
    db.refresh(event)
    return event


def serialize_event_card(event: CalendarEvent) -> dict[str, Any]:
    try:
        participants = json.loads(event.participants or "[]")
    except json.JSONDecodeError:
        participants = []
    return {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at.isoformat() if event.start_at else "",
        "end_at": event.end_at.isoformat() if event.end_at else "",
        "participants": participants,
        "status": event.status,
    }


def suggest_next_slot(start_at: datetime, end_at: datetime) -> tuple[datetime, datetime]:
    delta = end_at - start_at
    new_start = end_at
    return new_start, new_start + (delta if delta > timedelta() else timedelta(hours=1))
