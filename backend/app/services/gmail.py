"""Read-only Gmail search for one meeting. Wire JSON stays in this module."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.services.calendar_secrets import (
    GOOGLE_MAIL_SCOPES,
    CalendarTokenError,
    CalendarTransientError,
    scoped_access,
)

_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_GENERIC_TERMS = frozenset(
    {
        "会议",
        "例会",
        "周会",
        "月会",
        "沟通",
        "讨论",
        "同步",
        "电话",
        "视频会",
        "meeting",
        "sync",
        "call",
        "weekly",
        "1:1",
        "1on1",
    }
)
_THREAD_ID = re.compile(r"[A-Za-z0-9]+")
_FROM = re.compile(r"\s*(.*?)\s*<([^>]+)>")
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}$"
)
_ANGLE_RE = re.compile(r"""^\s*(?:"([^"]*)"|([^<"]*?))?\s*<([^<>"]+)>\s*([^<>]*)$""")
_FIELDS = ("from", "to", "cc")
_ATTENDEE_CAP = 10


class _Rejected(Exception):
    pass


class _Down(Exception):
    pass


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    thread_id: str
    sender: str
    sent_at: str
    subject: str
    sentences: tuple[str, ...]
    url: str


@dataclass(frozen=True)
class NoGrant:
    why: Literal["not_connected", "scope_missing", "rejected"]


@dataclass(frozen=True)
class MailDown:
    pass


def search_meeting_mail(
    db: Session,
    user_id: int,
    *,
    title: str,
    attendees: tuple[str, ...],
    start_day: date,
) -> tuple[MailMessage, ...] | NoGrant | MailDown:
    """Up to five threads. An empty tuple means Gmail answered and nothing matched."""
    try:
        access = scoped_access(db, user_id, GOOGLE_MAIL_SCOPES[0])
    except CalendarTokenError:
        return NoGrant("rejected")
    except CalendarTransientError:
        return MailDown()
    if access == "not_connected":
        return NoGrant("not_connected")
    if access == "scope_missing":
        return NoGrant("scope_missing")
    queries = _queries(attendees, title, 120, start_day)
    if not queries:
        return ()
    try:
        kept = _collect(access, queries)
        if len(kept) < 3:
            wider = _collect(access, _queries(attendees, title, 365, start_day))
            kept = _merge(kept, wider)[:5]
        return tuple(_read(access, kept, access.account_email))
    except _Rejected:
        return NoGrant("rejected")
    except _Down:
        return MailDown()


def _topic_terms(title: str) -> tuple[str, ...]:
    parts = re.split(r"[\s,，。、；;:：/|+]+", (title or "").strip())
    kept: list[str] = []
    for part in parts:
        token = part.strip()
        if not token or token.casefold() in _GENERIC_TERMS:
            continue
        if re.fullmatch(r"\d{1,4}([:：]\d{2})?", token):
            continue
        kept.append(token)
    return tuple(kept)


@dataclass(frozen=True)
class _Email:
    value: str

    def __post_init__(self) -> None:
        if _EMAIL_RE.fullmatch(self.value) is None:
            raise ValueError(self.value)


@dataclass(frozen=True)
class _Name:
    value: str

    def __post_init__(self) -> None:
        if not _name_ok(self.value):
            raise ValueError(self.value)


def _name_ok(value: str) -> bool:
    return len(value) >= 2 and not any(mark in value for mark in '@<>"\\')


def _email_or_none(value: str) -> _Email | None:
    text = value.strip()
    if _EMAIL_RE.fullmatch(text) is None:
        return None
    return _Email(text)


def _name_or_none(value: str) -> _Name | None:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    if not _name_ok(text):
        return None
    return _Name(text)


def _parse_attendee(raw: str) -> tuple[_Email | None, _Name | None]:
    text = str(raw or "").strip()
    if not text:
        return None, None
    match = _ANGLE_RE.fullmatch(text)
    if match:
        raw_name = match.group(1) if match.group(1) is not None else (match.group(2) or "")
        return _email_or_none(match.group(3)), _name_or_none(raw_name)
    if "<" in text or ">" in text:
        return None, None
    email = _email_or_none(text)
    if email is not None:
        return email, None
    return None, _name_or_none(text)


def _signals(attendees: tuple[str, ...]) -> tuple[tuple[_Email, ...], tuple[_Name, ...]]:
    emails: list[_Email] = []
    names: list[_Name] = []
    seen_emails: set[str] = set()
    seen_names: set[str] = set()
    added = 0
    for raw in attendees:
        email, name = _parse_attendee(raw)
        contributed = False
        if email is not None and email.value.casefold() not in seen_emails:
            seen_emails.add(email.value.casefold())
            emails.append(email)
            contributed = True
        if name is not None and name.value.casefold() not in seen_names:
            seen_names.add(name.value.casefold())
            names.append(name)
            contributed = True
        if not contributed:
            continue
        added += 1
        if added == _ATTENDEE_CAP:
            break
    return tuple(emails), tuple(names)


def _queries(attendees: tuple[str, ...], title: str, days: int, start_day: date) -> tuple[str, ...]:
    emails, names = _signals(attendees)
    terms = tuple(term for term in _topic_terms(title) if "<" not in term and ">" not in term)
    atoms: list[str] = []
    for email in emails:
        atoms.extend(f"{field}:{email.value}" for field in _FIELDS)
    for name in names:
        atoms.extend(f'{field}:"{name.value}"' for field in _FIELDS)
    atoms.extend(f'"{term}"' for term in terms)
    if not atoms:
        return ()
    before = (start_day + timedelta(days=1)).strftime("%Y/%m/%d")
    suffix = f"newer_than:{days}d before:{before} -filename:ics -in:spam -in:trash -in:drafts -in:chats"
    return (f"({' OR '.join(atoms)}) {suffix}",)


def _status(response) -> None:
    if response.status_code in (401, 403):
        raise _Rejected()
    if response.status_code != 200:
        raise _Down()


def _collect(access, queries: tuple[str, ...]) -> list[tuple[str, str]]:
    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    for query in queries:
        response = access.get(
            _MESSAGES_URL,
            params={"q": query, "maxResults": 10},
        )
        _status(response)
        body = response.json() if response.content else {}
        for item in (body or {}).get("messages") or []:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("id") or "").strip()
            thread_id = str(item.get("threadId") or message_id).strip()
            if not message_id or not _THREAD_ID.fullmatch(thread_id) or thread_id in seen:
                continue
            seen.add(thread_id)
            kept.append((message_id, thread_id))
            if len(kept) == 5:
                return kept
    return kept


def _merge(first: list[tuple[str, str]], second: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen = {thread_id for _message_id, thread_id in first}
    merged = list(first)
    for item in second:
        if item[1] in seen:
            continue
        seen.add(item[1])
        merged.append(item)
    return merged


def _read(access, kept: list[tuple[str, str]], account_email: str) -> list[MailMessage]:
    messages: list[MailMessage] = []
    for message_id, thread_id in kept:
        response = access.get(f"{_MESSAGES_URL}/{message_id}", params={"format": "full"})
        if response.status_code == 404:
            continue
        _status(response)
        parsed = _parse(response.json() if response.content else {}, account_email, message_id, thread_id)
        if parsed is not None:
            messages.append(parsed)
    return messages


def _parse(payload: dict, account_email: str, message_id: str, thread_id: str) -> MailMessage | None:
    if not isinstance(payload, dict):
        return None
    part = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    subject = _header(part, "Subject") or "(无主题)"
    sender = _sender(_header(part, "From"))
    body = _plain(part)
    snippet = str(payload.get("snippet") or "")
    sentences = _sentences(body, snippet, subject)
    if not sentences:
        return None
    return MailMessage(
        message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        sent_at=_sent_at(payload),
        subject=subject,
        sentences=sentences,
        url=f"https://mail.google.com/mail/?authuser={quote(account_email)}#all/{thread_id}",
    )


def _header(part: dict, name: str) -> str:
    for item in part.get("headers") or []:
        if isinstance(item, dict) and str(item.get("name") or "").lower() == name.lower():
            return str(item.get("value") or "").strip()
    return ""


def _sender(value: str) -> str:
    match = _FROM.match(value or "")
    if match and match.group(1).strip():
        return match.group(1).strip().strip('"')
    return (value or "").strip() or "未知发件人"


def _plain(part: dict) -> str:
    chunks: list[str] = []
    mime = str(part.get("mimeType") or "")
    data = str((part.get("body") or {}).get("data") or "")
    if mime == "text/plain" and data:
        chunks.append(_decode(data))
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            text = _plain(child)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")
    except (ValueError, UnicodeError):
        return ""


def _sentences(body: str, snippet: str, subject: str) -> tuple[str, ...]:
    source = "\n".join(
        line.strip() for line in (body or "").splitlines() if line.strip() and not line.strip().startswith(">")
    ).strip()
    if not source:
        source = (snippet or "").strip() or (subject or "").strip()
    if not source:
        return ()
    parts = re.split(r"(?<=[。！？!?])\s*", source)
    kept: list[str] = []
    for part in parts:
        text = part.strip()
        if not text or text not in source:
            continue
        kept.append(text)
        if len(kept) == 8:
            break
    if not kept:
        kept.append(source[:500])
    return tuple(kept)


def _sent_at(payload: dict) -> str:
    raw = str(payload.get("internalDate") or "").strip()
    try:
        stamp = datetime.fromtimestamp(int(raw) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return stamp.strftime("%Y-%m-%dT%H:%M:%S")
