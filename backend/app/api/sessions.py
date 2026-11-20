import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_session
from app.models.models import Message, Session as ChatSession, SessionCursor, SessionEvent, User
from app.services.message_format import serialize_message
from app.services.resume import (
    _STREAM_WAIT_SECONDS,
    discard_waiter,
    events_after,
    read_snapshot,
    register_waiter,
)

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


@router.get("/{session_id}/jobs")
def get_jobs(
    session_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    require_owned_session(db, session_id, current_user.id)
    snapshot = read_snapshot(db, user_id=int(current_user.id), session_id=int(session_id))
    db.commit()
    return snapshot


@router.get("/{session_id}/events")
def get_events(
    session_id: int,
    after: int = 0,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    require_owned_session(db, session_id, current_user.id)
    db.commit()
    user_id = int(current_user.id)
    owned_session = int(session_id)
    cursor = int(after)

    def _load(after_seq: int) -> list[dict]:
        from app.core.db import SessionLocal, set_rls_context

        owned = SessionLocal()
        try:
            set_rls_context(owned, user_id)
            rows = events_after(owned, session_id=owned_session, after=int(after_seq))
            owned.rollback()
            return rows
        finally:
            owned.close()

    async def stream():
        applied = cursor
        waiter = None
        try:
            waiter = register_waiter(owned_session)
            while True:
                waiter.clear()
                rows = await asyncio.to_thread(_load, applied)
                for item in rows:
                    seq = int(item["seq"])
                    if seq <= applied:
                        continue
                    applied = seq
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                try:
                    await asyncio.wait_for(waiter.wait(), timeout=_STREAM_WAIT_SECONDS)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            return
        finally:
            if waiter is not None:
                discard_waiter(owned_session, waiter)

    return StreamingResponse(stream(), media_type="text/event-stream")


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
    db.query(SessionEvent).filter(SessionEvent.session_id == session_id).delete()
    cursor = db.get(SessionCursor, session_id)
    if cursor is not None:
        db.delete(cursor)
    db.query(Message).filter(Message.session_id == session_id).delete()
    s = db.get(ChatSession, session_id)
    if s:
        db.delete(s)
    db.commit()
    return {"ok": True}
