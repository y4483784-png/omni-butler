"""A job stays running when a later turn starts."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.db import SessionLocal, set_rls_context
from app.models.models import CalendarEvent, Message, ScheduleConfirmClaim, Session as ChatSession, TaskRun, User
from app.services import task_run
from app.services.calendar import void_draft_schedule_cards
from app.services.schedule_confirm import arm_remote_claim, issue_schedule_claim


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"talk-{uuid4().hex}"
        user = User(external_id=external_id, name=external_id, is_active=1, password_hash="")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="talk")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _pair() -> tuple[int, int]:
    user_id = _user()
    return user_id, _session(user_id)


def _open_job(
    user_id: int,
    session_id: int,
    *,
    goal: str = "alpha",
    web: str = "allow",
) -> task_run.Turn:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = task_run.open_alongside(
            db,
            user_id=user_id,
            session_id=session_id,
            kind="job",
            goal=goal,
            web_constraint=web,
        )
        db.commit()
        return job
    finally:
        db.close()


def test_job_survives_begin_turn():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id)
    assert job.kind == "job"
    assert job.status == "running"
    assert job.goal == "alpha"
    assert job.web_constraint == "allow"
    assert job.intent_version == 1
    assert job.exec_token is None
    assert job.dispatch_token is None
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    assert turn.kind == "turn"
    assert turn.status == "running"
    kept = task_run.get(user_id=user_id, generation=job.generation)
    assert kept is not None
    assert kept.status == "running"
    assert kept.kind == "job"


def test_stopping_the_turn_leaves_the_job_running():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id)
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    stopped = task_run.stop(user_id=user_id, generation=turn.generation)
    assert stopped.status == "stopped"
    assert stopped.kind == "turn"
    kept = task_run.get(user_id=user_id, generation=job.generation)
    assert kept is not None
    assert kept.status == "running"


def test_continue_job_from_running_completed_or_stopped_rejects():
    user_id, session_id = _pair()
    running = task_run.begin_turn(user_id=user_id, session_id=session_id)
    with pytest.raises(task_run.LateResult) as caught:
        task_run.continue_job(user_id=user_id, generation=running.generation)
    assert caught.value.turn.status == "running"
    assert task_run.get(user_id=user_id, generation=running.generation).status == "running"

    task_run.finish(user_id=user_id, generation=running.generation)
    with pytest.raises(task_run.LateResult) as finished:
        task_run.continue_job(user_id=user_id, generation=running.generation)
    assert finished.value.turn.status == "completed"
    assert task_run.get(user_id=user_id, generation=running.generation).status == "completed"
    stayed = task_run.stop(user_id=user_id, generation=running.generation)
    assert stayed.status == "completed"

    other = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.stop(user_id=user_id, generation=other.generation)
    with pytest.raises(task_run.LateResult) as halted:
        task_run.continue_job(user_id=user_id, generation=other.generation)
    assert halted.value.turn.status == "stopped"
    assert task_run.get(user_id=user_id, generation=other.generation).status == "stopped"


def test_continue_job_from_waiting_returns_to_running():
    user_id, session_id = _pair()
    person = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.wait_for_person(user_id=user_id, generation=person.generation)
    resumed = task_run.continue_job(user_id=user_id, generation=person.generation)
    assert resumed.status == "running"

    resource = task_run.begin_turn(user_id=user_id, session_id=session_id)
    assert task_run.get(user_id=user_id, generation=person.generation).status == "stopped"
    task_run.wait_for_resource(user_id=user_id, generation=resource.generation)
    again = task_run.continue_job(user_id=user_id, generation=resource.generation)
    assert again.status == "running"


def test_event_inbox_open_alongside_still_inserts_a_turn():
    user_id, session_id = _pair()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        opened = task_run.open_alongside(db, user_id=user_id, session_id=session_id)
        db.commit()
    finally:
        db.close()
    assert opened.kind == "turn"
    assert opened.status == "running"
    job = _open_job(user_id, session_id, goal="kept")
    assert task_run.get(user_id=user_id, generation=opened.generation).status == "running"
    assert task_run.get(user_id=user_id, generation=job.generation).status == "running"


_STAMP = datetime(2020, 1, 2, 3, 4, 5)
_RAW = {
    "title": "talk confirm",
    "start_at": "2026-11-04T10:00:00",
    "end_at": "2026-11-04T11:00:00",
    "participants": [],
    "location": "",
    "description": "",
    "meet": False,
}


def _admin_id() -> int:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.external_id == "admin").one().id
    finally:
        db.close()


def _set_hold(user_id: int, generation: int, token: str, *, epoch: int = 0) -> None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
            .one()
        )
        row.exec_token = token
        row.exec_epoch = epoch
        row.last_progress_at = _STAMP
        db.commit()
    finally:
        db.close()


def _claim(token: str) -> ScheduleConfirmClaim:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        db.expunge(row)
        return row
    finally:
        db.close()


def _issue(user_id: int, session_id: int, task_id: int, *, title: str = "talk confirm") -> str:
    raw = {**_RAW, "title": title}
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        token, _expiry = issue_schedule_claim(
            db,
            user_id=user_id,
            session_id=session_id,
            action="calendar.create",
            raw=raw,
            task_run_ids=[task_id],
            expires_at=None,
        )
        return token
    finally:
        db.close()


def _card(session_id: int, role_token: str, title: str) -> None:
    db = SessionLocal()
    try:
        db.add(
            Message(
                session_id=session_id,
                role="assistant",
                content=title,
                schedule_card=json.dumps(
                    {"phase": "draft", "mode": "create", "title": title, "confirm_token": role_token},
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
    finally:
        db.close()


def _phase(session_id: int, token: str) -> str | None:
    db = SessionLocal()
    try:
        rows = db.query(Message).filter(Message.session_id == session_id, Message.role == "assistant").all()
    finally:
        db.close()
    for row in rows:
        card = json.loads(row.schedule_card or "{}")
        if isinstance(card, dict) and card.get("confirm_token") == token:
            return card.get("phase")
    return None


def test_supplement_changes_only_that_job_and_its_open_claim():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id, goal="alpha")
    other = _open_job(user_id, session_id, goal="beta", web="forbid")
    _set_hold(user_id, job.generation, "worker-old")
    armed = _issue(user_id, session_id, job.id, title="armed card")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        assert arm_remote_claim(
            db,
            armed,
            "remote-1",
            exec_token="worker-old",
            exec_epoch=0,
            intent_version=1,
        )
        assert (
            arm_remote_claim(
                db,
                armed,
                "remote-2",
                exec_token="worker-old",
                exec_epoch=0,
                intent_version=1,
            )
            is False
        )
    finally:
        db.close()
    armed_row = _claim(armed)
    assert armed_row.status == "unknown"
    assert armed_row.remote_request_id == "remote-1"
    assert armed_row.intent_version == 1
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        assert (
            task_run.renew_hold(
                db,
                user_id=user_id,
                generation=job.generation,
                exec_token="worker-old",
                exec_epoch=0,
                intent_version=1,
            )
            == 1
        )
    finally:
        db.close()
    renewed = task_run.get(user_id=user_id, generation=job.generation)
    assert renewed.last_progress_at == _STAMP
    assert renewed.hold_until is not None
    open_token = _issue(user_id, session_id, job.id, title="open card")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=job.generation,
            text="more",
            web="allow",
            web_constraint_message_id=9,
        )
        missed = task_run.renew_hold(
            db,
            user_id=user_id,
            generation=job.generation,
            exec_token="worker-old",
            exec_epoch=0,
            intent_version=1,
        )
    finally:
        db.close()
    assert applied.applied is True
    assert applied.task_run_id == job.id
    assert missed == 0
    current = task_run.get(user_id=user_id, generation=job.generation)
    assert current.status == "running"
    assert current.intent_version == 2
    assert current.exec_token is None
    assert current.hold_until is None
    assert current.continuation_due is True
    assert current.goal == "alphamore"
    assert current.web_constraint == "allow"
    assert current.web_constraint_message_id == 9
    untouched = task_run.get(user_id=user_id, generation=other.generation)
    assert untouched.status == "running"
    assert untouched.intent_version == 1
    assert untouched.goal == "beta"
    assert untouched.web_constraint == "forbid"
    kept = _claim(armed)
    assert kept.status == "unknown"
    assert kept.remote_request_id == "remote-1"
    assert kept.intent_version == 1
    cancelled = _claim(open_token)
    assert cancelled.status == "cancelled"
    assert cancelled.remote_request_id == ""


def test_redirect_to_forbid_denies_web_execution():
    user_id, session_id = _pair()
    allowed = _open_job(user_id, session_id, goal="look")
    _set_hold(user_id, allowed.generation, "worker-a")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        assert (
            task_run.claim_web_execution(
                db,
                user_id=user_id,
                generation=allowed.generation,
                exec_token="worker-a",
                exec_epoch=0,
                intent_version=1,
                tool="web",
            )
            == 1
        )
        assert (
            task_run.claim_web_execution(
                db,
                user_id=user_id,
                generation=allowed.generation,
                exec_token="worker-a",
                exec_epoch=0,
                intent_version=1,
                tool="calendar",
            )
            == 0
        )
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=allowed.generation,
            text="stop searching",
            web="forbid",
            web_constraint_message_id=4,
        )
    finally:
        db.close()
    assert applied.applied is True
    current = task_run.get(user_id=user_id, generation=allowed.generation)
    assert current.web_constraint == "forbid"
    assert current.intent_version == 2
    assert current.exec_token is None
    _set_hold(user_id, allowed.generation, "worker-b", epoch=current.exec_epoch)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        denied = task_run.claim_web_execution(
            db,
            user_id=user_id,
            generation=allowed.generation,
            exec_token="worker-b",
            exec_epoch=current.exec_epoch,
            intent_version=current.intent_version,
            tool="web_fetch",
        )
    finally:
        db.close()
    assert denied == 0
    denied_row = task_run.get(user_id=user_id, generation=allowed.generation)
    assert denied_row.web_constraint == "forbid"
    assert denied_row.last_progress_at == _STAMP


def test_release_hold_clears_the_worker_hold():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id)
    _set_hold(user_id, job.generation, "worker-c", epoch=3)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = (
            db.query(TaskRun)
            .filter(TaskRun.generation == job.generation, TaskRun.user_id == user_id)
            .one()
        )
        row.exec_epoch = 3
        row.dispatch_token = "reserved"
        row.dispatch_until = _STAMP
        db.commit()
        changed = task_run.release_hold(
            db,
            user_id=user_id,
            generation=job.generation,
            exec_token="worker-c",
            exec_epoch=3,
            intent_version=1,
            status="waiting_person",
        )
    finally:
        db.close()
    assert changed == 1
    released = task_run.get(user_id=user_id, generation=job.generation)
    assert released.status == "waiting_person"
    assert released.exec_token is None
    assert released.hold_until is None
    assert released.dispatch_token is None
    assert released.dispatch_until is None
    assert released.exec_epoch == 4


def test_waiting_resource_apply_intent_returns_to_running():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id, goal="wait")
    _set_hold(user_id, job.generation, "worker-d")
    task_run.wait_for_resource(user_id=user_id, generation=job.generation)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=job.generation,
            text="go",
            web="allow",
            web_constraint_message_id=None,
        )
    finally:
        db.close()
    assert applied.applied is True
    current = task_run.get(user_id=user_id, generation=job.generation)
    assert current.status == "running"
    assert current.exec_token is None
    assert current.continuation_due is True
    assert current.intent_version == 2
    assert current.goal == "waitgo"


def test_waiting_person_apply_intent_stays_waiting():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id, goal="ask")
    task_run.wait_for_person(user_id=user_id, generation=job.generation)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=job.generation,
            text="thanks",
            web="allow",
            web_constraint_message_id=None,
        )
    finally:
        db.close()
    assert applied.applied is True
    current = task_run.get(user_id=user_id, generation=job.generation)
    assert current.status == "waiting_person"
    assert current.exec_token is None
    assert current.continuation_due is True
    assert current.goal == "askthanks"


def test_apply_intent_on_a_turn_changes_nothing():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=turn.generation,
            text="nope",
            web="forbid",
            web_constraint_message_id=3,
        )
    finally:
        db.close()
    assert applied.applied is False
    current = task_run.get(user_id=user_id, generation=turn.generation)
    assert current.status == "running"
    assert current.kind == "turn"
    assert current.intent_version == 1
    assert current.web_constraint == "allow"
    assert current.goal == ""


def test_a_later_allow_job_leaves_the_forbid_job():
    user_id, session_id = _pair()
    first = _open_job(user_id, session_id, goal="secret", web="forbid")
    second = _open_job(user_id, session_id, goal="public", web="allow")
    assert task_run.get(user_id=user_id, generation=first.generation).web_constraint == "forbid"
    assert task_run.get(user_id=user_id, generation=second.generation).web_constraint == "allow"
    assert task_run.get(user_id=user_id, generation=first.generation).status == "running"
    assert second.status == "running"


def test_void_keeps_a_waiting_job_card_and_cancels_a_turn_draft():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id, goal="hold")
    task_run.wait_for_person(user_id=user_id, generation=job.generation)
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    job_token = _issue(user_id, session_id, job.id, title="job card")
    turn_token = _issue(user_id, session_id, turn.id, title="turn card")
    _card(session_id, job_token, "job card")
    _card(session_id, turn_token, "turn card")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        void_draft_schedule_cards(db, session_id)
    finally:
        db.close()
    assert _claim(job_token).status == "open"
    assert _phase(session_id, job_token) == "draft"
    assert _claim(turn_token).status == "cancelled"
    assert _phase(session_id, turn_token) == "cancelled"
    assert task_run.get(user_id=user_id, generation=job.generation).status == "waiting_person"


def test_void_keeps_the_running_job_named_by_a_supplement():
    user_id, session_id = _pair()
    job = _open_job(user_id, session_id, goal="run")
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    job_token = _issue(user_id, session_id, job.id, title="running card")
    turn_token = _issue(user_id, session_id, turn.id, title="draft card")
    _card(session_id, job_token, "running card")
    _card(session_id, turn_token, "draft card")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=job.generation,
            text="extra",
            web="allow",
            web_constraint_message_id=None,
        )
        void_draft_schedule_cards(db, session_id, keep_task_run_id=job.id)
    finally:
        db.close()
    assert applied.applied is True
    assert _claim(job_token).status == "cancelled"
    assert _phase(session_id, job_token) == "draft"
    assert _claim(turn_token).status == "cancelled"
    assert _phase(session_id, turn_token) == "cancelled"


def test_confirming_a_cancelled_unarmed_card_does_not_write(plain_client: TestClient):
    user_id = _admin_id()
    session_id = _session(user_id)
    job = _open_job(user_id, session_id, goal="order")
    token = _issue(user_id, session_id, job.id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        task_run.apply_intent(
            db,
            user_id=user_id,
            generation=job.generation,
            text="never mind",
            web="allow",
            web_constraint_message_id=None,
        )
    finally:
        db.close()
    assert _claim(token).status == "cancelled"
    saved = plain_client.post(
        "/api/calendar",
        json={**_RAW, "confirm_token": token},
    )
    assert saved.status_code == 409
    db = SessionLocal()
    try:
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "talk confirm").count() == 0
    finally:
        db.close()


def test_talk_revision_upgrades_sqlite(tmp_path):
    database = tmp_path / "talk.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    env["DATABASE_MIGRATE_URL"] = ""
    env["REDIS_ENABLED"] = "false"
    backend = Path(__file__).resolve().parents[1]
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stderr
    from sqlalchemy import create_engine

    engine = create_engine(env["DATABASE_URL"])
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("task_runs")}
    assert {
        "kind",
        "goal",
        "web_constraint",
        "web_constraint_message_id",
        "intent_version",
        "last_progress_at",
        "dispatch_token",
        "dispatch_epoch",
        "dispatch_until",
        "exec_token",
        "exec_epoch",
        "hold_until",
        "continuation_due",
    } <= columns
    claim_columns = {column["name"] for column in inspector.get_columns("schedule_confirm_claims")}
    assert "intent_version" in claim_columns
    with engine.connect() as conn:
        ddl = str(conn.execute(text("SELECT sql FROM sqlite_master WHERE name='task_runs'")).scalar())
    assert "turn" in ddl
    assert "job" in ddl
    assert "allow" in ddl
    assert "forbid" in ddl
    down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0018_picture"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert down.returncode == 0, down.stderr
    inspector = inspect(engine)
    remaining = {column["name"] for column in inspector.get_columns("task_runs")}
    assert "kind" not in remaining
    assert "intent_version" not in {
        column["name"] for column in inspector.get_columns("schedule_confirm_claims")
    }
    again = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode == 0, again.stderr
    engine.dispose()


def test_init_db_adds_talk_columns_on_an_existing_sqlite_file(tmp_path):
    database = tmp_path / "old.db"
    conn = sqlite3.connect(database)
    conn.execute(
        "CREATE TABLE task_runs ("
        "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, session_id INTEGER NOT NULL, "
        "generation INTEGER NOT NULL, status TEXT NOT NULL, allowed_tools TEXT NOT NULL, "
        "created_at DATETIME)"
    )
    conn.execute(
        "INSERT INTO task_runs (id, user_id, session_id, generation, status, allowed_tools, created_at) "
        "VALUES (1, 1, 1, 1, 'running', '[]', '2020-01-01 00:00:00')"
    )
    conn.execute(
        "CREATE TABLE schedule_confirm_claims ("
        "token TEXT PRIMARY KEY, user_id INTEGER, session_id INTEGER, status TEXT, "
        "remote_request_id TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO schedule_confirm_claims (token, user_id, session_id, status, remote_request_id) "
        "VALUES ('claim-1', 1, 1, 'open', '')"
    )
    conn.commit()
    conn.close()
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    env["DATABASE_MIGRATE_URL"] = ""
    env["REDIS_ENABLED"] = "false"
    script = """
from sqlalchemy import inspect, text
from app.core.db import engine, init_db

init_db()
columns = {column["name"] for column in inspect(engine).get_columns("task_runs")}
needed = {
    "kind", "goal", "web_constraint", "web_constraint_message_id", "intent_version",
    "last_progress_at", "dispatch_token", "dispatch_epoch", "dispatch_until",
    "exec_token", "exec_epoch", "hold_until", "continuation_due",
}
missing = needed - columns
assert not missing, missing
with engine.connect() as conn:
    row = conn.execute(text("SELECT kind, last_progress_at, intent_version, goal FROM task_runs WHERE id = 1")).one()
    claim = conn.execute(text("SELECT intent_version FROM schedule_confirm_claims WHERE token = 'claim-1'")).one()
assert row[0] == "turn", row
assert row[1] is None, row
assert row[2] == 1, row
assert row[3] == "", row
assert claim[0] == 1, claim
"""
    backend = Path(__file__).resolve().parents[1]
    ran = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
