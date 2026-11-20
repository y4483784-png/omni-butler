from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect

from app.agents.harness.gateway import invoke
from app.agents.harness.types import ToolContext
from app.api.chat import _save_assistant_turn
from app.core.db import SessionLocal, set_rls_context
from app.models.models import (
    CalendarEvent,
    Message,
    ScheduleConfirmClaim,
    Session as ChatSession,
    TaskRun,
    User,
)
from app.services import task_run
from app.services.schedule_confirm import publish_schedule_claim
from app.tasks.celery_app import celery_app
from app.tasks.job import commit_job_output
from app.tasks.watchdog import run_sweep


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"task-run-{uuid4().hex}"
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
        row = ChatSession(user_id=user_id, title="task run")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _pair() -> tuple[int, int]:
    user_id = _user()
    return user_id, _session(user_id)


def _naive(moment: datetime) -> datetime:
    if moment.tzinfo is not None and moment.tzinfo.utcoffset(moment) is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.replace(tzinfo=None, microsecond=0)


def _now() -> datetime:
    return _naive(datetime.now(timezone.utc))


def _job(user_id: int, session_id: int, *, goal: str = "watch") -> task_run.Turn:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = task_run.open_alongside(
            db,
            user_id=user_id,
            session_id=session_id,
            kind="job",
            goal=goal,
        )
        db.commit()
        return job
    finally:
        db.close()


def _load(user_id: int, generation: int) -> TaskRun:
    db = SessionLocal()
    try:
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
            .one()
        )
        db.expunge(row)
        return row
    finally:
        db.close()


def _patch(user_id: int, generation: int, **values) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
            .one()
        )
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()
    finally:
        db.close()


def _messages(session_id: int) -> list[Message]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.id.asc())
            .all()
        )
        for row in rows:
            db.expunge(row)
        return rows
    finally:
        db.close()


def _claim_count(user_id: int) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ScheduleConfirmClaim)
            .filter(ScheduleConfirmClaim.user_id == user_id)
            .count()
        )
    finally:
        db.close()


def _calendar_count(user_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
    finally:
        db.close()


def _sentence(stamp: datetime, generation: int) -> str:
    shown = stamp.strftime("%Y-%m-%d %H:%M:%S")
    return f"这一任务自 {shown} 起没有新的进展，仍在运行。generation={generation}"


def test_recent_progress_keeps_an_old_job_running():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    now = _now()
    _patch(
        user_id,
        job.generation,
        created_at=now - timedelta(minutes=25),
        last_progress_at=now - timedelta(minutes=1),
    )
    run_sweep()
    run_sweep()
    assert _load(user_id, job.generation).status == "running"


def test_renew_hold_leaves_last_progress_at():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    planted = datetime(2026, 11, 19, 8, 0, 0)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.generation == job.generation)
            .one()
        )
        row.exec_token = "worker"
        row.exec_epoch = 0
        row.intent_version = 1
        row.last_progress_at = planted
        db.commit()
        changed = task_run.renew_hold(
            db,
            user_id=user_id,
            generation=job.generation,
            exec_token="worker",
            exec_epoch=0,
            intent_version=1,
        )
    finally:
        db.close()
    assert changed == 1
    fresh = _load(user_id, job.generation)
    assert fresh.last_progress_at == planted
    assert fresh.hold_until is not None


def test_null_stamp_stays_running_without_a_notice():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    planted = datetime(2026, 11, 19, 1, 2, 3)
    _patch(
        user_id,
        job.generation,
        created_at=_now() - timedelta(minutes=25),
        last_progress_at=None,
        observed_progress_at=planted,
        notice_progress_at=None,
    )
    run_sweep()
    run_sweep()
    fresh = _load(user_id, job.generation)
    notes = _messages(session_id)
    assert fresh.status == "running"
    assert [row.content for row in notes] == ["已开始：watch"]
    assert fresh.notice_progress_at is None
    assert fresh.observed_progress_at == fresh.last_progress_at
    assert all("没有新的进展" not in row.content for row in notes)


def test_one_notice_per_stamp_survives_reload():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    stamp = datetime(2026, 11, 19, 4, 5, 6)
    _patch(
        user_id,
        job.generation,
        last_progress_at=stamp,
        observed_progress_at=None,
        notice_progress_at=None,
    )
    before_messages = len(_messages(session_id))
    claims = _claim_count(user_id)
    calendars = _calendar_count(user_id)
    run_sweep()
    first = _load(user_id, job.generation)
    assert first.status == "running"
    assert first.observed_progress_at == stamp
    assert first.notice_progress_at is None
    assert len(_messages(session_id)) == before_messages

    run_sweep()
    second = _load(user_id, job.generation)
    notes = _messages(session_id)
    assert second.status == "running"
    assert second.observed_progress_at == stamp
    assert second.notice_progress_at == stamp
    assert len(notes) == before_messages + 1
    notice = notes[-1]
    assert notice.role == "assistant"
    assert notice.content == _sentence(stamp, job.generation)
    assert notice.schedule_card == ""
    assert notice.citations == ""
    assert notice.artifact == ""
    assert notice.reasoning is None
    assert _claim_count(user_id) == claims
    assert _calendar_count(user_id) == calendars

    module = importlib.reload(importlib.import_module("app.tasks.watchdog"))
    module.run_sweep()
    assert len(_messages(session_id)) == before_messages + 1
    assert _load(user_id, job.generation).notice_progress_at == stamp

    module.run_sweep()
    assert len(_messages(session_id)) == before_messages + 1
    held = _load(user_id, job.generation)
    assert held.status == "running"
    assert held.notice_progress_at == stamp


def test_waiting_rows_are_left_alone():
    user_id, session_id = _pair()
    person = _job(user_id, session_id, goal="person")
    task_run.wait_for_person(user_id=user_id, generation=person.generation)
    resource_session = _session(user_id)
    resource = _job(user_id, resource_session, goal="resource")
    task_run.wait_for_resource(user_id=user_id, generation=resource.generation)
    last = datetime(2026, 11, 19, 6, 0, 0)
    observed = last
    created = _now() - timedelta(minutes=25)
    _patch(
        user_id,
        person.generation,
        created_at=created,
        last_progress_at=last,
        observed_progress_at=observed,
        notice_progress_at=None,
    )
    _patch(
        user_id,
        resource.generation,
        created_at=created,
        last_progress_at=last,
        observed_progress_at=observed,
        notice_progress_at=None,
    )
    db = SessionLocal()
    try:
        db.add(
            ScheduleConfirmClaim(
                token=uuid4().hex,
                user_id=user_id,
                session_id=session_id,
                status="open",
                expires_at=_now() + timedelta(hours=1),
                action="calendar.create",
                fingerprint=uuid4().hex,
                scope=f"calendar:user:{user_id}",
                generation_ids=json.dumps([person.id]),
            )
        )
        db.commit()
    finally:
        db.close()
    person_notes = len(_messages(session_id))
    resource_notes = len(_messages(resource_session))
    claims = _claim_count(user_id)
    run_sweep()
    run_sweep()
    person_row = _load(user_id, person.generation)
    resource_row = _load(user_id, resource.generation)
    assert person_row.status == "waiting_person"
    assert resource_row.status == "waiting_resource"
    for row in (person_row, resource_row):
        assert row.last_progress_at == last
        assert row.observed_progress_at == observed
        assert row.notice_progress_at is None
    assert len(_messages(session_id)) == person_notes
    assert len(_messages(resource_session)) == resource_notes
    assert _claim_count(user_id) == claims


def test_completed_and_stopped_stay():
    user_id, session_id = _pair()
    finished = _job(user_id, session_id, goal="done")
    task_run.finish(user_id=user_id, generation=finished.generation)
    stopped_user, stopped_session = _pair()
    stopped = _job(stopped_user, stopped_session, goal="halt")
    task_run.stop(user_id=stopped_user, generation=stopped.generation)
    old = _now() - timedelta(minutes=30)
    _patch(user_id, finished.generation, created_at=old)
    _patch(stopped_user, stopped.generation, created_at=old)
    run_sweep()
    run_sweep()
    assert _load(user_id, finished.generation).status == "completed"
    assert _load(stopped_user, stopped.generation).status == "stopped"
    assert _messages(session_id) == []
    assert _messages(stopped_session) == []


def test_a_new_stamp_notifies_once_and_a_cleared_stamp_does_not_repeat():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    first = datetime(2026, 11, 19, 7, 0, 0)
    second = datetime(2026, 11, 19, 7, 5, 0)
    _patch(user_id, job.generation, last_progress_at=first)
    run_sweep()
    run_sweep()
    assert len(_messages(session_id)) == 1
    assert _load(user_id, job.generation).notice_progress_at == first

    _patch(user_id, job.generation, last_progress_at=second)
    run_sweep()
    moved = _load(user_id, job.generation)
    assert moved.observed_progress_at == second
    assert moved.notice_progress_at == first
    assert len(_messages(session_id)) == 1
    run_sweep()
    told = _load(user_id, job.generation)
    assert told.notice_progress_at == second
    assert len(_messages(session_id)) == 2
    assert _messages(session_id)[-1].content == _sentence(second, job.generation)

    _patch(user_id, job.generation, last_progress_at=None)
    run_sweep()
    cleared = _load(user_id, job.generation)
    assert len(_messages(session_id)) == 2
    assert cleared.observed_progress_at is None
    assert cleared.notice_progress_at == second

    _patch(user_id, job.generation, last_progress_at=second)
    run_sweep()
    restored = _load(user_id, job.generation)
    assert restored.observed_progress_at == second
    assert restored.notice_progress_at == second
    assert len(_messages(session_id)) == 2
    run_sweep()
    again = _load(user_id, job.generation)
    assert again.observed_progress_at == second
    assert again.notice_progress_at == second
    assert len(_messages(session_id)) == 2
    assert again.status == "running"


def test_output_commit_gap():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    empty_session = _session(user_id)
    empty_job = _job(user_id, empty_session, goal="empty")
    start = datetime(2026, 11, 19, 12, 0, 0)
    _patch(user_id, job.generation, exec_token="holder", exec_epoch=1, intent_version=1)
    _patch(user_id, empty_job.generation, exec_token="holder", exec_epoch=1, intent_version=1)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        message_id, committed = commit_job_output(
            db,
            user_id=user_id,
            session_id=session_id,
            generation=job.generation,
            message_id=None,
            content="one",
            now=start,
            previous_commit_at=None,
            newer=True,
            call_ended=False,
            exec_token="holder",
            exec_epoch=1,
            intent_version=1,
        )
        assert message_id is not None
        assert committed == start
        assert _load(user_id, job.generation).last_progress_at == start
        assert [row.content for row in _messages(session_id)] == ["one"]

        same_id, same_at = commit_job_output(
            db,
            user_id=user_id,
            session_id=session_id,
            generation=job.generation,
            message_id=message_id,
            content="two",
            now=start + timedelta(seconds=4),
            previous_commit_at=committed,
            newer=True,
            call_ended=False,
            exec_token="holder",
            exec_epoch=1,
            intent_version=1,
        )
        assert same_id == message_id
        assert same_at == committed
        assert [row.content for row in _messages(session_id)] == ["one"]
        assert _load(user_id, job.generation).last_progress_at == start

        five = start + timedelta(seconds=5)
        updated_id, updated_at = commit_job_output(
            db,
            user_id=user_id,
            session_id=session_id,
            generation=job.generation,
            message_id=message_id,
            content="three",
            now=five,
            previous_commit_at=start,
            newer=True,
            call_ended=False,
            exec_token="holder",
            exec_epoch=1,
            intent_version=1,
        )
        assert updated_id == message_id
        assert updated_at == five
        assert [row.content for row in _messages(session_id)] == ["three"]
        assert _load(user_id, job.generation).last_progress_at == five

        ended = five + timedelta(seconds=1)
        ended_id, ended_at = commit_job_output(
            db,
            user_id=user_id,
            session_id=session_id,
            generation=job.generation,
            message_id=message_id,
            content="four",
            now=ended,
            previous_commit_at=updated_at,
            newer=True,
            call_ended=True,
            exec_token="holder",
            exec_epoch=1,
            intent_version=1,
        )
        assert ended_id == message_id
        assert ended_at == ended
        assert [row.content for row in _messages(session_id)] == ["four"]
        assert _load(user_id, job.generation).last_progress_at == ended
        assert _load(user_id, job.generation).status == "running"

        skipped_id, skipped_at = commit_job_output(
            db,
            user_id=user_id,
            session_id=empty_session,
            generation=empty_job.generation,
            message_id=None,
            content="",
            now=start,
            previous_commit_at=None,
            newer=True,
            call_ended=True,
            exec_token="holder",
            exec_epoch=1,
            intent_version=1,
        )
        assert skipped_id is None
        assert skipped_at is None
        assert _messages(empty_session) == []
        assert _load(user_id, empty_job.generation).last_progress_at is None
        assert _load(user_id, empty_job.generation).status == "running"
    finally:
        db.close()


def test_run_job_sets_last_progress_at_on_the_start_message():
    from app.services.resume import enqueue_job

    user_id, session_id = _pair()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        enqueue_job(db, user_id=user_id, session_id=session_id, goal="alpha")
    finally:
        db.close()
    db = SessionLocal()
    try:
        job = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.session_id == session_id, TaskRun.kind == "job")
            .one()
        )
        db.expunge(job)
    finally:
        db.close()
    assert job.status == "running"
    assert job.last_progress_at is not None
    assert [row.content for row in _messages(session_id)] == ["已开始：alpha"]


def test_save_assistant_turn_stamps_a_job_and_leaves_a_turn():
    user_id, session_id = _pair()
    job = _job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        _save_assistant_turn(db, session_id, "已保存", job_generation=job.generation)
    finally:
        db.close()
    saved = _load(user_id, job.generation)
    assert saved.status == "running"
    assert saved.last_progress_at is not None
    assert len(_messages(session_id)) == 1
    assert _messages(session_id)[0].role == "assistant"

    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        _save_assistant_turn(db, session_id, "轮次", job_generation=turn.generation)
    finally:
        db.close()
    assert _load(user_id, turn.generation).last_progress_at is None
    assert _load(user_id, turn.generation).kind == "turn"


def test_gateway_invoke_stamps_kb_and_leaves_a_shell_stamp(monkeypatch):
    user_id, session_id = _pair()
    job = _job(user_id, session_id, goal="kb")
    shell_session = _session(user_id)
    shell_job = _job(user_id, shell_session, goal="shell")
    planted = datetime(2026, 11, 19, 9, 8, 7)
    _patch(user_id, shell_job.generation, last_progress_at=planted)

    def hits(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.agents.tools.kb.retrieve", hits)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        invoke(
            "kb",
            ToolContext(
                db=db,
                message="查资料",
                history=[],
                user_id=user_id,
                task_id=job.id,
                generation=job.generation,
            ),
            {},
        )
        invoke(
            "shell",
            ToolContext(
                db=db,
                message="ls",
                history=[],
                user_id=user_id,
                task_id=shell_job.id,
                generation=shell_job.generation,
            ),
            {},
        )
    finally:
        db.close()
    assert _load(user_id, job.generation).last_progress_at is not None
    assert _load(user_id, job.generation).status == "running"
    assert _load(user_id, shell_job.generation).last_progress_at == planted


def test_publish_schedule_claim_stamps_a_job():
    user_id, session_id = _pair()
    job = _job(user_id, session_id, goal="card")
    assert _load(user_id, job.generation).last_progress_at is None
    card = {
        "phase": "draft",
        "mode": "create",
        "title": "watch confirm",
        "start_at": "2026-11-04T10:00:00",
        "end_at": "2026-11-04T11:00:00",
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
    }
    published = publish_schedule_claim(user_id, session_id, card, task_run_id=job.id)
    fresh = _load(user_id, job.generation)
    assert fresh.last_progress_at is not None
    assert fresh.status == "running"
    assert published.get("confirm_token")
    assert _claim_count(user_id) == 1


def test_watch_revision_upgrades_sqlite(tmp_path):
    database = tmp_path / "watch.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    env["DATABASE_MIGRATE_URL"] = ""
    env["REDIS_ENABLED"] = "false"
    backend = Path(__file__).resolve().parents[1]

    def alembic(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    from sqlalchemy import create_engine

    engine = create_engine(env["DATABASE_URL"])
    columns = {column["name"]: column for column in inspect(engine).get_columns("task_runs")}
    assert columns["observed_progress_at"]["nullable"] is True
    assert columns["notice_progress_at"]["nullable"] is True
    assert "last_progress_at" in columns
    down = alembic("downgrade", "0019_talk")
    assert down.returncode == 0, down.stderr
    remaining = {column["name"] for column in inspect(engine).get_columns("task_runs")}
    assert "observed_progress_at" not in remaining
    assert "notice_progress_at" not in remaining
    assert "last_progress_at" in remaining
    again = alembic("upgrade", "head")
    assert again.returncode == 0, again.stderr
    restored = {column["name"] for column in inspect(engine).get_columns("task_runs")}
    assert "observed_progress_at" in restored
    assert "notice_progress_at" in restored
    engine.dispose()


def test_beat_schedule_registers_the_watch():
    schedule = celery_app.conf.beat_schedule
    watch = schedule["watch-task-runs"]
    assert watch["task"] == "app.tasks.watchdog.sweep_task"
    assert watch["schedule"] == 60
    assert watch["options"]["queue"] == "maintenance"
    assert celery_app.conf.task_routes["app.tasks.watchdog.sweep_task"] == {"queue": "maintenance"}
