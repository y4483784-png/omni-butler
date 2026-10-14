from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_event
from app.models.models import User
from app.services.calendar import (
    cancel_event,
    check_conflict,
    create_event,
    free_slots_for,
    serialize_event_card,
    update_event,
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


def _conflict_payload(rows, slots) -> dict:
    return {
        "conflicts": [serialize_event_card(row) for row in rows],
        "suggested_slots": [
            {"start_at": start.isoformat(), "end_at": end.isoformat()} for start, end in slots
        ],
    }


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
        participants=list(body.participants or []),
        location=body.location or "",
        description=body.description or "",
    )
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
    if body.start_at is not None or body.end_at is not None:
        if start_at is None or end_at is None:
            raise HTTPException(status_code=400, detail="需要开始和结束时间")
        conflicts = [
            row
            for row in check_conflict(db, current_user.id, start_at, end_at)
            if row.id != event_id
        ]
        if conflicts:
            slots = free_slots_for(
                db, current_user.id, start_at, end_at, exclude_event_id=event_id
            )
            raise HTTPException(status_code=409, detail=_conflict_payload(conflicts, slots))
    event = update_event(
        db,
        event_id,
        title=body.title,
        start_at=_parse_dt(body.start_at),
        end_at=_parse_dt(body.end_at),
        participants=body.participants,
        location=body.location,
        description=body.description,
    )
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
    event = cancel_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="日程不存在")
    return serialize_event_card(event)
