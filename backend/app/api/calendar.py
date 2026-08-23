from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_event
from app.models.models import User
from app.services.calendar import cancel_event, serialize_event_card, update_event

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class CalendarUpdate(BaseModel):
    title: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    participants: list[str] = Field(default_factory=list)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


@router.patch("/{event_id}")
def patch_event(
    event_id: int,
    body: CalendarUpdate,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    require_owned_event(db, event_id, current_user.id)
    event = update_event(
        db,
        event_id,
        title=body.title,
        start_at=_parse_dt(body.start_at),
        end_at=_parse_dt(body.end_at),
        participants=body.participants if body.participants else None,
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
