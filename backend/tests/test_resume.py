from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.db import SessionLocal, set_rls_context
from app.core.llm import ModelTurn
from app.models.models import Message, Session as ChatSession, SessionCursor, SessionEvent, TaskRun, User
from app.services.resume import dispatch_task, enqueue_job, publish_event, read_snapshot
from app.tasks.job import run_job, set_executor


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"resume-{uuid4().hex}"
        user = User(external_id=external_id, name=external_id, is_active=1, password_hash="")
        db.add(user)
        db.commit()
        db.refresh(user)
        return int(user.id)
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="resume")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def test_seq_100_blocks_the_publisher_of_seq_101():
    user_id = _user()
    session_id = _session(user_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        db.add(SessionCursor(session_id=session_id, user_id=user_id, published_seq=99))
        db.commit()
    finally:
        db.close()

    first = SessionLocal()
    set_rls_context(first, user_id)
    allocated = publish_event(
        first,
        user_id=user_id,
        session_id=session_id,
        kind="status",
        body="held",
    )
    assert allocated == 100

    started = threading.Event()
    finished = threading.Event()
    box: dict = {}

    def second() -> None:
        other = SessionLocal()
        try:
            set_rls_context(other, user_id)
            started.set()
            box["seq"] = publish_event(
                other,
                user_id=user_id,
                session_id=session_id,
                kind="status",
                body="next",
            )
            other.commit()
        except Exception as exc:
            box["error"] = exc
            other.rollback()
        finally:
            other.close()
            finished.set()

    thread = threading.Thread(target=second)
    thread.start()
    assert started.wait(2)
    time.sleep(0.4)
    assert not finished.is_set()
    assert "seq" not in box
    first.commit()
    first.close()
    assert finished.wait(5)
    thread.join(timeout=5)
    assert "error" not in box
    assert box["seq"] == 101

    check = SessionLocal()
    try:
        set_rls_context(check, user_id)
        rows = (
            check.query(SessionEvent)
            .filter(SessionEvent.session_id == session_id)
            .order_by(SessionEvent.seq.asc())
            .all()
        )
        stored = [int(row.seq) for row in rows]
        event_ids = [int(row.id) for row in rows]
        cursor = check.get(SessionCursor, session_id)
        published = int(cursor.published_seq)
        snapshot = read_snapshot(check, user_id=user_id, session_id=session_id)
        check.commit()
    finally:
        check.close()
    assert stored == [100, 101]
    assert published == 101
    assert snapshot["cursor"] == published
    assert snapshot["cursor"] not in event_ids


@pytest.fixture(autouse=True)
def _quiet_executor():
    set_executor(None)
    yield
    set_executor(None)


def _open_job(user_id: int, session_id: int, *, goal: str = "alpha") -> int:
    from app.services import task_run

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        turn = task_run.open_alongside(
            db,
            user_id=user_id,
            session_id=session_id,
            kind="job",
            goal=goal,
        )
        db.commit()
        return int(turn.id)
    finally:
        db.close()


def _load(task_id: int) -> TaskRun:
    db = SessionLocal()
    try:
        row = db.get(TaskRun, int(task_id))
        db.expunge(row)
        return row
    finally:
        db.close()


def _notes(session_id: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Message)
            .filter(Message.session_id == session_id, Message.role == "assistant")
            .order_by(Message.id.asc())
            .all()
        )
        return [row.content for row in rows]
    finally:
        db.close()


def test_the_sweep_publishes_the_stored_token_and_the_worker_runs():
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    seen: dict = {}

    def executor(messages, _tools):
        seen["exec_token"] = _load(task_id).exec_token
        seen["goal"] = messages[0]["content"]
        return ModelTurn("done", "", (), "stop")

    published: list = []

    def spy(*args, **_kwargs):
        if args[0] != task_id:
            return None
        published.append(args)
        reserved = _load(task_id)
        assert reserved.dispatch_token == args[1]
        assert reserved.exec_token is None
        assert reserved.status == "running"
        return run_job(*args)

    set_executor(executor)
    with patch("app.tasks.job.run_job.delay", side_effect=spy):
        run_sweep()
    assert len(published) == 1
    assert seen["exec_token"]
    assert seen["goal"] == "alpha"
    finished = _load(task_id)
    assert finished.status == "completed"
    assert finished.exec_token is None


def test_a_new_job_reserves_without_waiting_for_the_beat():
    user_id = _user()
    session_id = _session(user_id)
    with patch("app.tasks.watchdog.run_sweep", side_effect=AssertionError("sweep")):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            task_id = enqueue_job(db, user_id=user_id, session_id=session_id, goal="alpha")
        finally:
            db.close()
    row = _load(task_id)
    assert row.status == "running"
    assert row.exec_token
    assert row.dispatch_token is None
    assert _notes(session_id) == ["已开始：alpha"]


def test_a_crash_before_publish_keeps_exec_token_null_and_a_later_token_adopts():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, task_id)
        row.continuation_due = True
        db.commit()
    finally:
        db.close()

    published: list = []

    def broker(*args, **_kwargs):
        published.append(args)
        raise RuntimeError("broker")

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with patch("app.tasks.job.run_job.delay", side_effect=broker):
            with pytest.raises(RuntimeError, match="broker"):
                dispatch_task(db, task_id)
    finally:
        db.close()

    reserved = _load(task_id)
    assert reserved.exec_token is None
    assert reserved.status == "running"
    assert reserved.continuation_due is True
    assert reserved.dispatch_token == published[0][1]
    first_token = reserved.dispatch_token
    first_epoch = int(reserved.dispatch_epoch)
    intent = int(reserved.intent_version)

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, task_id)
        row.dispatch_until = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()

    adopted: list = []

    def deliver(*args, **_kwargs):
        adopted.append(args)
        return run_job(*args)

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with patch("app.tasks.job.run_job.delay", side_effect=deliver):
            assert dispatch_task(db, task_id) == 1
    finally:
        db.close()

    running = _load(task_id)
    assert running.status == "running"
    assert running.exec_token
    assert adopted[0][1] != first_token
    epoch = int(running.exec_epoch)
    run_job(task_id, first_token, first_epoch, intent)
    again = _load(task_id)
    assert again.exec_epoch == epoch
    assert again.exec_token == running.exec_token
    assert again.status == "running"


def test_a_live_holder_publishes_nothing():
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        task_id = enqueue_job(db, user_id=user_id, session_id=session_id, goal="alpha")
    finally:
        db.close()
    held = _load(task_id)
    assert held.exec_token
    assert held.hold_until > datetime.now(timezone.utc).replace(tzinfo=None)
    published: list = []

    def spy(*args, **_kwargs):
        if args and args[0] == task_id:
            published.append(args)
        return None

    with patch("app.tasks.job.run_job.delay", side_effect=spy):
        run_sweep()
    assert published == []
    again = _load(task_id)
    assert again.exec_token == held.exec_token
    assert again.dispatch_token is None
    assert again.status == "running"


def test_two_deliveries_of_one_token_adopt_once():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    calls: list[int] = []

    def executor(_messages, _tools):
        calls.append(1)
        raise SystemExit(4)

    published: list = []

    def deliver(*args, **_kwargs):
        published.append(args)
        return run_job(*args)

    set_executor(executor)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with patch("app.tasks.job.run_job.delay", side_effect=deliver):
            with pytest.raises(SystemExit):
                dispatch_task(db, task_id)
    finally:
        db.close()
    assert len(published) == 1
    assert calls == [1]
    adopted = _load(task_id)
    assert adopted.exec_token
    run_job(*published[0])
    again = _load(task_id)
    assert again.exec_epoch == adopted.exec_epoch
    assert again.exec_token == adopted.exec_token
    assert calls == [1]


def test_waiting_person_is_reserved_only_when_continuation_is_due():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, task_id)
        row.status = "waiting_person"
        row.continuation_due = False
        row.exec_token = None
        row.dispatch_token = None
        db.commit()
    finally:
        db.close()

    published: list = []
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with patch("app.tasks.job.run_job.delay", side_effect=lambda *args, **_kwargs: published.append(args)):
            assert dispatch_task(db, task_id) == 0
    finally:
        db.close()
    assert published == []
    assert _load(task_id).status == "waiting_person"

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, task_id)
        row.continuation_due = True
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with patch("app.tasks.job.run_job.delay", side_effect=lambda *args, **_kwargs: published.append(args)):
            assert dispatch_task(db, task_id) == 1
    finally:
        db.close()
    reserved = _load(task_id)
    assert reserved.status == "running"
    assert reserved.continuation_due is True
    assert reserved.exec_token is None
    assert reserved.dispatch_token == published[0][1]


def test_waiting_resource_is_not_reserved():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, task_id)
        row.status = "waiting_resource"
        row.continuation_due = True
        row.exec_token = None
        row.dispatch_token = None
        db.commit()
    finally:
        db.close()
    published: list = []
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with patch("app.tasks.job.run_job.delay", side_effect=lambda *args, **_kwargs: published.append(args)):
            assert dispatch_task(db, task_id) == 0
    finally:
        db.close()
    assert published == []
    assert _load(task_id).status == "waiting_resource"
    assert _load(task_id).exec_token is None
