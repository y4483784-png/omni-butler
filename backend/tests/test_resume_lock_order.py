from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

URL = "postgresql+psycopg://omni:omni@127.0.0.1/omni_lock"


def _engine():
    try:
        engine = create_engine(URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


@pytest.fixture()
def pg():
    engine = _engine()
    if engine is None:
        pytest.skip("postgres is not available")
    from app.core.db import Base
    import app.models.models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER DATABASE omni_lock SET deadlock_timeout = '50ms'"))
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

    @event.listens_for(pg, "after_cursor_execute")
    def _pause(conn, _cursor, statement, _parameters, _context, _executemany):
        if "FOR UPDATE" in statement.upper() and not conn.info.get("paused"):
            conn.info["paused"] = True
            time.sleep(0.2)

    errors: list[BaseException] = []

    def finish() -> None:
        from app.api.calendar import record_create_success

        owned = Session()
        try:
            owned.connection().info["trace"] = True
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
    right = threading.Thread(target=insert)
    left.start()
    right.start()
    left.join(8)
    right.join(8)
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
    time.sleep(0.3)
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
