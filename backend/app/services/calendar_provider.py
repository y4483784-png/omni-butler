"""Calendar provider interface. Implementations must not be imported here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Protocol

_NAMED_EMAIL = re.compile(r"^(.+?)\s*<([^<>]+)>\s*$")


@dataclass(frozen=True)
class Invitee:
    """One guest. An email is invited. A bare name stays on the local row."""

    email: str
    name: str

    def __post_init__(self) -> None:
        email = self.email.strip()
        name = self.name.strip()
        if email and "@" not in email:
            raise ValueError("invitee email needs @")
        if not email and not name:
            raise ValueError("invitee needs an email or a name")
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "name", name)


def parse_invitee(value: object) -> Invitee | None:
    """A stored object, a `Name <email>` string, or a legacy string. `@` marks an email."""
    email = ""
    name = ""
    if isinstance(value, Invitee):
        email = value.email.strip()
        name = value.name.strip()
    elif isinstance(value, dict):
        email = str(value.get("email") or "").strip()
        name = str(value.get("name") or "").strip()
    else:
        text = str(value or "").strip()
        if text and text != "None":
            named = _NAMED_EMAIL.fullmatch(text)
            if named and "@" in named.group(2):
                name = named.group(1).strip()
                email = named.group(2).strip()
            elif "@" in text:
                email = text
            else:
                name = text
    if email and "@" not in email:
        name = name or email
        email = ""
    if not email and not name:
        return None
    return Invitee(email=email, name=name)


def parse_invitees(values: object) -> list[Invitee]:
    if isinstance(values, str):
        try:
            values = json.loads(values or "[]")
        except json.JSONDecodeError:
            return []
    if not isinstance(values, list):
        return []
    invitees: list[Invitee] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        invitee = parse_invitee(item)
        if invitee is None:
            continue
        key = (invitee.email.casefold(), invitee.name)
        if key in seen:
            continue
        seen.add(key)
        invitees.append(invitee)
    return invitees


def invitee_label(invitee: Invitee) -> str:
    return invitee.email or invitee.name


def invitee_wire(invitee: Invitee) -> dict[str, str]:
    """The card and HTTP shape. Both keys are always present."""
    return {"email": invitee.email, "name": invitee.name}


def invitee_labels(values: object) -> list[str]:
    return [invitee_label(item) for item in parse_invitees(values)]


def dump_invitees(values: object) -> str:
    """The only stored shape. Each item is an email and a name."""
    payload = [invitee_wire(item) for item in parse_invitees(values)]
    return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class CalendarAttachment:
    file_id: str
    title: str
    mime_type: str
    file_url: str


def attendee_body(values: object) -> list[dict[str, str]]:
    """Emails only. A name travels as displayName. No responseStatus, so Google treats them as needsAction.

    A name-only person is omitted. Google has no address to invite.
    """
    body: list[dict[str, str]] = []
    for item in parse_invitees(values):
        if not item.email:
            continue
        entry = {"email": item.email}
        if item.name:
            entry["displayName"] = item.name
        body.append(entry)
    return body


@dataclass
class CalendarEventDTO:
    id: int | None
    user_id: int
    title: str
    start_at: datetime | None
    end_at: datetime | None
    participants: list[str] = field(default_factory=list)
    status: str = "active"
    timezone: str = ""
    location: str = ""
    description: str = ""
    external_id: str = ""
    provider: str = "local"
    # Google-only list rows set these. Local rows leave them false.
    readonly: bool = False
    all_day: bool = False
    # Set when the signed-in user is an attendee and not the organizer.
    rsvp: str = ""
    # Filled from a Google response. Not a database column.
    meet_link: str = ""
    meet_status: str = ""
    attachment_ids: tuple[str, ...] = ()
    attachments: tuple[CalendarAttachment, ...] = ()
    meeting_code: str = ""


class CalendarProvider(Protocol):
    """Read and write one user's calendar.

    ``get_timezone`` on the local implementation is only the process-local
    zone, matching today's ``datetime.now()``. It is not a product default.
    A later external provider replaces it with the account timezone.

    ``find_free_slots`` on the local calendar does not apply working hours.
    Google intersects gaps only when settings actually include them.
    """

    def get_timezone(self) -> tzinfo:
        """Zone used for this calendar's wall clock."""
        ...

    def list_events(self, user_id: int, start: datetime, end: datetime) -> list[CalendarEventDTO]:
        """Active events overlapping ``[start, end)``."""
        ...

    def get_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        """One event owned by ``user_id``, or None."""
        ...

    def create_event(
        self,
        user_id: int,
        *,
        title: str,
        start_at: datetime,
        end_at: datetime,
        participants: list[str],
        location: str = "",
        description: str = "",
        meet: bool = False,
    ) -> CalendarEventDTO:
        """Insert an active event and return it.

        ``location`` and ``description`` match the local provider. A participant
        string that contains ``@`` is an email and is invited. Any other string
        is a name and stays on the local row. A time block passes an empty list.
        """
        ...

    def update_event(
        self,
        user_id: int,
        event_id: int,
        *,
        title: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        participants: list[str] | None = None,
        location: str | None = None,
        description: str | None = None,
        meet: bool = False,
    ) -> CalendarEventDTO | None:
        """Patch an owned event. None when it is missing or owned by someone else."""
        ...

    def cancel_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        """Soft-cancel an owned event. None when it is missing or not owned."""
        ...

    def find_free_slots(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        duration: timedelta,
        limit: int = 3,
        exclude_event_id: int | None = None,
    ) -> list[tuple[datetime, datetime]]:
        """Up to ``limit`` gaps of at least ``duration`` inside ``[start, end)``.

        Each returned pair is exactly ``duration`` long and starts at the
        beginning of a free gap. Adjacent events (one ends when the next
        starts) do not consume the boundary. Local gaps are not clipped to
        working hours. Google clips only when that account exposes them.
        """
        ...
