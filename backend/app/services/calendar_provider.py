"""Calendar provider interface. Implementations must not be imported here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Protocol


@dataclass
class CalendarEventDTO:
    id: int
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


class CalendarProvider(Protocol):
    """Read and write one user's calendar.

    ``get_timezone`` on the local implementation is only the process-local
    zone, matching today's ``datetime.now()``. It is not a product default.
    A later external provider replaces it with the account timezone.

    ``find_free_slots`` does not apply working hours. An external provider
    may later intersect these gaps with that calendar's own working hours.
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
    ) -> CalendarEventDTO:
        """Insert an active event and return it."""
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
        starts) do not consume the boundary. No working-hour filter.
        """
        ...
