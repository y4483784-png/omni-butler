from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.harness.constitution import load_constitution
from app.core.config import settings
from app.core.llm import Role, resolve
from app.agents.harness.execution import execution_denial
from app.agents.harness.types import ToolResult
from app.core.db import SessionLocal, set_rls_context
from app.core.generation_stop import current_key
from app.models.models import CalendarEvent, Message, Session as ChatSession, TaskRun, TaskStep, ToolCall, User
from app.services.loop_round import WEB_DENIAL, ModelTurn, ToolCallSpec, job_tools
from app.services.task_images import LoadedImages
from app.tasks.job import run_job, set_executor

PNG = b"\x89PNG\r\n\x1a\nrest"


@pytest.fixture(autouse=True)
def _no_executor():
    set_executor(None)
    yield
    set_executor(None)


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"loop-job-{uuid4().hex}"
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
        row = ChatSession(user_id=user_id, title="loop job")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def _pair() -> tuple[int, int]:
    user_id = _user()
    return user_id, _session(user_id)


def _admin_id() -> int:
    db = SessionLocal()
    try:
        return int(db.query(User).filter(User.external_id == "admin").one().id)
    finally:
        db.close()


def _job(session_id: int) -> TaskRun:
    db = SessionLocal()
    try:
        return db.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").one()
    finally:
        db.close()


def _calls(task_id: int) -> list[ToolCall]:
    db = SessionLocal()
    try:
        step = (
            db.query(TaskStep)
            .filter(TaskStep.task_run_id == task_id)
            .order_by(TaskStep.seq.asc())
            .first()
        )
        return (
            db.query(ToolCall)
            .filter(ToolCall.task_step_id == step.id)
            .order_by(ToolCall.position.asc())
            .all()
        )
    finally:
        db.close()


def _notes(session_id: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.query(Message).filter(Message.session_id == session_id, Message.role == "assistant").all()
        return [row.content for row in rows]
    finally:
        db.close()


def _turn(*specs: ToolCallSpec, content: str = "", finish: str = "tool_calls") -> ModelTurn:
    return ModelTurn(content=content, reasoning="", tool_calls=specs, finish=finish)


def test_two_reads_are_shown_and_the_job_finishes():
    user_id, session_id = _pair()
    seen: list[tuple[list, list]] = []
    invoked: list[str] = []
    answers = [
        _turn(ToolCallSpec("call-a", "kb", "{}"), ToolCallSpec("call-b", "web", "{}")),
        _turn(content="done", finish="stop"),
    ]

    def executor(messages, tools):
        seen.append((messages, tools))
        return answers.pop(0)

    def fake_invoke(name, _ctx, _args):
        invoked.append(name)
        return ToolResult(direct_answer=f"saw {name}")

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        run_job(user_id, session_id, "alpha")
    job = _job(session_id)
    assert job.status == "completed"
    assert job.exec_token is None
    assert job.continuation_due is False
    assert invoked == ["kb", "web"]
    assert _notes(session_id) == ["已开始：alpha", "saw kb", "saw web", "done"]
    assert current_key() is None
    second_tools = [item["function"]["name"] for item in seen[1][1]]
    assert "kb" in second_tools
    assert seen[1][0][1]["role"] == "assistant"
    assert seen[1][0][2]["role"] == "tool"
    assert seen[1][0][2]["tool_call_id"] == "call-a"


def test_a_provider_failure_leaves_the_hold():
    user_id, session_id = _pair()

    def executor(_messages, _tools):
        raise RuntimeError("down")

    set_executor(executor)
    with pytest.raises(RuntimeError, match="down"):
        run_job(user_id, session_id, "alpha")
    job = _job(session_id)
    assert job.status == "running"
    assert job.exec_token
    assert _notes(session_id) == ["已开始：alpha"]
    db = SessionLocal()
    try:
        assert db.query(TaskStep).filter(TaskStep.task_run_id == job.id).count() == 0
    finally:
        db.close()


def test_a_crash_after_save_leaves_the_calls_pending():
    user_id, session_id = _pair()
    set_executor(lambda _messages, _tools: _turn(ToolCallSpec("call-a", "kb", "{}")))
    with patch("app.services.loop_round.begin_read", side_effect=SystemExit(7)):
        with pytest.raises(SystemExit):
            run_job(user_id, session_id, "alpha")
    job = _job(session_id)
    assert job.status == "running"
    assert job.exec_token
    assert [(row.tool_call_id, row.state) for row in _calls(job.id)] == [("call-a", "pending")]


def test_a_calendar_write_waits_for_one_confirm(plain_client: TestClient):
    user_id = _admin_id()
    session_id = _session(user_id)
    title = f"loop-{uuid4().hex}"
    stamp = uuid4().int % 20000
    start = datetime(2031, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=stamp)
    raw = {
        "action": "create",
        "title": title,
        "start_at": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end_at": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    set_executor(lambda _messages, _tools: _turn(ToolCallSpec("call-a", "calendar", json.dumps(raw))))
    with patch("app.services.calendar.create_event", side_effect=AssertionError("created")):
        run_job(user_id, session_id, "meet")
    job = _job(session_id)
    assert job.status == "waiting_person"
    assert job.exec_token is None
    db = SessionLocal()
    try:
        assert db.query(CalendarEvent).filter(CalendarEvent.title == title).count() == 0
        card = json.loads(
            db.query(Message)
            .filter(Message.session_id == session_id, Message.schedule_card != "")
            .one()
            .schedule_card
        )
    finally:
        db.close()
    body = {
        "title": title,
        "start_at": raw["start_at"],
        "end_at": raw["end_at"],
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
        "confirm_token": card["confirm_token"],
    }
    try:
        first = plain_client.post("/api/calendar", json=body)
        assert first.status_code == 200, first.text
        second = plain_client.post("/api/calendar", json=body)
        assert second.status_code == 409
        db = SessionLocal()
        try:
            assert db.query(CalendarEvent).filter(CalendarEvent.title == title).count() == 1
            assert _calls(job.id)[0].state == "result"
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(CalendarEvent).filter(CalendarEvent.title == title).delete()
            db.commit()
        finally:
            db.close()


def test_forbid_omits_search_and_denies_it_again():
    user_id, session_id = _pair()
    seen: list[list[str]] = []
    answers = [
        _turn(ToolCallSpec("call-a", "web", "{}")),
        _turn(content="done", finish="stop"),
    ]

    def executor(_messages, tools):
        seen.append([item["function"]["name"] for item in tools])
        return answers.pop(0)

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", side_effect=AssertionError("invoked")):
        run_job(user_id, session_id, "look", "forbid")
    assert "web" not in seen[0]
    assert "web_fetch" not in seen[0]
    assert "mail.send" in seen[0]
    row = _calls(_job(session_id).id)[0]
    assert row.state == "rejected"
    assert row.result == WEB_DENIAL
    listed = job_tools(constitution=load_constitution(), web_constraint="allow")
    assert [item["function"]["name"] for item in listed] == [
        "kb",
        "web",
        "web_fetch",
        "calendar",
        "sandbox",
        "mail.send",
        "errand.place",
        "desktop.open",
        "desktop.run",
    ]
    by_name = {item["function"]["name"]: item["function"]["parameters"] for item in listed}
    assert by_name["kb"] == {"type": "object"}
    assert by_name["web"]["required"] == ["query"]
    assert by_name["web"]["properties"]["query"]["minLength"] == 1
    assert by_name["web"]["properties"]["query"]["maxLength"] == 128
    assert by_name["web"]["properties"]["ori_question"]["maxLength"] == 128
    assert "localization" not in by_name["web"]["properties"]
    assert by_name["web_fetch"]["required"] == ["url"]
    assert by_name["web_fetch"]["properties"]["url"]["maxLength"] == 8192
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        task_id = _job(session_id).id
        assert execution_denial(db, user_id=user_id, task_id=None, tool="web", require_task=False) is None
        assert execution_denial(db, user_id=user_id, task_id=None, tool="web", require_task=True) == "缺少任务，未执行。"
        assert execution_denial(db, user_id=user_id, task_id=task_id, tool="web", require_task=True) == WEB_DENIAL
        assert execution_denial(db, user_id=user_id, task_id=task_id, tool="web_fetch", require_task=True) == WEB_DENIAL
        assert _job(session_id).status == "completed"
    finally:
        db.close()


def test_a_stopped_job_does_not_call_the_model():
    user_id, session_id = _pair()
    called = {"model": 0, "tool": 0}

    def executor(_messages, _tools):
        called["model"] += 1
        return _turn(content="no", finish="stop")

    from app.tasks import job as job_mod

    original_commit = job_mod.commit_job_output

    def stop_on_start(*args, **kwargs):
        from app.services import task_run

        result = original_commit(*args, **kwargs)
        task_run.stop(user_id=kwargs["user_id"], generation=kwargs["generation"])
        return result

    set_executor(executor)
    with patch("app.tasks.job.commit_job_output", stop_on_start), patch(
        "app.agents.harness.gateway.invoke",
        side_effect=AssertionError("invoked"),
    ):
        run_job(user_id, session_id, "alpha")
    assert called == {"model": 0, "tool": 0}
    assert _job(session_id).status == "stopped"


def test_a_stale_intent_does_not_invoke():
    user_id, session_id = _pair()

    def executor(_messages, _tools):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            row = db.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").one()
            row.intent_version = 2
            db.commit()
        finally:
            db.close()
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", side_effect=AssertionError("invoked")):
        run_job(user_id, session_id, "alpha")
    job = _job(session_id)
    assert job.status == "running"
    assert job.intent_version == 2
    db = SessionLocal()
    try:
        assert db.query(TaskStep).filter(TaskStep.task_run_id == job.id).count() == 0
    finally:
        db.close()


def test_images_go_to_the_model_without_ocr():
    user_id, session_id = _pair()
    seen: list[list] = []
    url = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")

    def executor(messages, _tools):
        seen.append(messages)
        return _turn(content="saw it", finish="stop")

    def fake_load(_db, *, user_id, task_run_id, store):
        return LoadedImages("unavailable", ({"type": "image_url", "image_url": {"url": url}},))

    set_executor(executor)
    with patch("app.services.task_images.load_image_blocks", fake_load), patch(
        "app.rag.vision_ocr.ocr_image_bytes",
        side_effect=AssertionError("ocr"),
    ) as ocr:
        run_job(user_id, session_id, "picture")
    ocr.assert_not_called()
    content = seen[0][0]["content"]
    assert content[0] == {"type": "text", "text": "picture"}
    assert content[1]["type"] == "image_url"
    assert "ocr" not in json.dumps(content)


def test_dispatch_is_not_asked_to_plan():
    user_id, session_id = _pair()
    set_executor(lambda _messages, _tools: _turn(content="done", finish="stop"))
    with patch("app.agents.dispatch.assign_turn") as assigned, patch("app.agents.router.plan_tools") as planned:
        run_job(user_id, session_id, "alpha")
    assigned.assert_not_called()
    planned.assert_not_called()
    assert _job(session_id).status == "completed"
    assert _job(session_id).dispatch_token is None


def test_a_secret_errand_is_rejected_without_invoke():
    user_id, session_id = _pair()
    raw = {
        "merchant_key": "shop",
        "lines": [{"sku": "tea", "qty": 1, "query": "tea"}],
        "ship_to": {"name": "Ada", "phone": "1", "region": "HZ", "address": "1 Road"},
        "pay_method": "card",
        "amount_cap": 10,
        "password": "hidden",
    }
    answers = [
        _turn(ToolCallSpec("call-a", "errand.place", json.dumps(raw))),
        _turn(content="done", finish="stop"),
    ]
    set_executor(lambda _messages, _tools: answers.pop(0))
    with patch("app.agents.harness.gateway.invoke", side_effect=AssertionError("invoked")):
        run_job(user_id, session_id, "order")
    row = _calls(_job(session_id).id)[0]
    assert row.state == "rejected"
    assert "hidden" not in row.arguments
    assert "password" not in row.arguments


def _drive_held(user_id: int, session_id: int) -> None:
    from app.tasks.job import _drive

    job = _job(session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        _drive(db, user_id=user_id, task_id=job.id, worker=job.exec_token)
    finally:
        db.close()


def test_a_later_drive_runs_a_pending_read_before_the_model():
    user_id, session_id = _pair()
    order: list[str] = []

    def executor(messages, _tools):
        order.append("model")
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    def fake_invoke(name, _ctx, _args):
        order.append("invoke")
        return ToolResult(direct_answer=f"saw {name}")

    set_executor(executor)
    with patch("app.services.loop_round.begin_read", side_effect=SystemExit(7)):
        with pytest.raises(SystemExit):
            run_job(user_id, session_id, "alpha")
    assert [(row.tool_call_id, row.state) for row in _calls(_job(session_id).id)] == [("call-a", "pending")]
    order.clear()
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _drive_held(user_id, session_id)
    assert order[0] == "invoke"
    assert order.count("model") == 1
    job = _job(session_id)
    assert job.status == "completed"
    assert _calls(job.id)[0].state == "result"


def test_a_later_drive_runs_a_running_read_again():
    user_id, session_id = _pair()
    invokes: list[str] = []

    def executor(messages, _tools):
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    def fake_invoke(name, _ctx, _args):
        invokes.append(name)
        if len(invokes) == 1:
            raise SystemExit(7)
        return ToolResult(direct_answer="again")

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        with pytest.raises(SystemExit):
            run_job(user_id, session_id, "alpha")
    assert _calls(_job(session_id).id)[0].state == "running"
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        _drive_held(user_id, session_id)
    assert invokes == ["kb", "kb"]
    job = _job(session_id)
    assert job.status == "completed"
    assert _calls(job.id)[0].state == "result"
    assert _calls(job.id)[0].result == "again"


def test_a_later_drive_sends_one_request_from_stored_results():
    user_id, session_id = _pair()
    sent: list[list[dict]] = []

    def executor(messages, _tools):
        sent.append(messages)
        if len(sent) == 1:
            return _turn(ToolCallSpec("call-a", "kb", "{}"), ToolCallSpec("call-b", "web", "{}"))
        return _turn(content="done", finish="stop")

    def fake_invoke(name, _ctx, _args):
        return ToolResult(direct_answer=f"saw {name}")

    from app.tasks import job as job_module

    real_request = job_module._request

    def stop_before_results(db, task):
        step = (
            db.query(TaskStep)
            .filter(TaskStep.task_run_id == task.id)
            .order_by(TaskStep.seq.desc())
            .first()
        )
        if step is not None and step.phase == "results_ready":
            raise SystemExit(7)
        return real_request(db, task)

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", fake_invoke), patch.object(job_module, "_request", stop_before_results):
        with pytest.raises(SystemExit):
            run_job(user_id, session_id, "alpha")
    assert len(sent) == 1
    job = _job(session_id)
    assert job.status == "running"
    with patch("app.agents.harness.gateway.invoke", side_effect=AssertionError("invoked")):
        _drive_held(user_id, session_id)
    assert len(sent) == 2
    results = [item for item in sent[1] if item.get("role") == "tool"]
    assert [(item["tool_call_id"], item["content"]) for item in results] == [
        ("call-a", "saw kb"),
        ("call-b", "saw web"),
    ]
    assert _job(session_id).status == "completed"


def test_a_second_run_while_held_does_not_start_again():
    user_id, session_id = _pair()
    calls: list[str] = []

    def executor(_messages, _tools):
        calls.append("alpha")
        raise SystemExit(3)

    set_executor(executor)
    with pytest.raises(SystemExit):
        run_job(user_id, session_id, "alpha")
    assert _notes(session_id) == ["已开始：alpha"]
    with pytest.raises(SystemExit):
        run_job(user_id, session_id, "alpha")
    assert calls == ["alpha", "alpha"]
    assert _notes(session_id) == ["已开始：alpha"]
    db = SessionLocal()
    try:
        assert db.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").count() == 1
    finally:
        db.close()

    def finish(_messages, _tools):
        calls.append("beta")
        return _turn(content="done", finish="stop")

    set_executor(finish)
    run_job(user_id, session_id, "beta")
    assert calls == ["alpha", "alpha", "beta"]
    assert _notes(session_id).count("已开始：beta") == 1


def test_a_second_run_job_recovers_a_pending_read():
    user_id, session_id = _pair()
    order: list[str] = []

    def executor(messages, _tools):
        order.append("model")
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "kb", "{}"))

    def fake_invoke(name, _ctx, _args):
        order.append("invoke")
        return ToolResult(direct_answer=f"saw {name}")

    set_executor(executor)
    with patch("app.services.loop_round.begin_read", side_effect=SystemExit(7)):
        with pytest.raises(SystemExit):
            run_job(user_id, session_id, "alpha")
    assert [(row.tool_call_id, row.state) for row in _calls(_job(session_id).id)] == [("call-a", "pending")]
    assert _notes(session_id) == ["已开始：alpha"]
    order.clear()
    with patch("app.agents.harness.gateway.invoke", fake_invoke):
        run_job(user_id, session_id, "alpha")
    assert order[0] == "invoke"
    assert order.count("model") == 1
    assert _notes(session_id).count("已开始：alpha") == 1
    db = SessionLocal()
    try:
        assert db.query(TaskRun).filter(TaskRun.session_id == session_id, TaskRun.kind == "job").count() == 1
    finally:
        db.close()
    job = _job(session_id)
    assert job.status == "completed"
    assert _calls(job.id)[0].state == "result"


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolPiece:
    def __init__(self, index, call_id, name, arguments):
        self.index = index
        self.id = call_id
        self.function = _Fn(name, arguments)


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = None
        self.tool_calls = tool_calls


class _StreamChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, delta, finish_reason=None):
        self.choices = [_StreamChoice(delta, finish_reason)]
        self.usage = None


class _AsyncChunks:
    def __init__(self, chunks):
        self._it = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _install_job_stream(monkeypatch, outputs):
    from app.core import llm as llm_mod

    calls: list[dict] = []
    queue = list(outputs)

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return _AsyncChunks(item)

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {})()
            self.chat.completions = Completions()

    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: Client())
    return calls


def _search_client(monkeypatch, payload):
    posts: list[str] = []

    class Client:
        def __init__(self, *args, **kwargs):
            posts.append("client")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            posts.append(url)
            response = type("Response", (), {})()
            response.status_code = 200
            response.json = lambda: payload
            return response

    monkeypatch.setattr("app.services.deepseek_search.httpx.Client", Client)
    return posts


def test_search_keeps_the_following_completion_on_the_chat_model(monkeypatch):
    monkeypatch.delenv("EXECUTOR_MODEL", raising=False)
    monkeypatch.setattr(settings, "chat_model", "sentinel-chat")
    monkeypatch.setattr(settings, "chat_thinking", "on")
    monkeypatch.setattr(settings, "llm_api_key", "llm-secret")
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example/")
    posts = _search_client(
        monkeypatch,
        {
            "results": [
                {
                    "title": "News",
                    "url": "https://news.example/a",
                    "snippet": "snip",
                    "summary": "sum",
                    "content": "body",
                }
            ]
        },
    )
    calls = _install_job_stream(
        monkeypatch,
        [
            [
                _Chunk(_Delta(tool_calls=[_ToolPiece(0, "call-web", "web", '{"query":"news"}')])),
                _Chunk(_Delta(), "tool_calls"),
            ],
            [_Chunk(_Delta(content="done"), "stop")],
        ],
    )
    user_id, session_id = _pair()
    run_job(user_id, session_id, "today")
    search_posts = [url for url in posts if url != "client"]
    assert search_posts == ["https://llm.example/v1/tools/search"]
    assert all("web_search" not in url for url in posts)
    assert [item["model"] for item in calls] == [resolve(Role.CHAT).model, resolve(Role.CHAT).model]
    assert calls[1]["model"] == "sentinel-chat"
    link = "[News](https://news.example/a)"
    tool_messages = [item for item in calls[1]["messages"] if item.get("role") == "tool"]
    assert tool_messages and link in tool_messages[0]["content"]
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        stored = [
            row.content or ""
            for row in db.query(Message).filter(Message.session_id == session_id).all()
        ]
    finally:
        db.close()
    assert any(link in text for text in stored)
    assert _job(session_id).status == "completed"


def test_a_turn_without_a_tool_call_does_not_search(monkeypatch):
    posts = _search_client(monkeypatch, {"results": []})
    user_id, session_id = _pair()
    set_executor(lambda _messages, _tools: _turn(content="done", finish="stop"))
    run_job(user_id, session_id, "today")
    assert posts == []
    assert _job(session_id).status == "completed"


def test_forbid_does_not_construct_a_search_client(monkeypatch):
    posts = _search_client(monkeypatch, {"results": []})
    user_id, session_id = _pair()
    answers = [
        _turn(ToolCallSpec("call-a", "web", '{"query":"news"}')),
        _turn(content="done", finish="stop"),
    ]

    def executor(_messages, _tools):
        return answers.pop(0)

    set_executor(executor)
    run_job(user_id, session_id, "look", "forbid")
    assert posts == []
    row = _calls(_job(session_id).id)[0]
    assert row.state == "rejected"
    assert row.result == WEB_DENIAL


def test_a_saved_web_result_is_not_searched_again(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example")
    posts = _search_client(
        monkeypatch,
        {
            "results": [
                {
                    "title": "News",
                    "url": "https://news.example/a",
                    "snippet": "snip",
                    "summary": "sum",
                    "content": "body",
                }
            ]
        },
    )
    user_id, session_id = _pair()
    sent: list[list[dict]] = []

    def executor(messages, _tools):
        sent.append(messages)
        if any(item.get("role") == "tool" for item in messages):
            return _turn(content="done", finish="stop")
        return _turn(ToolCallSpec("call-a", "web", '{"query":"news"}'))

    from app.tasks import job as job_module

    real_request = job_module._request

    def stop_before_results(db, task):
        step = (
            db.query(TaskStep)
            .filter(TaskStep.task_run_id == task.id)
            .order_by(TaskStep.seq.desc())
            .first()
        )
        if step is not None and step.phase == "results_ready":
            raise SystemExit(7)
        return real_request(db, task)

    set_executor(executor)
    with patch.object(job_module, "_request", stop_before_results):
        with pytest.raises(SystemExit):
            run_job(user_id, session_id, "today")
    assert posts == ["client", "https://llm.example/v1/tools/search"]
    saved = _calls(_job(session_id).id)[0]
    assert saved.state == "result"
    assert "[News](https://news.example/a)" in saved.result
    _drive_held(user_id, session_id)
    assert posts == ["client", "https://llm.example/v1/tools/search"]
    assert len(sent) == 2
    assert _job(session_id).status == "completed"
