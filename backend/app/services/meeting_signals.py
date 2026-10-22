"""Shared meeting facts for Gmail and Drive. Drive does not keep its own generic-title list."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.services.calendar_provider import Invitee

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
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}$"
)
_ANGLE_RE = re.compile(r"""^\s*(?:"([^"]*)"|([^<"]*?))?\s*<([^<>"]+)>\s*([^<>]*)$""")
_ATTENDEE_CAP = 10


@dataclass(frozen=True)
class MeetingSignals:
    title: str
    invitees: tuple[Invitee, ...]
    attachment_ids: tuple[str, ...]
    start_day: date

    def topic_terms(self) -> tuple[str, ...]:
        parts = re.split(r"[\s,，。、；;:：/|+]+", (self.title or "").strip())
        kept: list[str] = []
        for part in parts:
            token = part.strip()
            if not token or token.casefold() in _GENERIC_TERMS:
                continue
            if re.fullmatch(r"\d{1,4}([:：]\d{2})?", token):
                continue
            kept.append(token)
        return tuple(kept)


def _name_ok(value: str) -> bool:
    return len(value) >= 2 and not any(mark in value for mark in '@<>"\\')


def _email_ok(value: str) -> bool:
    return _EMAIL_RE.fullmatch(value.strip()) is not None


def invitees_from_text(values: tuple[str, ...] | list[str]) -> tuple[Invitee, ...]:
    """Legacy address strings. A tail after `>` still keeps the name and the email."""
    people: list[Invitee] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        match = _ANGLE_RE.fullmatch(text)
        if match:
            raw_name = match.group(1) if match.group(1) is not None else (match.group(2) or "")
            email = match.group(3).strip()
            name = raw_name.strip()
            if len(name) >= 2 and name[0] == name[-1] == '"':
                name = name[1:-1].strip()
            if not _email_ok(email):
                email = ""
            if not _name_ok(name):
                name = ""
            if email or name:
                people.append(Invitee(email=email, name=name))
            continue
        if "<" in text or ">" in text:
            continue
        if _email_ok(text):
            people.append(Invitee(email=text, name=""))
        elif _name_ok(text):
            people.append(Invitee(email="", name=text))
    return tuple(people)


def capped_mail_people(invitees: tuple[Invitee, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """At most ten people. Each person can contribute an email atom and a name atom."""
    emails: list[str] = []
    names: list[str] = []
    seen_emails: set[str] = set()
    seen_names: set[str] = set()
    added = 0
    for person in invitees:
        contributed = False
        email = person.email.strip()
        if _email_ok(email) and email.casefold() not in seen_emails:
            seen_emails.add(email.casefold())
            emails.append(email)
            contributed = True
        name = person.name.strip()
        if _name_ok(name) and name.casefold() not in seen_names:
            seen_names.add(name.casefold())
            names.append(name)
            contributed = True
        if not contributed:
            continue
        added += 1
        if added == _ATTENDEE_CAP:
            break
    return tuple(emails), tuple(names)
