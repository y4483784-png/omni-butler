from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_session
from app.models.models import Message, Session as ChatSession, User
from app.services.message_format import serialize_message

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionRename(BaseModel):
    title: str


def _serialize(s: ChatSession) -> dict:
    return {"id": s.id, "title": s.title, "updated_at": s.updated_at.isoformat()}


@router.get("")
def list_sessions(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return [_serialize(s) for s in sessions]


@router.post("")
def create_session(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    s = ChatSession(user_id=current_user.id, title="新会话")
    db.add(s)
    db.commit()
    db.refresh(s)
    return _serialize(s)


@router.get("/{session_id}/messages")
def get_messages(
    session_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    require_owned_session(db, session_id, current_user.id)
    msgs = db.query(Message).filter(Message.session_id == session_id).order_by(Message.id).all()
    return [serialize_message(m) for m in msgs]


@router.patch("/{session_id}")
def rename(
    session_id: int,
    body: SessionRename,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    s = require_owned_session(db, session_id, current_user.id)
    s.title = body.title
    db.commit()
    return _serialize(s)


@router.delete("/{session_id}")
def delete(
    session_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    require_owned_session(db, session_id, current_user.id)
    db.query(Message).filter(Message.session_id == session_id).delete()
    s = db.get(ChatSession, session_id)
    if s:
        db.delete(s)
    db.commit()
    return {"ok": True}
