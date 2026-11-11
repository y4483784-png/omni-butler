from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

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
    reasoning = Column(Text, nullable=True)
    citations = Column(Text, default="")  # JSON list of {index, filename, snippet}
    schedule_card = Column(Text, default="")  # JSON object for calendar action card
    artifact = Column(Text, default="")  # JSON: kind/title/language/content for chart/code panel
    feedback = Column(String, default="")  # "" | up | down
    created_at = Column(DateTime, default=_utcnow)


class ScheduleConfirmClaim(Base):
    __tablename__ = "schedule_confirm_claims"
    __table_args__ = (
        Index(
            "uq_schedule_confirm_open_write",
            "user_id",
            "action",
            "fingerprint",
            unique=True,
            sqlite_where=text("status IN ('open', 'unknown')"),
            postgresql_where=text("status IN ('open', 'unknown')"),
        ),
    )
    token = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), index=True)
    status = Column(String, default="open")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    action = Column(String, nullable=False, default="")
    fingerprint = Column(String, nullable=False, default="")
    scope = Column(String, nullable=False, default="")
    generation_ids = Column(Text, nullable=False, default="[]")
    remote_request_id = Column(String, nullable=False, default="")


TASK_RUN_STATUSES = (
    "running",
    "waiting_person",
    "waiting_resource",
    "completed",
    "stopped",
)


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(repr(status) for status in TASK_RUN_STATUSES)),
            name="ck_task_runs_status",
        ),
        UniqueConstraint("user_id", "generation", name="uq_task_runs_user_generation"),
        Index("ix_task_runs_session_status", "user_id", "session_id", "status"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    generation = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    allowed_tools = Column(Text, nullable=False)
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
    __table_args__ = (
        Index(
            "uq_calendar_events_external",
            "user_id",
            "provider",
            "external_id",
            unique=True,
            sqlite_where=text("external_id <> ''"),
            postgresql_where=text("external_id <> ''"),
        ),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, default=1)
    title = Column(String, default="")
    start_at = Column(DateTime, index=True)
    end_at = Column(DateTime, index=True)
    participants = Column(Text, default="[]")  # JSON string list
    status = Column(String, default="active")  # active | cancelled
    # Reserved for an external calendar. Local rows keep these at the defaults.
    timezone = Column(String, default="")
    location = Column(String, default="")
    description = Column(Text, default="")
    external_id = Column(String, default="")
    provider = Column(String, default="local")
    created_at = Column(DateTime, default=_utcnow)


class CalendarConnection(Base):
    """One Google account per user. Access and refresh tokens are Fernet ciphertext."""

    __tablename__ = "calendar_connections"
    __table_args__ = (
        CheckConstraint("provider = 'google'", name="ck_calendar_connections_provider"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    provider = Column(String, nullable=False, default="google")
    google_sub = Column(String, nullable=False, default="")
    account_email = Column(String, nullable=False, default="")
    access_token = Column(Text, nullable=False, default="")
    refresh_token = Column(Text, nullable=False, default="")
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


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


EVENT_RECEIPT_STATES = (
    "received",
    "dispatched",
    "external_success",
)

EVENT_RECEIPT_ROUTES = (
    "noise",
    "held",
    "passthrough",
    "opened",
    "stopped",
    "queued",
    "kept",
    "reissued",
    "filed",
)


class EventReceipt(Base):
    """One inbound delivery. State is not a task status.

    ``received`` is acceptance into the inbox. ``dispatched`` is a new task.
    ``external_success`` is a confirmed write the other side accepted.
    ``route`` records which inbox rule fired. It does not merge ``completed``
    and ``stopped``.
    """

    __tablename__ = "event_receipts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "delivery_id",
            name="uq_event_receipts_delivery",
        ),
        CheckConstraint(
            "state IN ({})".format(", ".join(repr(item) for item in EVENT_RECEIPT_STATES)),
            name="ck_event_receipts_state",
        ),
        CheckConstraint(
            "route IN ({})".format(", ".join(repr(item) for item in EVENT_RECEIPT_ROUTES)),
            name="ck_event_receipts_route",
        ),
        CheckConstraint(
            "state <> 'external_success' OR external_request_id <> ''",
            name="ck_event_receipts_external",
        ),
        Index("ix_event_receipts_user_generation", "user_id", "generation"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    source = Column(String, nullable=False)
    delivery_id = Column(String, nullable=False)
    fingerprint = Column(String, nullable=False, default="")
    state = Column(String, nullable=False, default="received")
    route = Column(String, nullable=False)
    generation = Column(Integer, nullable=True)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=True)
    claim_token = Column(String, nullable=False, default="")
    external_request_id = Column(String, nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class MailAcceptance(Base):
    __tablename__ = "mail_acceptances"
    __table_args__ = (
        UniqueConstraint("user_id", "remote_request_id", name="uq_mail_acceptances_remote"),
        UniqueConstraint("user_id", "delivery_id", name="uq_mail_acceptances_delivery"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    remote_request_id = Column(String, nullable=False)
    delivery_id = Column(String, nullable=False)
    recipients = Column(Text, nullable=False, default="[]")
    subject = Column(Text, nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow)


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


LEDGER_SOURCES = ("turn", "panel", "store", "settle")
LEDGER_ACTORS = ("turn", "person")
LEDGER_DECISIONS = ("restored", "discarded")
SOURCE_ACTOR = {
    "turn": "turn",
    "panel": "person",
    "store": "person",
    "settle": "person",
}
LEDGER_SOURCE_ACTOR_SQL = "(" + " OR ".join(
    f"(source = {source!r} AND actor = {actor!r})" for source, actor in SOURCE_ACTOR.items()
) + ")"


class MemoryLedger(Base):
    """One append-only statement of a memory change. Settle flips pending and decision only."""

    __tablename__ = "memory_ledger"
    __table_args__ = (
        CheckConstraint(
            "source IN ({})".format(", ".join(repr(item) for item in LEDGER_SOURCES)),
            name="ck_memory_ledger_source",
        ),
        CheckConstraint(
            "actor IN ({})".format(", ".join(repr(item) for item in LEDGER_ACTORS)),
            name="ck_memory_ledger_actor",
        ),
        CheckConstraint(LEDGER_SOURCE_ACTOR_SQL, name="ck_memory_ledger_source_actor"),
        CheckConstraint(
            "decision IS NULL OR decision IN ({})".format(
                ", ".join(repr(item) for item in LEDGER_DECISIONS)
            ),
            name="ck_memory_ledger_decision",
        ),
        CheckConstraint("pending IN (0, 1)", name="ck_memory_ledger_pending"),
        Index(
            "uq_memory_ledger_turn",
            "user_id",
            "source_ref",
            "kind",
            "key",
            unique=True,
            sqlite_where=text("source = 'turn'"),
            postgresql_where=text("source = 'turn'"),
        ),
        Index(
            "uq_memory_ledger_settle",
            "user_id",
            "source_ref",
            unique=True,
            sqlite_where=text("source = 'settle'"),
            postgresql_where=text("source = 'settle'"),
        ),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False, default="")
    key = Column(String, nullable=False, default="")
    source = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    before = Column(Text, nullable=False, default="")
    after = Column(Text, nullable=False, default="")
    pending = Column(Integer, nullable=False, default=0)
    decision = Column(String, nullable=True)
    source_ref = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
