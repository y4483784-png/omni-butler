"""Internal event submit. Tests and a later mail entry call this. It does not read Gmail."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.models.models import User
from app.services.event_inbox import EventRejected, submit_event

router = APIRouter(prefix="/api/events", tags=["events"])


class InboxCard(BaseModel):
    title: str = ""
    start_at: str = ""
    end_at: str = ""
    participants: list[dict[str, str]] = Field(default_factory=list)
    location: str = ""
    description: str = ""
    meet: bool = False
    event_id: str | None = None
    external_id: str = ""
    response: str = ""


class InboxSubmit(BaseModel):
    source: str
    delivery_id: str
    session_id: int
    body: str = ""
    decision: str
    generation: int | None = None
    automated: bool = False
    kind: str = ""
    action: str = "calendar.create"
    card: InboxCard | None = None


@router.post("/inbox")
def submit_inbox(
    body: InboxSubmit,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    card = None if body.card is None else body.card.model_dump()
    try:
        view = submit_event(
            db,
            user_id=current_user.id,
            source=body.source,
            delivery_id=body.delivery_id,
            session_id=body.session_id,
            body=body.body,
            model_decision=body.decision,
            generation=body.generation,
            automated=body.automated,
            kind=body.kind,
            action=body.action,
            card=card,
        )
    except EventRejected as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return view.as_dict()
