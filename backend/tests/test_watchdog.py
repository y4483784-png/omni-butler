from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.core.db import SessionLocal
from app.models.models import (
    CalendarEvent,
    Message,
    ScheduleConfirmClaim,
    Session as ChatSession,
    TaskRun,
    ToolAuditLog,
    User,
)
from app.services import task_run
from app.tasks import watchdog
from app.tasks.celery_app import celery_app
from app.tasks.watchdog import (
    CHECK_INTERVAL,
    ORDINARY_STALL,
    SUSPENDED_SWEEPS,
    GenerationKey,
    RunView,
    SweepLedger,
    decide,
    run_sweep,
)


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


def _set_created_at(user_id: int, generation: int, created_at: datetime) -> None:
    stored = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
            .one()
        )
        row.created_at = stored
        db.commit()
    finally:
        db.close()


def _generation_count(user_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(TaskRun).filter(TaskRun.user_id == user_id).count()
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


def _side_effect_counts() -> tuple[int, int, int, int]:
    db = SessionLocal()
    try:
        return (
            db.query(CalendarEvent).count(),
            db.query(ScheduleConfirmClaim).count(),
            db.query(Message).count(),
            db.query(ToolAuditLog).count(),
        )
    finally:
        db.close()


def _empty_ledger() -> SweepLedger:
    return SweepLedger(previous_tick=None, credit={})


def test_waiting_statuses_produce_no_stop_keys():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    created = now - timedelta(minutes=25)
    runs = (
        RunView(GenerationKey(1, 1), "waiting_person", created),
        RunView(GenerationKey(1, 2), "waiting_resource", created),
    )
    decision, _ledger = decide(runs, now=now, ledger=_empty_ledger())
    assert decision.stops == ()


def test_completed_and_stopped_stay():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    created = now - timedelta(minutes=25)
    runs = (
        RunView(GenerationKey(3, 1), "completed", created),
        RunView(GenerationKey(3, 2), "stopped", created),
    )
    decision, _ledger = decide(runs, now=now, ledger=_empty_ledger())
    assert decision.stops == ()

    user_id, session_id = _pair()
    finished = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.finish(user_id=user_id, generation=finished.generation)
    _set_created_at(user_id, finished.generation, now - timedelta(minutes=30))
    run_sweep(now, _empty_ledger())
    assert task_run.get(user_id=user_id, generation=finished.generation).status == "completed"
    assert _generation_count(user_id) == 1

    stopped_user, stopped_session = _pair()
    stopped = task_run.begin_turn(user_id=stopped_user, session_id=stopped_session)
    task_run.stop(user_id=stopped_user, generation=stopped.generation)
    _set_created_at(stopped_user, stopped.generation, now - timedelta(minutes=30))
    run_sweep(now, _empty_ledger())
    assert task_run.get(user_id=stopped_user, generation=stopped.generation).status == "stopped"
    assert _generation_count(stopped_user) == 1


def test_sleep_credit_persists_across_sweeps():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    created = now - timedelta(minutes=25)
    key = GenerationKey(7, 3)
    run = RunView(key, "running", created)
    prior: dict[GenerationKey, timedelta] = {}
    incoming = SweepLedger(previous_tick=created, credit=prior)

    decision, ledger = decide((run,), now=now, ledger=incoming)
    assert decision.stops == ()
    assert ledger.credit[key] == timedelta(minutes=25)
    assert prior == {}
    assert incoming.previous_tick == created

    later = now + timedelta(seconds=60)
    second, ledger2 = decide((run,), now=later, ledger=ledger)
    assert second.stops == ()
    assert ledger.credit[key] == timedelta(minutes=25)
    assert ledger2.credit[key] == timedelta(minutes=25)
    assert later - created - ledger2.credit[key] == timedelta(minutes=1)

    contrast, _ledger = decide(
        (run,),
        now=now,
        ledger=SweepLedger(previous_tick=now, credit={}),
    )
    assert contrast.stops == (key,)


def test_exact_three_intervals_add_no_credit():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    created = now - ORDINARY_STALL
    key = GenerationKey(4, 1)
    decision, ledger = decide(
        (RunView(key, "running", created),),
        now=now,
        ledger=SweepLedger(previous_tick=now - timedelta(seconds=180), credit={}),
    )
    assert ledger.credit[key] == timedelta(0)
    assert decision.stops == (key,)


def test_stalled_running_row_stops_without_other_writes():
    source = Path(watchdog.__file__).read_text(encoding="utf-8")
    assert "publish_schedule_claim" not in source
    assert "CalendarEvent" not in source
    assert "gateway" not in source

    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _set_created_at(user_id, turn.generation, now - ORDINARY_STALL)
    before = _side_effect_counts()
    run_sweep(now, _empty_ledger())
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "stopped"
    assert _generation_count(user_id) == 1
    assert _side_effect_counts() == before


def test_waiting_person_with_an_open_claim_stays_waiting():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.wait_for_person(user_id=user_id, generation=turn.generation)
    db = SessionLocal()
    try:
        db.add(
            ScheduleConfirmClaim(
                token=uuid4().hex,
                user_id=user_id,
                session_id=session_id,
                status="open",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
                action="calendar.create",
                fingerprint=uuid4().hex,
                scope=f"calendar:user:{user_id}",
                generation_ids="[]",
            )
        )
        db.commit()
    finally:
        db.close()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _set_created_at(user_id, turn.generation, now - timedelta(minutes=25))
    assert _claim_count(user_id) == 1
    ledger = run_sweep(now, _empty_ledger())
    run_sweep(now + CHECK_INTERVAL, ledger)
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "waiting_person"
    assert _claim_count(user_id) == 1


def test_stall_stops_only_the_older_generation():
    user_id, older_session = _pair()
    newer_session = _session(user_id)
    older = task_run.begin_turn(user_id=user_id, session_id=older_session)
    newer = task_run.begin_turn(user_id=user_id, session_id=newer_session)
    other_id, other_session = _pair()
    other = task_run.begin_turn(user_id=other_id, session_id=other_session)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _set_created_at(user_id, older.generation, now - ORDINARY_STALL)
    run_sweep(now, _empty_ledger())
    assert task_run.get(user_id=user_id, generation=older.generation).status == "stopped"
    assert task_run.get(user_id=user_id, generation=newer.generation).status == "running"
    assert task_run.get(user_id=other_id, generation=other.generation).status == "running"
    assert _generation_count(user_id) == 2
    assert _generation_count(other_id) == 1


def test_beat_schedule_registers_the_watch():
    schedule = celery_app.conf.beat_schedule
    assert "recover-stale-document-ingests" in schedule
    assert "memory-schedule-pulse" in schedule
    watch = schedule["watch-task-runs"]
    assert watch["task"] == "app.tasks.watchdog.sweep_task"
    assert watch["schedule"] == 60
    assert watch["options"]["queue"] == "maintenance"
    assert celery_app.conf.task_routes["app.tasks.watchdog.sweep_task"] == {"queue": "maintenance"}
    assert CHECK_INTERVAL == timedelta(seconds=60)
    assert SUSPENDED_SWEEPS == 3
    assert ORDINARY_STALL == timedelta(minutes=20)
