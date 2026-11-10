"""A stopped generation does not send, and a stop that lands after entry still counts."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

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
