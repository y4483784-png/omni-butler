"""Read-only meeting brief. Sentences on the card are copied from fetched mail."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.agents.context import clip_history_for_prompt
from app.core.llm import Role, complete_json
from app.services.calendar import EventHint, _catalog_text, pick_event
from app.services.calendar_provider import Invitee, invitee_wire, parse_invitees
from app.services.drive import DriveDocs, search_meeting_docs
from app.services.gmail import MailDown, MailMessage, NoGrant, search_meeting_mail
from app.services.meeting_signals import MeetingSignals

_POINTED = re.compile(
    r"^帮我准备这场会：(?P<title>.+?)，(?P<day>\d{4}-\d{2}-\d{2})(?: (?P<clock>\d{2}:\d{2}) 开始| 全天)$"
)
_ASK = ("请", "麻烦", "能否", "确认")
_DEADLINE = ("截止", "之前", "deadline", "due")


@dataclass(frozen=True)
class MeetingFace:
    title: str
    start_at: str
    end_at: str
    all_day: bool
    timezone: str
    invitees: tuple[Invitee, ...]
    attachment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BriefEmail:
    message_id: str
    thread_id: str
    sender: str
    sent_at: str
    subject: str
    sentences: tuple[str, ...]
    url: str


@dataclass(frozen=True)
class FocusRef:
    message_id: str
    sentence_index: int


@dataclass(frozen=True)
class MailSection:
    state: str
    emails: tuple[BriefEmail, ...] = ()
    focus: tuple[FocusRef, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"found", "empty", "needs_consent", "unavailable"}:
            raise ValueError(self.state)
        if self.state == "found":
            if not 1 <= len(self.emails) <= 5:
                raise ValueError("found mail needs 1 to 5 emails")
            if len(self.focus) > 4:
                raise ValueError("at most 4 focus lines")
            by_id = {email.message_id: email for email in self.emails}
            for ref in self.focus:
                email = by_id.get(ref.message_id)
                if email is None or ref.sentence_index < 0 or ref.sentence_index >= len(email.sentences):
                    raise ValueError("focus ref is outside the fetched mail")
            return
        if self.emails or self.focus:
            raise ValueError("only found mail carries messages")


@dataclass(frozen=True)
class PickedBrief:
    meeting: MeetingFace
    mail: MailSection
    docs: DriveDocs
    connected: bool


@dataclass(frozen=True)
class ChooseMeeting:
    candidates: tuple[MeetingFace, ...]


@dataclass(frozen=True)
class MeetingNotFound:
    said: str


Prep = PickedBrief | ChooseMeeting | MeetingNotFound


@dataclass(frozen=True)
class PrepResult:
    prep: Prep

    @property
    def caption(self) -> str:
        return caption(self.prep)

    @property
    def card(self) -> dict:
        return to_wire(self.prep)


def prepare_meeting(
    db: Session,
    user_id: int,
    message: str,
    history: list[dict] | None,
    events,
    now: datetime,
    zone_name: str,
) -> PrepResult:
    """Resolve one meeting from the events already loaded, then search Gmail."""
    hint = _hint(message, history, events, now, zone_name)
    if hint is None:
        return PrepResult(MeetingNotFound(""))
    picked = pick_event(events, hint)
    if picked is None:
        pool = _choices(events, hint, now)
        if pool:
            return PrepResult(ChooseMeeting(tuple(_face(row, zone_name) for row in pool[:20])))
        return PrepResult(MeetingNotFound(hint.title.strip()))
    meeting = _face(picked, zone_name)
    start = _row_start(picked)
    signals = MeetingSignals(
        title=meeting.title,
        invitees=meeting.invitees,
        attachment_ids=meeting.attachment_ids,
        start_day=start.date() if start is not None else now.date(),
    )
    found = search_meeting_mail(db, user_id, signals)
    docs = search_meeting_docs(db, user_id, signals)
    return PrepResult(PickedBrief(meeting, _mail_section(found), docs, _connected(found, docs)))


def pointed_utterance(title: str, start_at: str, *, all_day: bool) -> str:
    day = (start_at or "")[:10]
    if all_day or len(start_at or "") < 16:
        return f"帮我准备这场会：{title}，{day} 全天"
    return f"帮我准备这场会：{title}，{day} {start_at[11:16]} 开始"


def _mail_section(found: tuple[MailMessage, ...] | NoGrant | MailDown) -> MailSection:
    if isinstance(found, NoGrant):
        return MailSection("needs_consent")
    if isinstance(found, MailDown):
        return MailSection("unavailable")
    emails = tuple(_email(item) for item in found if item.sentences)[:5]
    if not emails:
        return MailSection("empty")
    return MailSection("found", emails, _focus(emails))


def _connected(found: tuple[MailMessage, ...] | NoGrant | MailDown, docs: DriveDocs) -> bool:
    if isinstance(found, NoGrant) and found.why == "not_connected":
        return False
    return bool(docs.connected) or not isinstance(found, NoGrant)


def caption(prep: Prep) -> str:
    if isinstance(prep, ChooseMeeting):
        return "找到多场会，请在卡片上点要准备的那一场。"
    if isinstance(prep, MeetingNotFound):
        if prep.said:
            return f"没有找到「{prep.said}」这场会，这次没有搜邮件。"
        return "要准备哪一场会？请点卡片上的会前准备，或说出会议名称。"
    title = prep.meeting.title
    mail = prep.mail.state
    docs = prep.docs.state
    if mail == "empty" and docs == "empty":
        return f"找到了「{title}」，没有相关邮件，也没有相关云端文档。"
    parts: list[str] = []
    if mail == "found":
        parts.append(f"会前简报在卡片上。「{title}」。")
    elif mail == "empty":
        parts.append(f"找到了「{title}」，没有和这场会相关的邮件。")
    elif mail == "unavailable":
        parts.append(f"找到了「{title}」，暂时读不到 Gmail。这次没有邮件内容。")
    elif mail == "needs_consent" and docs == "needs_consent":
        if prep.connected:
            parts.append(f"找到了「{title}」。要搜邮件和查文档，需要授权读取 Gmail 和云端硬盘。日历连接会保留。")
        else:
            parts.append("要整理相关邮件和云端文档，需要先连接 Google 账户并授权读取 Gmail 和云端硬盘。")
    elif mail == "needs_consent":
        if prep.connected:
            parts.append(f"找到了「{title}」。要搜邮件，需要授权读取 Gmail。日历连接会保留。")
        else:
            parts.append("要整理相关邮件，需要先连接 Google 账户并授权读取 Gmail。")
    if docs == "empty" and mail != "empty":
        parts.append("没有找到相关文档。")
    elif docs == "needs_consent" and mail != "needs_consent":
        parts.append("要查相关文档，需要授权读取云端硬盘。日历连接会保留。")
    elif docs == "unavailable":
        parts.append("暂时读不到云端硬盘。这次没有文档。")
    return "".join(parts)


def to_wire(prep: Prep) -> dict:
    card: dict = {"mode": "brief", "phase": "info"}
    if isinstance(prep, PickedBrief):
        card.update(
            status="ready",
            connected=prep.connected,
            meeting=_meeting_wire(prep.meeting),
            mail={
                "state": prep.mail.state,
                "emails": [_email_wire(email) for email in prep.mail.emails],
                "focus": [
                    {"message_id": ref.message_id, "sentence_index": ref.sentence_index}
                    for ref in prep.mail.focus
                ],
            },
            docs={
                "state": prep.docs.state,
                "docs": [
                    {
                        "file_id": item.file_id,
                        "title": item.title,
                        "url": item.url,
                        "via": item.via,
                        "sentences": list(item.sentences),
                    }
                    for item in prep.docs.docs
                ],
            },
        )
        return card
    if isinstance(prep, ChooseMeeting):
        card.update(status="choose", candidates=[_meeting_wire(item) for item in prep.candidates])
        return card
    card.update(status="not_found", said=prep.said)
    return card


def _hint(message: str, history, events, now: datetime, zone_name: str) -> EventHint | None:
    pointed = _POINTED.match((message or "").strip())
    if pointed:
        clock = pointed.group("clock")
        return EventHint(
            title=pointed.group("title").strip(),
            day=date.fromisoformat(pointed.group("day")),
            clock=time.fromisoformat(clock) if clock else None,
        )
    parsed = complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你从用户的话里找出要准备的那一场已有会议。只输出 JSON："
                    '{"title":str,"day":str,"clock":str}。'
                    " day 是 YYYY-MM-DD，不知道就空字符串。clock 是 HH:MM，不知道就空字符串。"
                    " title 必须来自用户的话或下面的日程目录，不要编会议名称。"
                    f" 今天是 {now.date().isoformat()}。时区 {zone_name or '未设置'}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"日程目录：{_catalog_text(events)}\n"
                    f"最近对话：{json.dumps(clip_history_for_prompt(history, max_msgs=8, max_chars=300), ensure_ascii=False)}\n"
                    f"当前消息：{message}"
                ),
            },
        ],
        role=Role.PLANNER,
    )
    if not isinstance(parsed, dict):
        return None
    title = str(parsed.get("title") or "").strip()
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
    return EventHint(title=title, day=day, clock=clock)


def _choices(events, hint: EventHint, now: datetime) -> list:
    if hint.blank:
        end = now + timedelta(days=7)
        pool = []
        for row in events or []:
            start = _row_start(row)
            title = str(getattr(row, "title", "") or "").strip()
            if start is not None and title and now <= start < end:
                pool.append(row)
        return pool
    pool = list(events or [])
    if hint.day is not None:
        pool = [row for row in pool if (start := _row_start(row)) is not None and start.date() == hint.day]
    elif hint.clock is None:
        needle = hint.title.strip()
        exact = [row for row in pool if str(getattr(row, "title", "") or "").strip() == needle]
        return exact if len(exact) > 1 else []
    if hint.clock is not None:
        pool = [
            row
            for row in pool
            if (start := _row_start(row)) is not None
            and start.time().replace(second=0, microsecond=0) == hint.clock.replace(second=0, microsecond=0)
        ]
    return pool


def _face(row, zone_name: str) -> MeetingFace:
    start = _row_start(row)
    end = getattr(row, "end_at", None)
    return MeetingFace(
        title=str(getattr(row, "title", "") or "").strip() or "（无标题）",
        start_at=_iso(start),
        end_at=_iso(end if isinstance(end, datetime) else None),
        all_day=bool(getattr(row, "all_day", False)),
        timezone=zone_name or "",
        invitees=tuple(parse_invitees(getattr(row, "participants", None))),
        attachment_ids=tuple(getattr(row, "attachment_ids", ()) or ()),
    )


def _email(item: MailMessage) -> BriefEmail:
    return BriefEmail(
        message_id=item.message_id,
        thread_id=item.thread_id,
        sender=item.sender,
        sent_at=item.sent_at,
        subject=item.subject,
        sentences=item.sentences,
        url=item.url,
    )


def _focus(emails: tuple[BriefEmail, ...]) -> tuple[FocusRef, ...]:
    ranked: list[tuple[int, int, str]] = []
    for email_index, email in enumerate(emails):
        for sentence_index, sentence in enumerate(email.sentences):
            rank = _sentence_rank(sentence)
            if rank < 3:
                ranked.append((rank, email_index * 100 + sentence_index, email.message_id))
    ranked.sort()
    refs: list[FocusRef] = []
    seen: set[tuple[str, int]] = set()
    for _rank, packed, message_id in ranked:
        sentence_index = packed % 100
        key = (message_id, sentence_index)
        if key in seen:
            continue
        seen.add(key)
        refs.append(FocusRef(message_id, sentence_index))
        if len(refs) == 4:
            break
    return tuple(refs)


def _sentence_rank(sentence: str) -> int:
    if "？" in sentence or "?" in sentence:
        return 0
    folded = sentence.casefold()
    if any(word in folded for word in _DEADLINE):
        return 1
    if any(word in sentence for word in _ASK):
        return 2
    return 3


def _meeting_wire(meeting: MeetingFace) -> dict:
    return {
        "title": meeting.title,
        "start_at": meeting.start_at,
        "end_at": meeting.end_at,
        "all_day": meeting.all_day,
        "timezone": meeting.timezone,
        "attendees": [invitee_wire(person) for person in meeting.invitees],
    }


def _email_wire(email: BriefEmail) -> dict:
    return {
        "message_id": email.message_id,
        "thread_id": email.thread_id,
        "sender": email.sender,
        "sent_at": email.sent_at,
        "subject": email.subject,
        "sentences": list(email.sentences),
        "url": email.url,
    }


def _row_start(row) -> datetime | None:
    start = getattr(row, "start_at", None)
    return start if isinstance(start, datetime) else None


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()
