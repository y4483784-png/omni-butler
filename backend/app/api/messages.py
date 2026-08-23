from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_message
from app.models.models import Message, User
from app.services.message_format import serialize_message

router = APIRouter(prefix="/api/messages", tags=["messages"])


class FeedbackBody(BaseModel):
    rating: str | None = None  # up | down | null to clear


@router.patch("/{message_id}/feedback")
def set_feedback(
    message_id: int,
    body: FeedbackBody,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    m = require_owned_message(db, message_id, current_user.id)
    if m.role != "assistant":
        raise HTTPException(status_code=400, detail="仅可对助手回复评价")

    rating = body.rating
    if rating is not None and rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 须为 up、down 或 null")

    m.feedback = rating or ""
    db.commit()
    db.refresh(m)
    return serialize_message(m)
