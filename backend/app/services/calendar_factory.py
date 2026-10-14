"""The only module that chooses LocalCalendarProvider or GoogleCalendarProvider."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import CalendarConnection
from app.services.calendar_google import GoogleCalendarProvider
from app.services.calendar_local import LocalCalendarProvider


def calendar_provider_for(db: Session, user_id: int) -> LocalCalendarProvider | GoogleCalendarProvider:
    """Unconnected users stay on local rows. A connection row selects Google."""
    connection = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.user_id == user_id)
        .one_or_none()
    )
    if connection is None:
        return LocalCalendarProvider(db)
    return GoogleCalendarProvider(db, user_id)


def calendar_writes_remote(db: Session, user_id: int) -> bool:
    """True when a confirm must talk to Google before changing the local row."""
    return isinstance(calendar_provider_for(db, user_id), GoogleCalendarProvider)


def account_timezone_name(db: Session, user_id: int) -> str | None:
    """IANA id for a connected account. ``None`` when the user is not connected."""
    provider = calendar_provider_for(db, user_id)
    if isinstance(provider, GoogleCalendarProvider):
        return provider.timezone_name()
    return None
