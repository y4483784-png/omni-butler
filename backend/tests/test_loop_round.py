from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, set_rls_context
from app.models.models import CalendarEvent, Message, ScheduleConfirmClaim, Session as ChatSession, TaskImage, TaskRun, TaskStep, ToolCall, User
from app.services import task_run
from app.services.loop_round import (
    WEB_DENIAL,
    Call,
    CallState,
    Hold,
    IllegalPhase,
    LoopEvent,
    ModelTurn,
    Phase,
    Step,
    ToolCallSpec,
    apply,
    begin_read,
    cancel_open_calls,
    confirm_call,
    note_unknown,
    observe_ready,
    open_cards,
    phase_after,
    save_read_result,
    save_turn,
    take_created_hold,
)
from app.services.schedule_confirm import ScheduleConfirmClosed


@pytest.fixture(autouse=True)
def _release_sqlite_lock():
    yield
    from app.core.db import engine

    engine.dispose()


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"loop-{uuid4().hex}"
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
        row = ChatSession(user_id=user_id, title="loop")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def _held(web: str = "allow", goal: str = "alpha") -> tuple[Session, Hold, task_run.Turn]:
    user_id = _user()
    session_id = _session(user_id)
    db = SessionLocal()
    set_rls_context(db, user_id)
    turn = task_run.open_alongside(
        db,
        user_id=user_id,
        session_id=session_id,
        kind="job",
        goal=goal,
        web_constraint=web,
    )
    row = db.get(TaskRun, turn.id)
    row.exec_token = "worker"
    row.exec_epoch = 1
    row.hold_until = datetime(2030, 1, 1)
    db.commit()
    hold = Hold(
        user_id=user_id,
        generation=turn.generation,
        exec_token="worker",
        exec_epoch=1,
        intent_version=1,
    )
    return db, hold, turn


def _model(*specs: ToolCallSpec, content: str = "", reasoning: str = "") -> ModelTurn:
    return ModelTurn(
        content=content,
        reasoning=reasoning,
        tool_calls=specs,
        finish="tool_calls" if specs else "stop",
    )


def _steps(db: Session, task_run_id: int) -> list[TaskStep]:
    return (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == task_run_id)
        .order_by(TaskStep.seq.asc())
        .all()
    )


def _call_rows(db: Session, step_id: int) -> list[ToolCall]:
    return (
        db.query(ToolCall)
        .filter(ToolCall.task_step_id == step_id)
        .order_by(ToolCall.position.asc())
        .all()
    )


def _job(db: Session, task_run_id: int) -> TaskRun:
    row = db.get(TaskRun, task_run_id)
    db.refresh(row)
    return row


def _mail(subject: str) -> dict:
    return {"mail_to": ["one@example.com"], "subject": subject, "body": "hello"}


def _pending(call_id: str, name: str) -> Call:
    return Call(
        tool_call_id=call_id,
        name=name,
        state=CallState.PENDING,
        claim_token=None,
        intent_version=1,
    )


def test_phase_after_keeps_final_answer_distinct_from_ready_results():
    assert phase_after(()) is Phase.FINAL_ANSWER
    pending = (_pending("a", "web"),)
    assert phase_after(pending) is Phase.CALLS_SAVED
    mixed = (
        _pending("a", "web"),
        Call("b", "kb", CallState.RESULT, None, 1),
    )
    assert phase_after(mixed) is Phase.AWAITING_RESULTS
    ready = (
        Call("a", "web", CallState.RESULT, None, 1),
        Call("b", "kb", CallState.REJECTED, None, 1),
    )
    assert phase_after(ready) is Phase.RESULTS_READY
    saved = apply(None, LoopEvent.SAVE_CALLS, calls=pending, seq=3)
    denied = apply(saved, LoopEvent.DENY, call_id="a")
    assert denied.calls[0].state is CallState.REJECTED
    unknown = Step(
        seq=1,
        phase=Phase.AWAITING_RESULTS,
        calls=(Call("a", "desktop.open", CallState.UNKNOWN, "tok", 1),),
    )
    with pytest.raises(IllegalPhase):
        apply(unknown, LoopEvent.START_READ, call_id="a")
    with pytest.raises(IllegalPhase):
        apply(unknown, LoopEvent.OPEN_CARDS, call_id="a")
    with pytest.raises(IllegalPhase):
        apply(None, LoopEvent.SAVE_CALLS, calls=())


def test_save_turn_commits_pending_calls_before_any_result():
    db, hold, turn = _held()
    saved = save_turn(
        db,
        hold=hold,
        turn=_model(
            ToolCallSpec("call-a", "web", "{}"),
            ToolCallSpec("call-b", "kb", "{}"),
            content="looking",
            reasoning="because",
        ),
    )
    assert saved is not None
    assert saved.phase is Phase.CALLS_SAVED
    steps = _steps(db, turn.id)
    assert len(steps) == 1
    assert steps[0].content == "looking"
    assert steps[0].reasoning_content == "because"
    rows = _call_rows(db, steps[0].id)
    assert [(row.tool_call_id, row.state, row.result) for row in rows] == [
        ("call-a", "pending", None),
        ("call-b", "pending", None),
    ]
    again = save_turn(
        db,
        hold=hold,
        turn=_model(ToolCallSpec("call-a", "web", "{}"), ToolCallSpec("call-b", "kb", "{}"), content="looking"),
    )
    assert again is not None
    assert again.seq == saved.seq
    assert len(_steps(db, turn.id)) == 1


def test_a_failed_save_leaves_no_call_rows():
    db, hold, turn = _held()

    def fail() -> None:
        db.rollback()
        raise RuntimeError("forced")

    db.commit = fail
    with pytest.raises(RuntimeError, match="forced"):
        save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "web", "{}")))
    assert _steps(db, turn.id) == []
    assert db.query(ToolCall).filter(ToolCall.user_id == hold.user_id).count() == 0


def test_final_answer_completes_and_a_second_save_does_not_insert():
    db, hold, turn = _held()
    image = TaskImage(
        user_id=hold.user_id,
        task_run_id=turn.id,
        media_type="image/png",
        byte_length=4,
        sha256="abc",
        storage_key=f"task-image/{uuid4().hex}",
        purge_state="ready",
    )
    db.add(image)
    db.commit()
    try:
        saved = save_turn(db, hold=hold, turn=_model(content="done", reasoning=""))
        assert saved is not None
        assert saved.phase is Phase.FINAL_ANSWER
        job = _job(db, turn.id)
        assert job.status == "completed"
        assert job.exec_token is None
        assert job.continuation_due is False
        db.refresh(image)
        assert image.purge_state == "pending_delete"
        notes = db.query(Message).filter(Message.session_id == turn.session_id, Message.role == "assistant").all()
        assert [row.content for row in notes] == ["done"]
        again = save_turn(db, hold=hold, turn=_model(content="other"))
        assert again is not None
        assert again.seq == saved.seq
        assert len(_steps(db, turn.id)) == 1
    finally:
        db.delete(image)
        db.commit()


def test_forbid_stores_a_terminal_denial_for_web_and_web_fetch():
    for name in ("web", "web_fetch"):
        db, hold, turn = _held(web="forbid")
        save_turn(db, hold=hold, turn=_model(ToolCallSpec(name, name, "{}")))
        assert begin_read(db, hold=hold, tool_call_id=name) == "denied"
        row = _call_rows(db, _steps(db, turn.id)[0].id)[0]
        assert row.state == "rejected"
        assert row.result == WEB_DENIAL
        job = _job(db, turn.id)
        assert job.status == "running"
        assert job.exec_token == "worker"


def test_stale_intent_does_not_store_a_result():
    db, hold, turn = _held()
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "kb", "{}")))
    job = _job(db, turn.id)
    job.intent_version = 2
    db.commit()
    assert begin_read(db, hold=hold, tool_call_id="call-a") == "stale"
    row = _call_rows(db, _steps(db, turn.id)[0].id)[0]
    assert row.state == "pending"
    assert row.result is None


def test_holder_ready_keeps_the_token_and_clears_a_due_flag():
    db, hold, turn = _held()
    save_turn(
        db,
        hold=hold,
        turn=_model(ToolCallSpec("call-a", "web", "{}"), ToolCallSpec("call-b", "kb", "{}")),
    )
    assert begin_read(db, hold=hold, tool_call_id="call-a") == "invoke"
    first, entered = save_read_result(db, hold=hold, tool_call_id="call-a", result="pages")
    assert entered is False
    assert first is not None
    assert first.phase is Phase.AWAITING_RESULTS
    assert begin_read(db, hold=hold, tool_call_id="call-b") == "invoke"
    job = _job(db, turn.id)
    job.continuation_due = True
    db.commit()
    second, entered = save_read_result(
        db,
        hold=hold,
        tool_call_id="call-b",
        result="no",
        rejected=True,
    )
    assert entered is True
    assert second is not None
    assert second.phase is Phase.RESULTS_READY
    job = _job(db, turn.id)
    assert job.status == "running"
    assert job.exec_token == "worker"
    assert job.continuation_due is False
    notes = db.query(Message).filter(Message.session_id == turn.session_id, Message.role == "assistant").all()
    assert [row.content for row in notes] == ["pages", "no"]


def test_open_cards_leaves_waiting_and_the_old_epoch_cannot_renew():
    db, hold, turn = _held()
    past = datetime(2020, 1, 1)
    job = _job(db, turn.id)
    job.last_progress_at = past
    db.commit()
    renewed = task_run.renew_hold(
        db,
        user_id=hold.user_id,
        generation=hold.generation,
        exec_token=hold.exec_token,
        exec_epoch=hold.exec_epoch,
        intent_version=hold.intent_version,
    )
    assert renewed == 1
    assert _job(db, turn.id).last_progress_at == past
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "mail.send", json.dumps(_mail("Hello")))))
    assert _job(db, turn.id).last_progress_at != past
    step = _steps(db, turn.id)[0]
    _job(db, turn.id).last_progress_at = past
    db.commit()
    tokens = open_cards(db, hold=hold, step_id=step.id)
    assert len(tokens) == 1
    job = _job(db, turn.id)
    assert job.status == "waiting_person"
    assert job.exec_token is None
    assert job.continuation_due is False
    assert job.last_progress_at != past
    assert task_run.renew_hold(
        db,
        user_id=hold.user_id,
        generation=hold.generation,
        exec_token="worker",
        exec_epoch=1,
        intent_version=1,
    ) == 0
    card = json.loads(
        db.query(Message)
        .filter(Message.session_id == turn.session_id, Message.schedule_card != "")
        .one()
        .schedule_card
    )
    assert card["confirm_token"] == tokens[0]
    assert card["action"] == "mail.send"
    assert card["subject"] == "Hello"


def test_saving_a_result_moves_progress_and_a_renewal_does_not():
    db, hold, turn = _held()
    past = datetime(2020, 1, 1)
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "kb", "{}")))
    _job(db, turn.id).last_progress_at = past
    db.commit()
    assert begin_read(db, hold=hold, tool_call_id="call-a") == "invoke"
    assert _job(db, turn.id).last_progress_at != past
    _job(db, turn.id).last_progress_at = past
    db.commit()
    save_read_result(db, hold=hold, tool_call_id="call-a", result="hit")
    assert _job(db, turn.id).last_progress_at != past


def test_two_cards_set_continuation_only_on_the_last_confirm():
    db, hold, turn = _held()
    first = _mail("First")
    second = _mail("Second")
    save_turn(
        db,
        hold=hold,
        turn=_model(
            ToolCallSpec("call-a", "mail.send", json.dumps(first)),
            ToolCallSpec("call-b", "mail.send", json.dumps(second)),
        ),
    )
    step = _steps(db, turn.id)[0]
    tokens = open_cards(db, hold=hold, step_id=step.id)
    assert len(tokens) == 2
    confirm_call(db, user_id=hold.user_id, token=tokens[0], outcome="confirm", raw=first)
    job = _job(db, turn.id)
    assert job.status == "waiting_person"
    assert job.continuation_due is False
    assert job.exec_token is None
    confirm_call(db, user_id=hold.user_id, token=tokens[1], outcome="confirm", raw=second)
    job = _job(db, turn.id)
    assert job.continuation_due is True
    assert job.exec_token is None
    assert job.status == "waiting_person"
    assert _steps(db, turn.id)[0].phase == "results_ready"


def test_a_second_confirm_does_not_write_a_calendar_row():
    db, hold, turn = _held()
    title = f"loop-{uuid4().hex}"
    raw = {
        "action": "create",
        "title": title,
        "start_at": "2026-11-20T10:00:00",
        "end_at": "2026-11-20T11:00:00",
    }
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "calendar", json.dumps(raw))))
    step = _steps(db, turn.id)[0]
    tokens = open_cards(db, hold=hold, step_id=step.id)
    assert len(tokens) == 1
    before = db.query(CalendarEvent).filter(CalendarEvent.title == title).count()
    confirm_call(db, user_id=hold.user_id, token=tokens[0], outcome="confirm", raw=raw)
    with pytest.raises(ScheduleConfirmClosed):
        confirm_call(db, user_id=hold.user_id, token=tokens[0], outcome="confirm", raw=raw)
    assert db.query(CalendarEvent).filter(CalendarEvent.title == title).count() == before
    claim = db.get(ScheduleConfirmClaim, tokens[0])
    assert claim.status == "unknown"
    assert claim.remote_request_id == ""


def test_confirm_while_running_keeps_the_token_until_observe_ready():
    db, hold, turn = _held()
    raw = _mail("Alone")
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "mail.send", json.dumps(raw))))
    step = _steps(db, turn.id)[0]
    from app.services.calendar import draft_expires_at
    from app.services.schedule_confirm import bind_claim, parse_card_expiry

    token, _expiry = bind_claim(
        db,
        user_id=hold.user_id,
        session_id=turn.session_id,
        action="mail.send",
        raw=raw,
        task_run_ids=[turn.id],
        expires_at=parse_card_expiry(draft_expires_at()),
    )
    row = _call_rows(db, step.id)[0]
    row.state = "awaiting_confirm"
    row.claim_token = token
    step.phase = "awaiting_results"
    db.commit()
    confirm_call(db, user_id=hold.user_id, token=token, outcome="confirm", raw=raw)
    job = _job(db, turn.id)
    assert job.status == "running"
    assert job.exec_token == "worker"
    assert job.continuation_due is True
    assert observe_ready(db, hold=hold, step_id=step.id) is True
    job = _job(db, turn.id)
    assert job.exec_token == "worker"
    assert job.continuation_due is False
    assert job.status == "running"


def test_an_armed_desktop_open_with_no_finish_becomes_unknown():
    db, hold, turn = _held()
    raw = {"device_id": 7, "local_target": "report", "local_args": []}
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "desktop.open", json.dumps(raw))))
    step = _steps(db, turn.id)[0]
    from app.services.calendar import draft_expires_at
    from app.services.schedule_confirm import bind_claim, parse_card_expiry

    token, _expiry = bind_claim(
        db,
        user_id=hold.user_id,
        session_id=turn.session_id,
        action="desktop.open",
        raw=raw,
        task_run_ids=[turn.id],
        expires_at=parse_card_expiry(draft_expires_at()),
    )
    row = _call_rows(db, step.id)[0]
    row.state = "awaiting_confirm"
    row.claim_token = token
    claim = db.get(ScheduleConfirmClaim, token)
    claim.status = "unknown"
    claim.remote_request_id = "req-1"
    db.commit()
    with patch("app.services.local_action.finish_local") as finish:
        noted = note_unknown(db, hold=hold, tool_call_id="call-a")
        finish.assert_not_called()
    assert noted is not None
    assert _call_rows(db, step.id)[0].state == "unknown"
    again = note_unknown(db, hold=hold, tool_call_id="call-a")
    assert again is not None
    assert _call_rows(db, step.id)[0].state == "unknown"


def test_cancel_open_calls_stops_the_job_and_marks_images():
    db, hold, turn = _held()
    save_turn(
        db,
        hold=hold,
        turn=_model(ToolCallSpec("call-a", "kb", "{}"), ToolCallSpec("call-b", "web", "{}")),
    )
    assert begin_read(db, hold=hold, tool_call_id="call-a") == "invoke"
    save_read_result(db, hold=hold, tool_call_id="call-a", result="hit")
    image = TaskImage(
        user_id=hold.user_id,
        task_run_id=turn.id,
        media_type="image/png",
        byte_length=4,
        sha256="def",
        storage_key=f"task-image/{uuid4().hex}",
        purge_state="staged",
    )
    db.add(image)
    db.commit()
    try:
        assert cancel_open_calls(db, user_id=hold.user_id, generation=hold.generation) == 1
        db.commit()
        rows = _call_rows(db, _steps(db, turn.id)[0].id)
        assert [(row.tool_call_id, row.state) for row in rows] == [("call-a", "result"), ("call-b", "cancelled")]
        job = _job(db, turn.id)
        assert job.status == "stopped"
        assert job.exec_token is None
        assert job.continuation_due is False
        assert job.exec_epoch == 2
        db.refresh(image)
        assert image.purge_state == "tombstone"
    finally:
        db.delete(image)
        db.commit()


def test_take_created_hold_sets_the_token_and_leaves_dispatch_empty():
    user_id = _user()
    session_id = _session(user_id)
    db = SessionLocal()
    set_rls_context(db, user_id)
    turn = task_run.open_alongside(
        db,
        user_id=user_id,
        session_id=session_id,
        kind="job",
        goal="alpha",
    )
    assert take_created_hold(db, user_id=user_id, task_id=turn.id, exec_token="worker") == 1
    db.commit()
    job = _job(db, turn.id)
    assert job.exec_token == "worker"
    assert job.exec_epoch == 1
    assert job.dispatch_token is None
    assert job.dispatch_epoch == 0
    assert job.continuation_due is False
    assert take_created_hold(db, user_id=user_id, task_id=turn.id, exec_token="other") == 0
    db.rollback()


def test_an_illegal_confirm_writes_nothing():
    db, hold, turn = _held()
    raw = _mail("Early")
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "mail.send", json.dumps(raw))))
    step = _steps(db, turn.id)[0]
    from app.services.calendar import draft_expires_at
    from app.services.schedule_confirm import bind_claim, parse_card_expiry

    token, _expiry = bind_claim(
        db,
        user_id=hold.user_id,
        session_id=turn.session_id,
        action="mail.send",
        raw=raw,
        task_run_ids=[turn.id],
        expires_at=parse_card_expiry(draft_expires_at()),
    )
    row = _call_rows(db, step.id)[0]
    row.claim_token = token
    db.commit()
    with pytest.raises(ScheduleConfirmClosed):
        confirm_call(db, user_id=hold.user_id, token=token, outcome="confirm", raw=raw)
    assert _call_rows(db, step.id)[0].state == "pending"
    assert db.get(ScheduleConfirmClaim, token).status == "open"


def test_a_secret_errand_argument_is_not_stored():
    db, hold, turn = _held()
    raw = {
        "merchant_key": "shop",
        "lines": [{"sku": "tea", "qty": 1, "query": "tea"}],
        "ship_to": {"name": "Ada", "phone": "1", "region": "HZ", "address": "1 Road"},
        "pay_method": "card",
        "amount_cap": 10,
        "password": "hidden",
    }
    save_turn(db, hold=hold, turn=_model(ToolCallSpec("call-a", "errand.place", json.dumps(raw))))
    stored = _call_rows(db, _steps(db, turn.id)[0].id)[0].arguments
    assert "hidden" not in stored
    assert "password" not in stored


def test_open_cards_waits_until_reads_are_terminal():
    db, hold, turn = _held()
    save_turn(
        db,
        hold=hold,
        turn=_model(
            ToolCallSpec("call-a", "web", "{}"),
            ToolCallSpec("call-b", "mail.send", json.dumps(_mail("Later"))),
        ),
    )
    step = _steps(db, turn.id)[0]
    assert open_cards(db, hold=hold, step_id=step.id) == ()
    assert _job(db, turn.id).status == "running"
    assert db.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.user_id == hold.user_id).count() == 0
