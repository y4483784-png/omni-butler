"""Google Calendar provider.

``get_timezone`` reads the account IANA zone. Connected creates, updates, and
cancels talk to the primary calendar before the local row changes. List and
free-slot reads use the same primary ``events.list`` and do not write
``calendar_events``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.orm import Session

from app.models.models import CalendarConnection, CalendarEvent
from app.services import calendar_secrets
from app.services.calendar_local import LocalCalendarProvider, event_to_dto, free_gaps
from app.services.calendar_provider import CalendarEventDTO
from app.services.calendar_secrets import (
    CalendarTokenError,
    CalendarTransientError,
    refresh_access_token,
)

logger = logging.getLogger(__name__)

_TIMEZONE_URL = "https://www.googleapis.com/calendar/v3/users/me/settings/timezone"
_SETTINGS_URL = "https://www.googleapis.com/calendar/v3/users/me/settings"
_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_MISSING_TIMEZONE = "Etc/GMT"
# Transport failures and these statuses are retried once. 500 is transient with no retry.
_TIMEZONE_RETRY_STATUSES = frozenset({502, 503, 504})
_MAX_EVENT_PAGES = 5
_LIST_LIMIT = 20
_GOOGLE_ONLY_TITLE = "Google 日历上已有安排"
_UNTITLED = "（无标题）"
_READ_FAILED = "暂时读不到 Google 日历，未写入。"
_LIST_LOCAL_ONLY = "暂时读不到 Google 日历，以下只是这里保存的日程。"
_LIST_TRUNCATED = "这次没有列完。"
# Setting id we will parse if Google ever returns weekly hours. Absent today.
_WORKING_HOURS_IDS = frozenset({"workingHours", "working_hours"})
_WRITE_FAILED = "暂时无法写入 Google 日历，未保存。"
_LOCAL_SAVE_FAILED = "本地保存失败，未完成创建。"
_LOCAL_UPDATE_FAILED = "本地保存失败，未完成修改。"
_CANCEL_FAILED = "暂时无法在 Google 日历上取消，本地日程未改。"
_RECONNECT_REQUIRED = "需要重新连接 Google 日历后才能修改或取消。"
_TITLE_UNMATCHED = "没有在 Google 日历上找到同标题的日程，未写入。"


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


class CalendarTitleUnmatched(Exception):
    """Confirm-time title lookup missed. Nothing was written."""

    def __init__(self, message: str = _TITLE_UNMATCHED) -> None:
        super().__init__(message)
        self.message = message


class CalendarReconnectRequired(Exception):
    """Disconnected write to a row that still has a Google event id. The row stays as stored."""

    def __init__(self, message: str = _RECONNECT_REQUIRED) -> None:
        super().__init__(message)
        self.message = message


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


@dataclass(frozen=True)
class WorkingHoursWindow:
    """One weekday's open interval. Monday is 0, the same as ``datetime.weekday()``.

    Tests inject this. Today's settings response has no working-hours field,
    so a missing field means the whole requested window stays usable.
    """

    weekday: int
    start: time
    end: time


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
    """Account timezone plus primary-calendar reads and writes.

    List display overlays Google onto local rows and does not save that overlay.
    ``working_hours`` is an optional injected schedule. ``None`` reads settings.
    """

    def __init__(
        self,
        db: Session,
        user_id: int,
        *,
        http_request: Callable[..., httpx.Response] | None = None,
        working_hours: list[WorkingHoursWindow] | None = None,
    ):
        self.db = db
        self.user_id = user_id
        self._local = LocalCalendarProvider(db)
        self._http_request = http_request
        self.working_hours = working_hours
        self.list_notice = ""

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
        """Active local rows merged with one primary ``events.list`` read.

        A matching ``external_id`` keeps the local id and shows Google's title
        and times. Nothing is written back. Google-only rows have no local id.
        A failed read returns the local rows and sets ``list_notice``.
        """
        self.list_notice = ""
        local_rows = self._local.list_events(user_id, start, end, status=status)
        if status != "active":
            return local_rows
        try:
            items, truncated = self._read_event_pages(start, end, self.timezone_name())
        except (CalendarRemoteReadError, CalendarTokenError, CalendarTransientError):
            self.list_notice = _LIST_LOCAL_ONLY
            return local_rows[:_LIST_LIMIT]
        if truncated:
            self.list_notice = _LIST_TRUNCATED
        return self._merge_list(user_id, local_rows, items, start, end)[:_LIST_LIMIT]

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
        """Change Google first, then the local row.

        A time change reads the primary calendar and excludes this row by
        ``external_id`` plus ``event_id``. A linked Google row is patched.
        A pre-connect local row is inserted, then marked ``provider='google'``.
        Participants are stored locally and are not sent as attendees.
        """
        event = self.db.get(CalendarEvent, event_id)
        if not event or event.user_id != user_id:
            return None
        start_at = _as_naive(start_at) if start_at is not None else None
        end_at = _as_naive(end_at) if end_at is not None else None
        changing_time = start_at is not None or end_at is not None
        google_fields = any(
            value is not None for value in (title, start_at, end_at, location, description)
        )
        external_id = (event.external_id or "").strip()
        linked = (event.provider or "") == "google" and bool(external_id)
        if not google_fields and participants is None:
            return event_to_dto(event)
        if linked and not google_fields:
            return self._local.update_event(user_id, event_id, participants=participants)

        zone_name = self.timezone_name()
        effective_start = start_at if start_at is not None else event.start_at
        effective_end = end_at if end_at is not None else event.end_at
        if changing_time and (effective_start is None or effective_end is None):
            raise CalendarRemoteWriteError()
        if changing_time:
            conflicts = self._conflicts_in_window(
                user_id,
                effective_start,
                effective_end,
                exclude_event_id=event_id,
                zone_name=zone_name,
            )
            if conflicts:
                raise CalendarOverlapError(conflicts)

        inserted_id = ""
        sent: dict = {}
        if linked:
            sent = _patch_body(
                title=title,
                start_at=start_at,
                end_at=end_at,
                effective_start=effective_start,
                effective_end=effective_end,
                location=location,
                description=description,
                zone_name=zone_name,
            )
            self._patch_google_event(external_id, sent)
        else:
            if effective_start is None or effective_end is None:
                raise CalendarRemoteWriteError()
            inserted_id = self._insert_google_event(
                title=title if title is not None else (event.title or ""),
                start_at=effective_start,
                end_at=effective_end,
                location=location if location is not None else (event.location or ""),
                description=description if description is not None else (event.description or ""),
                zone_name=zone_name,
            )
            if self._external_id_taken(user_id, inserted_id):
                self._delete_google_event(inserted_id)
                raise CalendarRemoteWriteError(_LOCAL_UPDATE_FAILED)
        self._commit_update(
            event,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            location=location,
            description=description,
            zone_name=zone_name,
            inserted_id=inserted_id,
            sent=sent,
            external_id=external_id,
        )
        self.db.refresh(event)
        return event_to_dto(event)

    def cancel_event(self, user_id: int, event_id: int) -> CalendarEventDTO | None:
        """Delete a linked Google event first. Soft-cancel the local row only after that.

        A pure local row is soft-cancelled with no Google call. Cancel does not
        read the primary calendar for conflicts. 404 and 410 count as gone.
        """
        event = self.db.get(CalendarEvent, event_id)
        if not event or event.user_id != user_id:
            return None
        external_id = (event.external_id or "").strip()
        if (event.provider or "") == "google" and external_id:
            self._delete_google_event_required(external_id)
            try:
                return self._local.cancel_event(user_id, event_id)
            except Exception:
                self.db.rollback()
                logger.error("google calendar local cancel failed event_id=%s", external_id)
                raise CalendarRemoteWriteError(_CANCEL_FAILED) from None
        return self._local.cancel_event(user_id, event_id)

    def confirm_titled_change(
        self,
        user_id: int,
        *,
        action: str,
        title: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        participants: list[str] | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> CalendarEventDTO:
        """Patch or delete the one primary event whose summary equals ``title``.

        The id comes from ``events.list`` at confirm time. A local row is
        updated only when it already has that ``external_id``. No row is inserted.
        """
        needle = (title or "").strip()
        zone_name = self.timezone_name()
        match = self._event_for_title(needle, zone_name)
        gid = str(match.get("id") or "").strip()
        local_rows = self._local.rows_for_external_ids(user_id, [gid])
        local = local_rows[0] if local_rows else None
        if action == "cancel":
            if local is not None:
                cancelled = self.cancel_event(user_id, local.id)
                if cancelled is None:
                    raise CalendarRemoteWriteError(_CANCEL_FAILED)
                return cancelled
            self._delete_google_event_required(gid)
            return _remote_only_dto(
                user_id,
                title=needle,
                start_at=start_at,
                end_at=end_at,
                zone_name=zone_name,
                external_id=gid,
                status="cancelled",
            )
        if start_at is None or end_at is None:
            raise CalendarRemoteWriteError()
        if local is not None:
            updated = self.update_event(
                user_id,
                local.id,
                title=needle,
                start_at=start_at,
                end_at=end_at,
                participants=participants,
                location=location,
                description=description,
            )
            if updated is None:
                raise CalendarRemoteWriteError()
            return updated
        conflicts = self._conflicts_in_window(
            user_id,
            _as_naive(start_at),
            _as_naive(end_at),
            exclude_event_id=None,
            zone_name=zone_name,
            exclude_external_id=gid,
        )
        if conflicts:
            raise CalendarOverlapError(conflicts)
        sent = _patch_body(
            title=needle,
            start_at=start_at,
            end_at=end_at,
            effective_start=start_at,
            effective_end=end_at,
            location=location,
            description=description,
            zone_name=zone_name,
        )
        self._patch_google_event(gid, sent)
        return _remote_only_dto(
            user_id,
            title=needle,
            start_at=start_at,
            end_at=end_at,
            zone_name=zone_name,
            external_id=gid,
            location=location or "",
            description=description or "",
        )

    def _event_for_title(self, title: str, zone_name: str) -> dict:
        """One live primary event with this exact summary. Otherwise nothing is writable."""
        if not title:
            raise CalendarTitleUnmatched()
        now = datetime.now(ZoneInfo(zone_name)).replace(tzinfo=None, microsecond=0)
        items = self._list_primary_events(now, datetime(2100, 1, 1), zone_name)
        matches = [
            item
            for item in items
            if isinstance(item, dict)
            and not _hidden_on_list(item)
            and str(item.get("id") or "").strip()
            and str(item.get("summary") or "").strip() == title
        ]
        if len(matches) != 1:
            raise CalendarTitleUnmatched()
        return matches[0]

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

    def conflicts_for_update(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_event_id: int | None = None,
    ) -> list:
        """Busy rows for a time change, excluding this event's Google id and local id."""
        return self._conflicts_in_window(
            user_id,
            _as_naive(start),
            _as_naive(end),
            exclude_event_id=exclude_event_id,
            zone_name=self.timezone_name(),
        )

    def primary_events_for_title(
        self, start: datetime, end: datetime, zone_name: str
    ) -> list[dict]:
        """Visible primary events for connected update/cancel prefill.

        ``summary`` is the stripped Google summary. ``start_at`` and ``end_at``
        are naive wall times, or None when the item has no usable span.
        Hidden rows are skipped. Nothing is stored. Confirm does not call this
        and still requires ``summary.strip() == title``.
        """
        items = self._list_primary_events(_as_naive(start), _as_naive(end), zone_name)
        found: list[dict] = []
        for item in items:
            if not isinstance(item, dict) or _hidden_on_list(item):
                continue
            if not str(item.get("id") or "").strip():
                continue
            view = _event_view(item)
            found.append(
                {
                    "summary": str(item.get("summary") or "").strip(),
                    "start_at": None if view is None else view.start,
                    "end_at": None if view is None else view.end,
                }
            )
        return found

    def readonly_titles(self, user_id: int, start: datetime, end: datetime) -> list[str]:
        """Titles of primary-calendar events that have no local row.

        Cancelled items are skipped. A matching ``external_id`` is not read-only.
        Empty summaries use the display title 「（无标题）」. Nothing is stored.
        """
        items = self._list_primary_events(_as_naive(start), _as_naive(end), self.timezone_name())
        ids = [str(item.get("id") or "") for item in items if isinstance(item, dict)]
        linked = {
            row.external_id
            for row in self._local.rows_for_external_ids(user_id, ids)
            if row.external_id
        }
        titles: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") == "cancelled":
                continue
            gid = str(item.get("id") or "")
            if gid and gid in linked:
                continue
            summary = str(item.get("summary") or "").strip()
            titles.append(summary or _UNTITLED)
        return titles

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
        and are not a schedule. When settings omit working hours, the whole
        requested window stays available. Injected ``working_hours`` clip the
        window; unspecified weekdays are then closed. Nothing here is
        hard-coded to a daytime range.
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
        hours = self._working_hours_for_gaps()
        if not hours:
            return free_gaps(intervals, start, end, duration, limit)
        windows = _intersect_working_hours(start, end, hours)
        return _slots_in_windows(intervals, windows, duration, limit)

    def overlap_rows(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_event_id: int | None = None,
        status: str = "active",
    ) -> list[CalendarEvent]:
        """Local rows only, so update/cancel lookup cannot select a Google-only item.

        Time-change conflicts use ``conflicts_for_update``. That read excludes the
        row by ``external_id`` and does not treat a Google row's stored interval
        as a second busy block.
        """
        return self._local.overlap_rows(
            user_id,
            start,
            end,
            exclude_event_id=exclude_event_id,
            status=status,
        )

    def get_row(self, event_id: int) -> CalendarEvent | None:
        return self._local.get_row(event_id)

    def _conflicts_in_window(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_event_id: int | None,
        zone_name: str,
        exclude_external_id: str = "",
    ) -> list:
        _intervals, conflicts = self._read_window(
            user_id,
            start,
            end,
            zone_name,
            exclude_event_id=exclude_event_id,
            exclude_external_id=exclude_external_id,
        )
        return conflicts

    def _read_window(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        zone_name: str,
        *,
        exclude_event_id: int | None = None,
        exclude_external_id: str = "",
    ) -> tuple[list[tuple[datetime, datetime]], list]:
        items = self._list_primary_events(start, end, zone_name)
        excluded_external = (exclude_external_id or "").strip()
        if not excluded_external and exclude_event_id is not None:
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
        items, truncated = self._read_event_pages(start, end, zone_name)
        if truncated:
            logger.warning("google calendar events list failed status=truncated")
            raise CalendarRemoteReadError()
        return items

    def _read_event_pages(
        self, start: datetime, end: datetime, zone_name: str
    ) -> tuple[list[dict], bool]:
        """Return items and whether a page remained after the cap.

        The page token is not stored. ``truncated`` is true when the cap was
        hit with ``nextPageToken`` still set. Callers decide whether that is
        a hard failure (confirm) or a partial list (display).
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
                return items, False
        return items, True

    def _merge_list(
        self,
        user_id: int,
        local_rows: list[CalendarEventDTO],
        items: list[dict],
        start: datetime,
        end: datetime,
    ) -> list[CalendarEventDTO]:
        """Dedupe by ``external_id``. Overlay is display-only; rows are not saved."""
        suppressed: set[str] = set()
        views: list[_EventView] = []
        seen_ids: set[str] = set()
        for item in items:
            gid = str(item.get("id") or "")
            if gid:
                if gid in seen_ids:
                    continue
                seen_ids.add(gid)
            if gid and _hidden_on_list(item):
                suppressed.add(gid)
                continue
            view = _event_view(item)
            if view is None or not _overlaps(view.start, view.end, start, end):
                continue
            views.append(view)

        linked_ids = [view.gid for view in views if view.gid]
        linked_ids.extend(suppressed)
        linked = {
            row.external_id: event_to_dto(row)
            for row in self._local.rows_for_external_ids(user_id, linked_ids)
            if row.external_id
        }

        merged: list[CalendarEventDTO] = []
        used: set[str] = set()
        for view in views:
            local = linked.get(view.gid) if view.gid else None
            if local is not None and view.gid:
                if view.gid in used:
                    continue
                used.add(view.gid)
                merged.append(_overlay_dto(local, view))
                continue
            merged.append(_readonly_dto(user_id, view))
            if view.gid:
                used.add(view.gid)

        for row in local_rows:
            external_id = row.external_id or ""
            if external_id and (external_id in used or external_id in suppressed):
                continue
            merged.append(row)
        merged.sort(key=lambda row: (row.start_at or datetime.min, row.id or 0, row.title))
        return merged

    def _working_hours_for_gaps(self) -> list[WorkingHoursWindow]:
        """Injected hours win. Otherwise parse settings, which omit the field today."""
        if self.working_hours is not None:
            return [item for item in self.working_hours if _valid_hours(item)]
        return self._fetch_working_hours()

    def _fetch_working_hours(self) -> list[WorkingHoursWindow]:
        """Read ``settings.list``. A missing field or a failed read means no clip.

        This is not an ``events.list`` failure: suggest still uses the full window.
        """
        try:
            response = self._call("GET", _SETTINGS_URL)
        except (CalendarRemoteReadError, CalendarTokenError, CalendarTransientError):
            logger.warning("google calendar settings list failed status=network")
            return []
        if response.status_code != 200:
            logger.warning(
                "google calendar settings list failed status=%s", response.status_code
            )
            return []
        body = _json_object(response)
        items = body.get("items") if body else None
        if not isinstance(items, list):
            return []
        return parse_working_hours(items)

    def _commit_update(
        self,
        event: CalendarEvent,
        *,
        title: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        participants: list[str] | None,
        location: str | None,
        description: str | None,
        zone_name: str,
        inserted_id: str,
        sent: dict,
        external_id: str,
    ) -> None:
        """Apply the confirmed edit after Google has accepted it. One commit."""
        self.db.refresh(event)
        restore = _restore_body(event, sent, zone_name=zone_name) if sent else {}
        if title is not None:
            event.title = title
        if start_at is not None:
            event.start_at = start_at
        if end_at is not None:
            event.end_at = end_at
        if participants is not None:
            event.participants = json.dumps(list(participants), ensure_ascii=False)
        if location is not None:
            event.location = location
        if description is not None:
            event.description = description
        event.timezone = zone_name
        if inserted_id:
            event.provider = "google"
            event.external_id = inserted_id
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            if inserted_id:
                self._delete_google_event(inserted_id)
            elif restore and external_id:
                self._restore_patch(external_id, restore)
            raise CalendarRemoteWriteError(_LOCAL_UPDATE_FAILED) from None

    def _patch_google_event(self, event_id: str, body: dict) -> None:
        """PATCH one primary event. No attendees and no sendUpdates."""
        if not event_id or not body:
            return
        url = f"{_EVENTS_URL}/{quote(event_id, safe='')}"
        try:
            response = self._call("PATCH", url, json=body)
        except CalendarRemoteReadError:
            logger.warning("google calendar events patch failed status=network")
            raise CalendarRemoteWriteError() from None
        if response.status_code not in (200, 201):
            logger.warning("google calendar events patch failed status=%s", response.status_code)
            raise CalendarRemoteWriteError()

    def _restore_patch(self, event_id: str, body: dict) -> None:
        """Best-effort undo of a patch when the local commit fails. Log only the event id."""
        try:
            self._patch_google_event(event_id, body)
        except CalendarRemoteError:
            logger.error("google calendar compensate patch failed event_id=%s", event_id)

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

    def _delete_google_event_required(self, event_id: str) -> None:
        """Delete one primary event. 404 and 410 mean it is already gone.

        Any other failure leaves the caller to keep the local row active.
        """
        url = f"{_EVENTS_URL}/{quote(event_id, safe='')}"
        try:
            response = self._call("DELETE", url)
        except CalendarRemoteReadError:
            logger.warning("google calendar events delete failed status=network")
            raise CalendarRemoteWriteError(_CANCEL_FAILED) from None
        if response.status_code in (200, 204, 404, 410):
            return
        logger.warning("google calendar events delete failed status=%s", response.status_code)
        raise CalendarRemoteWriteError(_CANCEL_FAILED)

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
        response = self._timezone_response(access)
        if response.status_code == 404:
            return _MISSING_TIMEZONE
        if response.status_code == 200:
            value = _setting_value(response)
            if not value:
                return _MISSING_TIMEZONE
            return value
        logger.warning(
            "google calendar timezone lookup failed status=%s", response.status_code
        )
        if response.status_code in (401, 403):
            raise CalendarTokenError()
        if response.status_code == 500 or response.status_code in _TIMEZONE_RETRY_STATUSES:
            raise CalendarTransientError()
        raise CalendarTokenError()

    def _timezone_response(self, access: str) -> httpx.Response:
        """GET the timezone setting. Transport errors and 502/503/504 retry once."""
        request = self._http_request or _http_request
        first = _timezone_once(request, access)
        if first is None or first.status_code in _TIMEZONE_RETRY_STATUSES:
            second = _timezone_once(request, access)
            if second is not None:
                return second
            logger.warning("google calendar timezone lookup failed status=network")
            raise CalendarTransientError()
        return first


def _timezone_once(request, access: str) -> httpx.Response | None:
    try:
        return request(
            "GET",
            _TIMEZONE_URL,
            headers={"Authorization": f"Bearer {access}"},
        )
    except httpx.HTTPError:
        return None


def _remote_only_dto(
    user_id: int,
    *,
    title: str,
    start_at: datetime | None,
    end_at: datetime | None,
    zone_name: str,
    external_id: str,
    status: str = "active",
    location: str = "",
    description: str = "",
) -> CalendarEventDTO:
    """A confirm result with no local id. Nothing is inserted."""
    return CalendarEventDTO(
        id=None,
        user_id=user_id,
        title=title,
        start_at=_as_naive(start_at) if start_at is not None else None,
        end_at=_as_naive(end_at) if end_at is not None else None,
        status=status,
        timezone=zone_name,
        location=location,
        description=description,
        external_id=external_id,
        provider="google",
    )


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


def _patch_body(
    *,
    title: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
    effective_start: datetime | None,
    effective_end: datetime | None,
    location: str | None,
    description: str | None,
    zone_name: str,
) -> dict:
    """Fields the user actually sent. Times stay wall-clock. No attendees."""
    body: dict = {}
    if title is not None:
        body["summary"] = title
    if location is not None:
        body["location"] = location
    if description is not None:
        body["description"] = description
    if start_at is not None or end_at is not None:
        body["start"] = {"dateTime": _wall_clock(effective_start), "timeZone": zone_name}
        body["end"] = {"dateTime": _wall_clock(effective_end), "timeZone": zone_name}
    return body


def _restore_body(event: CalendarEvent, sent: dict, *, zone_name: str) -> dict:
    """Previous local values for the fields a patch just wrote."""
    body: dict = {}
    if "summary" in sent:
        body["summary"] = event.title or ""
    if "location" in sent:
        body["location"] = event.location or ""
    if "description" in sent:
        body["description"] = event.description or ""
    if "start" in sent or "end" in sent:
        if event.start_at is not None and event.end_at is not None:
            body["start"] = {"dateTime": _wall_clock(event.start_at), "timeZone": zone_name}
            body["end"] = {"dateTime": _wall_clock(event.end_at), "timeZone": zone_name}
    return body


def _rfc3339(wall: datetime, zone: ZoneInfo) -> str:
    """Account-zone offset on the same wall clock. Not a conversion to UTC."""
    aware = _as_naive(wall).replace(tzinfo=zone)
    return aware.isoformat(timespec="seconds")


def _overlaps(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> bool:
    return start < window_end and end > window_start


@dataclass(frozen=True)
class _EventView:
    gid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    busy: bool


def _hidden_on_list(item: dict) -> bool:
    """Cancelled, declined, and working-location rows are not shown and not busy."""
    if str(item.get("status") or "") == "cancelled":
        return True
    if str(item.get("eventType") or "") == "workingLocation":
        return True
    return _self_declined(item)


def _event_view(item: dict) -> _EventView | None:
    if not isinstance(item, dict) or _hidden_on_list(item):
        return None
    span = _span_from_item(item)
    if span is None:
        return None
    start, end, all_day = span
    summary = str(item.get("summary") or "").strip()
    return _EventView(
        gid=str(item.get("id") or ""),
        title=summary or _UNTITLED,
        start=start,
        end=end,
        all_day=all_day,
        busy=str(item.get("transparency") or "") != "transparent",
    )


def _span_from_item(item: dict) -> tuple[datetime, datetime, bool] | None:
    start_info = item.get("start") if isinstance(item.get("start"), dict) else {}
    end_info = item.get("end") if isinstance(item.get("end"), dict) else {}
    if start_info.get("date") and end_info.get("date"):
        # end.date is exclusive. The whole local day is busy; we do not create all-day events.
        start = _date_wall(str(start_info.get("date") or ""))
        end = _date_wall(str(end_info.get("date") or ""))
        all_day = True
    else:
        start = _wall_from_google(str(start_info.get("dateTime") or ""))
        end = _wall_from_google(str(end_info.get("dateTime") or ""))
        all_day = False
    if start is None or end is None or end <= start:
        return None
    return start, end, all_day


def _overlay_dto(local: CalendarEventDTO, view: _EventView) -> CalendarEventDTO:
    """Local identity, Google title and times. The stored row is not modified."""
    return CalendarEventDTO(
        id=local.id,
        user_id=local.user_id,
        title=view.title,
        start_at=view.start,
        end_at=view.end,
        participants=list(local.participants),
        status=local.status,
        timezone=local.timezone,
        location=local.location,
        description=local.description,
        external_id=local.external_id,
        provider=local.provider or "local",
        readonly=False,
        all_day=view.all_day,
    )


def _readonly_dto(user_id: int, view: _EventView) -> CalendarEventDTO:
    return CalendarEventDTO(
        id=None,
        user_id=user_id,
        title=view.title,
        start_at=view.start,
        end_at=view.end,
        status="active",
        external_id=view.gid,
        provider="google",
        readonly=True,
        all_day=view.all_day,
    )


def _busy_span(item: dict) -> tuple[datetime, datetime] | None:
    """Busy interval in wall-clock time, or None when the item does not block."""
    view = _event_view(item)
    if view is None or not view.busy:
        return None
    return view.start, view.end


def parse_working_hours(items: list) -> list[WorkingHoursWindow]:
    """Weekly hours from a settings list, or an empty list when the field is absent.

    An empty result means the caller should not clip the window. It does not
    mean a default daytime schedule.
    """
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") not in _WORKING_HOURS_IDS:
            continue
        return _hours_from_value(item.get("value"))
    return []


def _hours_from_value(value) -> list[WorkingHoursWindow]:
    raw = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    windows: list[WorkingHoursWindow] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        weekday = _weekday_index(entry.get("weekday"))
        start = _clock_value(entry.get("start"))
        end = _clock_value(entry.get("end"))
        if weekday is None or start is None or end is None or end <= start:
            continue
        windows.append(WorkingHoursWindow(weekday=weekday, start=start, end=end))
    return windows


def _weekday_index(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        if 0 <= number <= 6:
            return number
    return None


def _clock_value(value) -> time | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _valid_hours(item: WorkingHoursWindow) -> bool:
    return 0 <= item.weekday <= 6 and item.end > item.start


def _intersect_working_hours(
    start: datetime, end: datetime, hours: list[WorkingHoursWindow]
) -> list[tuple[datetime, datetime]]:
    """Clip ``[start, end)`` to each weekday's open interval. Night is not a start."""
    by_weekday: dict[int, list[tuple[time, time]]] = {}
    for item in hours:
        if not _valid_hours(item):
            continue
        by_weekday.setdefault(item.weekday, []).append((item.start, item.end))
    windows: list[tuple[datetime, datetime]] = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        for open_start, open_end in by_weekday.get(day.weekday(), []):
            seg_start = max(
                day.replace(
                    hour=open_start.hour, minute=open_start.minute, second=0, microsecond=0
                ),
                start,
            )
            seg_end = min(
                day.replace(hour=open_end.hour, minute=open_end.minute, second=0, microsecond=0),
                end,
            )
            if seg_end > seg_start:
                windows.append((seg_start, seg_end))
        day += timedelta(days=1)
    windows.sort(key=lambda pair: pair[0])
    return windows


def _slots_in_windows(
    busy: list[tuple[datetime, datetime]],
    windows: list[tuple[datetime, datetime]],
    duration: timedelta,
    limit: int,
) -> list[tuple[datetime, datetime]]:
    slots: list[tuple[datetime, datetime]] = []
    for begin, finish in windows:
        if len(slots) >= limit:
            break
        slots.extend(free_gaps(busy, begin, finish, duration, limit - len(slots)))
    return slots


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
