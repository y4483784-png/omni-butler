from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import set_rls_context
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_event
from app.models.models import User
from app.services.calendar_secrets import (
    CalendarOAuthError,
    CalendarTokenError,
    app_home_url,
    build_authorize_url,
    connect_google_account,
    connection_status,
    disconnect_google,
    load_oauth_state,
    oauth_configuration_error,
    sign_oauth_state,
)
from app.services.calendar import (
    REMOTE_READ_ERRORS,
    calendar_now,
    cancel_event,
    check_conflict,
    create_event,
    free_slots_for,
    serialize_event_card,
    update_event,
)
from app.services.calendar_google import (
    CalendarOverlapError,
    CalendarReconnectRequired,
    CalendarRemoteReadError,
    CalendarRemoteWriteError,
)
from app.services.calendar_factory import (
    account_timezone_name,
    calendar_provider_for,
    calendar_writes_remote,
)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class CalendarWrite(BaseModel):
    title: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    participants: list[str] | None = None
    location: str | None = None
    description: str | None = None


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


_READ_FAILED = "暂时读不到 Google 日历，未写入。"


def _conflict_payload(rows, slots) -> dict:
    conflicts = []
    for row in rows:
        item = serialize_event_card(row)
        if item.get("id") is None:
            item["readonly"] = True
            item["title"] = "Google 日历上已有安排"
        conflicts.append(item)
    return {
        "conflicts": conflicts,
        "suggested_slots": [
            {"start_at": start.isoformat(), "end_at": end.isoformat()} for start, end in slots
        ],
    }


def _conflict_http(db, user_id, start_at, end_at, conflicts, *, exclude_event_id=None):
    try:
        slots = free_slots_for(
            db, user_id, start_at, end_at, exclude_event_id=exclude_event_id
        )
    except REMOTE_READ_ERRORS:
        raise HTTPException(status_code=502, detail=_READ_FAILED) from None
    raise HTTPException(status_code=409, detail=_conflict_payload(conflicts, slots))


def _slots_or_empty(db, user_id, start_at, end_at, *, exclude_event_id=None):
    try:
        return free_slots_for(
            db, user_id, start_at, end_at, exclude_event_id=exclude_event_id
        )
    except REMOTE_READ_ERRORS:
        return []


@router.get("")
def list_calendar_events(
    start: str | None = None,
    end: str | None = None,
    status: str = "active",
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    """Events overlapping [start, end) for the signed-in user. Default is the next 7 days."""
    today = calendar_now(db, current_user.id).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        start_at = _parse_dt(start) or today
        end_at = _parse_dt(end) or (today + timedelta(days=7))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="时间格式无效") from exc
    if end_at <= start_at:
        raise HTTPException(status_code=400, detail="结束时间需晚于开始时间")
    provider = calendar_provider_for(db, current_user.id)
    rows = provider.list_events(
        current_user.id, start_at, end_at, status=status or "active"
    )
    body = [serialize_event_card(row) for row in rows[:20]]
    notice = str(getattr(provider, "list_notice", "") or "")
    # Unconnected and successful reads stay a JSON array. A Google read
    # problem still returns the local rows, plus the sentence, and is not a 500.
    if notice:
        return {"events": body, "notice": notice}
    return body


@router.post("")
def create_calendar_event(
    body: CalendarWrite,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    title = (body.title or "").strip()
    start_at = _parse_dt(body.start_at)
    end_at = _parse_dt(body.end_at)
    if not title or start_at is None:
        raise HTTPException(status_code=400, detail="需要标题和开始时间")
    if end_at is None:
        end_at = start_at + timedelta(hours=1)
    participants = list(body.participants or [])
    location = body.location or ""
    description = body.description or ""
    if calendar_writes_remote(db, current_user.id):
        return _create_connected_event(
            db,
            user_id=current_user.id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            location=location,
            description=description,
        )
    conflicts = check_conflict(db, current_user.id, start_at, end_at)
    if conflicts:
        slots = free_slots_for(db, current_user.id, start_at, end_at)
        raise HTTPException(status_code=409, detail=_conflict_payload(conflicts, slots))
    event = create_event(
        db,
        user_id=current_user.id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        location=location,
        description=description,
    )
    return serialize_event_card(event)


def _create_connected_event(
    db,
    *,
    user_id: int,
    title: str,
    start_at: datetime,
    end_at: datetime,
    participants: list[str],
    location: str,
    description: str,
):
    """List primary, then insert on Google, then the local row. List failure writes nothing."""
    try:
        event = create_event(
            db,
            user_id=user_id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
            location=location,
            description=description,
        )
    except CalendarOverlapError as exc:
        _conflict_http(db, user_id, start_at, end_at, exc.conflicts)
    except CalendarRemoteReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc) or _READ_FAILED) from None
    except CalendarRemoteWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except CalendarTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    return serialize_event_card(event)


@router.patch("/{event_id}")
def patch_event(
    event_id: int,
    body: CalendarWrite,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    current = require_owned_event(db, event_id, current_user.id)
    start_at = _parse_dt(body.start_at) if body.start_at is not None else current.start_at
    end_at = _parse_dt(body.end_at) if body.end_at is not None else current.end_at
    changing_time = body.start_at is not None or body.end_at is not None
    if changing_time and (start_at is None or end_at is None):
        raise HTTPException(status_code=400, detail="需要开始和结束时间")
    # Unconnected time changes still use local rows only. A connected change
    # reads the primary calendar inside the provider and must not treat a
    # Google row's stored interval as the busy block.
    if changing_time and not calendar_writes_remote(db, current_user.id):
        conflicts = [
            row
            for row in check_conflict(db, current_user.id, start_at, end_at)
            if row.id != event_id
        ]
        if conflicts:
            slots = _slots_or_empty(
                db, current_user.id, start_at, end_at, exclude_event_id=event_id
            )
            raise HTTPException(status_code=409, detail=_conflict_payload(conflicts, slots))
    try:
        event = update_event(
            db,
            event_id,
            user_id=current_user.id,
            title=body.title,
            start_at=_parse_dt(body.start_at),
            end_at=_parse_dt(body.end_at),
            participants=body.participants,
            location=body.location,
            description=body.description,
        )
    except CalendarReconnectRequired as exc:
        raise HTTPException(status_code=409, detail=exc.message) from None
    except CalendarOverlapError as exc:
        _conflict_http(
            db, current_user.id, start_at, end_at, exc.conflicts, exclude_event_id=event_id
        )
    except CalendarRemoteReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc) or _READ_FAILED) from None
    except CalendarRemoteWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except CalendarTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    if not event:
        raise HTTPException(status_code=404, detail="日程不存在")
    return serialize_event_card(event)


@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    require_owned_event(db, event_id, current_user.id)
    try:
        event = cancel_event(db, event_id, user_id=current_user.id)
    except CalendarReconnectRequired as exc:
        raise HTTPException(status_code=409, detail=exc.message) from None
    except CalendarRemoteWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except CalendarRemoteReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc) or _READ_FAILED) from None
    except CalendarTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    if not event:
        raise HTTPException(status_code=404, detail="日程不存在")
    return serialize_event_card(event)


# Connected update and cancel go to Google first. List merges a read-only overlay.
@router.get("/google/status")
def google_calendar_status(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    status = connection_status(db, current_user.id)
    if not status["connected"]:
        return status
    try:
        zone = account_timezone_name(db, current_user.id)
    except (CalendarTokenError, httpx.HTTPError):
        zone = ""
    if zone:
        status["timezone"] = zone
    return status


@router.get("/google/connect")
def google_calendar_connect(current_user: User = Depends(get_current_user)):
    message = oauth_configuration_error()
    if message:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse(build_authorize_url(sign_oauth_state(current_user.id)), status_code=302)


@router.get("/google/callback")
def google_calendar_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    if error:
        raise HTTPException(status_code=400, detail="Google 未同意授权，未保存连接。")
    if not state or not code:
        raise HTTPException(status_code=400, detail="授权回调缺少参数，未保存连接。")
    try:
        state_user_id = load_oauth_state(state)
    except SignatureExpired:
        raise HTTPException(status_code=400, detail="授权状态已过期，未保存连接。")
    except (BadSignature, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="授权状态无效，未保存连接。")
    if state_user_id != current_user.id:
        raise HTTPException(status_code=400, detail="授权状态与当前登录用户不一致，未保存连接。")
    set_rls_context(db, state_user_id)
    try:
        connect_google_account(db, user_id=state_user_id, code=code)
    except CalendarOAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    return RedirectResponse(app_home_url(), status_code=302)


@router.delete("/google/connection")
def google_calendar_disconnect(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    set_rls_context(db, current_user.id)
    return disconnect_google(db, current_user.id)
