from __future__ import annotations

import logging

from app.core.db import maintenance_session, set_rls_context
from app.models.models import TASK_RUN_STATUSES, Message, TaskRun
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_EFFECT: dict[str, str] = {
    "running": "leave",
    "waiting_person": "leave",
    "waiting_resource": "leave",
    "completed": "leave",
    "stopped": "leave",
}


def _validate_effect() -> None:
    if set(_EFFECT) != set(TASK_RUN_STATUSES):
        raise RuntimeError("watchdog effect table statuses drifted")
    for status, effect in _EFFECT.items():
        if effect not in {"stop", "leave"}:
            raise RuntimeError("watchdog effect is unknown")
        if status == "running":
            if effect != "leave":
                raise RuntimeError("running watchdog effect is not leave")
        elif effect != "leave":
            raise RuntimeError("non-running watchdog effect is not leave")


_validate_effect()


def load_open_ids(db) -> tuple[int, ...]:
    rows = (
        db.query(TaskRun.id)
        .filter(TaskRun.status.in_(("running", "waiting_person", "waiting_resource")))
        .all()
    )
    return tuple(int(row[0]) for row in rows)


def notice_running(db, task_run_id: int) -> None:
    row = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None or row.status != "running":
        db.rollback()
        return
    set_rls_context(db, int(row.user_id))
    stamp = row.last_progress_at
    if stamp is not None and stamp == row.observed_progress_at and stamp != row.notice_progress_at:
        shown = stamp.strftime("%Y-%m-%d %H:%M:%S")
        db.add(
            Message(
                session_id=row.session_id,
                role="assistant",
                content=f"这一任务自 {shown} 起没有新的进展，仍在运行。generation={row.generation}",
                reasoning=None,
                citations="",
                schedule_card="",
                artifact="",
            )
        )
        row.notice_progress_at = stamp
    row.observed_progress_at = stamp
    db.commit()


def run_sweep() -> None:
    db = maintenance_session()
    try:
        try:
            ids = load_open_ids(db)
        except Exception:
            logger.exception("watchdog load failed")
            db.rollback()
            return
        for task_run_id in ids:
            try:
                notice_running(db, task_run_id)
            except Exception:
                logger.exception("watchdog notice failed task_run_id=%s", task_run_id)
                db.rollback()
        for task_run_id in ids:
            try:
                from app.services.resume import dispatch_task

                dispatch_task(db, int(task_run_id))
            except Exception:
                logger.exception("dispatch failed task_run_id=%s", task_run_id)
                db.rollback()
    finally:
        db.close()


@celery_app.task(name="app.tasks.watchdog.sweep_task", queue="maintenance")
def sweep_task() -> None:
    run_sweep()
    try:
        from app.services.task_images import reconcile_task_images

        reconcile_task_images()
    except Exception:
        logger.exception("task image reconcile failed")
