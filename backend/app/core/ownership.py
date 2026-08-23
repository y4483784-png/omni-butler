"""Resource ownership checks for multi-user isolation."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import CalendarEvent, Document, MemoryItem, Message, Session as ChatSession


def require_owned_session(db: Session, session_id: int, user_id: int) -> ChatSession:
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


def require_owned_document(db: Session, doc_id: int, user_id: int) -> Document:
    doc = db.get(Document, doc_id)
    if not doc or doc.user_id != user_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


def require_owned_event(db: Session, event_id: int, user_id: int) -> CalendarEvent:
    event = db.get(CalendarEvent, event_id)
    if not event or event.user_id != user_id:
        raise HTTPException(status_code=404, detail="日程不存在")
    return event


def require_owned_message(db: Session, message_id: int, user_id: int) -> Message:
    m = db.get(Message, message_id)
    if not m:
        raise HTTPException(status_code=404, detail="消息不存在")
    require_owned_session(db, m.session_id, user_id)
    return m


def require_owned_memory(db: Session, memory_id: int, user_id: int) -> MemoryItem:
    row = db.get(MemoryItem, memory_id)
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return row
