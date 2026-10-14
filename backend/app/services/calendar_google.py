"""Google Calendar provider.

``get_timezone`` reads the account IANA zone from the Calendar settings API.
List, get, create, update, cancel, and free-slot reads and writes still go
through ``LocalCalendarProvider`` until a later slice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.orm import Session

from app.models.models import CalendarConnection, CalendarEvent
from app.services import calendar_secrets
from app.services.calendar_local import LocalCalendarProvider
from app.services.calendar_provider import CalendarEventDTO
from app.services.calendar_secrets import CalendarTokenError, refresh_access_token

logger = logging.getLogger(__name__)

_TIMEZONE_URL = "https://www.googleapis.com/calendar/v3/users/me/settings/timezone"
_MISSING_TIMEZONE = "Etc/GMT"


def _http_request(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    return calendar_secrets._http_request(
        method, url, data=data, params=params, headers=headers
    )


class GoogleCalendarProvider:
    """Account timezone is live. Other methods still delegate to local rows."""

    def __init__(
        self,
        db: Session,
        user_id: int,
        *,
        http_request: Callable[..., httpx.Response] | None = None,
    ):
        self.db = db
        self.user_id = user_id
        self._local = LocalCalendarProvider(db)
        self._http_request = http_request

    def timezone_name(self) -> str:
        """IANA id from the settings API. Missing or empty becomes ``Etc/GMT``."""
        name = self._fetch_timezone_value()
        if not _is_iana_zone(name):
            logger.warning("google calendar timezone lookup failed status=invalid-zone")
            return _MISSING_TIMEZONE
        return name

    def get_timezone(self) -> tzinfo:
        return ZoneInfo(self.timezone_name())

    def list_events(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        status: str = "active",
    ) -> list[CalendarEventDTO]:
        return self._local.list_events(user_id, start, end, status=status)

    def get_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        return self._local.get_event(user_id, event_id)

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
    ) -> CalendarEventDTO:
        return self._local.create_event(
            user_id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            location=location,
            description=description,
        )

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
    ) -> CalendarEventDTO | None:
        return self._local.update_event(
            user_id,
            event_id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            location=location,
            description=description,
        )

    def cancel_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        return self._local.cancel_event(user_id, event_id)

    def find_free_slots(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        duration: timedelta,
        limit: int = 3,
        exclude_event_id: int | None = None,
    ) -> list[tuple[datetime, datetime]]:
        return self._local.find_free_slots(
            user_id,
            start,
            end,
            duration,
            limit=limit,
            exclude_event_id=exclude_event_id,
        )

    def overlap_rows(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_event_id: int | None = None,
        status: str = "active",
    ) -> list[CalendarEvent]:
        return self._local.overlap_rows(
            user_id,
            start,
            end,
            exclude_event_id=exclude_event_id,
            status=status,
        )

    def get_row(self, event_id: int) -> CalendarEvent | None:
        return self._local.get_row(event_id)

    def _fetch_timezone_value(self) -> str:
        connection = (
            self.db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == self.user_id)
            .one_or_none()
        )
        if connection is None:
            return _MISSING_TIMEZONE
        access = refresh_access_token(self.db, connection)
        request = self._http_request or _http_request
        try:
            response = request(
                "GET",
                _TIMEZONE_URL,
                headers={"Authorization": f"Bearer {access}"},
            )
        except httpx.HTTPError:
            logger.warning("google calendar timezone lookup failed status=network")
            raise CalendarTokenError() from None
        if response.status_code == 404:
            return _MISSING_TIMEZONE
        if response.status_code != 200:
            logger.warning(
                "google calendar timezone lookup failed status=%s", response.status_code
            )
            raise CalendarTokenError()
        value = _setting_value(response)
        if not value:
            return _MISSING_TIMEZONE
        return value


def _setting_value(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    return str(body.get("value") or "").strip()


def _is_iana_zone(name: str) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True
