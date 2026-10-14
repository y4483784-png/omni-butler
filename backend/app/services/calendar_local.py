"""Local calendar provider. The only module that queries ``calendar_events`` for the service."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.models import CalendarEvent
from app.services.calendar_provider import CalendarEventDTO


def _participants_from_row(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


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
        # Process-local zone, same clock ``datetime.now()`` already uses.
        # Slot extraction must keep calling ``datetime.now()`` until a later slice.
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
    ) -> list[CalendarEvent]:
        query = self.db.query(CalendarEvent).filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.status == "active",
            CalendarEvent.start_at < end,
            CalendarEvent.end_at > start,
        )
        if exclude_event_id is not None:
            query = query.filter(CalendarEvent.id != exclude_event_id)
        return query.order_by(CalendarEvent.start_at.asc()).all()

    def list_events(self, user_id: int, start: datetime, end: datetime) -> list[CalendarEventDTO]:
        return [event_to_dto(row) for row in self.overlap_rows(user_id, start, end)]

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
    ) -> CalendarEventDTO:
        event = CalendarEvent(
            user_id=user_id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=json.dumps(participants, ensure_ascii=False),
            status="active",
            timezone="",
            location="",
            description="",
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
    ) -> CalendarEventDTO | None:
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
            event.participants = json.dumps(participants, ensure_ascii=False)
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
        # Gaps only. Working hours belong to a future external calendar, not here.
        if duration <= timedelta(0) or limit <= 0 or end <= start:
            return []
        busy = self.overlap_rows(user_id, start, end, exclude_event_id=exclude_event_id)
        slots: list[tuple[datetime, datetime]] = []
        cursor = start
        for event in busy:
            ev_start = event.start_at or cursor
            ev_end = event.end_at or cursor
            if ev_start > cursor and ev_start - cursor >= duration:
                slots.append((cursor, cursor + duration))
                if len(slots) >= limit:
                    return slots
            if ev_end > cursor:
                cursor = ev_end
        if end - cursor >= duration and len(slots) < limit:
            slots.append((cursor, cursor + duration))
        return slots
