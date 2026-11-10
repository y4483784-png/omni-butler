"""A stopped generation does not send, and a stop that lands after entry still counts."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.agents.harness.constitution import load_constitution
from app.core.db import SessionLocal, init_db
from app.core.generation_stop import GenerationStopped, bind, reset
from app.core.llm import Role
from app.models.models import Session as ChatSession
from app.models.models import User
from app.services import task_run

load_constitution()


def _admin_id() -> int:
    init_db()
    db = SessionLocal()
    try:
        return db.query(User).filter(User.external_id == "admin").one().id
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="停止")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _spoken_response(text: str):
    message = SimpleNamespace(content=text, reasoning_content=None, model_extra=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=1)
    return SimpleNamespace(choices=[choice], usage=usage)


class _Completions:
    def __init__(self, create):
        self.create = create


class _Chat:
    def __init__(self, create):
        self.completions = _Completions(create)


class _Client:
    def __init__(self, create):
        self.chat = _Chat(create)


def test_worker_sees_stop_committed_after_start_and_before_create(monkeypatch):
    from app.core import llm as llm_mod

    started = threading.Event()
    release = threading.Event()
    creates = {"n": 0}
    real_slot = llm_mod.model_slot

    @contextmanager
    def blocking_slot(model, *, lease_seconds=None):
        started.set()
        assert release.wait(5)
        with real_slot(model, lease_seconds=lease_seconds):
            yield

    def create(**_kwargs):
        creates["n"] += 1
        return _spoken_response("sent")

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "model_slot", blocking_slot)
    monkeypatch.setattr(llm_mod, "_get_sync_client", lambda: _Client(create))

    user_id = _admin_id()
    turn = task_run.begin_turn(user_id=user_id, session_id=_session(user_id))

    async def exercise():
        token = bind(user_id=user_id, generation=turn.generation)
        try:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    llm_mod.complete_text,
                    [{"role": "user", "content": "hello"}],
                    role=Role.CHAT,
                )
            )
            assert await asyncio.to_thread(started.wait, 5)
            task_run.stop(user_id=user_id, generation=turn.generation)
            release.set()
            with pytest.raises(GenerationStopped):
                await worker
        finally:
            release.set()
            reset(token)

    asyncio.run(exercise())
    assert creates["n"] == 0


def test_unbound_complete_text_still_creates(monkeypatch):
    from app.core import llm as llm_mod

    creates = {"n": 0}

    def create(**_kwargs):
        creates["n"] += 1
        return _spoken_response("sent")

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_sync_client", lambda: _Client(create))
    spoken = llm_mod.complete_text([{"role": "user", "content": "hello"}], role=Role.CHAT)
    assert creates["n"] == 1
    assert spoken.content == "sent"


def test_empty_key_bound_and_stopped_skips_the_mock(monkeypatch):
    from app.core import llm as llm_mod

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "")
    user_id = _admin_id()
    turn = task_run.begin_turn(user_id=user_id, session_id=_session(user_id))
    task_run.stop(user_id=user_id, generation=turn.generation)

    async def exercise():
        token = bind(user_id=user_id, generation=turn.generation)
        try:
            with pytest.raises(GenerationStopped):
                llm_mod.complete_text([{"role": "user", "content": "hello"}], role=Role.CHAT)
            with pytest.raises(GenerationStopped):
                async for _delta in llm_mod.stream_chat(
                    [{"role": "user", "content": "hello"}],
                    role=Role.CHAT,
                ):
                    raise AssertionError("mock text was produced")
        finally:
            reset(token)

    asyncio.run(exercise())


def _empty_workflow(**kwargs):
    task_run.stop(user_id=kwargs["user_id"], generation=kwargs["generation"])
    return {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [],
        "schedule_card": None,
        "direct_answer": None,
        "pending_calendar": None,
    }


def test_late_chunk_stays_out_of_the_reply(plain_client: TestClient):
    from app.core.llm import AnswerDelta
    from app.models.models import Message

    user_id = _admin_id()
    session_id = _session(user_id)

    async def fake_stream(*_args, **_kwargs):
        yield AnswerDelta("KEEP_THIS_CHUNK")
        latest = task_run.list_for_user(user_id=user_id)[-1]
        task_run.stop(user_id=latest.user_id, generation=latest.generation)
        yield AnswerDelta("LATE_CHUNK_SHOULD_NOT_APPEAR")

    streaming = {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [{"role": "user", "content": "hello"}],
        "schedule_card": None,
        "direct_answer": None,
        "pending_calendar": None,
    }
    with (
        patch("app.api.chat._run_workflow_sync", return_value=streaming),
        patch("app.api.chat.stream_chat", side_effect=fake_stream),
    ):
        response = plain_client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "hello"},
        )

    assert response.status_code == 200
    assert response.request.url.path == "/api/chat"
    assert "KEEP_THIS" in response.text
    assert "LATE_CHUNK_SHOULD_NOT_APPEAR" not in response.text
    db = SessionLocal()
    try:
        saved = (
            db.query(Message)
            .filter(Message.session_id == session_id, Message.role == "assistant")
            .all()
        )
        text = "\n".join(row.content or "" for row in saved)
    finally:
        db.close()
    assert "KEEP_THIS" in text
    assert "LATE_CHUNK_SHOULD_NOT_APPEAR" not in text


def test_confirmed_event_remains_after_stop(plain_client: TestClient):
    from app.models.models import CalendarEvent

    user_id = _admin_id()
    db = SessionLocal()
    try:
        event = CalendarEvent(
            user_id=user_id,
            title="已确认的会",
            start_at=datetime(2026, 11, 4, 10, 0),
            end_at=datetime(2026, 11, 4, 11, 0),
            status="active",
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        event_id = event.id
        before = db.query(CalendarEvent).count()
    finally:
        db.close()

    with patch("app.api.chat._run_workflow_sync", side_effect=_empty_workflow):
        response = plain_client.post(
            "/api/chat",
            json={"session_id": _session(user_id), "message": "你好"},
        )

    assert response.status_code == 200
    assert response.request.url.path == "/api/chat"
    db = SessionLocal()
    try:
        assert db.query(CalendarEvent).count() == before
        kept = db.get(CalendarEvent, event_id)
        assert kept is not None
        assert kept.title == "已确认的会"
    finally:
        db.close()


def test_stopped_generation_writes_no_memory(plain_client: TestClient, monkeypatch):
    from app.core.config import settings
    from app.models.models import MemoryItem

    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_use_llm", True)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "context_summary_enabled", True)
    monkeypatch.setattr(settings, "context_manager_enabled", True)
    monkeypatch.setattr(settings, "max_context_turns", 0)
    monkeypatch.setattr(settings, "context_summary_min_overflow_turns", 1)
    calls: list[str] = []

    def spy(*_args, **_kwargs):
        calls.append("complete_json")
        return None

    monkeypatch.setattr("app.services.memory.complete_json", spy)
    monkeypatch.setattr("app.services.session.complete_json", spy)
    monkeypatch.setattr("app.agents.context.summary.complete_json", spy)

    user_id = _admin_id()
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="新会话")
        db.add(row)
        db.commit()
        db.refresh(row)
        session_id = row.id
        before = db.query(MemoryItem).filter(MemoryItem.user_id == user_id).count()
    finally:
        db.close()

    with patch("app.api.chat._run_workflow_sync", side_effect=_empty_workflow):
        response = plain_client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "请记住我喜欢用表格"},
        )

    assert response.status_code == 200
    assert response.request.url.path == "/api/chat"
    assert calls == []
    db = SessionLocal()
    try:
        after = db.query(MemoryItem).filter(MemoryItem.user_id == user_id).count()
    finally:
        db.close()
    assert after == before
