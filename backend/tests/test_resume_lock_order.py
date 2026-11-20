from __future__ import annotations

import os
import threading
import time
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("RESUME_LOCK_DATABASE_URL", "postgresql+psycopg://omni:omni@127.0.0.1/omni_lock")
REQUIRED = os.environ.get("RESUME_LOCK_REQUIRED") == "1"


def _engine():
    try:
        engine = create_engine(URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        if REQUIRED:
            pytest.fail(f"postgres lock database is required: {exc}")
        return None


def _wait_for_lock(engine, timeout: float = 4) -> bool:
    deadline = time.time() + timeout
    probe = engine.connect()
    try:
        while time.time() < deadline:
            waiting = probe.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                )
            ).scalar()
            if waiting and int(waiting) > 0:
                return True
            time.sleep(0.02)
        return False
    finally:
        probe.close()


@pytest.fixture()
def pg():
    engine = _engine()
    if engine is None:
        pytest.skip("postgres is not available")
    from app.core.db import Base
    import app.models.models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    @event.listens_for(engine, "connect")
    def _short_deadlock(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("SET deadlock_timeout = '50ms'")
        cursor.close()

    engine.dispose()
    yield engine
    engine.dispose()


def _seed(session):
    from app.models.models import CalendarEvent, ScheduleConfirmClaim, Session as ChatSession, TaskRun, TaskStep, ToolCall, User

    user = User(external_id="lock-user", name="lock", is_active=1, password_hash="")
    session.add(user)
    session.flush()
    chat = ChatSession(user_id=user.id, title="lock")
    session.add(chat)
    session.flush()
    job = TaskRun(
        user_id=user.id,
        session_id=chat.id,
        generation=1,
        status="waiting_person",
        allowed_tools="[]",
        kind="job",
        goal="calendar",
        continuation_due=False,
        exec_epoch=2,
        intent_version=1,
    )
    session.add(job)
    session.flush()
    step = TaskStep(user_id=user.id, task_run_id=job.id, seq=1, phase="awaiting_results", content="")
    session.add(step)
    session.flush()
    claim = ScheduleConfirmClaim(
        token="lock-token",
        user_id=user.id,
        session_id=chat.id,
        status="unknown",
        action="calendar.create",
        fingerprint="lock",
        scope="",
        generation_ids=f"[{job.id}]",
        remote_request_id="req-lock",
        intent_version=1,
    )
    session.add(claim)
    session.add(
        ToolCall(
            user_id=user.id,
            task_step_id=step.id,
            tool_call_id="call-lock",
            position=0,
            name="calendar",
            arguments="{}",
            state="awaiting_confirm",
            claim_token="lock-token",
            intent_version=1,
        )
    )
    event_row = CalendarEvent(
        user_id=user.id,
        title="lock",
        start_at=datetime(2026, 11, 20, 10, 0),
        end_at=datetime(2026, 11, 20, 11, 0),
        external_id="google-lock",
        provider="google",
    )
    session.add(event_row)
    session.commit()
    return user.id, chat.id, job.id, event_row.id


def test_inverted_cursor_and_job_locks_deadlock_on_postgres(pg):
    Session = sessionmaker(bind=pg)
    db = Session()
    user_id, session_id, job_id, _event_id = _seed(db)
    from app.services.resume import publish_event

    publish_event(db, user_id=user_id, session_id=session_id, kind="status", body="seed")
    db.commit()
    db.close()
    cursor_held = threading.Event()
    job_held = threading.Event()
    errors: list[BaseException] = []

    def hold_cursor() -> None:
        conn = pg.connect()
        trans = conn.begin()
        try:
            conn.execute(text("SET deadlock_timeout = '50ms'"))
            conn.execute(
                text("SELECT published_seq FROM session_cursors WHERE session_id = :s FOR UPDATE"),
                {"s": session_id},
            )
            cursor_held.set()
            assert job_held.wait(2)
            conn.execute(text("SELECT id FROM task_runs WHERE id = :id FOR UPDATE"), {"id": job_id})
            trans.commit()
        except Exception as exc:
            errors.append(exc)
            trans.rollback()
        finally:
            conn.close()

    def hold_job() -> None:
        assert cursor_held.wait(2)
        conn = pg.connect()
        trans = conn.begin()
        try:
            conn.execute(text("SET deadlock_timeout = '50ms'"))
            conn.execute(text("SELECT id FROM task_runs WHERE id = :id FOR UPDATE"), {"id": job_id})
            job_held.set()
            conn.execute(
                text("SELECT published_seq FROM session_cursors WHERE session_id = :s FOR UPDATE"),
                {"s": session_id},
            )
            trans.commit()
        except Exception as exc:
            errors.append(exc)
            trans.rollback()
        finally:
            conn.close()

    first = threading.Thread(target=hold_cursor)
    second = threading.Thread(target=hold_job)
    first.start()
    second.start()
    first.join(5)
    second.join(5)
    assert errors
    assert any("deadlock" in str(exc).lower() for exc in errors)


def test_calendar_finish_and_a_new_job_commit_together(pg):
    Session = sessionmaker(bind=pg)
    db = Session()
    user_id, session_id, _job_id, event_id = _seed(db)
    db.close()
    order: list[str] = []

    @event.listens_for(pg, "after_cursor_execute")
    def _trace(conn, _cursor, statement, _parameters, _context, _executemany):
        if conn.info.get("trace"):
            order.append(statement)

    held = threading.Event()
    release = threading.Event()

    @event.listens_for(pg, "after_cursor_execute")
    def _pause(conn, _cursor, statement, _parameters, _context, _executemany):
        if (
            conn.info.get("hold_gate")
            and "FOR UPDATE" in statement.upper()
            and "task_runs" in statement.lower()
            and not conn.info.get("paused")
        ):
            conn.info["paused"] = True
            held.set()
            assert release.wait(5)

    errors: list[BaseException] = []

    def finish() -> None:
        from app.api.calendar import record_create_success

        owned = Session()
        try:
            owned.connection().info["trace"] = True
            owned.connection().info["hold_gate"] = True
            record_create_success(owned, user_id, "lock-token", event_id)
        except Exception as exc:
            errors.append(exc)
        finally:
            owned.close()

    def insert() -> None:
        from app.services.task_run import _insert_running

        owned = Session()
        try:
            _insert_running(owned, user_id=user_id, session_id=session_id, kind="job", goal="second")
            owned.commit()
        except Exception as exc:
            errors.append(exc)
        finally:
            owned.close()

    left = threading.Thread(target=finish)
    left.start()
    assert held.wait(5)
    right = threading.Thread(target=insert)
    right.start()
    assert _wait_for_lock(pg)
    release.set()
    left.join(5)
    right.join(5)
    assert not left.is_alive()
    assert not right.is_alive()
    assert errors == []
    joined = "\n".join(order).lower()
    assert "task_runs" in joined and "session_cursors" in joined
    assert joined.index("task_runs") < joined.index("session_cursors")


def test_a_live_confirm_holding_the_claim_is_not_marked_unknown(pg):
    Session = sessionmaker(bind=pg)
    db = Session()
    user_id, _session_id, job_id, _event_id = _seed(db)
    from app.models.models import ScheduleConfirmClaim

    claim = db.get(ScheduleConfirmClaim, "lock-token")
    claim.armed_at = datetime(2000, 1, 1)
    db.commit()
    db.close()
    held = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def confirm() -> None:
        from app.services.loop_round import note_confirmed

        owned = Session()
        try:
            owned.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.token == "lock-token").with_for_update().one()
            held.set()
            assert release.wait(5)
            note_confirmed(owned, user_id=user_id, token="lock-token")
            owned.commit()
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    def recover() -> None:
        from app.services.loop_round import reconcile_parked_writes

        owned = Session()
        try:
            reconcile_parked_writes(owned, job_id)
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    left = threading.Thread(target=confirm)
    left.start()
    assert held.wait(5)
    right = threading.Thread(target=recover)
    right.start()
    assert _wait_for_lock(pg)
    release.set()
    left.join(5)
    right.join(5)
    assert errors == []
    check = Session()
    try:
        from app.models.models import ToolCall

        call = check.query(ToolCall).filter(ToolCall.claim_token == "lock-token").one()
        assert call.state == "result"
        assert call.result == "已确认"
    finally:
        check.close()


def _hold_claim_gate(engine, held: threading.Event, release: threading.Event) -> None:
    @event.listens_for(engine, "after_cursor_execute")
    def _pause(conn, _cursor, statement, _parameters, _context, _executemany):
        if (
            conn.info.get("hold_gate")
            and "for update" in statement.lower()
            and "schedule_confirm_claims" in statement.lower()
            and not conn.info.get("paused")
        ):
            conn.info["paused"] = True
            held.set()
            assert release.wait(5)


def test_open_cards_reusing_a_claim_does_not_deadlock_with_an_amendment(pg):
    Session = sessionmaker(bind=pg)
    db = Session()
    user_id, session_id, job_id, _event_id = _seed(db)
    from app.models.models import ScheduleConfirmClaim, TaskRun, TaskStep, ToolCall
    from app.services.schedule_confirm import write_fingerprint, write_parts, write_scope

    raw = {"mail_to": ["reuse@example.com"], "subject": "reuse", "body": "body"}
    parts = write_parts("mail.send", raw)
    job = db.get(TaskRun, job_id)
    job.status = "running"
    job.exec_token = "worker"
    job.exec_epoch = 1
    job.intent_version = 1
    job.hold_until = datetime(2030, 1, 1)
    step = db.query(TaskStep).filter(TaskStep.task_run_id == job_id).one()
    step.phase = "calls_saved"
    db.query(ToolCall).filter(ToolCall.task_step_id == step.id).delete()
    db.add(
        ToolCall(
            user_id=user_id,
            task_step_id=step.id,
            tool_call_id="call-reuse",
            position=1,
            name="mail.send",
            arguments=__import__("json").dumps(raw),
            state="pending",
            intent_version=1,
        )
    )
    db.add(
        ScheduleConfirmClaim(
            token="reuse-token",
            user_id=user_id,
            session_id=session_id,
            status="open",
            action="mail.send",
            fingerprint=write_fingerprint(parts),
            scope=write_scope(user_id, parts),
            generation_ids=f"[{job_id}]",
            intent_version=1,
        )
    )
    db.commit()
    generation = int(job.generation)
    step_id = int(step.id)
    db.close()
    held = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    _hold_claim_gate(pg, held, release)

    def open_again() -> None:
        from app.services.loop_round import Hold, open_cards

        owned = Session()
        try:
            owned.connection().info["hold_gate"] = True
            open_cards(
                owned,
                hold=Hold(user_id=user_id, generation=generation, exec_token="worker", exec_epoch=1, intent_version=1),
                step_id=step_id,
            )
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    def amend() -> None:
        from app.services.task_run import apply_intent

        owned = Session()
        try:
            apply_intent(
                owned,
                user_id=user_id,
                generation=generation,
                text="补充",
                web="allow",
                web_constraint_message_id=None,
            )
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    left = threading.Thread(target=open_again)
    left.start()
    assert held.wait(5)
    right = threading.Thread(target=amend)
    right.start()
    assert _wait_for_lock(pg)
    release.set()
    left.join(5)
    right.join(5)
    assert not left.is_alive()
    assert not right.is_alive()
    assert errors == []


def test_continue_from_card_does_not_deadlock_with_consume(pg):
    Session = sessionmaker(bind=pg)
    db = Session()
    user_id, session_id, job_id, _event_id = _seed(db)
    import json

    from app.models.models import Message, ScheduleConfirmClaim, TaskRun
    from app.services.schedule_confirm import write_fingerprint, write_parts, write_scope

    raw = {
        "title": "锁",
        "start_at": "2026-11-20T10:00:00",
        "end_at": "2026-11-20T11:00:00",
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
    }
    parts = write_parts("calendar.create", raw)
    job = db.get(TaskRun, job_id)
    job.status = "waiting_person"
    message = Message(
        session_id=session_id,
        role="assistant",
        content="list",
        schedule_card=json.dumps(
            {"mode": "list", "suggested_slots": [{"start_at": raw["start_at"], "end_at": raw["end_at"]}]}
        ),
    )
    db.add(message)
    db.flush()
    db.add(
        ScheduleConfirmClaim(
            token="continue-token",
            user_id=user_id,
            session_id=session_id,
            status="open",
            action="calendar.create",
            fingerprint=write_fingerprint(parts),
            scope=write_scope(user_id, parts),
            generation_ids=f"[{job_id}]",
            intent_version=1,
        )
    )
    db.commit()
    message_id = int(message.id)
    db.close()
    held = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    _hold_claim_gate(pg, held, release)

    def continue_card() -> None:
        from app.services.schedule_confirm import continue_from_card

        owned = Session()
        try:
            owned.connection().info["hold_gate"] = True
            continue_from_card(
                owned,
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                action="create",
                raw=raw,
            )
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    def consume() -> None:
        from app.services.schedule_confirm import consume_schedule_claim

        owned = Session()
        try:
            consume_schedule_claim(owned, user_id, "continue-token", action="calendar.create", raw=raw)
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    left = threading.Thread(target=continue_card)
    left.start()
    assert held.wait(5)
    right = threading.Thread(target=consume)
    right.start()
    assert _wait_for_lock(pg)
    release.set()
    left.join(5)
    right.join(5)
    assert not left.is_alive()
    assert not right.is_alive()
    assert errors == []


def test_two_cards_do_not_deadlock_with_confirmation_of_the_second(pg):
    Session = sessionmaker(bind=pg)
    db = Session()
    user_id, session_id, job_id, _event_id = _seed(db)
    import json

    from app.models.models import ScheduleConfirmClaim, TaskRun, TaskStep, ToolCall
    from app.services.schedule_confirm import write_fingerprint, write_parts, write_scope

    first = {"mail_to": ["one@example.com"], "subject": "one", "body": "one"}
    second = {"mail_to": ["two@example.com"], "subject": "two", "body": "two"}
    parts = write_parts("mail.send", second)
    job = db.get(TaskRun, job_id)
    job.status = "running"
    job.exec_token = "worker"
    job.exec_epoch = 1
    job.intent_version = 1
    job.hold_until = datetime(2030, 1, 1)
    step = db.query(TaskStep).filter(TaskStep.task_run_id == job_id).one()
    step.phase = "calls_saved"
    db.query(ToolCall).filter(ToolCall.task_step_id == step.id).delete()
    for position, (call_id, raw) in enumerate((("call-1", first), ("call-2", second))):
        db.add(
            ToolCall(
                user_id=user_id,
                task_step_id=step.id,
                tool_call_id=call_id,
                position=position,
                name="mail.send",
                arguments=json.dumps(raw),
                state="pending",
                intent_version=1,
            )
        )
    db.add(
        ScheduleConfirmClaim(
            token="second-token",
            user_id=user_id,
            session_id=session_id,
            status="open",
            action="mail.send",
            fingerprint=write_fingerprint(parts),
            scope=write_scope(user_id, parts),
            generation_ids=f"[{job_id}]",
            intent_version=1,
        )
    )
    db.commit()
    generation = int(job.generation)
    step_id = int(step.id)
    db.close()
    held = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    _hold_claim_gate(pg, held, release)

    def open_both() -> None:
        from app.services.loop_round import Hold, open_cards

        owned = Session()
        try:
            owned.connection().info["hold_gate"] = True
            open_cards(
                owned,
                hold=Hold(user_id=user_id, generation=generation, exec_token="worker", exec_epoch=1, intent_version=1),
                step_id=step_id,
            )
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    def confirm_second() -> None:
        from app.services.schedule_confirm import consume_schedule_claim

        owned = Session()
        try:
            consume_schedule_claim(owned, user_id, "second-token", action="mail.send", raw=second)
        except Exception as exc:
            errors.append(exc)
            owned.rollback()
        finally:
            owned.close()

    left = threading.Thread(target=open_both)
    left.start()
    assert held.wait(5)
    right = threading.Thread(target=confirm_second)
    right.start()
    assert _wait_for_lock(pg)
    release.set()
    left.join(5)
    right.join(5)
    assert not left.is_alive()
    assert not right.is_alive()
    assert errors == []
