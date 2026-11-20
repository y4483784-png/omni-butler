"""Postgres lock order for amendment, stop, and owned persist."""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from test_resume_lock_order import REQUIRED, _backend_pid, _engine, _release, _terminate_other_backends, _wait_until_blocked


@pytest.fixture()
def pg():
    engine = _engine()
    if engine is None:
        if REQUIRED:
            pytest.fail("postgres lock database is required")
        pytest.skip("postgres is not available")
    from app.core.db import Base
    import app.models.models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    try:
        _terminate_other_backends(engine)
    finally:
        engine.dispose()


def _seed(session):
    from app.models.models import ScheduleConfirmClaim, Session as ChatSession, TaskDelivery, TaskRun, TaskStep, ToolCall, ToolCallAttempt, User

    user = User(external_id="owner-lock", name="owner", is_active=1, password_hash="")
    session.add(user)
    session.flush()
    chat = ChatSession(user_id=user.id, title="owner")
    session.add(chat)
    session.flush()
    job = TaskRun(
        user_id=user.id,
        session_id=chat.id,
        generation=1,
        status="running",
        allowed_tools="[]",
        kind="job",
        goal="read",
        exec_token="worker",
        exec_epoch=1,
        intent_version=1,
        hold_until=datetime(2099, 1, 1),
    )
    session.add(job)
    session.flush()
    step = TaskStep(user_id=user.id, task_run_id=job.id, seq=1, phase="awaiting_results", content="")
    session.add(step)
    session.flush()
    call = ToolCall(
        user_id=user.id,
        task_step_id=step.id,
        tool_call_id="call-lock",
        position=0,
        name="kb",
        arguments="{}",
        state="running",
        intent_version=1,
        owner_token="owner",
        attempt=1,
    )
    session.add(call)
    session.flush()
    delivery = TaskDelivery(
        delivery_id="delivery-lock",
        worker_incarnation="inc",
        celery_request_id="req",
        child_pid=1,
        task_run_id=job.id,
    )
    session.add(delivery)
    session.flush()
    session.add(
        ToolCallAttempt(
            tool_call_pk=call.id,
            owner_token="owner",
            attempt=1,
            delivery_id="delivery-lock",
            handed_off_at=datetime(2026, 1, 1),
        )
    )
    session.add(
        ScheduleConfirmClaim(
            token="owner-claim",
            user_id=user.id,
            session_id=chat.id,
            status="open",
            action="calendar.create",
            fingerprint="owner-lock",
            scope="",
            generation_ids=f"[{job.id}]",
            intent_version=1,
        )
    )
    session.commit()
    return user.id, job.id


def test_amendment_waits_for_the_claim_lock(pg):
    Session = sessionmaker(bind=pg)
    holder = Session()
    waiter = Session()
    _seed(holder)
    user_id = holder.query(__import__("app.models.models", fromlist=["User"]).User).first().id
    release = threading.Event()
    holder_pid: list[int] = []
    waiter_pid: list[int] = []
    threads: list[threading.Thread] = []

    def hold():
        from app.core.db import set_rls_context

        set_rls_context(holder, int(user_id))
        holder_pid.append(_backend_pid(holder.connection()))
        holder.execute(text("SELECT token FROM schedule_confirm_claims WHERE token = 'owner-claim' FOR UPDATE"))
        release.wait(5)
        holder.rollback()

    def wait():
        from app.core.db import set_rls_context
        from app.services.task_run import apply_intent

        time.sleep(0.1)
        set_rls_context(waiter, int(user_id))
        waiter_pid.append(_backend_pid(waiter.connection()))
        apply_intent(waiter, user_id=int(user_id), generation=1, text="more", web="allow", web_constraint_message_id=None)

    threads.append(threading.Thread(target=hold))
    threads.append(threading.Thread(target=wait))
    threads[0].start()
    threads[1].start()
    try:
        assert _wait_until_blocked(pg, holder_pid, waiter_pid)
    finally:
        _release(release, threads)
        holder.close()
        waiter.close()


def test_persist_waits_for_the_job_lock(pg):
    Session = sessionmaker(bind=pg)
    holder = Session()
    waiter = Session()
    user_id, job_id = _seed(holder)
    release = threading.Event()
    holder_pid: list[int] = []
    waiter_pid: list[int] = []
    threads: list[threading.Thread] = []

    def hold():
        from app.core.db import set_rls_context

        set_rls_context(holder, int(user_id))
        holder_pid.append(_backend_pid(holder.connection()))
        holder.execute(text("SELECT id FROM task_runs WHERE id = :id FOR UPDATE"), {"id": job_id})
        release.wait(5)
        holder.rollback()

    def wait():
        from app.core.db import set_rls_context
        from app.services.delivery_bind import bind_delivery
        from app.services.loop_round import Hold, save_read_result

        time.sleep(0.1)
        set_rls_context(waiter, int(user_id))
        waiter_pid.append(_backend_pid(waiter.connection()))
        bind_delivery("delivery-lock")
        save_read_result(
            waiter,
            hold=Hold(user_id=int(user_id), generation=1, exec_token="worker", exec_epoch=1, intent_version=1),
            tool_call_id="call-lock",
            result="kept",
        )

    threads.append(threading.Thread(target=hold))
    threads.append(threading.Thread(target=wait))
    threads[0].start()
    threads[1].start()
    try:
        assert _wait_until_blocked(pg, holder_pid, waiter_pid)
    finally:
        _release(release, threads)
        holder.close()
        waiter.close()


def test_open_cards_waits_for_the_claim_lock(pg):
    Session = sessionmaker(bind=pg)
    holder = Session()
    waiter = Session()
    user_id, job_id = _seed(holder)
    from app.models.models import ScheduleConfirmClaim, TaskStep, ToolCall
    from app.services.schedule_confirm import canonical_action, write_fingerprint, write_parts

    raw = {"mail_to": ["one@example.com"], "subject": "Hello", "body": "hello"}
    action = canonical_action("mail.send")
    fingerprint = write_fingerprint(write_parts(action, raw))
    claim = holder.query(ScheduleConfirmClaim).one()
    claim.action = action
    claim.fingerprint = fingerprint
    step = TaskStep(user_id=user_id, task_run_id=job_id, seq=2, phase="calls_saved", content="")
    holder.add(step)
    holder.flush()
    holder.add(
        ToolCall(
            user_id=user_id,
            task_step_id=step.id,
            tool_call_id="call-mail",
            position=0,
            name="mail.send",
            arguments='{"mail_to": ["one@example.com"], "subject": "Hello", "body": "hello"}',
            state="pending",
            intent_version=1,
        )
    )
    holder.commit()
    step_id = step.id
    release = threading.Event()
    holder_pid: list[int] = []
    waiter_pid: list[int] = []
    threads: list[threading.Thread] = []

    def hold():
        from app.core.db import set_rls_context

        set_rls_context(holder, int(user_id))
        holder_pid.append(_backend_pid(holder.connection()))
        holder.execute(text("SELECT token FROM schedule_confirm_claims WHERE token = 'owner-claim' FOR UPDATE"))
        release.wait(5)
        holder.rollback()

    def wait():
        from app.core.db import set_rls_context
        from app.services.loop_round import Hold, open_cards

        time.sleep(0.1)
        set_rls_context(waiter, int(user_id))
        waiter_pid.append(_backend_pid(waiter.connection()))
        open_cards(
            waiter,
            hold=Hold(user_id=int(user_id), generation=1, exec_token="worker", exec_epoch=1, intent_version=1),
            step_id=step_id,
        )

    threads.append(threading.Thread(target=hold))
    threads.append(threading.Thread(target=wait))
    threads[0].start()
    threads[1].start()
    try:
        assert _wait_until_blocked(pg, holder_pid, waiter_pid)
    finally:
        _release(release, threads)
        holder.close()
        waiter.close()


def test_stop_waits_for_the_job_lock(pg):
    Session = sessionmaker(bind=pg)
    holder = Session()
    waiter = Session()
    user_id, job_id = _seed(holder)
    release = threading.Event()
    holder_pid: list[int] = []
    waiter_pid: list[int] = []
    threads: list[threading.Thread] = []

    def hold():
        from app.core.db import set_rls_context

        set_rls_context(holder, int(user_id))
        holder_pid.append(_backend_pid(holder.connection()))
        holder.execute(text("SELECT id FROM task_runs WHERE id = :id FOR UPDATE"), {"id": job_id})
        release.wait(5)
        holder.rollback()

    def wait():
        from app.core.db import set_rls_context
        from app.services.loop_round import cancel_open_calls

        time.sleep(0.1)
        set_rls_context(waiter, int(user_id))
        waiter_pid.append(_backend_pid(waiter.connection()))
        cancel_open_calls(waiter, user_id=int(user_id), generation=1)

    threads.append(threading.Thread(target=hold))
    threads.append(threading.Thread(target=wait))
    threads[0].start()
    threads[1].start()
    try:
        assert _wait_until_blocked(pg, holder_pid, waiter_pid)
    finally:
        _release(release, threads)
        holder.close()
        waiter.close()
