from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.core.llm import Role, complete_json
from app.services.calendar import EventHint, _catalog_text, draft_expires_at, match_events, pick_event
from app.services.calendar_provider import Invitee, invitee_wire, parse_invitees
from app.services.drive import OfficialRead, read_official_minutes

_MIN_PASTE = 80
_OFFICIAL_PASTE = "这场会还没有官方纪要。把会议记录或转写贴过来。不会编造不存在的纪要。"
_ATTACHMENT_CONSENT = "日历上有纪要附件。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。"
_MEET_CONSENT = "这场会有 Meet 智能纪要。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。"
_UNAVAILABLE = "暂时读不到这场会的官方纪要。可以把记录贴过来。不会编造官方纪要。"
_NOT_FOUND = "没有找到这场会。这次没有读官方纪要，也没有编造纪要。"
_POINTED = re.compile(
    r"^帮我整理这场会的纪要：(?P<title>.+?)，(?P<day>\d{4}-\d{2}-\d{2})"
    r"(?: (?P<clock>\d{2}:\d{2}) 开始| 全天)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DUE = re.compile(
    r"(下周|下星期)([一二三四五六日天])|(今天|明天|后天)|(?:周|星期)([一二三四五六日天])"
)
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


@dataclass(frozen=True)
class FollowMail:
    to: tuple[Invitee, ...]
    subject: str
    body: str


@dataclass(frozen=True)
class ActionItem:
    id: str
    text: str
    owner: str
    due_said: str
    evidence: str
    start_at: str
    end_at: str


@dataclass(frozen=True)
class PastedMinutes:
    paste: str
    points: tuple[str, ...]
    actions: tuple[ActionItem, ...]
    mail: FollowMail

    def __post_init__(self) -> None:
        for point in self.points:
            if point not in self.paste:
                raise ValueError("point is not in the paste")
        for action in self.actions:
            if action.text not in self.paste:
                raise ValueError("action text is not in the paste")
            if action.evidence not in self.paste:
                raise ValueError("evidence is not in the paste")
            if action.due_said and action.due_said not in self.paste:
                raise ValueError("due_said is not in the paste")
            if action.owner and action.owner not in action.text:
                raise ValueError("owner is not in the action text")


@dataclass(frozen=True)
class NeedsPaste:
    checked: tuple[str, ...] = ()


@dataclass(frozen=True)
class NeedsConsent:
    consent: Literal["attachment", "meet"]
    checked: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unavailable:
    checked: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotFound:
    pass


@dataclass(frozen=True)
class ChooseFollow:
    candidates: tuple[dict, ...]


@dataclass(frozen=True)
class SourcedMinutes:
    minutes: PastedMinutes
    kind: Literal["meet_smart_notes", "attachment_notes"]
    title: str
    url: str


FollowOutcome = (
    PastedMinutes | SourcedMinutes | NeedsPaste | NeedsConsent | Unavailable | NotFound | ChooseFollow
)


@dataclass(frozen=True)
class FollowResult:
    outcome: FollowOutcome
    now: datetime

    @property
    def caption(self) -> str:
        return caption(self.outcome)

    @property
    def card(self) -> dict:
        return to_wire(self.outcome, self.now)


def follow_utterance(title: str, start_at: str, *, all_day: bool = False) -> str:
    day = (start_at or "")[:10]
    if all_day or len(start_at or "") < 16:
        return f"帮我整理这场会的纪要：{title}，{day} 全天"
    return f"帮我整理这场会的纪要：{title}，{day} {start_at[11:16]} 开始"


def follow_up(message: str, events, now: datetime, db=None, user_id: int | None = None) -> FollowResult:
    text = (message or "").strip()
    match = _POINTED.match(text)
    if match:
        body = text[match.end() :].strip()
        hint = _hint_from_pointed(match)
    else:
        body = text
        hint = None
    if len(body) >= _MIN_PASTE:
        title, invitees = _meeting(match, events) if match else ("", ())
        return FollowResult(_draft_minutes(body, title, invitees, now), now)
    if hint is None:
        hint = _hint_from_model(text, events, now)
    row = pick_event(events, hint)
    if row is None:
        matched = match_events(events, hint)
        if len(matched) > 1:
            return FollowResult(ChooseFollow(tuple(_candidate(item) for item in matched)), now)
        return FollowResult(NotFound(), now)
    title = str(getattr(row, "title", "") or "").strip()
    invitees = tuple(parse_invitees(getattr(row, "participants", None)))
    read = read_official_minutes(
        db,
        user_id,
        meeting_code=str(getattr(row, "meeting_code", "") or ""),
        attachments=tuple(getattr(row, "attachments", ()) or ()),
    )
    return _from_read(read, title, invitees, now)


def caption(outcome: FollowOutcome) -> str:
    minutes = _pasted(outcome)
    if minutes is not None:
        if any(item.start_at for item in minutes.actions):
            return "纪要在卡片上。勾选待办并确认之后，才会写入日历。邮件需要你自己复制或打开。"
        if minutes.actions or minutes.points:
            return "纪要在卡片上。这次没有可写入日历的日期。邮件需要你自己复制或打开。"
        return "这段纪要里没有抄出待办。没有编造。"
    if isinstance(outcome, NeedsPaste):
        return _OFFICIAL_PASTE
    if isinstance(outcome, NeedsConsent):
        return _MEET_CONSENT if outcome.consent == "meet" else _ATTACHMENT_CONSENT
    if isinstance(outcome, Unavailable):
        return _UNAVAILABLE
    if isinstance(outcome, NotFound):
        return _NOT_FOUND
    return ""


def to_wire(outcome: FollowOutcome, now: datetime) -> dict:
    if isinstance(outcome, NeedsPaste):
        card: dict = {"mode": "followup", "phase": "info", "status": "needs_paste"}
        if outcome.checked:
            card["checked"] = list(outcome.checked)
        return card
    if isinstance(outcome, NeedsConsent):
        return {
            "mode": "followup",
            "phase": "info",
            "status": "needs_consent",
            "consent": outcome.consent,
            "checked": list(outcome.checked),
        }
    if isinstance(outcome, Unavailable):
        return {
            "mode": "followup",
            "phase": "info",
            "status": "unavailable",
            "checked": list(outcome.checked),
        }
    if isinstance(outcome, NotFound):
        return {"mode": "followup", "phase": "info", "status": "not_found"}
    if isinstance(outcome, ChooseFollow):
        return {
            "mode": "followup",
            "phase": "info",
            "status": "choose",
            "candidates": list(outcome.candidates),
        }
    minutes = _pasted(outcome)
    assert minutes is not None
    phase = "draft" if any(item.start_at for item in minutes.actions) else "info"
    card = {
        "mode": "followup",
        "phase": phase,
        "status": "ready",
        "source": _source(outcome),
        "points": list(minutes.points),
        "actions": [
            {
                "id": item.id,
                "text": item.text,
                "owner": item.owner,
                "due_said": item.due_said,
                "evidence": item.evidence,
                "start_at": item.start_at,
                "end_at": item.end_at,
            }
            for item in minutes.actions
        ],
        "mail": {
            "to": [invitee_wire(person) for person in minutes.mail.to],
            "subject": minutes.mail.subject,
            "body": minutes.mail.body,
        },
    }
    if phase == "draft":
        card["expires_at"] = draft_expires_at(now)
    return card


def _pasted(outcome: FollowOutcome) -> PastedMinutes | None:
    if isinstance(outcome, PastedMinutes):
        return outcome
    if isinstance(outcome, SourcedMinutes):
        return outcome.minutes
    return None


def _source(outcome: FollowOutcome) -> dict:
    if isinstance(outcome, SourcedMinutes):
        return {"kind": outcome.kind, "title": outcome.title, "url": outcome.url}
    return {"kind": "pasted"}


def _draft_minutes(body: str, title: str, invitees: tuple[Invitee, ...], now: datetime) -> PastedMinutes:
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你从会议纪要里抄出要点和待办。只输出 JSON："
                    '{"points":[str],"actions":[{"text":str,"owner":str,"due_said":str,"evidence":str}]}。'
                    " points、text、evidence、due_said 必须是纪要里的原文字段。"
                    " owner 必须出现在该条 text 里，否则用空字符串。"
                    " 不要编造纪要里没有的人、日期或句子。不要输出开始时间。"
                ),
            },
            {"role": "user", "content": body},
        ],
        role=Role.PLANNER,
    )
    points, actions = _from_model(body, parsed, now)
    mail = FollowMail(
        to=_recipients(body, actions, invitees),
        subject=_subject(title),
        body="\n".join(item.text for item in actions),
    )
    return PastedMinutes(body, points, actions, mail)


def _from_read(read: OfficialRead, title: str, invitees: tuple[Invitee, ...], now: datetime) -> FollowResult:
    if read.status == "found" and read.minutes is not None:
        minutes = _draft_minutes(read.minutes.text, title, invitees, now)
        return FollowResult(
            SourcedMinutes(minutes, read.minutes.source, read.minutes.title, read.minutes.url),
            now,
        )
    if read.status == "needs_consent" and read.consent in ("attachment", "meet"):
        return FollowResult(NeedsConsent(read.consent, read.checked), now)
    if read.status == "unavailable":
        return FollowResult(Unavailable(read.checked), now)
    return FollowResult(NeedsPaste(read.checked), now)


def _hint_from_pointed(match: re.Match[str]) -> EventHint:
    clock = match.group("clock")
    return EventHint(
        title=match.group("title").strip(),
        day=date.fromisoformat(match.group("day")),
        clock=time.fromisoformat(clock) if clock else None,
    )


def _hint_from_model(message: str, events, now: datetime) -> EventHint:
    if not list(events or []):
        return EventHint()
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你从用户的话里找出要整理纪要的那一场已有会议。只输出 JSON："
                    '{"title":str,"day":str,"clock":str}。'
                    " day 是 YYYY-MM-DD，不知道就空字符串。clock 是 HH:MM，不知道就空字符串。"
                    " title 必须来自用户的话或日程目录，不要编会议名称。"
                    f" 今天是 {now.date().isoformat()}。"
                ),
            },
            {
                "role": "user",
                "content": f"日程目录：{_catalog_text(events)}\n当前消息：{message}",
            },
        ],
        role=Role.PLANNER,
    )
    if not isinstance(parsed, dict):
        return EventHint()
    title = _accepted_title(str(parsed.get("title") or ""), message, events)
    day_text = str(parsed.get("day") or "").strip()
    clock_text = str(parsed.get("clock") or "").strip()
    try:
        day = date.fromisoformat(day_text) if day_text else None
    except ValueError:
        day = None
    try:
        clock = time.fromisoformat(clock_text) if clock_text else None
    except ValueError:
        clock = None
    if clock is not None:
        clock = clock.replace(second=0, microsecond=0)
    return EventHint(title=title, day=day, clock=clock)


def _accepted_title(title: str, message: str, events) -> str:
    cleaned = title.strip()
    if not cleaned:
        return ""
    if cleaned in (message or ""):
        return cleaned
    for row in events or []:
        catalog = str(getattr(row, "title", "") or "").strip()
        if catalog and cleaned == catalog:
            return cleaned
    return ""


def _candidate(row) -> dict:
    start = getattr(row, "start_at", None)
    end = getattr(row, "end_at", None)
    return {
        "title": str(getattr(row, "title", "") or "").strip() or "（无标题）",
        "start_at": start.replace(microsecond=0).isoformat() if isinstance(start, datetime) else "",
        "end_at": end.replace(microsecond=0).isoformat() if isinstance(end, datetime) else "",
        "all_day": bool(getattr(row, "all_day", False)),
    }


def _meeting(match: re.Match[str], events) -> tuple[str, tuple[Invitee, ...]]:
    clock = match.group("clock")
    hint = EventHint(
        title=match.group("title").strip(),
        day=date.fromisoformat(match.group("day")),
        clock=time.fromisoformat(clock) if clock else None,
    )
    row = pick_event(events, hint)
    if row is None:
        return "", ()
    title = str(getattr(row, "title", "") or "").strip()
    return title, tuple(parse_invitees(getattr(row, "participants", None)))


def _from_model(paste: str, parsed: object, now: datetime) -> tuple[tuple[str, ...], tuple[ActionItem, ...]]:
    if not isinstance(parsed, dict):
        return (), ()
    points: list[str] = []
    raw_points = parsed.get("points")
    if isinstance(raw_points, list):
        for item in raw_points:
            text = _span(item, paste)
            if text:
                points.append(text)
    actions: list[ActionItem] = []
    raw_actions = parsed.get("actions")
    if isinstance(raw_actions, list):
        for raw in raw_actions:
            action = _action(paste, raw, now, len(actions) + 1)
            if action is not None:
                actions.append(action)
    return tuple(points), tuple(actions)


def _span(value: object, paste: str) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text and text in paste:
        return text
    return ""


def _action(paste: str, raw: object, now: datetime, index: int) -> ActionItem | None:
    if not isinstance(raw, dict):
        return None
    text = _span(raw.get("text"), paste)
    if not text:
        return None
    evidence_raw = raw.get("evidence")
    if evidence_raw is None:
        evidence = ""
    elif not isinstance(evidence_raw, str):
        return None
    else:
        evidence = evidence_raw.strip()
        if evidence and evidence not in paste:
            return None
    owner_raw = raw.get("owner")
    owner = owner_raw.strip() if isinstance(owner_raw, str) else ""
    if owner and owner not in text:
        owner = ""
    due_raw = raw.get("due_said")
    due = due_raw.strip() if isinstance(due_raw, str) else ""
    if due and due not in paste:
        due = ""
    start_at, end_at = _due_window(due, now) if due else ("", "")
    return ActionItem(f"a{index}", text, owner, due, evidence, start_at, end_at)


def _due_window(due_said: str, now: datetime) -> tuple[str, str]:
    match = _DUE.search(due_said)
    if match is None:
        return "", ""
    if match.group(1):
        day = _named_day(now, match.group(2), next_week=True)
    elif match.group(3) == "今天":
        day = now.date()
    elif match.group(3) == "明天":
        day = now.date() + timedelta(days=1)
    elif match.group(3) == "后天":
        day = now.date() + timedelta(days=2)
    else:
        marker = match.group(4)
        if not marker:
            return "", ""
        day = _named_day(now, marker, next_week=False)
    start = datetime(day.year, day.month, day.day, 9, 0, 0)
    end = start + timedelta(hours=1)
    return start.isoformat(), end.isoformat()


def _named_day(now: datetime, marker: str, *, next_week: bool) -> date:
    monday = now.date() - timedelta(days=now.weekday())
    day = monday + timedelta(days=_WEEKDAY[marker])
    if next_week:
        day += timedelta(days=7)
    return day


def _subject(title: str) -> str:
    cleaned = title.strip()
    return f"跟进：{cleaned}" if cleaned else "跟进：会议"


def _recipients(
    paste: str,
    actions: tuple[ActionItem, ...],
    invitees: tuple[Invitee, ...],
) -> tuple[Invitee, ...]:
    people: list[Invitee] = []
    seen: set[str] = set()

    def add(person: Invitee) -> None:
        key = person.email.casefold() if person.email else f"name:{person.name}"
        if key in seen:
            return
        seen.add(key)
        people.append(person)

    for raw in _EMAIL.findall(paste):
        try:
            person = Invitee(email=raw, name="")
        except ValueError:
            continue
        named = next((item for item in invitees if item.email.casefold() == person.email.casefold()), None)
        add(named or person)
    for action in actions:
        if not action.owner:
            continue
        for person in invitees:
            if action.owner == person.name or action.owner == person.email:
                add(person)
                break
    return tuple(people)
