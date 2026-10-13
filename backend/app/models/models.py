from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.core.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    external_id = Column(String, unique=True, index=True)  # login username
    name = Column(String, default="")
    password_hash = Column(String, default="")
    is_active = Column(Integer, default=1)  # 1=active (SQLite-friendly bool)
    is_admin = Column(Integer, default=0)


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    title = Column(String, default="新会话")
    pending_calendar = Column(Text, default="")  # JSON draft while a calendar flow is incomplete
    # ContextManager (H5): compacted older turns + working-state metadata (not UI history)
    context_summary = Column(Text, default="")
    context_summary_upto_message_id = Column(Integer, default=0)
    working_state = Column(Text, default="")  # JSON: evidence refs, last analysis, doc ids
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), index=True)
    role = Column(String)  # user | assistant
    content = Column(Text)
    citations = Column(Text, default="")  # JSON list of {index, filename, snippet}
    schedule_card = Column(Text, default="")  # JSON object for calendar action card
    artifact = Column(Text, default="")  # JSON: kind/title/language/content for chart/code panel
    feedback = Column(String, default="")  # "" | up | down
    created_at = Column(DateTime, default=_utcnow)


class Document(Base):
    """Knowledge-base file metadata (Phase 2 min slice)."""

    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, default=1)
    filename = Column(String)
    stored_path = Column(String, default="")  # MinIO/S3 object key; legacy rows may hold absolute paths
    status = Column(String, default="pending")  # pending | processing | ready | failed
    stage = Column(String, default="pending")  # pending | parsing | chunking | embedding | ready | failed
    error = Column(String, default="")
    chunk_count = Column(Integer, default=0)
    char_count = Column(Integer, default=0)
    warning = Column(String, default="")  # e.g. low text / OCR used
    parser_version = Column(Integer, default=0)
    ingest_task_id = Column(String, default="", index=True)
    ingest_started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class Chunk(Base):
    """Text chunk stored in SQLite for keyword retrieval (Qdrant later)."""

    __tablename__ = "chunks"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    chunk_index = Column(Integer, default=0)
    content = Column(Text)
    kind = Column(String, default="text")  # text | heading | table | ocr | schema | page
    heading = Column(String, default="")
    page = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class CalendarEvent(Base):
    """Local calendar event for Phase 3 calendar actions."""

    __tablename__ = "calendar_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, default=1)
    title = Column(String, default="")
    start_at = Column(DateTime, index=True)
    end_at = Column(DateTime, index=True)
    participants = Column(Text, default="[]")  # JSON string list
    status = Column(String, default="active")  # active | cancelled
    created_at = Column(DateTime, default=_utcnow)


class ToolAuditLog(Base):
    """Durable harness/tool audit (Open WebUI-style). JSONL remains a local backup."""

    __tablename__ = "tool_audit_logs"
    id = Column(Integer, primary_key=True)
    request_id = Column(String, default="", index=True)
    user_id = Column(Integer, index=True, nullable=True)
    tool = Column(String, default="")
    decision = Column(String, default="")  # allow | deny | error
    risk = Column(String, default="")
    reason = Column(String, default="")
    engine = Column(String, default="")
    ok = Column(Integer, nullable=True)
    evidence_count = Column(Integer, default=0)
    elapsed_ms = Column(Integer, default=0)
    args = Column(Text, default="{}")
    created_at = Column(DateTime, default=_utcnow, index=True)


class MemoryItem(Base):
    """Cross-session long-term memory (LangGraph-store inspired: user namespace + key)."""

    __tablename__ = "memories"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, default=1)
    kind = Column(String, default="preference")  # identity | preference | entity
    key = Column(String, default="")  # upsert key within (user_id, kind)
    content = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
