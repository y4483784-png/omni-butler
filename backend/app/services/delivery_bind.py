"""Parent-minted delivery ids.

The numeric pid and the Celery request id can recur inside one worker
incarnation. The parent stores the id on the Request object and in a
per-pid mailbox. Exit updates that id. The child claims the mailbox line
and checks it against the row.
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import TaskDelivery

_current: ContextVar[str | None] = ContextVar("omni_delivery", default=None)
_installed = False
_published_dir: str | None = None
_published_value: str | None = None


class IncarnationMissing(RuntimeError):
    """This parent has not published an incarnation, or the file is not that value."""


def _incarnation_file() -> Path:
    return _root() / "incarnation"


def publish_incarnation() -> str:
    """Mint the incarnation for this parent lifetime and replace any leftover file.

    A second call for the same directory in this process returns the value it
    already wrote. A prefork child must not call this. A new process does not
    keep a file left by the previous parent.
    """
    global _published_dir, _published_value
    directory = str(_root())
    if _published_dir == directory and _published_value:
        return _published_value
    value = uuid4().hex
    path = _incarnation_file()
    temporary = path.with_name(f".incarnation-{os.getpid()}-{uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)
    _published_dir = directory
    _published_value = value
    return value


def incarnation() -> str:
    path = _incarnation_file()
    if not path.is_file():
        raise IncarnationMissing()
    existing = path.read_text(encoding="utf-8").strip()
    if not existing:
        raise IncarnationMissing()
    return existing


def _require_published() -> str:
    directory = str(_root())
    if _published_dir != directory or not _published_value:
        raise IncarnationMissing()
    current = incarnation()
    if current != _published_value:
        raise IncarnationMissing()
    return current


def current_delivery() -> str | None:
    return _current.get()


def bind_delivery(delivery_id: str) -> None:
    _current.set(delivery_id)


def reset_delivery() -> None:
    _current.set(None)


def _root() -> Path:
    path = Path(os.environ.get("OMNI_DELIVERY_DIR", "/tmp/omni-deliveries"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mailbox(pid: int) -> Path:
    return _root() / f"{incarnation()}-{int(pid)}"


def _consumed(pid: int) -> Path:
    return _root() / f"{incarnation()}-{int(pid)}.consumed"


def _append(pid: int, delivery_id: str, request_id: str) -> None:
    with _mailbox(pid).open("a", encoding="utf-8") as handle:
        handle.write(f"{delivery_id} {request_id}\n")
        handle.flush()


def insert_delivery(
    db: Session,
    *,
    request_id: str,
    pid: int,
    task_run_id: int | None,
) -> str:
    delivery_id = uuid4().hex
    db.add(
        TaskDelivery(
            delivery_id=delivery_id,
            worker_incarnation=incarnation(),
            celery_request_id=str(request_id),
            child_pid=int(pid),
            task_run_id=None if task_run_id is None else int(task_run_id),
        )
    )
    db.flush()
    bind_delivery(delivery_id)
    return delivery_id


def ensure_delivery(db: Session, *, task_run_id: int) -> str | None:
    found = current_delivery()
    if found:
        return found
    if not settings.celery_task_always_eager:
        return None
    publish_incarnation()
    return insert_delivery(
        db,
        request_id=f"eager-{uuid4().hex}",
        pid=os.getpid(),
        task_run_id=task_run_id,
    )


def mint_for_request(db: Session, request, pid: int, task_run_id: int | None) -> str:
    _require_published()
    delivery_id = insert_delivery(
        db,
        request_id=str(request.id),
        pid=int(pid),
        task_run_id=task_run_id,
    )
    request._omni_delivery_id = delivery_id
    _append(int(pid), delivery_id, str(request.id))
    db.commit()
    return delivery_id


def mark_exit(db: Session, delivery_id: str, exitcode: str) -> int:
    if not delivery_id:
        return 0
    result = db.execute(
        update(TaskDelivery)
        .where(TaskDelivery.delivery_id == str(delivery_id))
        .where(TaskDelivery.exited_at.is_(None))
        .values(exited_at=datetime.now(timezone.utc).replace(tzinfo=None), exitcode=exitcode[:200])
    )
    db.commit()
    return int(result.rowcount or 0)


def exit_request(db: Session, request, exitcode: str) -> int:
    delivery_id = getattr(request, "_omni_delivery_id", None)
    if not delivery_id:
        return 0
    return mark_exit(db, str(delivery_id), exitcode)


def claim_mailbox(db: Session, request_id: str, pid: int, *, wait_seconds: float = 2.0) -> str | None:
    """Bind the parent delivery for this request and pid.

    The pool can enter the child before ``on_accepted`` returns, so the
    mailbox line and its committed row can appear a moment later. A miss
    after the wait returns None. The caller rejects the task instead of
    treating that as a completed consume.
    """
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        chosen = _claim_once(db, request_id, pid)
        if chosen is not None:
            return chosen
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def _claim_once(db: Session, request_id: str, pid: int) -> str | None:
    db.rollback()
    try:
        path = _mailbox(pid)
    except IncarnationMissing:
        return None
    if not path.exists():
        return None
    consumed: set[str] = set()
    marker = _consumed(pid)
    if marker.exists():
        consumed = {line.strip() for line in marker.read_text(encoding="utf-8").splitlines() if line.strip()}
    chosen: str | None = None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        parts = line.split()
        if len(parts) != 2:
            continue
        delivery_id, req = parts
        if req != str(request_id) or delivery_id in consumed:
            continue
        row = db.get(TaskDelivery, delivery_id)
        if row is None or row.exited_at is not None:
            continue
        if row.worker_incarnation != incarnation() or row.celery_request_id != str(request_id):
            continue
        chosen = delivery_id
        break
    if chosen is None:
        return None
    with marker.open("a", encoding="utf-8") as handle:
        handle.write(chosen + "\n")
    bind_delivery(chosen)
    return chosen


def install_parent_hooks() -> None:
    global _installed
    publish_incarnation()
    if _installed:
        return
    from celery.worker.request import Request

    from app.core.db import SessionLocal

    original_accept = Request.on_accepted
    original_failure = Request.on_failure

    def accept(self, pid, time_accepted):
        result = original_accept(self, pid, time_accepted)
        task_run_id = None
        args = getattr(self, "args", None) or ()
        if args:
            try:
                task_run_id = int(args[0])
            except (TypeError, ValueError):
                task_run_id = None
        db = SessionLocal()
        try:
            mint_for_request(db, self, int(pid), task_run_id)
        finally:
            db.close()
        return result

    def failed(self, exc_info, send_failed_event=True, return_ok=False):
        try:
            exc = getattr(exc_info, "exception", None)
            if type(exc).__name__ == "ExceptionWithTraceback":
                exc = getattr(exc, "exc", exc)
            name = type(exc).__name__ if exc is not None else ""
            if name == "WorkerLostError":
                db = SessionLocal()
                try:
                    exit_request(db, self, str(exc))
                finally:
                    db.close()
        finally:
            return original_failure(
                self,
                exc_info,
                send_failed_event=send_failed_event,
                return_ok=return_ok,
            )

    Request.on_accepted = accept
    Request.on_failure = failed
    _installed = True
