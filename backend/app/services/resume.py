from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.config import settings
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

_RESERVE_IDLE = """
UPDATE task_runs
SET dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :until
WHERE id = :id
  AND status = 'running'
  AND exec_token IS NULL
  AND (dispatch_token IS NULL OR dispatch_until < :now)
"""

_RETIRE_HOLDER = """
UPDATE task_runs
SET exec_token = NULL,
    hold_until = NULL,
    dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :until
WHERE id = :id
  AND status = 'running'
  AND exec_token IS NOT NULL
  AND hold_until < :now
"""

_RESERVE_WAITING = """
UPDATE task_runs
SET status = 'running',
    dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :until
WHERE id = :id
  AND status = 'waiting_person'
  AND continuation_due = true
  AND exec_token IS NULL
  AND (dispatch_token IS NULL OR dispatch_until < :now)
"""

_ADOPT = """
UPDATE task_runs
SET exec_token = :worker,
    exec_epoch = exec_epoch + 1,
    hold_until = :until,
    dispatch_token = NULL,
    dispatch_until = NULL,
    continuation_due = false
WHERE id = :id
  AND status = 'running'
  AND dispatch_token = :token
  AND dispatch_epoch = :epoch
  AND intent_version = :intent
  AND dispatch_until >= :now
  AND exec_token IS NULL
"""


def _clock(now: datetime | None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.replace(microsecond=0)


def _window(now: datetime | None) -> tuple[datetime, datetime]:
    moment = _clock(now)
    return moment, moment + timedelta(seconds=float(settings.llm_timeout))


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


def reserve_idle(db: Session, *, task_run_id: int, token: str, now: datetime | None = None) -> int:
    moment, until = _window(now)
    result = db.execute(
        text(_RESERVE_IDLE),
        {"id": int(task_run_id), "token": token, "now": moment, "until": until},
    )
    return int(result.rowcount or 0)


def retire_holder(db: Session, *, task_run_id: int, token: str, now: datetime | None = None) -> int:
    moment, until = _window(now)
    result = db.execute(
        text(_RETIRE_HOLDER),
        {"id": int(task_run_id), "token": token, "now": moment, "until": until},
    )
    return int(result.rowcount or 0)


def reserve_waiting(db: Session, *, task_run_id: int, token: str, now: datetime | None = None) -> int:
    moment, until = _window(now)
    result = db.execute(
        text(_RESERVE_WAITING),
        {"id": int(task_run_id), "token": token, "now": moment, "until": until},
    )
    return int(result.rowcount or 0)


def adopt(
    db: Session,
    *,
    task_run_id: int,
    token: str,
    epoch: int,
    intent_version: int,
    worker: str,
    now: datetime | None = None,
) -> int:
    moment, until = _window(now)
    result = db.execute(
        text(_ADOPT),
        {
            "id": int(task_run_id),
            "token": token,
            "epoch": int(epoch),
            "intent": int(intent_version),
            "worker": worker,
            "now": moment,
            "until": until,
        },
    )
    return int(result.rowcount or 0)


def dispatch_task(db: Session, task_run_id: int, *, now: datetime | None = None) -> int:
    token = uuid4().hex
    for attempt in (reserve_idle, retire_holder, reserve_waiting):
        matched = attempt(db, task_run_id=int(task_run_id), token=token, now=now)
        if matched != 1:
            db.rollback()
            continue
        db.commit()
        _publish_run(db, int(task_run_id), token)
        return 1
    return 0


def _publish_run(db: Session, task_run_id: int, token: str) -> None:
    row = db.get(TaskRun, int(task_run_id))
    if row is None or row.dispatch_token != token:
        return
    from app.tasks.job import run_job

    run_job.delay(
        int(row.id),
        str(row.dispatch_token),
        int(row.dispatch_epoch or 0),
        int(row.intent_version or 1),
    )


def enqueue_job(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    goal: str,
    web: str = "allow",
    message_id: int | None = None,
) -> int:
    from app.services import task_run

    turn = task_run.open_alongside(
        db,
        user_id=int(user_id),
        session_id=int(session_id),
        kind="job",
        goal=goal or "",
        web_constraint=web or "allow",
        web_constraint_message_id=message_id,
    )
    task_id = int(turn.id)
    db.commit()
    dispatch_task(db, task_id)
    return task_id

