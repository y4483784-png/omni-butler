"""Local calendar provider. The only module that queries ``calendar_events`` for the service."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.models import CalendarEvent
from app.services.calendar_provider import CalendarEventDTO, dump_invitees, invitee_wire, parse_invitees


def _participants_from_row(raw: str | None) -> list[dict[str, str]]:
    return [invitee_wire(item) for item in parse_invitees(raw)]


def free_gaps(
    busy: list[tuple[datetime | None, datetime | None]],
    start: datetime,
    end: datetime,
    duration: timedelta,
    limit: int,
) -> list[tuple[datetime, datetime]]:
    """Up to ``limit`` gaps of exactly ``duration`` inside ``[start, end)``.

    ``busy`` is walked in the order given (local rows are already ordered by
    start). Overlapping intervals collapse onto the cursor. An event that
    ends when the next one starts does not consume that boundary. This helper
    does not clip to working hours.
    """
    if duration <= timedelta(0) or limit <= 0 or end <= start:
        return []
    slots: list[tuple[datetime, datetime]] = []
    cursor = start
    for ev_start, ev_end in busy:
        ev_start = ev_start or cursor
        ev_end = ev_end or cursor
        if ev_start > cursor and ev_start - cursor >= duration:
            slots.append((cursor, cursor + duration))
            if len(slots) >= limit:
                return slots
        if ev_end > cursor:
            cursor = ev_end
    if end - cursor >= duration and len(slots) < limit:
        slots.append((cursor, cursor + duration))
    return slots


def event_to_dto(event: CalendarEvent) -> CalendarEventDTO:
    return CalendarEventDTO(
        id=event.id,
        user_id=event.user_id,
        title=event.title or "",
        start_at=event.start_at,
        end_at=event.end_at,
        participants=_participants_from_row(event.participants),
        status=event.status or "",
        timezone=event.timezone or "",
        location=event.location or "",
        description=event.description or "",
        external_id=event.external_id or "",
        provider=event.provider or "local",
    )


class LocalCalendarProvider:
    """Stores events on ``calendar_events``. Times stay naive wall-clock values."""

    def __init__(self, db: Session):
        self.db = db

    def get_timezone(self):
        # Process-local zone. Unconnected users still use this clock.
        # A connected account uses GoogleCalendarProvider.get_timezone instead.
        current = datetime.now().astimezone()
        assert current.tzinfo is not None
        return current.tzinfo

    def overlap_rows(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_event_id: int | None = None,
        status: str = "active",
    ) -> list[CalendarEvent]:
        query = self.db.query(CalendarEvent).filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_at < end,
            CalendarEvent.end_at > start,
        )
        if status and status != "all":
            query = query.filter(CalendarEvent.status == status)
        if exclude_event_id is not None:
            query = query.filter(CalendarEvent.id != exclude_event_id)
        return query.order_by(CalendarEvent.start_at.asc()).all()

    def list_events(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        status: str = "active",
    ) -> list[CalendarEventDTO]:
        return [event_to_dto(row) for row in self.overlap_rows(user_id, start, end, status=status)]

    def rows_for_external_ids(
        self,
        user_id: int,
        external_ids: list[str],
        *,
        status: str = "active",
    ) -> list[CalendarEvent]:
        """Active rows whose non-empty ``external_id`` is in ``external_ids``."""
        ids = [item for item in external_ids if item]
        if not ids:
            return []
        query = self.db.query(CalendarEvent).filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.external_id.in_(ids),
        )
        if status and status != "all":
            query = query.filter(CalendarEvent.status == status)
        return query.all()

    def get_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        event = self.db.get(CalendarEvent, event_id)
        if not event or event.user_id != user_id:
            return None
        return event_to_dto(event)

    def get_row(self, event_id: int) -> CalendarEvent | None:
        """Unscoped lookup used by the legacy service functions, which have no user id."""
        return self.db.get(CalendarEvent, event_id)

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
        del meet
        event = CalendarEvent(
            user_id=user_id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=dump_invitees(participants),
            status="active",
            timezone="",
            location=location or "",
            description=description or "",
            external_id="",
            provider="local",
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event_to_dto(event)

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
        del meet
        event = self.db.get(CalendarEvent, event_id)
        if not event or event.user_id != user_id:
            return None
        if title is not None:
            event.title = title
        if start_at is not None:
            event.start_at = start_at
        if end_at is not None:
            event.end_at = end_at
        if participants is not None:
            event.participants = dump_invitees(participants)
        if location is not None:
            event.location = location
        if description is not None:
            event.description = description
        self.db.commit()
        self.db.refresh(event)
        return event_to_dto(event)

    def cancel_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        event = self.db.get(CalendarEvent, event_id)
        if not event or event.user_id != user_id:
            return None
        event.status = "cancelled"
        self.db.commit()
        self.db.refresh(event)
        return event_to_dto(event)

    def find_free_slots(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        duration: timedelta,
        limit: int = 3,
        exclude_event_id: int | None = None,
    ) -> list[tuple[datetime, datetime]]:
        # Gaps only. Working hours are not applied here.
        busy = [
            (event.start_at, event.end_at)
            for event in self.overlap_rows(
                user_id, start, end, exclude_event_id=exclude_event_id
            )
        ]
        return free_gaps(busy, start, end, duration, limit)
