"""Record one job in the session. The tool loop is not in this unit."""

from __future__ import annotations

from app.core.db import SessionLocal, set_rls_context
from app.core.generation_stop import bind, reset
from app.models.models import Message
from app.services import task_run
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.job.run_job", queue="default")
def run_job(
    user_id: int,
    session_id: int,
    goal: str,
    web_constraint: str = "allow",
    web_constraint_message_id: int | None = None,
) -> None:
    db = SessionLocal()
    turn = None
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
        text = (goal or "").strip()
        db.add(
            Message(
                session_id=int(session_id),
                role="assistant",
                content=f"已开始：{text}" if text else "已开始。",
            )
        )
        db.commit()
    finally:
        db.close()
    if turn is None:
        return
    token = bind(user_id=int(user_id), generation=turn.generation)
    try:
        return
    finally:
        reset(token)
