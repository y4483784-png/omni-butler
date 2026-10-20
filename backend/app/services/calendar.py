"""Calendar service helpers for Phase 3 local schedule actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime as _Wall
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import Role, complete_json
from app.agents.context import clip_history_for_prompt
from app.models.models import CalendarEvent
from app.services.calendar_factory import calendar_provider_for, calendar_writes_remote
from app.services.calendar_google import (
    CalendarReconnectRequired,
    CalendarRemoteReadError,
    CalendarRemoteWriteError,
    CalendarTitleUnmatched,
)
from app.services.calendar_secrets import CalendarTokenError, CalendarTransientError

REMOTE_READ_ERRORS = (CalendarRemoteReadError, CalendarTokenError, CalendarTransientError)

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
    # Create fallthrough only: "list" switches to the read-only list reply.
    requested_action: str = ""
    list_day: str = ""
    # Model classification. Empty means the card layer treats a write as a meeting.
    kind: str = ""
    description: str = ""
    location: str = ""
    # The start the model returned, before a local regex fallback moves it.
    model_start: datetime | None = None
    # The end the model returned. None means an update keeps the matched duration.
    specified_end: datetime | None = None
    # True when the model returned a note the user asked to change.
    notes_specified: bool = False
    # Update/cancel: the name used to find an event. The card title is separate.
    search_title: str = ""
    new_title: str = ""
    change_title: bool = False
    change_participants: bool = False
    change_location: bool = False
    change_kind: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_at": self.start_at.isoformat() if self.start_at else "",
            "end_at": self.end_at.isoformat() if self.end_at else "",
            "participants": list(self.participants),
            "missing_fields": list(self.missing_fields),
            "original_request": self.original_request,
            "reference_start_at": self.reference_start_at.isoformat() if self.reference_start_at else "",
            "requested_action": self.requested_action,
            "list_day": self.list_day,
            "kind": self.kind,
            "description": self.description,
            "location": self.location,
            "search_title": self.search_title,
            "new_title": self.new_title,
            "change_title": self.change_title,
            "change_participants": self.change_participants,
            "change_location": self.change_location,
            "change_kind": self.change_kind,
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
            requested_action=str(data.get("requested_action") or ""),
            list_day=_civil_day(str(data.get("list_day") or "")),
            kind=str(data.get("kind") or ""),
            description=str(data.get("description") or ""),
            location=str(data.get("location") or ""),
            search_title=str(data.get("search_title") or ""),
            new_title=str(data.get("new_title") or ""),
            change_title=bool(data.get("change_title")),
            change_participants=bool(data.get("change_participants")),
            change_location=bool(data.get("change_location")),
            change_kind=bool(data.get("change_kind")),
        )


def calendar_hint(message: str, history: list[dict] | None = None) -> bool:
    text = " ".join(
        [message or ""]
        + [str(m.get("content") or "") for m in (history or []) if m.get("role") == "user"]
    )
    return bool(_CALENDAR_HINTS.search(text) and _CALENDAR_TIME.search(text))


def is_calendar_cancel(message: str) -> bool:
    return bool(_CANCEL_PAT.search(message or ""))


_CALENDAR_ACTIONS = frozenset({"create", "update", "cancel", "list", "suggest"})
_HORIZON_END = datetime(2100, 1, 1)


def _classify_prompt(now: datetime | None, timezone_name: str | None) -> str:
    """Five actions, one JSON field. Examples name the words the old keywords missed."""
    clock = now or datetime.now()
    day = clock.date().isoformat()
    zone = (timezone_name or "").strip()
    if zone:
        today = f"今天是 {day}，账户时区是 {zone}。"
    else:
        today = f"今天是 {day}。"
    return (
        "你是日程动作分类器。只输出 JSON："
        '{"action":str}。'
        f" {today}"
        " action 必须正好是 create、update、cancel、list、suggest 之一。"
        " 不要输出把握程度，不要输出第二个动作。"
        " create 是新建一条日程，例如「新建」「安排」「约一场会」。"
        " update 是改已有日程，例如「改期」「修改」「推迟」「提前」「改成」「改为」「换到」「挪到」「延后」。"
        " cancel 是删掉已有日程，例如「删除」「取消」「撤销」「删掉」。"
        " list 是查看已有日程，例如「查日程」「有哪些」「我的日程」。"
        " suggest 是看空闲，例如「有空」「空闲」「什么时候方便」。"
    )


def classify_calendar_action(
    message: str,
    history: list[dict] | None = None,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> str:
    """One of the five actions, or empty when the model did not name one.

    Transport failures propagate from ``complete_json`` when the caller asks
    it to raise. A missing or unexpected action is empty. This function does
    not look for keywords in the sentence.
    """
    parsed = complete_json(
        [
            {"role": "system", "content": _classify_prompt(now, timezone_name)},
            {
                "role": "user",
                "content": (
                    f"最近对话：{json.dumps(clip_history_for_prompt(history, max_msgs=8, max_chars=300), ensure_ascii=False)}\n"
                    f"当前消息：{message}"
                ),
            },
        ],
        role=Role.PLANNER,
    )
    if not isinstance(parsed, dict):
        return ""
    action = str(parsed.get("action") or "").strip().lower()
    if action in _CALENDAR_ACTIONS:
        return action
    return ""


def _extract_system_prompt(
    now: datetime | None,
    timezone_name: str | None,
    *,
    existing_event: bool = False,
) -> str:
    """Name the calendar day that 「明天」 and 「后天」 are relative to.

    A connected account also gets that account's IANA id. An unconnected
    prompt names the date only and does not invent a product zone. The model
    still returns offset-less wall times; nothing here rewrites them.

    Update and cancel ask for the existing event's name. The model is not
    told to concatenate participant nicknames into that title.
    """
    clock = now or datetime.now()
    day = clock.date().isoformat()
    zone = (timezone_name or "").strip()
    if zone:
        today = f"今天是 {day}，账户时区是 {zone}。"
    else:
        today = f"今天是 {day}。"
    existing = ""
    if existing_event:
        existing = (
            " 修改或撤销时，title 是已有日程的名称，不要把参与人昵称或其他词拼进标题。"
        )
        fields = (
            '{"title":str,"new_title":str,"start_at":str,"end_at":str,"participants":[str],'
            '"location":str,"missing_fields":[str],"kind":str,"description":str,'
            '"change_title":bool,"change_end":bool,"change_notes":bool,'
            '"change_participants":bool,"change_location":bool,"change_kind":bool}'
        )
        scheduling = ""
        existing += (
            " title 是用来寻找已有日程的称呼，不要把参与人昵称拼进去。"
            " 下面会附上已有日程的标题和起止，没有编号。不要编造编号，也不要输出编号。"
            " new_title 是要写成的新标题，只在 change_title 为 true 时填写，否则输出空字符串。"
            " change_title 只在用户明确要改标题时为 true，否则为 false。"
            " 修改时 kind 为 meeting 或 block。"
            " 留给自己的一段时间是 block，拿不准就 meeting。"
            " change_kind 只在用户明确要改会议还是时间块时为 true，否则为 false。"
            " change_end 只在用户明确要改结束时间或时长时为 true，否则为 false。"
            " end_at 只在 change_end 为 true 时填写，否则输出空字符串。"
            " 没给出 change_end，或它为 false、空，都表示不改结束时间和时长。"
            " change_notes 只在用户明确要改备注时为 true，否则为 false。"
            " description 只在 change_notes 为 true 时填写用户要的新备注，否则输出空字符串。"
            " 没给出 change_notes，或它为 false、空，都表示不改备注。"
            " change_participants 只在用户明确要改参与人时为 true，否则为 false。"
            " participants 只在 change_participants 为 true 时填写，否则输出空数组。"
            " change_location 只在用户明确要改地点时为 true，否则为 false。"
            " location 只在 change_location 为 true 时填写，否则输出空字符串。"
            " 不要把已有备注缩短后填进 description，也不要把时长默认写成一小时。"
        )
    else:
        fields = (
            '{"title":str,"start_at":str,"end_at":str,"participants":[str],'
            '"missing_fields":[str],"kind":str,"description":str,"location":str}'
        )
        scheduling = (
            " 用户说了时长就写 end_at，用开始之后的那段无偏移时间。"
            " 个人时间块参与人可空，不要把参与人放进 missing_fields。"
            " kind 为 meeting 或 block。开会或有别人参加是 meeting；"
            " 留给自己的一段时间是 block；拿不准就 meeting。"
            " description 是用户说的备注，没说就输出空字符串，不要放进 missing_fields。"
            " location 是用户说的地点，没说就输出空字符串。"
        )
    return (
        "你是日程信息提取器。只输出 JSON："
        f"{fields}。"
        " start_at/end_at 用无偏移的 ISO 8601；缺失则输出空字符串。"
        " 参与人可以省略，不要因为没有参与人写入 missing_fields。"
        " 用户没说结束时间时 end_at 留空，不要把 end_at 放进 missing_fields。"
        f" {today}"
        "「明天」「后天」相对这一天。"
        " start_at/end_at 仍输出无偏移的 ISO 8601。"
        f"{scheduling}"
        f"{existing}"
    )


def _explicit_yes(parsed: dict, key: str) -> bool:
    """True only when the model explicitly says the user asked for this change.

    Missing, null, false, and empty all mean don't change. A filled end time
    or note is not itself a request.
    """
    if key not in parsed:
        return False
    value = parsed.get(key)
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _civil_day(raw: str) -> str:
    """A model-named civil day, or empty when the 7-day list window should be used."""
    text = (raw or "").strip()
    if len(text) != 10:
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return text


def _list_day_prompt(now: datetime | None, timezone_name: str | None) -> str:
    clock = now or datetime.now()
    day = clock.date().isoformat()
    zone = (timezone_name or "").strip()
    if zone:
        today = f"今天是 {day}，账户时区是 {zone}。"
    else:
        today = f"今天是 {day}。"
    return (
        '你只输出 JSON：{"day":str}。'
        f" {today}"
        " 用户点名了某一天（含明天、后天或具体日期）时，day 填那一天的 YYYY-MM-DD。"
        " 用户说本周、这几天，或没有点名某一天时，day 输出空字符串。"
    )


def _catalog_text(rows) -> str:
    """Titles and wall times only. Ids are not shown to the model."""
    lines: list[str] = []
    for row in rows or []:
        title = str(getattr(row, "title", "") or "").strip() or "（无标题）"
        start = getattr(row, "start_at", None)
        end = getattr(row, "end_at", None)
        if isinstance(start, datetime) and isinstance(end, datetime):
            lines.append(f"{title} {start:%Y-%m-%d %H:%M}–{end:%H:%M}")
        elif isinstance(start, datetime):
            lines.append(f"{title} {start:%Y-%m-%d %H:%M}")
        else:
            lines.append(title)
    if not lines:
        return "（没有未结束的日程）"
    return "\n".join(lines)


def extract_event(
    message: str,
    history: list[dict] | None = None,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    existing_event: bool = False,
    listed: str = "",
) -> EventDraft:
    catalog = ""
    if existing_event:
        catalog = f"已有未结束的日程（只有标题和起止，没有编号）：\n{listed or '（没有未结束的日程）'}\n"
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": _extract_system_prompt(
                    now, timezone_name, existing_event=existing_event
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{catalog}"
                    f"最近对话：{json.dumps(clip_history_for_prompt(history, max_msgs=8, max_chars=300), ensure_ascii=False)}\n"
                    f"当前消息：{message}"
                ),
            },
        ],
        role=Role.PLANNER,
    )
    if not isinstance(parsed, dict):
        draft = EventDraft(original_request=message)
        _supplement_draft_from_message(draft, message, allow_end=not existing_event, now=now)
        return _finalize_prefill(draft, fill_hour=not existing_event)

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
    raw_kind = str(parsed.get("kind") or "").strip().lower()
    draft.kind = raw_kind if raw_kind in {"meeting", "block"} else ""
    draft.location = str(parsed.get("location") or "").strip()
    # Keep the model's start. End and notes on an existing event count only
    # when change_end / change_notes are explicitly true.
    draft.model_start = start_at
    if not existing_event:
        draft.specified_end = end_at
        draft.description = str(parsed.get("description") or "").strip()
    else:
        # The lookup name is not the card title. A model id, if any, is ignored.
        draft.search_title = str(parsed.get("title") or "").strip()
        draft.title = draft.search_title
        if _explicit_yes(parsed, "change_title"):
            draft.change_title = True
            draft.new_title = str(parsed.get("new_title") or "").strip()
        if _explicit_yes(parsed, "change_participants"):
            draft.change_participants = True
        else:
            draft.participants = []
        if _explicit_yes(parsed, "change_location"):
            draft.change_location = True
        else:
            draft.location = ""
        if _explicit_yes(parsed, "change_kind") and draft.kind in {"meeting", "block"}:
            draft.change_kind = True
        else:
            draft.kind = ""
            draft.change_kind = False
        # Empty or unset flags mean the user did not ask to change that field.
        # A model-filled end or a shortened note is ignored unless the flag is yes.
        draft.specified_end = end_at if _explicit_yes(parsed, "change_end") else None
        if draft.specified_end is None:
            draft.end_at = None
        notes = str(parsed.get("description") or "").strip()
        if _explicit_yes(parsed, "change_notes"):
            draft.description = notes
            draft.notes_specified = True
        else:
            draft.description = ""
            draft.notes_specified = False
    _supplement_draft_from_message(draft, message, allow_end=not existing_event, now=now)
    if existing_event and draft.search_title == "" and draft.title:
        draft.search_title = draft.title
    return _finalize_prefill(draft, fill_hour=not existing_event)


def extract_list_day(
    message: str,
    history: list[dict] | None = None,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> str:
    """Civil day for a list that regex already selected. Empty keeps the 7-day window."""
    parsed = complete_json(
        [
            {"role": "system", "content": _list_day_prompt(now, timezone_name)},
            {
                "role": "user",
                "content": (
                    f"最近对话：{json.dumps(clip_history_for_prompt(history, max_msgs=8, max_chars=300), ensure_ascii=False)}\n"
                    f"当前消息：{message}"
                ),
            },
        ],
        role=Role.PLANNER,
    )
    if not isinstance(parsed, dict):
        return ""
    return _civil_day(str(parsed.get("day") or ""))


def _free_slot_prompt(now: datetime | None, timezone_name: str | None) -> str:
    clock = now or datetime.now()
    day = clock.date().isoformat()
    zone = (timezone_name or "").strip()
    if zone:
        today = f"今天是 {day}，账户时区是 {zone}。"
    else:
        today = f"今天是 {day}。"
    return (
        "你在帮用户从已经列出的日程里挑少量合适的空闲时段。"
        f" {today}"
        " 列出的时间是账户墙钟。"
        " 按列出的这一天挑合适时段。"
        " 不要推荐半夜到清晨，除非用户明确要夜里，或这一天只剩夜里有空。"
        " 列表里已经占用的时段不能说成有空，也不要推荐和它们重叠的时间。"
        " 拿不准某一段是不是空闲时，不要断言有空，请用户看列表。"
        " 不要编造日程，不要创建。"
        ' 只输出 JSON：{"slots":[{"start_at":str,"end_at":str}],"note":str}。'
        " start_at 和 end_at 用无偏移的 ISO 8601。"
        " note 用一句中文说明，不得把列出的忙时说成有空。"
    )


def extract_free_slots(
    message: str,
    history: list[dict] | None = None,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    listed: str = "",
) -> tuple[list[tuple[datetime, datetime]], str] | None:
    """Model-picked slots. None means the call failed. An empty list is a real answer.

    Clock values are kept as the model wrote them. Nothing here drops a night slot.
    """
    try:
        parsed = complete_json(
            [
                {"role": "system", "content": _free_slot_prompt(now, timezone_name)},
                {
                    "role": "user",
                    "content": (
                        f"已列出的日程：\n{listed}\n"
                        f"最近对话：{json.dumps(clip_history_for_prompt(history, max_msgs=8, max_chars=300), ensure_ascii=False)}\n"
                        f"当前消息：{message}"
                    ),
                },
            ],
            role=Role.PLANNER,
        )
    except Exception:
        return None
    if not isinstance(parsed, dict) or "slots" not in parsed:
        return None
    slots: list[tuple[datetime, datetime]] = []
    raw_slots = parsed.get("slots") or []
    if not isinstance(raw_slots, list):
        return None
    for item in raw_slots:
        if not isinstance(item, dict):
            continue
        start_at = _parse_dt(str(item.get("start_at") or ""))
        end_at = _parse_dt(str(item.get("end_at") or ""))
        if start_at is None or end_at is None:
            continue
        slots.append((start_at, end_at))
    return slots, str(parsed.get("note") or "").strip()


DRAFT_TTL = timedelta(minutes=30)


def _finalize_prefill(draft: EventDraft, *, fill_hour: bool = True) -> EventDraft:
    """Optional participants. Create still fills a missing end as start + 1 hour.

    Update and cancel pass ``fill_hour=False``. A missing end stays empty.
    """
    if fill_hour and draft.start_at is not None and draft.end_at is None:
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
    missing = [item for item in existing if item not in {"end_at", "participants", "description", "kind"}]
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


def confirm_titled_change(
    db: Session,
    user_id: int,
    *,
    action: str,
    title: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    participants: list[str] | None = None,
    location: str | None = None,
    description: str | None = None,
    external_id: str | None = None,
):
    """Connected confirm. Prefer the card's Google id, else one exact title."""
    if not calendar_writes_remote(db, user_id):
        raise CalendarTitleUnmatched()
    provider = calendar_provider_for(db, user_id)
    confirmer = getattr(provider, "confirm_titled_change", None)
    if confirmer is None:
        raise CalendarRemoteWriteError()
    return confirmer(
        user_id,
        action=action,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        location=location,
        description=description,
        external_id=external_id,
    )


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
    card = {
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
    if getattr(event, "all_day", False):
        card["all_day"] = True
    if getattr(event, "readonly", False):
        card["id"] = None
        card["readonly"] = True
    return card


def draft_expires_at(now: datetime | None = None) -> str:
    """UTC expiry for a confirm card, as ISO-8601 with a numeric offset.

    Naive ``now`` is a UTC wall time (the container clock). Aware values are
    converted to UTC. An offset-less stamp is read as browser-local time, so a
    UTC+8 client treated a fresh 30-minute draft as already expired.
    """
    base = now if now is not None else datetime.now(timezone.utc)
    if base.tzinfo is None or base.tzinfo.utcoffset(base) is None:
        base = base.replace(tzinfo=timezone.utc)
    else:
        base = base.astimezone(timezone.utc)
    return (base.replace(microsecond=0) + DRAFT_TTL).isoformat()


def titles_match(query: str, title: str) -> bool:
    needle = (query or "").strip()
    hay = (title or "").strip()
    if not needle or not hay:
        return False
    return needle.casefold() in hay.casefold() or hay.casefold() in needle.casefold()


def _ends_after_now(row, now: datetime) -> bool:
    """In progress stays. An all-day row stays when its exclusive end day is today or later."""
    end = getattr(row, "end_at", None)
    if not isinstance(end, _Wall):
        return False
    if bool(getattr(row, "all_day", False)):
        return end.date() >= now.date()
    return end > now


def _row_has_identity(row, *, require_google_id: bool) -> bool:
    external = str(getattr(row, "external_id", "") or "").strip()
    if require_google_id:
        return bool(external)
    if getattr(row, "id", None) is not None or external:
        return True
    # A real DTO always has an id field. A display double that omitted it can still be listed.
    fields = getattr(row, "__dataclass_fields__", None)
    if fields is not None and "id" in fields:
        return False
    return "id" not in getattr(row, "__dict__", {})


def upcoming_events(
    db: Session,
    user_id: int,
    now: datetime,
    *,
    provider=None,
    connected: bool | None = None,
) -> tuple[list, str]:
    """One read from account-wall now through year 2100.

    Connected reads use the provider's primary span when it has one, which
    merges Google rows with local rows by external id. Callers narrow that
    list in memory. Ended rows are omitted. A non-empty notice means the read
    failed or stopped early; update and cancel must not rank that list.
    """
    if provider is None:
        provider = calendar_provider_for(db, user_id)
    if connected is None:
        connected = bool(calendar_writes_remote(db, user_id))
    start = now.replace(microsecond=0)
    span = getattr(provider, "events_between", None)
    if span is not None:
        rows, notice = span(user_id, start, _HORIZON_END)
    else:
        rows = list(provider.list_events(user_id, start, _HORIZON_END))
        notice = str(getattr(provider, "list_notice", "") or "")
    require_google_id = bool(connected) and not notice
    kept = [
        row
        for row in rows
        if _ends_after_now(row, start) and _row_has_identity(row, require_google_id=require_google_id)
    ]
    return kept, str(notice or "")


def rank_one_event(events, query: str):
    """The one event whose title is the unique exact hit, else the unique soft hit.

    Exact equality wins over ``titles_match``. Several equally good titles are
    not reduced to the first row. What else to rank on is not settled, so a
    tie returns None.
    """
    needle = (query or "").strip()
    if not needle:
        return None
    exact = [
        row
        for row in events
        if str(getattr(row, "title", "") or "").strip() == needle
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    soft = [
        row
        for row in events
        if titles_match(needle, str(getattr(row, "title", "") or ""))
    ]
    if len(soft) == 1:
        return soft[0]
    return None


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
    """Reschedule clocks come from extraction. This no longer splits the sentence.

    It does not move ``start_at`` and does not fill a one-hour end.
    """
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
