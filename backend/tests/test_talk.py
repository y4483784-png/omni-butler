"""A job stays running when a later turn starts."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from app.core.db import SessionLocal, set_rls_context
from app.models.models import Session as ChatSession
from app.models.models import User
from app.services import task_run


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


def _open_job(user_id: int, session_id: int, *, goal: str = "alpha") -> task_run.Turn:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = task_run.open_alongside(
            db,
            user_id=user_id,
            session_id=session_id,
            kind="job",
            goal=goal,
            web_constraint="allow",
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
