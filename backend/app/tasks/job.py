from __future__ import annotations

from datetime import datetime, timezone

from app.core.db import SessionLocal, set_rls_context
from app.core.generation_stop import bind, reset
from app.models.models import Message
from app.services import task_run
from app.tasks.celery_app import celery_app


def commit_job_output(
    db,
    *,
    user_id: int,
    session_id: int,
    generation: int,
    message_id: int | None,
    content: str,
    now: datetime,
    previous_commit_at: datetime | None,
    newer: bool,
    call_ended: bool,
) -> tuple[int | None, datetime | None]:
    due = task_run.output_commit_due(
        have_output=bool(content),
        newer=newer,
        previous_commit_at=previous_commit_at,
        now=now,
        call_ended=call_ended,
    )
    if not due:
        return message_id, previous_commit_at
    if message_id is None:
        row = Message(
            session_id=int(session_id),
            role="assistant",
            content=content,
            reasoning=None,
            citations="",
            schedule_card="",
            artifact="",
        )
        db.add(row)
        db.flush()
        message_id = int(row.id)
    else:
        row = db.get(Message, message_id)
        row.content = content
    task_run.touch_progress(
        db,
        user_id=int(user_id),
        generation=int(generation),
        now=now,
    )
    db.commit()
    return message_id, now


@celery_app.task(name="app.tasks.job.run_job", queue="default")
def run_job(
    user_id: int,
    session_id: int,
    goal: str,
    web_constraint: str = "allow",
    web_constraint_message_id: int | None = None,
) -> None:
    db = SessionLocal()
    try:
        set_rls_context(db, int(user_id))
        turn = task_run.open_alongside(
            db,
            user_id=int(user_id),
            session_id=int(session_id),
            kind="job",
            goal=goal or "",
            web_constraint=web_constraint or "allow",
            web_constraint_message_id=web_constraint_message_id,
        )
        token = bind(user_id=int(user_id), generation=turn.generation)
        try:
            text = (goal or "").strip()
            commit_job_output(
                db,
                user_id=int(user_id),
                session_id=int(session_id),
                generation=int(turn.generation),
                message_id=None,
                content=f"已开始：{text}" if text else "已开始。",
                now=datetime.now(timezone.utc),
                previous_commit_at=None,
                newer=True,
                call_ended=True,
            )
        finally:
            reset(token)
    finally:
        db.close()
