"""Google Calendar provider.

``get_timezone`` reads the account IANA zone. Connected creates list the
primary calendar, then insert on Google before the local row. Update, cancel,
and list display still use local rows until a later slice.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.orm import Session

from app.models.models import CalendarConnection, CalendarEvent
from app.services import calendar_secrets
from app.services.calendar_local import LocalCalendarProvider, event_to_dto, free_gaps
from app.services.calendar_provider import CalendarEventDTO
from app.services.calendar_secrets import CalendarTokenError, refresh_access_token

logger = logging.getLogger(__name__)

_TIMEZONE_URL = "https://www.googleapis.com/calendar/v3/users/me/settings/timezone"
_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_MISSING_TIMEZONE = "Etc/GMT"
_MAX_EVENT_PAGES = 5
_GOOGLE_ONLY_TITLE = "Google 日历上已有安排"
_READ_FAILED = "暂时读不到 Google 日历，未写入。"
_WRITE_FAILED = "暂时无法写入 Google 日历，未保存。"
_LOCAL_SAVE_FAILED = "本地保存失败，未完成创建。"


class CalendarRemoteError(Exception):
    """A Google calendar call failed. Confirm must not report success."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CalendarRemoteReadError(CalendarRemoteError):
    def __init__(self, message: str = _READ_FAILED) -> None:
        super().__init__(message)


class CalendarRemoteWriteError(CalendarRemoteError):
    def __init__(self, message: str = _WRITE_FAILED) -> None:
        super().__init__(message)


class CalendarOverlapError(Exception):
    """Proposed time overlaps local rows or busy Google intervals. Nothing was written."""

    def __init__(self, conflicts: list) -> None:
        super().__init__("calendar conflict")
        self.conflicts = conflicts


@dataclass
class GoogleOnlyConflict:
    """Busy time that exists only on Google. Not a local event and not choosable."""

    start_at: datetime
    end_at: datetime
    id: int | None = None
    user_id: int = 0
    title: str = _GOOGLE_ONLY_TITLE
    participants: list[str] = field(default_factory=list)
    status: str = "active"
    timezone: str = ""
    location: str = ""
    description: str = ""
    external_id: str = ""
    provider: str = "google"


def _http_request(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    json: dict | None = None,
) -> httpx.Response:
    return calendar_secrets._http_request(
        method, url, data=data, params=params, headers=headers, json=json
    )


class GoogleCalendarProvider:
    """Account timezone and connected creates are live. Other writes stay local."""

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
        # Merging the primary calendar into list and sidebar is a later slice.
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
        """List primary, refuse overlaps, insert on Google, then store the local row.

        Wall-clock values are stored as given. They are not converted to UTC.
        Participants stay on the local row and are not sent as attendees.
        """
        start_at = _as_naive(start_at)
        end_at = _as_naive(end_at)
        zone_name = self.timezone_name()
        conflicts = self.conflicts_for_create(user_id, start_at, end_at, zone_name=zone_name)
        if conflicts:
            raise CalendarOverlapError(conflicts)
        google_id = self._insert_google_event(
            title=title,
            start_at=start_at,
            end_at=end_at,
            location=location,
            description=description,
            zone_name=zone_name,
        )
        return self._save_local_row(
            user_id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            location=location,
            description=description,
            zone_name=zone_name,
            google_id=google_id,
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
        # Google patch / adopt-on-edit is a later slice.
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
        # Google delete-on-cancel is a later slice.
        return self._local.cancel_event(user_id, event_id)

    def conflicts_for_create(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        zone_name: str | None = None,
    ) -> list:
        """Local active overlaps merged with busy primary-calendar intervals.

        Raises ``CalendarRemoteReadError`` when ``events.list`` fails. A failure
        is not treated as an empty calendar.
        """
        zone = zone_name if zone_name is not None else self.timezone_name()
        _intervals, conflicts = self._read_window(user_id, start, end, zone)
        return conflicts

    def find_free_slots(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        duration: timedelta,
        limit: int = 3,
        exclude_event_id: int | None = None,
    ) -> list[tuple[datetime, datetime]]:
        """Gaps in the union of local rows and busy ``events.list`` intervals.

        Calendar API v3 settings expose the account timezone but not weekly
        working hours (Issue Tracker 231620767). ``workingLocation`` events
        say where someone is, not which hours are open, so they are not busy
        and are not a schedule. Until a settings payload actually includes
        working hours, the whole requested window stays available. Nothing
        here is hard-coded to 09:00–18:00.
        """
        if duration <= timedelta(0) or limit <= 0 or end <= start:
            return []
        intervals, _conflicts = self._read_window(
            user_id,
            start,
            end,
            self.timezone_name(),
            exclude_event_id=exclude_event_id,
        )
        return free_gaps(intervals, start, end, duration, limit)

    def overlap_rows(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_event_id: int | None = None,
        status: str = "active",
    ) -> list[CalendarEvent]:
        # Update conflict checks still use local rows. Create uses ``_read_window``.
        return self._local.overlap_rows(
            user_id,
            start,
            end,
            exclude_event_id=exclude_event_id,
            status=status,
        )

    def get_row(self, event_id: int) -> CalendarEvent | None:
        return self._local.get_row(event_id)

    def _read_window(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        zone_name: str,
        *,
        exclude_event_id: int | None = None,
    ) -> tuple[list[tuple[datetime, datetime]], list]:
        items = self._list_primary_events(start, end, zone_name)
        excluded_external = ""
        if exclude_event_id is not None:
            owned = self._local.get_event(user_id, exclude_event_id)
            if owned is not None and owned.external_id:
                excluded_external = owned.external_id

        busy: list[tuple[datetime, datetime, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            gid = str(item.get("id") or "")
            if gid and gid == excluded_external:
                continue
            span = _busy_span(item)
            if span is None or not _overlaps(span[0], span[1], start, end):
                continue
            busy.append((span[0], span[1], gid))

        local_rows = self._local.overlap_rows(
            user_id, start, end, exclude_event_id=exclude_event_id, status="active"
        )
        matched = {
            row.external_id: row
            for row in self._local.rows_for_external_ids(
                user_id, [gid for _start, _end, gid in busy if gid]
            )
            if row.external_id and (exclude_event_id is None or row.id != exclude_event_id)
        }

        intervals: list[tuple[datetime, datetime]] = []
        conflicts: list = []
        seen_local: set[int] = set()
        for g_start, g_end, gid in busy:
            intervals.append((g_start, g_end))
            local = matched.get(gid) if gid else None
            if local is not None:
                if local.id in seen_local:
                    continue
                seen_local.add(local.id)
                conflicts.append(local)
                continue
            conflicts.append(GoogleOnlyConflict(start_at=g_start, end_at=g_end))

        for row in local_rows:
            if row.id in seen_local:
                continue
            # A Google-backed row is busy only via the live interval. A stale
            # local copy whose event is outside this window must not block twice.
            if (row.external_id or "") and (row.provider or "") == "google":
                continue
            if row.start_at is None or row.end_at is None:
                continue
            intervals.append((row.start_at, row.end_at))
            conflicts.append(row)

        intervals.sort(key=lambda pair: pair[0])
        conflicts.sort(key=lambda row: row.start_at or datetime.min)
        return intervals, conflicts

    def _list_primary_events(self, start: datetime, end: datetime, zone_name: str) -> list[dict]:
        """One read of primary ``events.list``. Page tokens are not stored.

        More than ``_MAX_EVENT_PAGES`` is a failed read: a partial window could
        hide a conflict, so confirm must not write.
        """
        zone = ZoneInfo(zone_name)
        query = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "showDeleted": "false",
            "timeMin": _rfc3339(start, zone),
            "timeMax": _rfc3339(end, zone),
            "timeZone": zone_name,
            "maxResults": "250",
        }
        items: list[dict] = []
        page_token = ""
        for _page in range(_MAX_EVENT_PAGES):
            params = dict(query)
            if page_token:
                params["pageToken"] = page_token
            response = self._call("GET", _EVENTS_URL, params=params)
            if response.status_code != 200:
                logger.warning(
                    "google calendar events list failed status=%s", response.status_code
                )
                raise CalendarRemoteReadError()
            body = _json_object(response)
            if body is None:
                logger.warning("google calendar events list failed status=invalid-body")
                raise CalendarRemoteReadError()
            batch = body.get("items") or []
            if not isinstance(batch, list):
                logger.warning("google calendar events list failed status=invalid-body")
                raise CalendarRemoteReadError()
            items.extend(item for item in batch if isinstance(item, dict))
            page_token = str(body.get("nextPageToken") or "")
            if not page_token:
                return items
        logger.warning("google calendar events list failed status=truncated")
        raise CalendarRemoteReadError()

    def _insert_google_event(
        self,
        *,
        title: str,
        start_at: datetime,
        end_at: datetime,
        location: str,
        description: str,
        zone_name: str,
    ) -> str:
        body = {
            "summary": title,
            "location": location or "",
            "description": description or "",
            "start": {"dateTime": _wall_clock(start_at), "timeZone": zone_name},
            "end": {"dateTime": _wall_clock(end_at), "timeZone": zone_name},
        }
        # No attendees and no sendUpdates: participants stay local and no mail goes out.
        try:
            response = self._call("POST", _EVENTS_URL, json=body)
        except CalendarRemoteReadError:
            logger.warning("google calendar events insert failed status=network")
            raise CalendarRemoteWriteError() from None
        if response.status_code not in (200, 201):
            logger.warning(
                "google calendar events insert failed status=%s", response.status_code
            )
            raise CalendarRemoteWriteError()
        payload = _json_object(response) or {}
        google_id = str(payload.get("id") or "").strip()
        if not google_id:
            logger.warning("google calendar events insert failed status=missing-id")
            raise CalendarRemoteWriteError()
        return google_id

    def _save_local_row(
        self,
        user_id: int,
        *,
        title: str,
        start_at: datetime,
        end_at: datetime,
        participants: list[str],
        location: str,
        description: str,
        zone_name: str,
        google_id: str,
    ) -> CalendarEventDTO:
        try:
            if self._external_id_taken(user_id, google_id):
                self._delete_google_event(google_id)
                raise CalendarRemoteWriteError(_LOCAL_SAVE_FAILED)
            event = CalendarEvent(
                user_id=user_id,
                title=title,
                start_at=start_at,
                end_at=end_at,
                participants=json.dumps(list(participants), ensure_ascii=False),
                status="active",
                timezone=zone_name,
                location=location or "",
                description=description or "",
                external_id=google_id,
                provider="google",
            )
            self.db.add(event)
            self.db.commit()
        except CalendarRemoteWriteError:
            raise
        except Exception:
            self.db.rollback()
            self._delete_google_event(google_id)
            raise CalendarRemoteWriteError(_LOCAL_SAVE_FAILED) from None
        self.db.refresh(event)
        return event_to_dto(event)

    def _external_id_taken(self, user_id: int, google_id: str) -> bool:
        if not google_id:
            return False
        return (
            self._local.rows_for_external_ids(user_id, [google_id], status="all") != []
        )

    def _delete_google_event(self, event_id: str) -> None:
        """Best-effort compensate. On failure the log keeps only the event id."""
        if not event_id:
            return
        url = f"{_EVENTS_URL}/{quote(event_id, safe='')}"
        try:
            response = self._call("DELETE", url)
        except CalendarRemoteError:
            logger.error("google calendar compensate delete failed event_id=%s", event_id)
            return
        if response.status_code not in (200, 204, 404, 410):
            logger.error("google calendar compensate delete failed event_id=%s", event_id)

    def _call(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        access = self._access_token()
        request = self._http_request or _http_request
        try:
            return request(
                method,
                url,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {access}"},
            )
        except httpx.HTTPError:
            logger.warning("google calendar http failed status=network")
            raise CalendarRemoteReadError() from None

    def _access_token(self) -> str:
        connection = (
            self.db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == self.user_id)
            .one_or_none()
        )
        if connection is None:
            raise CalendarTokenError()
        return refresh_access_token(self.db, connection)

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
    body = _json_object(response)
    if not body:
        return ""
    return str(body.get("value") or "").strip()


def _json_object(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _is_iana_zone(name: str) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


def _as_naive(value: datetime) -> datetime:
    """Keep the clock digits. Do not convert an aware value into another zone."""
    if value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def _wall_clock(value: datetime) -> str:
    return _as_naive(value).isoformat(timespec="seconds")


def _rfc3339(wall: datetime, zone: ZoneInfo) -> str:
    """Account-zone offset on the same wall clock. Not a conversion to UTC."""
    aware = _as_naive(wall).replace(tzinfo=zone)
    return aware.isoformat(timespec="seconds")


def _overlaps(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> bool:
    return start < window_end and end > window_start


def _busy_span(item: dict) -> tuple[datetime, datetime] | None:
    """Busy interval in wall-clock time, or None when the item does not block."""
    if str(item.get("status") or "") == "cancelled":
        return None
    if str(item.get("transparency") or "") == "transparent":
        return None
    if str(item.get("eventType") or "") == "workingLocation":
        return None
    if _self_declined(item):
        return None
    start_info = item.get("start") if isinstance(item.get("start"), dict) else {}
    end_info = item.get("end") if isinstance(item.get("end"), dict) else {}
    if start_info.get("date") and end_info.get("date"):
        # end.date is exclusive. The whole local day is busy; we do not create all-day events.
        start = _date_wall(str(start_info.get("date") or ""))
        end = _date_wall(str(end_info.get("date") or ""))
    else:
        start = _wall_from_google(str(start_info.get("dateTime") or ""))
        end = _wall_from_google(str(end_info.get("dateTime") or ""))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _self_declined(item: dict) -> bool:
    attendees = item.get("attendees")
    if not isinstance(attendees, list):
        return False
    for person in attendees:
        if not isinstance(person, dict):
            continue
        if person.get("self") and str(person.get("responseStatus") or "") == "declined":
            return True
    return False


def _wall_from_google(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _date_wall(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
