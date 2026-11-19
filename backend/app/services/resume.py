from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.models.models import SESSION_EVENT_KINDS, ScheduleConfirmClaim, SessionEvent, TaskRun

_INSERT_CURSOR_POSTGRES = """
INSERT INTO session_cursors (session_id, user_id, published_seq)
VALUES (:session, :user_id, 0)
ON CONFLICT (session_id) DO NOTHING
"""

_INSERT_CURSOR_SQLITE = """
INSERT OR IGNORE INTO session_cursors (session_id, user_id, published_seq)
VALUES (:session, :user_id, 0)
"""

_LOCK_CURSOR_POSTGRES = """
SELECT published_seq FROM session_cursors WHERE session_id = :session FOR UPDATE
"""

_LOCK_CURSOR_SQLITE = """
UPDATE session_cursors SET published_seq = published_seq WHERE session_id = :session
"""

_READ_CURSOR = """
SELECT published_seq FROM session_cursors WHERE session_id = :session
"""

_BUMP_CURSOR = """
UPDATE session_cursors SET published_seq = published_seq + 1 WHERE session_id = :session
"""


def _dialect(db: Session) -> str:
    bind = db.get_bind()
    return "" if bind is None else bind.dialect.name


def _begin_publication(db: Session) -> None:
    """SQLite publication starts with BEGIN IMMEDIATE. PostgreSQL locks on FOR UPDATE."""
    if _dialect(db) != "sqlite":
        return
    connection = db.connection()
    raw = connection.connection.dbapi_connection
    if raw.in_transaction:
        return
    raw.isolation_level = None
    connection.exec_driver_sql("BEGIN IMMEDIATE")

    def _restore(session, transaction) -> None:
        if transaction.parent is not None:
            event.listen(session, "after_transaction_end", _restore, once=True)
            return
        raw.isolation_level = ""

    event.listen(db, "after_transaction_end", _restore, once=True)


def _ensure_cursor(db: Session, *, user_id: int, session_id: int) -> None:
    statement = _INSERT_CURSOR_POSTGRES if _dialect(db) == "postgresql" else _INSERT_CURSOR_SQLITE
    db.execute(text(statement), {"session": int(session_id), "user_id": int(user_id)})


def _locked_seq(db: Session, *, session_id: int) -> int:
    params = {"session": int(session_id)}
    if _dialect(db) == "postgresql":
        return int(db.execute(text(_LOCK_CURSOR_POSTGRES), params).scalar_one())
    db.execute(text(_LOCK_CURSOR_SQLITE), params)
    return int(db.execute(text(_READ_CURSOR), params).scalar_one())


def publish_event(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    kind: str,
    body: str,
) -> int:
    if kind not in SESSION_EVENT_KINDS:
        raise ValueError(kind)
    _begin_publication(db)
    _ensure_cursor(db, user_id=int(user_id), session_id=int(session_id))
    current = _locked_seq(db, session_id=int(session_id))
    seq = current + 1
    db.add(
        SessionEvent(
            user_id=int(user_id),
            session_id=int(session_id),
            seq=seq,
            kind=kind,
            body=body if isinstance(body, str) else "",
        )
    )
    db.execute(text(_BUMP_CURSOR), {"session": int(session_id)})
    db.flush()
    return seq


def read_snapshot(db: Session, *, user_id: int, session_id: int) -> dict:
    _begin_publication(db)
    _ensure_cursor(db, user_id=int(user_id), session_id=int(session_id))
    published_seq = _locked_seq(db, session_id=int(session_id))
    jobs = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == int(user_id), TaskRun.session_id == int(session_id))
        .order_by(TaskRun.generation.asc())
        .all()
    )
    claims = (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == int(user_id),
            ScheduleConfirmClaim.session_id == int(session_id),
            ScheduleConfirmClaim.status == "open",
        )
        .order_by(ScheduleConfirmClaim.created_at.asc())
        .all()
    )
    return {
        "cursor": published_seq,
        "jobs": [
            {
                "id": int(row.id),
                "status": row.status,
                "goal": row.goal or "",
                "generation": int(row.generation),
                "kind": row.kind,
            }
            for row in jobs
        ],
        "claims": [
            {"token": row.token, "status": row.status, "action": row.action}
            for row in claims
        ],
    }

