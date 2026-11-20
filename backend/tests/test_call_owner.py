"""Behavior of one call owner, one delivery, and the parked acknowledgement."""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from unittest.mock import patch

from app.agents.harness.types import ToolResult
from app.core.db import SessionLocal, set_rls_context
from app.models.models import (
    TaskDelivery,
    TaskRun,
    TaskStep,
    ToolCall,
    ToolCallAttempt,
    ToolCallEffect,
    ToolCallEffectConflict,
)
from app.services.call_owner import acknowledge, audit_unattributed, insert_effect, mark_legacy_unresolved
from app.services.call_policy import worker_queues
from app.services.delivery_bind import (
    IncarnationMissing,
    claim_mailbox,
    exit_request,
    incarnation,
    mark_exit,
    mint_for_request,
    publish_incarnation,
)
from app.services.task_run import apply_intent
from app.tasks.job import set_executor
from test_loop_job import _calls, _deliver, _dispatch, _drive_held, _job, _pair, _turn
from app.services.loop_round import ToolCallSpec


def _intent(user_id: int, generation: int, text: str, web: str = "allow") -> None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        applied = apply_intent(
            db,
            user_id=user_id,
            generation=generation,
            text=text,
            web=web,
            web_constraint_message_id=None,
        )
        assert applied.applied is True
    finally:
        db.close()


def test_supplement_during_a_running_read_does_not_start_another():
    user_id, session_id = _pair()
    invokes: list[str] = []

    def executor(messages, _tools):
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        job = _job(session_id)
        _intent(user_id, int(job.generation), " plus more", "forbid")
        fresh = _job(session_id)
        assert fresh.exec_token == job.exec_token
        assert fresh.continuation_due is False
        return ToolResult(direct_answer="answer-1")

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _deliver(user_id, session_id, "alpha")
    assert invokes == ["kb"]
    job = _job(session_id)
    assert "plus more" in (job.goal or "")
    assert _calls(job.id)[0].result == "answer-1"


def test_pending_web_sibling_is_denied_after_forbid():
    user_id, session_id = _pair()
    invokes: list[str] = []

    def executor(messages, _tools):
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "kb", "{}"), ToolCallSpec("call-b", "web", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        job = _job(session_id)
        _intent(user_id, int(job.generation), " no web", "forbid")
        return ToolResult(direct_answer="from-kb")

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _deliver(user_id, session_id, "alpha")
    assert invokes == ["kb"]
    rows = {row.tool_call_id: row for row in _calls(_job(session_id).id)}
    assert rows["call-a"].result == "from-kb"
    assert rows["call-b"].state == "rejected"


def test_expired_hold_with_a_live_owner_does_not_invoke_again():
    user_id, session_id = _pair()
    invokes: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def executor(messages, _tools):
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "web", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        started.set()
        assert release.wait(5)
        return ToolResult(direct_answer="late-body")

    def run():
        set_executor(executor)
        with patch("app.agents.harness.gateway.invoke", fake_invoke):
            _deliver(user_id, session_id, "alpha")

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    job = _job(session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, job.id)
        row.hold_until = datetime(2000, 1, 1)
        row.dispatch_until = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()
    _dispatch(user_id, session_id, expire=True)
    assert invokes == ["web"]
    parked = _job(session_id)
    assert parked.status == "waiting_person"
    assert parked.continuation_due is False
    release.set()
    worker.join(5)
    assert invokes == ["web"]
    finished = _calls(_job(session_id).id)[0]
    assert finished.state == "result"
    assert finished.result == "late-body"
    assert finished.unresolved_at is None
    assert _job(session_id).status == "waiting_person"


def test_crash_before_handoff_can_be_replaced_and_after_handoff_cannot():
    user_id, session_id = _pair()
    invokes: list[str] = []

    def executor(messages, _tools):
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        return ToolResult(direct_answer="once")

    set_executor(executor)
    with patch("app.services.call_owner.mark_handoff", side_effect=SystemExit(7)):
        with pytest_raises():
            with patch("app.agents.harness.gateway.invoke", fake_invoke):
                _deliver(user_id, session_id, "alpha")
    row = _calls(_job(session_id).id)[0]
    assert row.state == "running"
    assert invokes == []
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        attempt = db.query(ToolCallAttempt).filter(ToolCallAttempt.tool_call_pk == row.id).one()
        assert attempt.handed_off_at is None
        delivery = db.get(TaskDelivery, attempt.delivery_id)
        delivery.exited_at = datetime(2026, 1, 1)
        job = db.get(TaskRun, _job(session_id).id)
        job.hold_until = datetime(2000, 1, 1)
        from app.services.call_owner import recover_expired

        assert recover_expired(db, job, now=datetime(2001, 1, 1)) == "replace"
        db.commit()
        replaced = db.query(ToolCall).filter(ToolCall.id == row.id).one()
        step = db.get(TaskStep, replaced.task_step_id)
        assert replaced.state == "pending"
        assert step.phase == "calls_saved"
    finally:
        db.close()
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _dispatch(user_id, session_id, expire=True)
    assert invokes == ["kb"]
    assert _calls(_job(session_id).id)[0].result == "once"


def pytest_raises():
    import pytest

    return pytest.raises(SystemExit)


def test_duplicate_delivery_does_not_invoke_the_handed_off_call(monkeypatch):
    user_id, session_id = _pair()
    invokes: list[str] = []

    def executor(messages, _tools):
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "web", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        raise SystemExit(7)

    set_executor(executor)
    import pytest

    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        with pytest.raises(SystemExit):
            _deliver(user_id, session_id, "alpha")
    assert invokes == ["web"]
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _drive_held(user_id, session_id)
    assert invokes == ["web"]
    assert _calls(_job(session_id).id)[0].state == "running"


def test_same_incarnation_pid_reuse_exits_only_the_bound_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNI_DELIVERY_DIR", str(tmp_path))
    (tmp_path / "incarnation").write_text("prior-parent\n", encoding="utf-8")
    published = publish_incarnation()
    assert published != "prior-parent"
    db = SessionLocal()
    try:
        set_rls_context(db, 1)
        first = SimpleNamespace(id="req-same")
        second = SimpleNamespace(id="req-same")
        mint_for_request(db, first, 100, None)
        mint_for_request(db, second, 100, None)
        assert first._omni_delivery_id != second._omni_delivery_id
        assert exit_request(db, first, "signal 9") == 1
        dead = db.get(TaskDelivery, first._omni_delivery_id)
        live = db.get(TaskDelivery, second._omni_delivery_id)
        db.refresh(dead)
        db.refresh(live)
        assert dead.exited_at is not None
        assert live.exited_at is None
        assert dead.worker_incarnation == live.worker_incarnation == incarnation()
        claimed = claim_mailbox(db, "req-same", 100)
        assert claimed == second._omni_delivery_id
    finally:
        db.close()


def test_cross_incarnation_exit_does_not_mark_the_other_row():
    db = SessionLocal()
    try:
        suffix = uuid4().hex
        one = TaskDelivery(
            delivery_id=f"del-a-{suffix}",
            worker_incarnation="inc-a",
            celery_request_id=f"req-{suffix}",
            child_pid=100,
        )
        other = TaskDelivery(
            delivery_id=f"del-b-{suffix}",
            worker_incarnation="inc-b",
            celery_request_id=f"req-{suffix}",
            child_pid=100,
        )
        db.add(one)
        db.add(other)
        db.commit()
        assert mark_exit(db, one.delivery_id, "signal 9") == 1
        db.refresh(one)
        db.refresh(other)
        assert one.exited_at is not None
        assert other.exited_at is None
    finally:
        db.close()


def test_acknowledgement_creates_one_call_only_for_the_displayed_id():
    user_id, session_id = _pair()
    invokes: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def executor(messages, _tools):
        return _turn(ToolCallSpec("call-parked", "web", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        started.set()
        assert release.wait(5)
        return ToolResult(direct_answer="old")

    def run():
        set_executor(executor)
        with patch("app.agents.harness.gateway.invoke", fake_invoke):
            _deliver(user_id, session_id, "alpha")

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    job = _job(session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, job.id)
        row.hold_until = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()
    _dispatch(user_id, session_id, expire=True)
    parked = _job(session_id)
    assert parked.status == "waiting_person"
    _intent(user_id, int(parked.generation), " call-parked please", "allow")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        assert acknowledge(db, user_id=user_id, generation=int(parked.generation), call_id="call-parked", submitted_control=False) is False
        assert acknowledge(db, user_id=user_id, generation=int(parked.generation), call_id="call-other", submitted_control=True) is False
        assert acknowledge(db, user_id=user_id, generation=int(parked.generation), call_id="call-parked", submitted_control=True) is True
        rows = _calls(parked.id)
        assert len(rows) == 2
        assert [row.state for row in rows] == ["running", "pending"]
    finally:
        db.close()
    release.set()
    worker.join(5)
    assert invokes == ["web"]


def test_late_result_after_stop_does_not_revive_the_call():
    user_id, session_id = _pair()

    def executor(messages, _tools):
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    def fake_invoke(name, _ctx, _args):
        from app.services import task_run

        task_run.stop(user_id=user_id, generation=_job(session_id).generation)
        return ToolResult(direct_answer="too-late")

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _deliver(user_id, session_id, "alpha")
    job = _job(session_id)
    assert job.status == "stopped"
    row = _calls(job.id)[0]
    assert row.state == "cancelled"
    assert row.result != "too-late"
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        effects = db.query(ToolCallEffect).filter(ToolCallEffect.tool_call_pk == row.id).all()
        assert [item.body for item in effects] == ["too-late"]
        assert job.status == "stopped"
    finally:
        db.close()


def _bare_job(db, user_id: int, session_id: int) -> TaskRun:
    job = TaskRun(
        user_id=user_id,
        session_id=session_id,
        generation=1,
        status="running",
        allowed_tools="[]",
        kind="job",
        goal="effect",
        intent_version=1,
        exec_epoch=1,
    )
    db.add(job)
    db.flush()
    return job


def test_effect_conflict_keeps_the_first_body():
    user_id, session_id = _pair()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = _bare_job(db, user_id, session_id)
        step = TaskStep(user_id=user_id, task_run_id=job.id, seq=9, phase="awaiting_results", content="")
        db.add(step)
        db.flush()
        call = ToolCall(
            user_id=user_id,
            task_step_id=step.id,
            tool_call_id="call-effect",
            position=0,
            name="kb",
            arguments="{}",
            state="running",
            intent_version=1,
            owner_token="owner",
            attempt=1,
        )
        db.add(call)
        db.flush()
        assert insert_effect(db, tool_call_pk=call.id, owner_token="owner", attempt=1, body="first") == "stored"
        assert insert_effect(db, tool_call_pk=call.id, owner_token="owner", attempt=1, body="first") == "same"
        assert insert_effect(db, tool_call_pk=call.id, owner_token="owner", attempt=1, body="other") == "conflict"
        db.commit()
        kept = db.query(ToolCallEffect).filter(ToolCallEffect.tool_call_pk == call.id).one()
        assert kept.body == "first"
        assert db.query(ToolCallEffectConflict).filter(ToolCallEffectConflict.tool_call_pk == call.id).count() == 1
        assert audit_unattributed(db, tool_call_pk=call.id, body="legacy") == "stored"
        legacy = (
            db.query(ToolCallEffect)
            .filter(ToolCallEffect.tool_call_pk == call.id, ToolCallEffect.owner_token.is_(None))
            .one()
        )
        assert legacy.owner_token is None
        assert call.owner_token == "owner"
    finally:
        db.close()


def test_legacy_mark_is_dry_run_safe_and_does_not_mint_an_owner():
    user_id, session_id = _pair()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = _bare_job(db, user_id, session_id)
        step = TaskStep(user_id=user_id, task_run_id=job.id, seq=8, phase="awaiting_results", content="")
        db.add(step)
        db.flush()
        call = ToolCall(
            user_id=user_id,
            task_step_id=step.id,
            tool_call_id="legacy",
            position=0,
            name="kb",
            arguments="{}",
            state="running",
            intent_version=1,
        )
        db.add(call)
        job.status = "running"
        db.commit()
        preview = mark_legacy_unresolved(db, dry_run=True)
        assert preview["applied"] is False
        db.refresh(call)
        assert call.unresolved_at is None
        assert call.owner_token is None
        applied = mark_legacy_unresolved(db, dry_run=False)
        db.commit()
        assert applied["applied"] is True
        db.refresh(call)
        db.refresh(job)
        assert call.unresolved_at is not None
        assert call.owner_token is None
        assert call.state == "running"
        assert job.status == "waiting_person"
        assert job.continuation_due is False
        again = mark_legacy_unresolved(db, dry_run=False)
        db.commit()
        assert again["calls"] == 0
    finally:
        db.close()
    from app.services.call_policy import unresolved_sentence

    sentence = unresolved_sentence("legacy")
    snapshot, messages, events = _connected(user_id, session_id)
    visible = snapshot["jobs"][-1]
    assert visible["status"] == "waiting_person"
    assert visible["unresolved_call_id"] == "legacy"
    assert visible["unresolved_sentence"] == sentence
    cards = [
        item["scheduleCard"]
        for item in messages
        if item["scheduleCard"] and item["scheduleCard"].get("mode") == "acknowledge"
    ]
    assert len(cards) == 1
    assert cards[0]["call_id"] == "legacy"
    assert cards[0]["sentence"] == sentence
    assert ("status", "waiting_person") in events
    assert ("message", sentence) in events


def test_a_missed_claim_rejects_instead_of_acking(monkeypatch):
    import pytest
    from celery.exceptions import Reject

    from app.tasks.job import run_job

    monkeypatch.setattr("app.core.config.settings.celery_task_always_eager", False)
    monkeypatch.setattr("app.tasks.job.settings.celery_task_always_eager", False)
    run_job.push_request(id="req-miss")
    try:
        with patch("app.services.delivery_bind.claim_mailbox", return_value=None):
            with pytest.raises(Reject) as caught:
                run_job.run(1, "token", 1, 1)
    finally:
        run_job.pop_request()
    assert caught.value.requeue is True


def test_mint_does_not_adopt_a_leftover_incarnation(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("OMNI_DELIVERY_DIR", str(tmp_path))
    (tmp_path / "incarnation").write_text("prior-parent\n", encoding="utf-8")
    db = SessionLocal()
    try:
        set_rls_context(db, 1)
        before = db.query(TaskDelivery).filter(TaskDelivery.celery_request_id == "req-prior").count()
        with pytest.raises(IncarnationMissing):
            mint_for_request(db, SimpleNamespace(id="req-prior"), 100, None)
        assert (tmp_path / "incarnation").read_text(encoding="utf-8").strip() == "prior-parent"
        assert db.query(TaskDelivery).filter(TaskDelivery.celery_request_id == "req-prior").count() == before
        published = publish_incarnation()
        assert published != "prior-parent"
        assert publish_incarnation() == published
        request = SimpleNamespace(id="req-prior")
        mint_for_request(db, request, 100, None)
        row = db.get(TaskDelivery, request._omni_delivery_id)
        assert row is not None
        assert row.worker_incarnation == published == incarnation()
    finally:
        db.close()


def test_claim_does_not_mint_an_incarnation(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNI_DELIVERY_DIR", str(tmp_path))
    db = SessionLocal()
    try:
        set_rls_context(db, 1)
        assert claim_mailbox(db, "req-missing", os.getpid(), wait_seconds=0) is None
        assert not (tmp_path / "incarnation").exists()
    finally:
        db.close()


def test_worker_queue_does_not_consume_the_default_backlog():
    assert worker_queues() == ("ingest", "maintenance")
    assert "default" not in worker_queues(consume_loop=True)
    entry = Path(__file__).resolve().parents[1] / "entrypoint.sh"
    text = entry.read_text(encoding="utf-8")
    assert "-Q ingest,maintenance" in text
    assert "default" not in text
    assert "loop" not in text
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    assert "loop" not in compose.read_text(encoding="utf-8")


def _connected(user_id: int, session_id: int):
    from app.models.models import Message, SessionEvent
    from app.services.message_format import serialize_message
    from app.services.resume import read_snapshot

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        snapshot = read_snapshot(db, user_id=user_id, session_id=session_id)
        messages = [
            serialize_message(row)
            for row in db.query(Message).filter(Message.session_id == session_id).order_by(Message.id.asc()).all()
        ]
        events = [
            (row.kind, row.body)
            for row in db.query(SessionEvent)
            .filter(SessionEvent.session_id == session_id)
            .order_by(SessionEvent.seq.asc())
            .all()
        ]
        return snapshot, messages, events
    finally:
        db.close()


def test_unobserved_park_publishes_the_acknowledgement_and_keeps_the_owner():
    from app.services.call_policy import unresolved_sentence

    user_id, session_id = _pair()
    invokes: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def executor(messages, _tools):
        return _turn(ToolCallSpec("call-a", "web", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        started.set()
        assert release.wait(5)
        return ToolResult(direct_answer="late-body")

    def run():
        set_executor(executor)
        with patch("app.agents.harness.gateway.invoke", fake_invoke):
            _deliver(user_id, session_id, "alpha")

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    job = _job(session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, job.id)
        row.hold_until = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()
    _dispatch(user_id, session_id, expire=True)
    sentence = unresolved_sentence("call-a")
    snapshot, messages, events = _connected(user_id, session_id)
    parked = snapshot["jobs"][-1]
    assert parked["status"] == "waiting_person"
    assert parked["unresolved_call_id"] == "call-a"
    assert parked["unresolved_sentence"] == sentence
    cards = [item["scheduleCard"] for item in messages if item["scheduleCard"] and item["scheduleCard"].get("mode") == "acknowledge"]
    assert len(cards) == 1
    assert cards[0]["call_id"] == "call-a"
    assert cards[0]["sentence"] == sentence
    assert ("status", "waiting_person") in events
    assert ("message", sentence) in events
    _intent(user_id, int(job.generation), " call-a please continue", "allow")
    assert invokes == ["web"]
    assert len(_calls(job.id)) == 1
    release.set()
    worker.join(5)
    assert invokes == ["web"]
    finished = _calls(job.id)[0]
    assert finished.state == "result"
    assert finished.result == "late-body"


def _observed_exit_after_handoff(name: str) -> None:
    from app.services.call_policy import UNKNOWN_SENTENCE

    user_id, session_id = _pair()
    invokes: list[str] = []

    def executor(messages, _tools):
        return _turn(ToolCallSpec("call-a", name, "{}"))

    def fake_invoke(tool_name, _ctx, _args):
        invokes.append(tool_name)
        raise SystemExit(7)

    set_executor(executor)
    import pytest

    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        with pytest.raises(SystemExit):
            _deliver(user_id, session_id, "alpha")
    assert invokes == [name]
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        call = _calls(_job(session_id).id)[0]
        attempt = db.query(ToolCallAttempt).filter(ToolCallAttempt.tool_call_pk == call.id).one()
        delivery = db.get(TaskDelivery, attempt.delivery_id)
        delivery.exited_at = datetime(2026, 1, 1)
        job = db.get(TaskRun, _job(session_id).id)
        job.hold_until = datetime(2000, 1, 1)
        db.commit()
        delivery_id = attempt.delivery_id
    finally:
        db.close()
    _dispatch(user_id, session_id, expire=True)
    job = _job(session_id)
    call = _calls(job.id)[0]
    assert call.state == "unknown"
    assert call.result == UNKNOWN_SENTENCE
    assert job.status == "waiting_person"
    assert job.continuation_due is False
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        step = db.get(TaskStep, call.task_step_id)
        assert step.phase == "results_ready"
        attempts = db.query(ToolCallAttempt).filter(ToolCallAttempt.tool_call_pk == call.id).all()
        assert [row.delivery_id for row in attempts] == [delivery_id]
    finally:
        db.close()
    snapshot, messages, events = _connected(user_id, session_id)
    visible = snapshot["jobs"][-1]
    assert visible["status"] == "waiting_person"
    assert visible["unresolved_call_id"] == "call-a"
    assert visible["unresolved_sentence"] == UNKNOWN_SENTENCE
    assert any(UNKNOWN_SENTENCE in (item["content"] or "") for item in messages)
    assert ("status", "waiting_person") in events
    assert ("message", UNKNOWN_SENTENCE) in events
    assert not any(item["scheduleCard"] and item["scheduleCard"].get("mode") == "acknowledge" for item in messages)
    _intent(user_id, int(job.generation), " try again", "allow")

    def later(messages, _tools):
        return _turn(content="noted", finish="stop")

    set_executor(later)
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _dispatch(user_id, session_id)
    assert invokes == [name]
    fresh = _calls(job.id)[0]
    assert fresh.state == "unknown"
    assert fresh.result == UNKNOWN_SENTENCE
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        attempts = db.query(ToolCallAttempt).filter(ToolCallAttempt.delivery_id == delivery_id).all()
        assert len(attempts) == 1
    finally:
        db.close()


def test_observed_exit_after_handoff_shows_unknown_and_does_not_replay():
    for name in ("web", "web_fetch", "sandbox"):
        _observed_exit_after_handoff(name)


def test_migration_upgrade_does_not_stamp_running_calls(tmp_path):
    import subprocess
    import sys

    from sqlalchemy import create_engine, text

    database = tmp_path / "owner.db"
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

    before = alembic("upgrade", "0025_confirm_until")
    assert before.returncode == 0, before.stderr
    engine = create_engine(env["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (external_id, name, password_hash, is_active, is_admin) "
                "VALUES ('legacy-user', 'legacy', '', 1, 0)"
            )
        )
        user_id = conn.execute(text("SELECT id FROM users")).scalar_one()
        conn.execute(text("INSERT INTO sessions (user_id, title) VALUES (:user_id, 'legacy')"), {"user_id": user_id})
        session_id = conn.execute(text("SELECT id FROM sessions")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO task_runs (user_id, session_id, generation, status, allowed_tools, kind, goal, "
                "web_constraint, intent_version, dispatch_epoch, exec_epoch, continuation_due) "
                "VALUES (:user_id, :session_id, 1, 'running', '[]', 'job', 'legacy', 'allow', 1, 0, 1, 0)"
            ),
            {"user_id": user_id, "session_id": session_id},
        )
        task_id = conn.execute(text("SELECT id FROM task_runs")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO task_steps (user_id, task_run_id, seq, phase, content, reasoning_content) "
                "VALUES (:user_id, :task_id, 1, 'awaiting_results', '', '')"
            ),
            {"user_id": user_id, "task_id": task_id},
        )
        step_id = conn.execute(text("SELECT id FROM task_steps")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO tool_calls (user_id, task_step_id, tool_call_id, position, name, arguments, state, intent_version) "
                "VALUES (:user_id, :step_id, 'legacy-call', 0, 'web', '{}', 'running', 1)"
            ),
            {"user_id": user_id, "step_id": step_id},
        )
    upgraded = alembic("upgrade", "0026_call_owner")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as conn:
        call = conn.execute(text("SELECT state, owner_token, unresolved_at FROM tool_calls")).one()
        job = conn.execute(text("SELECT status, continuation_due FROM task_runs")).one()
    assert call.state == "running"
    assert call.owner_token is None
    assert call.unresolved_at is None
    assert job.status == "running"
    assert not job.continuation_due
    engine.dispose()


def test_preflight_reports_without_writing():
    from app.services.call_owner import mark_legacy_unresolved as preview

    user_id, session_id = _pair()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        before = db.query(ToolCall).filter(ToolCall.unresolved_at.isnot(None)).count()
        report = preview(db, dry_run=True)
        after = db.query(ToolCall).filter(ToolCall.unresolved_at.isnot(None)).count()
        assert report["applied"] is False
        assert after == before
        assert os.environ.get("OMNI_CONSUME_LOOP_QUEUE", "") == ""
    finally:
        db.close()
