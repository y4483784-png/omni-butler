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
from app.services.resume import dispatch_task, enqueue_job, publish_event, read_snapshot
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
        db.add(SessionCursor(session_id=session_id, user_id=user_id, published_seq=99))
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
