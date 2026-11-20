from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.db import SessionLocal, set_rls_context
from app.core.llm import ModelTurn, ToolCallSpec
from app.models.models import (
    Message,
    ScheduleConfirmClaim,
    Session as ChatSession,
    SessionCursor,
    SessionEvent,
    TaskRun,
    TaskStep,
    ToolCall,
    User,
)
from app.services.local_action import LocalAction, commit_local, finish_local
from app.services.resume import dispatch_task, enqueue_job, events_after, publish_event, read_snapshot
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
        cursor = check.get(SessionCursor, session_id)
        published = int(cursor.published_seq)
        snapshot = read_snapshot(check, user_id=user_id, session_id=session_id)
        check.commit()
    finally:
        check.close()
    assert stored == [100, 101]
    assert published == 101
    assert snapshot["cursor"] == published
    assert snapshot["cursor"] == 101
    received = [seq for seq in stored if seq > 100]
    assert received == [101]
    assert 100 in stored


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


def _dispatch(user_id: int, task_id: int, *, expire: bool = False) -> None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        if expire:
            row = db.get(TaskRun, task_id)
            row.hold_until = datetime(2000, 1, 1)
            row.dispatch_until = datetime(2000, 1, 1)
            db.commit()
        dispatch_task(db, task_id)
    finally:
        db.close()


def _steps(task_id: int) -> list[TaskStep]:
    db = SessionLocal()
    try:
        rows = db.query(TaskStep).filter(TaskStep.task_run_id == task_id).order_by(TaskStep.seq.asc()).all()
        for row in rows:
            db.expunge(row)
        return rows
    finally:
        db.close()


def _calls(task_id: int) -> list[ToolCall]:
    db = SessionLocal()
    try:
        step_ids = [row.id for row in db.query(TaskStep).filter(TaskStep.task_run_id == task_id).all()]
        rows = (
            db.query(ToolCall)
            .filter(ToolCall.task_step_id.in_(step_ids or [0]))
            .order_by(ToolCall.position.asc())
            .all()
        )
        for row in rows:
            db.expunge(row)
        return rows
    finally:
        db.close()


def test_saved_calls_run_on_the_next_worker_before_the_model():
    from app.agents.harness.types import ToolResult

    user_id = _user()
    session_id = _session(user_id)
    order: list[str] = []

    def executor(messages, _tools):
        order.append("model")
        if any(item.get("role") == "tool" for item in messages):
            return ModelTurn("done", "", (), "stop")
        return ModelTurn("", "", (ToolCallSpec("call-a", "kb", "{}"),), "stop")

    def fake_invoke(name, _ctx, _args):
        order.append("invoke")
        return ToolResult(direct_answer=f"saw {name}")

    set_executor(executor)
    with patch("app.services.loop_round.begin_read", side_effect=SystemExit(7)):
        with pytest.raises(SystemExit):
            db = SessionLocal()
            try:
                set_rls_context(db, user_id)
                enqueue_job(db, user_id=user_id, session_id=session_id, goal="alpha")
            finally:
                db.close()
    db = SessionLocal()
    try:
        task_id = int(
            db.query(TaskRun)
            .filter(TaskRun.session_id == session_id, TaskRun.kind == "job")
            .one()
            .id
        )
    finally:
        db.close()
    assert [(row.tool_call_id, row.state) for row in _calls(task_id)] == [("call-a", "pending")]
    order.clear()
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _dispatch(user_id, task_id, expire=True)
    assert order[0] == "invoke"
    assert order.count("model") == 1
    assert _load(task_id).status == "completed"
    assert _calls(task_id)[0].state == "result"


def test_an_armed_desktop_write_is_looked_up_and_not_sent():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    token = f"arm-{uuid4().hex}"
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        step = TaskStep(
            user_id=user_id,
            task_run_id=task_id,
            seq=1,
            phase="awaiting_results",
            content="",
            reasoning_content="",
        )
        db.add(step)
        db.flush()
        db.add(
            ToolCall(
                user_id=user_id,
                task_step_id=step.id,
                tool_call_id="call-a",
                position=0,
                name="desktop.open",
                arguments=json.dumps({"local_target": "report"}),
                state="awaiting_confirm",
                claim_token=token,
                intent_version=1,
            )
        )
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=user_id,
                session_id=session_id,
                status="unknown",
                action="desktop.open",
                fingerprint=token,
                scope="",
                generation_ids=json.dumps([task_id]),
                remote_request_id="req-1",
                intent_version=1,
            )
        )
        db.commit()
    finally:
        db.close()

    def executor(_messages, _tools):
        assert _calls(task_id)[0].state == "unknown"
        return ModelTurn("done", "", (), "stop")

    sent: list[str] = []
    set_executor(executor)
    with (
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
        patch("app.services.local_action.finish_local", side_effect=lambda *args, **kwargs: sent.append("finish")),
    ):
        _dispatch(user_id, task_id)
    assert sent == []
    call = _calls(task_id)[0]
    assert call.state == "unknown"
    assert call.result == "结果未知"
    claim = SessionLocal()
    try:
        stored = claim.get(ScheduleConfirmClaim, token)
        assert stored.remote_request_id == "req-1"
        assert stored.status == "unknown"
    finally:
        claim.close()


def test_an_old_exec_epoch_discards_the_result():
    from app.services import task_run

    user_id = _user()
    session_id = _session(user_id)
    seen: dict = {}

    def executor(_messages, _tools):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            row = db.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").one()
            seen["token"] = row.exec_token
            seen["epoch"] = int(row.exec_epoch)
            seen["intent"] = int(row.intent_version)
            seen["generation"] = int(row.generation)
            row.exec_epoch = int(row.exec_epoch) + 1
            db.commit()
        finally:
            db.close()
        return ModelTurn("discarded", "", (), "stop")

    set_executor(executor)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        task_id = enqueue_job(db, user_id=user_id, session_id=session_id, goal="alpha")
    finally:
        db.close()
    assert _steps(task_id) == []
    assert "discarded" not in _notes(session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        renewed = task_run.renew_hold(
            db,
            user_id=user_id,
            generation=seen["generation"],
            exec_token=seen["token"],
            exec_epoch=seen["epoch"],
            intent_version=seen["intent"],
        )
    finally:
        db.close()
    assert renewed == 0
    assert _load(task_id).status == "running"


def test_a_saved_final_answer_completes_without_the_model():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        db.add(
            TaskStep(
                user_id=user_id,
                task_run_id=task_id,
                seq=1,
                phase="final_answer",
                content="done",
                reasoning_content="",
            )
        )
        db.commit()
    finally:
        db.close()
    called: list[int] = []
    set_executor(lambda _messages, _tools: called.append(1))
    _dispatch(user_id, task_id)
    finished = _load(task_id)
    assert called == []
    assert finished.status == "completed"
    assert finished.exec_token is None
    assert finished.continuation_due is False
    assert len(_steps(task_id)) == 1


def test_waiting_person_without_continuation_returns_before_the_model():
    from app.tasks import job as job_module

    user_id = _user()
    session_id = _session(user_id)
    called: list[int] = []
    original = job_module.commit_job_output

    def park(*args, **kwargs):
        result = original(*args, **kwargs)
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            row = db.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").one()
            row.status = "waiting_person"
            row.continuation_due = False
            db.commit()
        finally:
            db.close()
        return result

    set_executor(lambda _messages, _tools: called.append(1))
    with patch("app.tasks.job.commit_job_output", park):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            task_id = enqueue_job(db, user_id=user_id, session_id=session_id, goal="alpha")
        finally:
            db.close()
    assert called == []
    waiting = _load(task_id)
    assert waiting.status == "waiting_person"
    assert waiting.continuation_due is False


def _admin() -> int:
    db = SessionLocal()
    try:
        return int(db.query(User).filter(User.external_id == "admin").one().id)
    finally:
        db.close()


def test_a_confirm_card_gets_a_seq_when_no_step_is_added():
    from app.services.schedule_confirm import followup_create_raw, issue_schedule_claim

    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        before = db.query(TaskStep).filter(TaskStep.task_run_id == task_id).count()
        token, _expiry = issue_schedule_claim(
            db,
            user_id=user_id,
            session_id=session_id,
            action="calendar.create",
            raw=followup_create_raw(
                {
                    "id": "act-1",
                    "text": "send the note",
                    "start_at": "2026-11-20T09:00:00",
                    "end_at": "2026-11-20T09:30:00",
                    "evidence": "",
                }
            ),
            task_run_ids=[task_id],
            expires_at=None,
        )
        steps = db.query(TaskStep).filter(TaskStep.task_run_id == task_id).count()
        events = (
            db.query(SessionEvent)
            .filter(SessionEvent.session_id == session_id, SessionEvent.kind == "confirm")
            .all()
        )
    finally:
        db.close()
    assert before == 0
    assert steps == 0
    assert [row.body for row in events] == [token]
    assert int(events[0].seq) > 0


def test_aborting_the_chat_fetch_leaves_the_job_running(plain_client):
    session_id = plain_client.post("/api/sessions").json()["id"]

    def router(_messages, result_model, *_args, **_kwargs):
        return result_model.model_validate(
            {"relation": "new", "target_generation": None, "web": "allow", "ask": ""}
        )

    with (
        patch("app.core.llm.complete_json_object", router),
        patch("app.api.chat._run_workflow_sync", return_value={"intent": "chat", "forced": None, "citations": [], "thinking_steps": [], "llm_messages": [], "direct_answer": "", "answer_reasoning": ""}),
    ):
        with plain_client.stream(
            "POST",
            "/api/chat",
            json={"session_id": session_id, "message": "start alpha"},
        ) as response:
            assert response.status_code == 200
    db = SessionLocal()
    try:
        rows = (
            db.query(TaskRun)
            .filter(TaskRun.session_id == session_id, TaskRun.kind == "job")
            .all()
        )
        for row in rows:
            db.expunge(row)
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0].status == "running"
    assert rows[0].kind == "job"


def test_a_client_at_cursor_100_receives_101_and_closing_the_stream_leaves_the_job(plain_client):
    user_id = _admin()
    session_id = plain_client.post("/api/sessions").json()["id"]
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        cursor = db.get(SessionCursor, session_id)
        if cursor is None:
            db.add(SessionCursor(session_id=session_id, user_id=user_id, published_seq=99))
        else:
            cursor.published_seq = 99
        db.commit()
        first = publish_event(db, user_id=user_id, session_id=session_id, kind="status", body="running")
        second = publish_event(db, user_id=user_id, session_id=session_id, kind="status", body="still")
        db.commit()
    finally:
        db.close()
    assert first == 100
    assert second == 101
    snapshot = plain_client.get(f"/api/sessions/{session_id}/jobs").json()
    assert snapshot["cursor"] == 101
    assert any(row["id"] == task_id and row["status"] == "running" and row["kind"] == "job" for row in snapshot["jobs"])

    async def one_event() -> bytes:
        import anyio

        from app.main import app

        chunks: list[bytes] = []
        got_body = anyio.Event()
        requested = False
        cookie = "; ".join(f"{key}={value}" for key, value in plain_client.cookies.items())
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/api/sessions/{session_id}/events",
            "raw_path": f"/api/sessions/{session_id}/events".encode(),
            "query_string": b"after=100",
            "headers": [(b"cookie", cookie.encode())],
            "client": ("127.0.0.1", 123),
            "server": ("testserver", 80),
            "root_path": "",
        }

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await got_body.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
            elif message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                got_body.set()

        with anyio.fail_after(5):
            await app(scope, receive, send)
        return b"".join(chunks)

    with patch("app.services.task_run.stop") as stop:
        import anyio

        seen = anyio.run(one_event).decode()
    assert "data:" in seen
    line = next(part for part in seen.split("\n") if part.startswith("data:"))
    assert json.loads(line[len("data:") :].strip())["seq"] == 101
    assert stop.call_count == 0
    assert _load(task_id).status == "running"


def test_a_second_job_event_is_not_hidden_by_the_first_jobs_step_seq():
    user_id = _user()
    session_id = _session(user_id)
    first = _open_job(user_id, session_id, goal="one")
    second = _open_job(user_id, session_id, goal="two")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        db.add(
            TaskStep(
                user_id=user_id,
                task_run_id=first,
                seq=50,
                phase="calls_saved",
                content="",
                reasoning_content="",
            )
        )
        db.commit()
        published = publish_event(
            db,
            user_id=user_id,
            session_id=session_id,
            kind="status",
            body=str(second),
        )
        db.commit()
        delivered = events_after(db, session_id=session_id, after=0)
        first_window = read_snapshot(db, user_id=user_id, session_id=session_id)
        db.commit()
        second_window = read_snapshot(db, user_id=user_id, session_id=session_id)
        db.commit()
    finally:
        db.close()
    assert published == 3
    assert [row["seq"] for row in delivered] == [1, 2, 3]
    assert delivered[-1]["body"] == str(second)
    assert first_window["cursor"] == 3
    assert second_window == first_window
    assert {row["id"]: row["status"] for row in first_window["jobs"]} == {first: "running", second: "running"}


def test_an_event_published_after_the_stream_opens_is_delivered(plain_client):
    user_id = _admin()
    session_id = plain_client.post("/api/sessions").json()["id"]
    task_id = _open_job(user_id, session_id)

    async def one_event() -> bytes:
        import anyio

        from app.main import app

        chunks: list[bytes] = []
        started = anyio.Event()
        got_body = anyio.Event()
        requested = False
        cookie = "; ".join(f"{key}={value}" for key, value in plain_client.cookies.items())
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/api/sessions/{session_id}/events",
            "raw_path": f"/api/sessions/{session_id}/events".encode(),
            "query_string": b"after=0",
            "headers": [(b"cookie", cookie.encode())],
            "client": ("127.0.0.1", 123),
            "server": ("testserver", 80),
            "root_path": "",
        }

        def publish() -> None:
            db = SessionLocal()
            try:
                set_rls_context(db, user_id)
                publish_event(db, user_id=user_id, session_id=session_id, kind="status", body="later")
                db.commit()
            finally:
                db.close()

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await got_body.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                started.set()
            elif message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                if b"later" in message["body"]:
                    got_body.set()

        async def publish_after_open() -> None:
            await started.wait()
            await anyio.to_thread.run_sync(publish)

        with anyio.fail_after(5):
            async with anyio.create_task_group() as group:
                group.start_soon(publish_after_open)
                await app(scope, receive, send)
        return b"".join(chunks)

    with patch("app.services.task_run.stop") as stop:
        import anyio

        seen = anyio.run(one_event).decode()
    assert "later" in seen
    assert stop.call_count == 0
    assert _load(task_id).status == "running"


def test_a_snapshot_waits_for_the_status_commit():
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    first = SessionLocal()
    set_rls_context(first, user_id)
    row = first.get(TaskRun, task_id)
    row.status = "waiting_person"
    allocated = publish_event(
        first,
        user_id=user_id,
        session_id=session_id,
        kind="status",
        body="waiting_person",
    )
    started = threading.Event()
    finished = threading.Event()
    box: dict = {}

    def reader() -> None:
        other = SessionLocal()
        try:
            set_rls_context(other, user_id)
            started.set()
            box["snapshot"] = read_snapshot(other, user_id=user_id, session_id=session_id)
            other.commit()
        except Exception as exc:
            box["error"] = exc
            other.rollback()
        finally:
            other.close()
            finished.set()

    thread = threading.Thread(target=reader)
    thread.start()
    assert started.wait(2)
    time.sleep(0.4)
    assert not finished.is_set()
    first.commit()
    first.close()
    assert finished.wait(5)
    thread.join(timeout=5)
    assert "error" not in box
    snapshot = box["snapshot"]
    assert snapshot["cursor"] == allocated
    assert snapshot["jobs"][0]["status"] == "waiting_person"
    assert snapshot["jobs"][0]["id"] == task_id


def test_arming_a_claim_is_in_the_same_cursor_as_the_snapshot():
    from app.services.schedule_confirm import arm_remote_claim

    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    token = f"arm-{uuid4().hex}"
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = db.get(TaskRun, task_id)
        job.exec_token = "worker"
        job.exec_epoch = 2
        job.intent_version = 1
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=user_id,
                session_id=session_id,
                status="open",
                action="calendar.create",
                fingerprint=token,
                scope="",
                generation_ids=json.dumps([task_id]),
                remote_request_id="",
                intent_version=1,
            )
        )
        db.commit()
        armed = arm_remote_claim(
            db,
            token,
            "req-9",
            exec_token="worker",
            exec_epoch=2,
            intent_version=1,
        )
        claim = db.get(ScheduleConfirmClaim, token)
        status = claim.status
        marker = claim.remote_request_id
        seqs = [
            int(row.seq)
            for row in db.query(SessionEvent)
            .filter(SessionEvent.session_id == session_id, SessionEvent.kind == "confirm", SessionEvent.body == token)
            .all()
        ]
        snapshot = read_snapshot(db, user_id=user_id, session_id=session_id)
        db.commit()
        cursor = snapshot["cursor"]
        job_status = snapshot["jobs"][0]["status"]
    finally:
        db.close()
    assert armed is True
    assert status == "unknown"
    assert marker == "req-9"
    assert seqs
    assert cursor == max(seqs)
    assert job_status == "running"


def test_a_sweep_does_not_publish_a_foreground_turn():
    from app.services import task_run
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        turn = task_run.open_alongside(db, user_id=user_id, session_id=session_id, kind="turn", goal="talk")
        turn_id = int(turn.id)
        db.commit()
    finally:
        db.close()
    with patch("app.tasks.job.run_job.delay") as delay:
        run_sweep()
        probe = SessionLocal()
        try:
            set_rls_context(probe, user_id)
            assert dispatch_task(probe, turn_id) == 0
        finally:
            probe.close()
    assert all(call.args[0] != turn_id for call in delay.call_args_list)
    stored = _load(turn_id)
    assert stored.kind == "turn"
    assert stored.exec_token is None
    assert stored.dispatch_token is None
    assert stored.status == "running"


def test_a_stale_stream_holder_does_not_keep_the_later_text():
    import asyncio

    from app.core.llm import AnswerDelta
    from app.services.loop_round import Hold
    from app.tasks.job import _stream_turn

    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = db.get(TaskRun, task_id)
        job.exec_token = "holder"
        job.exec_epoch = 4
        job.intent_version = 1
        db.commit()
    finally:
        db.close()

    async def fake_stream(*_args, **_kwargs):
        yield AnswerDelta("kept")
        other = SessionLocal()
        try:
            set_rls_context(other, user_id)
            row = other.get(TaskRun, task_id)
            row.exec_token = "newer"
            row.exec_epoch = 5
            row.intent_version = 2
            other.commit()
        finally:
            other.close()
        yield AnswerDelta("beta")

    class Pulse:
        def lost(self) -> bool:
            return False

    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = db.get(TaskRun, task_id)
        hold = Hold(
            user_id=user_id,
            generation=int(job.generation),
            exec_token="holder",
            exec_epoch=4,
            intent_version=1,
        )
        with patch("app.core.llm.stream_chat", fake_stream):
            streamed = asyncio.run(_stream_turn(db, job, [], [], hold, Pulse()))
    finally:
        db.close()
    assert streamed is None
    stored = SessionLocal()
    try:
        rows = stored.query(Message).filter(Message.session_id == session_id).all()
        text = " ".join(row.content or "" for row in rows)
    finally:
        stored.close()
    assert "kept" in text
    assert "beta" not in text


def test_a_quiet_stream_renews_the_hold_and_a_dead_holder_is_reclaimed():
    import asyncio

    from app.core.config import settings

    user_id = _user()
    session_id = _session(user_id)
    seen: dict = {}
    previous = settings.llm_timeout
    settings.llm_timeout = 3

    async def quiet(*_args, **_kwargs):
        from app.core.llm import AnswerDelta

        other = SessionLocal()
        try:
            set_rls_context(other, user_id)
            row = other.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").one()
            seen["id"] = int(row.id)
            seen["first"] = row.hold_until
        finally:
            other.close()
        await asyncio.sleep(2.0)
        seen["second"] = _load(seen["id"]).hold_until
        probe = SessionLocal()
        try:
            set_rls_context(probe, user_id)
            seen["live"] = dispatch_task(probe, seen["id"])
        finally:
            probe.close()
        if False:
            yield AnswerDelta("unused")
        raise RuntimeError("drop the holder")

    try:
        set_executor(None)
        with (
            patch("app.tasks.job._loop_enabled", return_value=True),
            patch("app.core.llm.stream_chat", quiet),
            pytest.raises(RuntimeError, match="drop the holder"),
        ):
            db = SessionLocal()
            try:
                set_rls_context(db, user_id)
                enqueue_job(db, user_id=user_id, session_id=session_id, goal="quiet")
            finally:
                db.close()
        assert seen["second"] > seen["first"]
        assert seen["live"] == 0
        held = _load(seen["id"])
        old_token = held.exec_token
        assert old_token
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            row = db.get(TaskRun, seen["id"])
            row.hold_until = datetime(2000, 1, 1)
            row.dispatch_until = datetime(2000, 1, 1)
            db.commit()
        finally:
            db.close()
        set_executor(lambda _messages, _tools: ModelTurn("done", "", (), "stop"))
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            assert dispatch_task(db, seen["id"]) == 1
        finally:
            db.close()
        reclaimed = _load(seen["id"])
        assert reclaimed.exec_token != old_token
        assert reclaimed.status == "completed"
    finally:
        settings.llm_timeout = previous
        set_executor(None)


def test_a_long_tool_renews_the_hold_and_a_live_holder_is_not_reclaimed():
    from app.agents.harness.types import ToolResult
    from app.core.config import settings

    user_id = _user()
    session_id = _session(user_id)
    seen: dict = {}
    previous = settings.llm_timeout
    settings.llm_timeout = 3

    def executor(messages, _tools):
        if "id" not in seen:
            found = SessionLocal()
            try:
                set_rls_context(found, user_id)
                seen["id"] = int(
                    found.query(TaskRun)
                    .filter(TaskRun.session_id == session_id, TaskRun.kind == "job")
                    .one()
                    .id
                )
            finally:
                found.close()
        if any(item.get("role") == "tool" for item in messages):
            return ModelTurn("done", "", (), "stop")
        return ModelTurn("", "", (ToolCallSpec("call-long", "kb", "{}"),), "tool_calls")

    def fake_invoke(_name, _ctx, _args):
        task_id = seen["id"]
        first = _load(task_id).hold_until
        time.sleep(2.0)
        second = _load(task_id).hold_until
        probe = SessionLocal()
        try:
            set_rls_context(probe, user_id)
            seen["live"] = dispatch_task(probe, task_id)
        finally:
            probe.close()
        seen["renewed"] = second > first
        seen["token"] = _load(task_id).exec_token
        return ToolResult(direct_answer="slept")

    try:
        set_executor(executor)
        with patch("app.agents.harness.gateway.invoke", fake_invoke):
            db = SessionLocal()
            try:
                set_rls_context(db, user_id)
                task_id = enqueue_job(db, user_id=user_id, session_id=session_id, goal="tool")
            finally:
                db.close()
        task_id = seen["id"]
        assert seen["renewed"] is True
        assert seen["live"] == 0
        assert seen["token"]
        assert _load(task_id).status == "completed"
        assert _calls(task_id)[0].state == "result"
    finally:
        settings.llm_timeout = previous
        set_executor(None)


@pytest.mark.parametrize(
    ("name", "action", "claim_status", "expect_state", "expect_result"),
    [
        ("desktop.open", "desktop.open", "unknown", "unknown", "结果未知"),
        ("calendar", "calendar.create", "unknown", "unknown", "结果未知"),
        ("mail.send", "mail.send", "unknown", "unknown", "结果未知"),
        ("errand.place", "errand.place", "unknown", "unknown", "结果未知"),
        ("desktop.open", "desktop.open", "written", "result", "已写入：req-1"),
        ("calendar", "calendar.create", "written", "result", "已写入：req-1"),
        ("mail.send", "mail.send", "written", "result", "已写入：req-1"),
        ("errand.place", "errand.place", "written", "result", "已写入：req-1"),
    ],
)
def test_a_lost_write_is_reconstructed_without_sending(
    name: str,
    action: str,
    claim_status: str,
    expect_state: str,
    expect_result: str,
):
    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    token = f"recover-{uuid4().hex}"
    arguments = json.dumps({"action": "create"}) if name == "calendar" else "{}"
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        step = TaskStep(
            user_id=user_id,
            task_run_id=task_id,
            seq=1,
            phase="awaiting_results",
            content="",
            reasoning_content="",
        )
        db.add(step)
        db.flush()
        db.add(
            ToolCall(
                user_id=user_id,
                task_step_id=step.id,
                tool_call_id="call-recover",
                position=0,
                name=name,
                arguments=arguments,
                state="awaiting_confirm",
                claim_token=token,
                intent_version=1,
            )
        )
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=user_id,
                session_id=session_id,
                status=claim_status,
                action=action,
                fingerprint=token,
                scope="",
                generation_ids=json.dumps([task_id]),
                remote_request_id="req-1",
                intent_version=1,
            )
        )
        db.commit()
    finally:
        db.close()

    def executor(_messages, _tools):
        call = _calls(task_id)[0]
        assert call.state == expect_state
        assert call.result == expect_result
        return ModelTurn("done", "", (), "stop")

    sent: list[str] = []
    set_executor(executor)
    with (
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
        patch("app.services.local_action.finish_local", side_effect=lambda *args, **kwargs: sent.append("finish")),
    ):
        _dispatch(user_id, task_id)
    assert sent == []
    call = _calls(task_id)[0]
    assert call.state == expect_state
    assert call.result == expect_result
    claim = SessionLocal()
    try:
        stored = claim.get(ScheduleConfirmClaim, token)
        assert stored.status == claim_status
        assert stored.remote_request_id == "req-1"
    finally:
        claim.close()


def test_an_old_adopter_does_not_borrow_the_replacement_hold():
    from sqlalchemy.orm import Session as OrmSession

    user_id = _user()
    session_id = _session(user_id)
    task_id = _open_job(user_id, session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = db.get(TaskRun, task_id)
        job.dispatch_token = "dispatch-1"
        job.dispatch_epoch = 1
        job.intent_version = 1
        job.dispatch_until = datetime(2030, 1, 1)
        job.exec_token = None
        job.exec_epoch = 0
        job.hold_until = None
        db.add(
            TaskStep(
                user_id=user_id,
                task_run_id=task_id,
                seq=1,
                phase="calls_saved",
                content="",
                reasoning_content="",
            )
        )
        db.commit()
    finally:
        db.close()
    seen = {"model": 0, "renewed": []}
    state = {"phase": "watch"}
    real_commit = OrmSession.commit

    def wrapped(self):
        real_commit(self)
        if state["phase"] != "watch":
            return
        state["phase"] = "took"
        other = SessionLocal()
        try:
            set_rls_context(other, user_id)
            row = other.get(TaskRun, task_id)
            row.exec_token = "wb"
            row.exec_epoch = int(row.exec_epoch or 0) + 1
            row.hold_until = datetime(2030, 1, 1)
            row.dispatch_token = None
            other.commit()
        finally:
            other.close()

    def executor(*_args, **_kwargs):
        seen["model"] += 1
        return ModelTurn("borrowed", "", (), "stop")

    from app.services import task_run

    real_renew = task_run.renew_hold

    def renew(*args, **kwargs):
        seen["renewed"].append(kwargs.get("exec_token"))
        return real_renew(*args, **kwargs)

    set_executor(executor)
    with (
        patch.object(OrmSession, "commit", wrapped),
        patch("app.tasks.job._loop_enabled", return_value=True),
        patch("app.services.task_run.renew_hold", renew),
        patch("app.services.loop_round.task_run.renew_hold", renew),
    ):
        run_job(task_id, "dispatch-1", 1, 1)
    set_executor(None)
    stored = _load(task_id)
    assert stored.exec_token == "wb"
    assert seen["model"] == 0
    assert seen["renewed"] == []
    check = SessionLocal()
    try:
        text = " ".join(
            row.content or ""
            for row in check.query(Message).filter(Message.session_id == session_id)
        )
    finally:
        check.close()
    assert "borrowed" not in text


def _parked_job(user_id: int, session_id: int, specs: list[tuple[str, str, dict]]):
    from app.services import task_run
    from app.services.loop_round import Hold, open_cards, save_turn

    db = SessionLocal()
    set_rls_context(db, user_id)
    turn = task_run.open_alongside(db, user_id=user_id, session_id=session_id, kind="job", goal="parked")
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
    save_turn(
        db,
        hold=hold,
        turn=ModelTurn(
            "",
            "",
            tuple(ToolCallSpec(call_id, name, json.dumps(raw)) for call_id, name, raw in specs),
            "tool_calls",
        ),
    )
    step = db.query(TaskStep).filter(TaskStep.task_run_id == turn.id).one()
    tokens = open_cards(db, hold=hold, step_id=step.id)
    return db, int(turn.id), tokens


_ERRAND = {
    "merchant_key": "shop",
    "lines": [{"sku": "tea", "qty": 1, "query": "tea"}],
    "ship_to": {"name": "Ada", "phone": "1", "region": "HZ", "address": "1 Road"},
    "pay_method": "card",
    "amount_cap": 10,
}


@pytest.mark.parametrize(
    ("name", "raw", "claim_status", "expect_state", "expect_result"),
    [
        ("desktop.open", {"local_target": "notes"}, "unknown", "unknown", "结果未知"),
        ("calendar", {"action": "create", "title": "parked", "start_at": "2026-11-20T10:00:00", "end_at": "2026-11-20T11:00:00"}, "unknown", "unknown", "结果未知"),
        ("mail.send", {"mail_to": ["one@example.com"], "subject": "parked", "body": "hello"}, "unknown", "unknown", "结果未知"),
        ("errand.place", _ERRAND, "unknown", "unknown", "结果未知"),
        ("desktop.open", {"local_target": "notes"}, "written", "result", "已写入：req-1"),
        ("calendar", {"action": "create", "title": "parked-w", "start_at": "2026-11-20T12:00:00", "end_at": "2026-11-20T13:00:00"}, "written", "result", "已写入：req-1"),
        ("mail.send", {"mail_to": ["one@example.com"], "subject": "parked-w", "body": "hello"}, "written", "result", "已写入：req-1"),
        ("errand.place", _ERRAND, "written", "result", "已写入：req-1"),
    ],
)
def test_a_parked_lost_write_is_recovered_without_sending(name, raw, claim_status, expect_state, expect_result):
    from app.services.schedule_confirm import arm_remote_claim
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    spec = dict(raw)
    if name in {"desktop.open", "desktop.run"}:
        spec["device_id"] = 7
    db, task_id, tokens = _parked_job(user_id, session_id, [("call-a", name, spec)])
    try:
        set_rls_context(db, user_id)
        assert arm_remote_claim(db, tokens[0], "req-1") is True
        if claim_status == "unknown":
            from app.services.schedule_confirm import renew_live_confirm

            assert renew_live_confirm(tokens[0]) is True
            db.expire_all()
            claim = db.get(ScheduleConfirmClaim, tokens[0])
            claim.confirm_until = datetime(2000, 1, 1)
        else:
            claim = db.get(ScheduleConfirmClaim, tokens[0])
            claim.status = "written"
        db.commit()
        assert db.query(ToolCall).filter(ToolCall.claim_token == tokens[0]).one().state == "awaiting_confirm"
        assert db.get(TaskRun, task_id).status == "waiting_person"
        assert db.get(TaskRun, task_id).continuation_due is False
    finally:
        db.close()
    sent: list[str] = []
    set_executor(lambda *_args, **_kwargs: ModelTurn("done", "", (), "stop"))
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
        patch("app.services.local_action.finish_local", side_effect=lambda *args, **kwargs: sent.append("finish")),
    ):
        run_sweep()
    assert sent == []
    call = _calls(task_id)[0]
    assert call.state == expect_state
    assert call.result == expect_result
    stored = _load(task_id)
    assert stored.exec_token != "worker"


def test_a_live_card_stays_waiting_while_a_crashed_sibling_is_recovered():
    from app.services.schedule_confirm import arm_remote_claim
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    specs = [
        ("call-old", "mail.send", {"mail_to": ["old@example.com"], "subject": "old", "body": "old"}),
        ("call-live", "mail.send", {"mail_to": ["live@example.com"], "subject": "live", "body": "live"}),
        ("call-open", "desktop.open", {"device_id": 7, "local_target": "notes"}),
    ]
    db, task_id, tokens = _parked_job(user_id, session_id, specs)
    db.close()
    plain_db, plain_id, _plain_tokens = _parked_job(
        user_id,
        session_id,
        [("call-plain", "mail.send", {"mail_to": ["plain@example.com"], "subject": "plain", "body": "plain"})],
    )
    plain_db.close()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        from app.services.schedule_confirm import renew_live_confirm

        assert arm_remote_claim(db, tokens[0], "req-old") is True
        assert arm_remote_claim(db, tokens[1], "req-live") is True
        assert renew_live_confirm(tokens[0]) is True
        assert renew_live_confirm(tokens[1]) is True
        db.expire_all()
        old = db.get(ScheduleConfirmClaim, tokens[0])
        old.confirm_until = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()
    sent: list[str] = []
    calls = {"model": 0}

    def executor(*_args, **_kwargs):
        calls["model"] += 1
        return ModelTurn("done", "", (), "stop")

    set_executor(executor)
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id, plain_id)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
    ):
        run_sweep()
    by_token = {row.claim_token: row for row in _calls(task_id)}
    assert by_token[tokens[0]].state == "unknown"
    assert by_token[tokens[0]].result == "结果未知"
    assert by_token[tokens[1]].state == "awaiting_confirm"
    assert by_token[tokens[2]].state == "awaiting_confirm"
    mixed = _load(task_id)
    assert mixed.status == "waiting_person"
    assert mixed.continuation_due is False
    assert mixed.exec_token is None
    untouched = _load(plain_id)
    assert untouched.status == "waiting_person"
    assert untouched.continuation_due is False
    assert _calls(plain_id)[0].state == "awaiting_confirm"
    assert sent == []
    assert calls["model"] == 0


def test_a_desktop_license_stays_waiting_until_the_real_finish():
    import hashlib
    import secrets

    from app.agents.harness.desktop_channel import recognize_package
    from app.models.models import DesktopDevice
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    presented = secrets.token_urlsafe(32)
    seeded = SessionLocal()
    try:
        set_rls_context(seeded, user_id)
        device = DesktopDevice(
            user_id=user_id,
            token_hash=hashlib.sha256(presented.encode("utf-8")).hexdigest(),
            label="late",
        )
        seeded.add(device)
        seeded.commit()
        device_id = int(device.id)
    finally:
        seeded.close()
    db, task_id, tokens = _parked_job(
        user_id,
        session_id,
        [("call-desk", "desktop.open", {"device_id": device_id, "local_target": "notes"})],
    )
    db.close()
    proof = recognize_package(presented)
    assert proof is not None
    action = LocalAction("desktop.open", device_id, "notes", ())
    owned = SessionLocal()
    try:
        set_rls_context(owned, user_id)
        license_row = commit_local(
            owned,
            user_id=user_id,
            token=tokens[0],
            action=action,
            proof=proof,
        )
    finally:
        owned.close()
    assert license_row["kind"] == "armed"
    sent: list[str] = []
    set_executor(lambda *_args, **_kwargs: ModelTurn("done", "", (), "stop"))
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
    ):
        run_sweep()
    assert _calls(task_id)[0].state == "awaiting_confirm"
    assert _load(task_id).continuation_due is False
    finisher = SessionLocal()
    try:
        set_rls_context(finisher, user_id)
        finish_local(
            finisher,
            user_id=user_id,
            token=tokens[0],
            request_id=license_row["request_id"],
            action=action,
            proof=proof,
        )
    finally:
        finisher.close()
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
    ):
        run_sweep()
    finished = _calls(task_id)[0]
    assert finished.state == "result"
    assert finished.result == f"已写入：{license_row['request_id']}"
    assert sent == []


def test_a_slow_mail_send_outlives_the_first_lease_without_becoming_unknown(monkeypatch):
    from app.core.config import settings
    from app.models.models import EventReceipt
    from app.services.mail_entry import OutboundMail, SendAccepted, bind_mail_transport, commit_send
    from app.services.schedule_confirm import write_fingerprint, write_parts
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    raw = {"mail_to": ["slow@example.com"], "subject": "slow", "body": "body"}
    db, task_id, tokens = _parked_job(user_id, session_id, [("call-mail", "mail.send", raw)])
    parts = write_parts("mail.send", raw)
    fingerprint = write_fingerprint(parts)
    try:
        set_rls_context(db, user_id)
        db.add(
            EventReceipt(
                user_id=user_id,
                session_id=session_id,
                source="mail",
                delivery_id=f"out:{fingerprint}",
                fingerprint=fingerprint,
                state="received",
                route="filed",
                claim_token=tokens[0],
                body="body",
            )
        )
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(settings, "llm_timeout", 0.4)
    monkeypatch.setattr(settings, "mail_transport", "slow")
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    class Slow:
        def send(self, _draft):
            started.set()
            assert release.wait(5)
            return SendAccepted("provider-slow")

        def lookup(self, _provider_id):
            return "accepted"

    bind_mail_transport(Slow())

    def send() -> None:
        owned = SessionLocal()
        try:
            set_rls_context(owned, user_id)
            commit_send(
                owned,
                user_id=user_id,
                token=tokens[0],
                draft=OutboundMail(("slow@example.com",), "slow", "body"),
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            owned.close()

    worker = threading.Thread(target=send)
    worker.start()
    assert started.wait(5)
    deadline = time.time() + 4
    renewed = False
    first = None
    while time.time() < deadline:
        check = SessionLocal()
        try:
            set_rls_context(check, user_id)
            claim = check.get(ScheduleConfirmClaim, tokens[0])
            until = None if claim is None else claim.confirm_until
        finally:
            check.close()
        if first is None:
            first = until
        elif until is not None and first is not None and until > first and datetime.now(timezone.utc).replace(tzinfo=None) >= first:
            renewed = True
            break
        time.sleep(0.05)
    sent: list[str] = []
    set_executor(lambda *_args, **_kwargs: ModelTurn("done", "", (), "stop"))
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
    ):
        run_sweep()
    during = _calls(task_id)[0]
    release.set()
    worker.join(5)
    bind_mail_transport(None)
    assert renewed
    assert not worker.is_alive()
    assert errors == []
    assert during.state == "awaiting_confirm"
    assert _load(task_id).continuation_due is False
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
    ):
        run_sweep()
    after = _calls(task_id)[0]
    assert after.state == "result"
    assert after.result.startswith("已写入：")
    assert "结果未知" not in (after.result or "")
    assert sent == []


def test_a_rejected_claim_can_be_armed_again_without_a_dead_lease():
    from app.services.schedule_confirm import arm_remote_claim, finish_confirm, reopen_known_rejection
    from app.tasks.watchdog import run_sweep

    user_id = _user()
    session_id = _session(user_id)
    raw = {"mail_to": ["retry@example.com"], "subject": "retry", "body": "body"}
    db, task_id, tokens = _parked_job(user_id, session_id, [("call-retry", "mail.send", raw)])
    try:
        set_rls_context(db, user_id)
        assert arm_remote_claim(db, tokens[0], "req-old") is True
        claim = db.get(ScheduleConfirmClaim, tokens[0])
        claim.confirm_until = datetime(2000, 1, 1)
        db.commit()
        reopen_known_rejection(db, tokens[0])
        db.expire_all()
        assert arm_remote_claim(db, tokens[0], "req-new") is True
    finally:
        db.close()
    sent: list[str] = []
    set_executor(lambda *_args, **_kwargs: ModelTurn("done", "", (), "stop"))
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
        patch("app.services.local_action.finish_local", side_effect=lambda *args, **kwargs: sent.append("finish")),
    ):
        run_sweep()
    waiting = _calls(task_id)[0]
    assert waiting.state == "awaiting_confirm"
    assert waiting.result != "结果未知"
    assert _load(task_id).continuation_due is False
    finisher = SessionLocal()
    try:
        set_rls_context(finisher, user_id)
        finish_confirm(finisher, tokens[0])
    finally:
        finisher.close()
    with (
        patch("app.tasks.watchdog.load_open_ids", return_value=(task_id,)),
        patch("app.agents.harness.gateway.invoke", side_effect=lambda *args, **kwargs: sent.append("invoke")),
        patch("app.services.schedule_confirm.arm_remote_claim", side_effect=lambda *args, **kwargs: sent.append("arm")),
        patch("app.services.local_action.finish_local", side_effect=lambda *args, **kwargs: sent.append("finish")),
    ):
        run_sweep()
    finished = _calls(task_id)[0]
    assert finished.state == "result"
    assert finished.result == "已写入：req-new"
    assert "结果未知" not in (finished.result or "")
    assert sent == []


def test_one_failed_lease_renew_does_not_stop_the_next(monkeypatch):
    from app.core.config import settings
    from app.services import schedule_confirm
    from app.services.schedule_confirm import arm_remote_claim, live_provider_call

    user_id = _user()
    session_id = _session(user_id)
    raw = {"mail_to": ["pulse@example.com"], "subject": "pulse", "body": "body"}
    db, _task_id, tokens = _parked_job(user_id, session_id, [("call-pulse", "mail.send", raw)])
    try:
        set_rls_context(db, user_id)
        assert arm_remote_claim(db, tokens[0], "req-pulse") is True
    finally:
        db.close()
    monkeypatch.setattr(settings, "llm_timeout", 0.12)
    calls = {"n": 0}
    recovered = threading.Event()
    real = schedule_confirm.renew_live_confirm

    def flaky(token: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("renew down")
        if calls["n"] >= 3:
            recovered.set()
        return real(token)

    monkeypatch.setattr(schedule_confirm, "renew_live_confirm", flaky)
    with live_provider_call(tokens[0]):
        assert recovered.wait(2)
    assert calls["n"] >= 3
