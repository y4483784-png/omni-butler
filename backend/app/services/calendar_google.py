"""Google Calendar provider.

``get_timezone`` reads the account IANA zone. Connected creates, updates, and
cancels talk to the primary calendar before the local row changes. List and
free-slot reads use the same primary ``events.list`` and do not write
``calendar_events``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, tzinfo
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.orm import Session

from app.models.models import CalendarConnection, CalendarEvent
from app.services import calendar_secrets
from app.services.calendar_local import (
    LocalCalendarProvider,
    _participants_from_row,
    event_to_dto,
    free_gaps,
)
from app.services.calendar_provider import (
    CalendarEventDTO,
    Invitee,
    attendee_body,
    dump_invitees,
    invitee_wire,
    parse_invitee,
    parse_invitees,
)
from app.services.calendar_secrets import (
    CalendarTokenError,
    CalendarTransientError,
    refresh_access_token,
)

logger = logging.getLogger(__name__)

_TIMEZONE_URL = "https://www.googleapis.com/calendar/v3/users/me/settings/timezone"
_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
_FREEBUSY_CAP = 50
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

    def events_between(
        self, user_id: int, start: datetime, end: datetime
    ) -> tuple[list[CalendarEventDTO], str]:
        """Full primary merge for one window. No display cap.

        The notice is ``_LIST_LOCAL_ONLY`` or ``_LIST_TRUNCATED`` when the read
        failed or a page remained. Location, description, and participants are
        filled from the Google item when the merged row left them empty.
        """
        self.list_notice = ""
        local_rows = self._local.list_events(user_id, start, end, status="active")
        try:
            items, truncated = self._read_event_pages(start, end, self.timezone_name())
        except (CalendarRemoteReadError, CalendarTokenError, CalendarTransientError):
            self.list_notice = _LIST_LOCAL_ONLY
            return local_rows, _LIST_LOCAL_ONLY
        notice = _LIST_TRUNCATED if truncated else ""
        self.list_notice = notice
        merged = self._merge_list(user_id, local_rows, items, start, end)
        return _with_google_details(merged, items), notice

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
        meet: bool = False,
    ) -> CalendarEventDTO:
        """List primary, refuse overlaps, insert on Google, then store the local row.

        Wall-clock values are stored as given. They are not converted to UTC.
        Emails are attendees with ``sendUpdates=all``. A bare name stays local.
        """
        start_at = _as_naive(start_at)
        end_at = _as_naive(end_at)
        zone_name = self.timezone_name()
        conflicts = self.conflicts_for_create(user_id, start_at, end_at, zone_name=zone_name)
        if conflicts:
            raise CalendarOverlapError(conflicts)
        google_id, meet_link, meet_status = self._insert_google_event(
            title=title,
            start_at=start_at,
            end_at=end_at,
            location=location,
            description=description,
            zone_name=zone_name,
            participants=participants,
            meet=meet,
        )
        saved = self._save_local_row(
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
        if meet_link or meet_status:
            return replace(saved, meet_link=meet_link, meet_status=meet_status)
        return saved

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
        """Change Google first, then the local row.

        A time change reads the primary calendar and excludes this row by
        ``external_id`` plus ``event_id``. A linked Google row is patched.
        A pre-connect local row is inserted, then marked ``provider='google'``.
        Participants are stored on the local row. An insert invites emails.
        A guest-list change reads the event, then patches the full attendee
        array. A title, time, location, or notes change omits that array.
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
        guest_edit = participants is not None and not _same_guests(event.participants, participants)
        if not google_fields and participants is None and not meet:
            return event_to_dto(event)
        if linked and not google_fields and not guest_edit and not meet:
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
        previous_attendees = None
        meet_link = ""
        meet_status = ""
        if linked:
            attendees = None
            current = None
            if guest_edit or meet:
                current = self._get_google_event(external_id)
            if guest_edit and current is not None:
                raw = current.get("attendees")
                previous_attendees = list(raw) if isinstance(raw, list) else []
                attendees = merged_attendees(participants, current)
            sent = _patch_body(
                title=title,
                start_at=start_at,
                end_at=end_at,
                effective_start=effective_start,
                effective_end=effective_end,
                location=location,
                description=description,
                zone_name=zone_name,
                attendees=attendees,
            )
            if meet and current is not None and not conference_view(current)[0]:
                sent["conferenceData"] = _meet_request(uuid.uuid4().hex)
            patched = self._patch_google_event(external_id, sent)
            if meet:
                meet_link, meet_status = conference_view(patched)
                if not meet_link and current is not None:
                    previous_link, _previous_status = conference_view(current)
                    if previous_link:
                        meet_link, meet_status = previous_link, ""
        else:
            if effective_start is None or effective_end is None:
                raise CalendarRemoteWriteError()
            inserted_id, meet_link, meet_status = self._insert_google_event(
                title=title if title is not None else (event.title or ""),
                start_at=effective_start,
                end_at=effective_end,
                location=location if location is not None else (event.location or ""),
                description=description if description is not None else (event.description or ""),
                zone_name=zone_name,
                participants=participants if participants is not None else event.participants,
                meet=meet,
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
            previous_attendees=previous_attendees,
        )
        self.db.refresh(event)
        saved = event_to_dto(event)
        if meet_link or meet_status:
            return replace(saved, meet_link=meet_link, meet_status=meet_status)
        return saved

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
        external_id: str | None = None,
        meet: bool = False,
    ) -> CalendarEventDTO:
        """Patch or delete one primary event.

        The Google id selects the event. A missing id writes nothing. A local
        row is updated only when it already has that id. No row is inserted.
        """
        needle = (title or "").strip()
        zone_name = self.timezone_name()
        match = self._event_for_confirm(needle, external_id, zone_name)
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
                meet=meet,
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
        attendees = None
        if participants is not None and not _same_guests(_participant_names(match), participants):
            current = self._get_google_event(gid)
            attendees = merged_attendees(participants, current)
        sent = _patch_body(
            title=needle,
            start_at=start_at,
            end_at=end_at,
            effective_start=start_at,
            effective_end=end_at,
            location=location,
            description=description,
            zone_name=zone_name,
            attendees=attendees,
        )
        if meet and not conference_view(match)[0]:
            current = self._get_google_event(gid)
            if not conference_view(current)[0]:
                sent["conferenceData"] = _meet_request(uuid.uuid4().hex)
        patched = self._patch_google_event(gid, sent)
        link, status = conference_view(patched) if meet else ("", "")
        return _remote_only_dto(
            user_id,
            title=needle,
            start_at=start_at,
            end_at=end_at,
            zone_name=zone_name,
            external_id=gid,
            location=location or "",
            description=description or "",
            meet_link=link,
            meet_status=status,
        )

    def _event_for_title(self, title: str, zone_name: str) -> dict:
        """One live primary event with this exact summary. Otherwise nothing is writable."""
        return self._event_for_confirm(title, None, zone_name)

    def _event_for_confirm(self, title: str, external_id: str | None, zone_name: str) -> dict:
        """One Google id. Title is not a second lookup."""
        del title
        gid = (external_id or "").strip()
        if not gid:
            raise CalendarTitleUnmatched()
        now = datetime.now(ZoneInfo(zone_name)).replace(tzinfo=None, microsecond=0)
        items = self._list_primary_events(now, datetime(2100, 1, 1), zone_name)
        visible = [
            item
            for item in items
            if isinstance(item, dict)
            and not _hidden_on_list(item)
            and str(item.get("id") or "").strip()
        ]
        matches = [item for item in visible if str(item.get("id") or "").strip() == gid]
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
        ``location``, ``description``, and ``participants`` come from the live
        item. ``id`` is the Google event id. Attendees are not how this app
        stores participants, so a linked local row fills those three fields
        when the Google item left them empty. Hidden rows are skipped. Nothing
        is stored. Confirm writes only when the card already carries this id.
        """
        items = self._list_primary_events(_as_naive(start), _as_naive(end), zone_name)
        visible = [
            item
            for item in items
            if isinstance(item, dict)
            and not _hidden_on_list(item)
            and str(item.get("id") or "").strip()
        ]
        local_by_id = {
            row.external_id: row
            for row in self._local.rows_for_external_ids(
                self.user_id, [str(item.get("id") or "") for item in visible]
            )
            if row.external_id
        }
        found: list[dict] = []
        for item in visible:
            gid = str(item.get("id") or "").strip()
            view = _event_view(item)
            participants = _participant_names(item)
            location = str(item.get("location") or "").strip()
            description = str(item.get("description") or "").strip()
            local = local_by_id.get(gid)
            if local is not None:
                if not participants:
                    participants = _participants_from_row(local.participants)
                if not location:
                    location = (local.location or "").strip()
                if not description:
                    description = (local.description or "").strip()
            found.append(
                {
                    "id": gid,
                    "summary": str(item.get("summary") or "").strip(),
                    "start_at": None if view is None else view.start,
                    "end_at": None if view is None else view.end,
                    "participants": participants,
                    "location": location,
                    "description": description,
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
        participants: object = None,
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
        self.freebusy_note = ""
        if duration <= timedelta(0) or limit <= 0 or end <= start:
            return []
        invitees = parse_invitees(participants)
        emails: list[str] = []
        seen_emails: set[str] = set()
        skipped: list[str] = []
        for invitee in invitees:
            if invitee.email:
                folded = invitee.email.casefold()
                if folded in seen_emails:
                    continue
                seen_emails.add(folded)
                emails.append(invitee.email)
            elif invitee.name:
                skipped.append(invitee.name)
        if emails:
            return self._mutual_slots(start, end, duration, limit, emails, skipped)
        if skipped:
            self.freebusy_note = "没有查" + "、".join(skipped) + "的空闲。"
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

    def _grant_has_freebusy(self) -> bool:
        connection = (
            self.db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == self.user_id)
            .one_or_none()
        )
        if connection is None:
            return False
        granted = {part for part in (connection.scopes or "").split() if part}
        return _FREEBUSY_SCOPE in granted

    def _mutual_slots(
        self,
        start: datetime,
        end: datetime,
        duration: timedelta,
        limit: int,
        emails: list[str],
        skipped: list[str],
    ) -> list[tuple[datetime, datetime]]:
        """Open slots for primary plus each email. A hidden calendar is not free."""
        notes: list[str] = []
        if skipped:
            notes.append("没有查" + "、".join(skipped) + "的空闲。")
        if not self._grant_has_freebusy():
            self.freebusy_note = "重新连接 Google 日历"
            return []
        zone_name = self.timezone_name()
        zone = ZoneInfo(zone_name)
        chosen = emails[: _FREEBUSY_CAP - 1]
        left_out = emails[_FREEBUSY_CAP - 1 :]
        if left_out:
            notes.append("没有查" + "、".join(left_out) + "的空闲。")
        body = {
            "timeMin": _rfc3339(_as_naive(start), zone),
            "timeMax": _rfc3339(_as_naive(end), zone),
            "timeZone": zone_name,
            "items": [{"id": "primary"}] + [{"id": email} for email in chosen],
        }
        try:
            response = self._call("POST", _FREEBUSY_URL, json=body)
        except CalendarRemoteReadError:
            logger.warning("google calendar freebusy failed status=network")
            raise CalendarRemoteReadError(_READ_FAILED) from None
        if response.status_code != 200:
            logger.warning("google calendar freebusy failed status=%s", response.status_code)
            raise CalendarRemoteReadError(_READ_FAILED)
        payload = _json_object(response)
        if payload is None:
            raise CalendarRemoteReadError(_READ_FAILED)
        busy, hidden = _busy_from_freebusy(payload)
        if hidden:
            notes.append("、".join(hidden) + "的空闲看不到。")
            self.freebusy_note = "".join(notes)
            return []
        self.freebusy_note = "".join(notes)
        hours = self._working_hours_for_gaps()
        if not hours:
            return free_gaps(busy, _as_naive(start), _as_naive(end), duration, limit)
        windows = _intersect_working_hours(_as_naive(start), _as_naive(end), hours)
        return _slots_in_windows(busy, windows, duration, limit)

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
        previous_attendees: list | None = None,
    ) -> None:
        """Apply the confirmed edit after Google has accepted it. One commit."""
        self.db.refresh(event)
        restore = (
            _restore_body(
                event, sent, zone_name=zone_name, previous_attendees=previous_attendees
            )
            if sent
            else {}
        )
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

    def answer_invitation(self, external_id: str, status: str) -> dict:
        """Set this account's response. The click is the only writer."""
        gid = (external_id or "").strip()
        current = self._get_google_event(gid)
        attendees = rsvp_attendees(current, status)
        self._patch_google_event(gid, {"attendees": attendees})
        return {
            "external_id": gid,
            "title": str(current.get("summary") or ""),
            "rsvp": status,
            "phase": "confirmed",
        }

    def _get_google_event(self, event_id: str) -> dict:
        """One primary event. A failed read does not continue into a patch."""
        if not event_id:
            raise CalendarRemoteReadError(_READ_FAILED)
        url = f"{_EVENTS_URL}/{quote(event_id, safe='')}"
        try:
            response = self._call("GET", url)
        except CalendarRemoteReadError:
            logger.warning("google calendar events get failed status=network")
            raise CalendarRemoteReadError(_READ_FAILED) from None
        if response.status_code != 200:
            logger.warning("google calendar events get failed status=%s", response.status_code)
            raise CalendarRemoteReadError(_READ_FAILED)
        body = _json_object(response)
        if not isinstance(body, dict):
            raise CalendarRemoteReadError(_READ_FAILED)
        return body

    def _patch_google_event(self, event_id: str, body: dict) -> dict:
        """PATCH one primary event. ``sendUpdates=all`` only when attendees are present."""
        if not event_id or not body:
            return {}
        url = f"{_EVENTS_URL}/{quote(event_id, safe='')}"
        params: dict[str, str] = {}
        if "attendees" in body:
            params["sendUpdates"] = "all"
        if "conferenceData" in body:
            params["conferenceDataVersion"] = "1"
        try:
            response = self._call("PATCH", url, json=body, params=params or None)
        except CalendarRemoteReadError:
            logger.warning("google calendar events patch failed status=network")
            raise CalendarRemoteWriteError() from None
        if response.status_code not in (200, 201):
            logger.warning("google calendar events patch failed status=%s", response.status_code)
            raise CalendarRemoteWriteError()
        return _json_object(response) or {}

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
        participants: object = None,
        meet: bool = False,
    ) -> tuple[str, str, str]:
        body = {
            "summary": title,
            "location": location or "",
            "description": description or "",
            "start": {"dateTime": _wall_clock(start_at), "timeZone": zone_name},
            "end": {"dateTime": _wall_clock(end_at), "timeZone": zone_name},
        }
        attendees = attendee_body(participants)
        params: dict[str, str] = {}
        if attendees:
            body["attendees"] = attendees
            params["sendUpdates"] = "all"
        request_id = uuid.uuid4().hex
        if meet:
            body["conferenceData"] = _meet_request(request_id)
            params["conferenceDataVersion"] = "1"
        try:
            response = self._call("POST", _EVENTS_URL, json=body, params=params or None)
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
        link, status = conference_view(payload) if meet else ("", "")
        return google_id, link, status

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
                participants=dump_invitees(participants),
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
    meet_link: str = "",
    meet_status: str = "",
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
        meet_link=meet_link,
        meet_status=meet_status,
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
    attendees: list | None = None,
) -> dict:
    """Fields the user actually sent. Times stay wall-clock.

    ``attendees`` is omitted unless this confirm changes the guest list.
    An omitted array leaves the existing guests in place. A list, including
    an empty one, replaces the whole array.
    """
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
    if attendees is not None:
        body["attendees"] = attendees
    return body


def _guest_identity(invitee: Invitee) -> tuple[str, str]:
    if invitee.email:
        return ("email", invitee.email.casefold())
    return ("name", invitee.name)


def _same_guests(stored: object, new: object) -> bool:
    """Same people. A newly typed name is a change. An empty name is not a different person."""
    left = parse_invitees(stored)
    right = parse_invitees(new)
    if {_guest_identity(item) for item in left} != {_guest_identity(item) for item in right}:
        return False
    stored_names = {item.email.casefold(): item.name for item in left if item.email}
    for item in right:
        if item.email and item.name and stored_names.get(item.email.casefold(), "") != item.name:
            return False
    return True


def _meet_request(request_id: str) -> dict:
    return {
        "createRequest": {
            "requestId": request_id,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    }


def conference_view(item: dict) -> tuple[str, str]:
    """A real Meet URL, or pending when Google has not finished. Never a guessed URL."""
    if not isinstance(item, dict):
        return "", ""
    link = str(item.get("hangoutLink") or "").strip()
    data = item.get("conferenceData") if isinstance(item.get("conferenceData"), dict) else {}
    if not link:
        points = data.get("entryPoints") if isinstance(data.get("entryPoints"), list) else []
        for point in points:
            if not isinstance(point, dict):
                continue
            if str(point.get("entryPointType") or "") != "video":
                continue
            link = str(point.get("uri") or "").strip()
            if link:
                break
    if link:
        return link, ""
    request = data.get("createRequest") if isinstance(data.get("createRequest"), dict) else {}
    status = request.get("status") if isinstance(request.get("status"), dict) else {}
    if str(status.get("statusCode") or "").lower() == "pending":
        return "", "pending"
    return "", ""


def merged_attendees(confirmed: object, google_item: dict) -> list[dict]:
    """The full email list on the card. Remaining guests keep their responseStatus."""
    previous: dict[str, dict] = {}
    raw = google_item.get("attendees") if isinstance(google_item, dict) else None
    if isinstance(raw, list):
        for person in raw:
            if not isinstance(person, dict):
                continue
            email = str(person.get("email") or "").strip()
            if email:
                previous[email.casefold()] = person
    body: list[dict] = []
    seen: set[str] = set()
    for invitee in parse_invitees(confirmed):
        email = invitee.email.strip()
        if not email:
            continue
        folded = email.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        entry: dict = {"email": email}
        display = invitee.name.strip()
        if not display:
            display = str((previous.get(folded) or {}).get("displayName") or "").strip()
        if display:
            entry["displayName"] = display
        status = str((previous.get(folded) or {}).get("responseStatus") or "").strip()
        if status:
            entry["responseStatus"] = status
        body.append(entry)
    return body


def _restore_body(
    event: CalendarEvent,
    sent: dict,
    *,
    zone_name: str,
    previous_attendees: list | None = None,
) -> dict:
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
    if "attendees" in sent:
        body["attendees"] = list(previous_attendees or [])
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
    rsvp: str = ""
    meet_link: str = ""


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
        rsvp=_self_rsvp(item),
        meet_link=conference_view(item)[0],
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


def _with_google_details(rows: list[CalendarEventDTO], items: list[dict]) -> list[CalendarEventDTO]:
    """Fill empty location, description, and participants from the live item."""
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    filled: list[CalendarEventDTO] = []
    for row in rows:
        item = by_id.get((row.external_id or "").strip())
        if item is None:
            filled.append(row)
            continue
        participants = list(row.participants)
        location = row.location or ""
        description = row.description or ""
        if not participants:
            participants = _participant_names(item)
        if not location:
            location = str(item.get("location") or "").strip()
        if not description:
            description = str(item.get("description") or "").strip()
        if (
            participants == list(row.participants)
            and location == (row.location or "")
            and description == (row.description or "")
        ):
            filled.append(row)
            continue
        filled.append(
            replace(row, participants=participants, location=location, description=description)
        )
    return filled


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
        rsvp=view.rsvp,
        meet_link=view.meet_link,
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
        rsvp=view.rsvp,
        meet_link=view.meet_link,
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


def _participant_names(item: dict) -> list[dict[str, str]]:
    """Other people on a Google event. The calendar owner is not a participant.

    Email and displayName stay together. A declined guest is skipped.
    """
    attendees = item.get("attendees")
    if not isinstance(attendees, list):
        return []
    people: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for person in attendees:
        if not isinstance(person, dict) or person.get("self"):
            continue
        if str(person.get("responseStatus") or "") == "declined":
            continue
        invitee = parse_invitee(
            {
                "email": str(person.get("email") or "").strip(),
                "name": str(person.get("displayName") or "").strip(),
            }
        )
        if invitee is None:
            continue
        key = (invitee.email.casefold(), invitee.name)
        if key in seen:
            continue
        seen.add(key)
        people.append(invitee_wire(invitee))
    return people


def _self_rsvp(item: dict) -> str:
    """The signed-in user's response, or empty when they organize the event."""
    organizer = item.get("organizer")
    if isinstance(organizer, dict) and organizer.get("self"):
        return ""
    attendees = item.get("attendees")
    if not isinstance(attendees, list):
        return ""
    for person in attendees:
        if not isinstance(person, dict) or not person.get("self"):
            continue
        if person.get("organizer"):
            return ""
        return str(person.get("responseStatus") or "needsAction")
    return ""


_RSVP_STATUS = frozenset({"accepted", "declined", "tentative"})
_NOT_AN_INVITE = "这不是需要回复的邀请，未写入。"


def rsvp_attendees(google_item: dict, status: str) -> list[dict]:
    """Full attendee array with only the self responseStatus changed."""
    if status not in _RSVP_STATUS:
        raise CalendarRemoteWriteError(_NOT_AN_INVITE)
    raw = google_item.get("attendees") if isinstance(google_item, dict) else None
    if not isinstance(raw, list):
        raise CalendarRemoteWriteError(_NOT_AN_INVITE)
    organizer = google_item.get("organizer")
    if isinstance(organizer, dict) and organizer.get("self"):
        raise CalendarRemoteWriteError(_NOT_AN_INVITE)
    found = False
    body: list[dict] = []
    for person in raw:
        if not isinstance(person, dict):
            continue
        copied = dict(person)
        if person.get("self"):
            if person.get("organizer"):
                raise CalendarRemoteWriteError(_NOT_AN_INVITE)
            copied["responseStatus"] = status
            found = True
        body.append(copied)
    if not found:
        raise CalendarRemoteWriteError(_NOT_AN_INVITE)
    return body


def _busy_from_freebusy(payload: dict) -> tuple[list[tuple[datetime, datetime]], list[str]]:
    calendars = payload.get("calendars") if isinstance(payload, dict) else None
    if not isinstance(calendars, dict):
        raise CalendarRemoteReadError(_READ_FAILED)
    busy: list[tuple[datetime, datetime]] = []
    hidden: list[str] = []
    for cal_id, info in calendars.items():
        if not isinstance(info, dict):
            hidden.append(str(cal_id))
            continue
        errors = info.get("errors")
        if isinstance(errors, list) and any(
            isinstance(err, dict) and str(err.get("reason") or "") == "notFound" for err in errors
        ):
            hidden.append(str(cal_id))
            continue
        for block in info.get("busy") or []:
            if not isinstance(block, dict):
                continue
            block_start = _wall_from_google(str(block.get("start") or ""))
            block_end = _wall_from_google(str(block.get("end") or ""))
            if block_start is None or block_end is None or block_end <= block_start:
                continue
            busy.append((block_start, block_end))
    return busy, hidden


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
