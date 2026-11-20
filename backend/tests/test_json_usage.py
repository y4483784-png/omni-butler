from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.core.llm import LLMInfraError, Role, complete_json, complete_json_object, resolve


class _Kept(BaseModel):
    kept: bool


class _Msg:
    def __init__(self, content: str):
        self.content = content
        self.reasoning_content = None
        self.model_extra = None


class _Resp:
    def __init__(self, content: str, usage):
        self.choices = [SimpleNamespace(message=_Msg(content), finish_reason="stop")]
        self.usage = usage


class _APIConnectionError(Exception):
    pass


def _install(monkeypatch, outputs):
    from app.core import llm as llm_mod

    calls: list[dict] = []
    queue = list(outputs)

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_sync_client", lambda: Client())
    return calls


def _rows(generation: int):
    from app.core.db import SessionLocal
    from app.models.models import LlmCall

    db = SessionLocal()
    try:
        return db.query(LlmCall).filter(LlmCall.generation == generation).all()
    finally:
        db.close()


def _clear(generation: int) -> None:
    from app.core.db import SessionLocal
    from app.models.models import LlmCall

    db = SessionLocal()
    try:
        db.query(LlmCall).filter(LlmCall.generation == generation).delete()
        db.commit()
    finally:
        db.close()


def _assert_missing_counts(row, role: Role) -> None:
    assert row.role == role.value
    assert row.model == resolve(role).model
    assert row.user_id == 1
    assert row.prompt_tokens is None
    assert row.completion_tokens is None


def test_json_stop_writes_one_llm_call_and_leaves_a_missing_count_null(monkeypatch):
    from app.core.generation_stop import bind, reset

    body = '{"kept":true}'
    calls = _install(monkeypatch, [_Resp(body, None), _Resp(body, None)])
    _clear(92021)
    _clear(92022)

    token = bind(user_id=1, generation=92021)
    try:
        parsed = complete_json([{"role": "user", "content": "plan"}], role=Role.PLANNER)
    finally:
        reset(token)
    assert parsed == {"kept": True}
    stored = _rows(92021)
    assert len(stored) == 1
    _assert_missing_counts(stored[0], Role.PLANNER)

    token = bind(user_id=1, generation=92022)
    try:
        decision = complete_json_object(
            [{"role": "user", "content": "route"}],
            _Kept,
            role=Role.ROUTER,
        )
    finally:
        reset(token)
    assert decision == _Kept(kept=True)
    stored = _rows(92022)
    assert len(stored) == 1
    _assert_missing_counts(stored[0], Role.ROUTER)
    assert len(calls) == 2


def test_json_provider_error_before_a_response_writes_no_row(monkeypatch):
    from app.core.generation_stop import bind, reset

    calls = _install(
        monkeypatch,
        [_APIConnectionError("down"), _APIConnectionError("down")],
    )
    _clear(92023)
    _clear(92024)

    token = bind(user_id=1, generation=92023)
    try:
        parsed = complete_json([{"role": "user", "content": "plan"}], role=Role.PLANNER)
    finally:
        reset(token)
    assert parsed is None
    assert _rows(92023) == []

    token = bind(user_id=1, generation=92024)
    try:
        with pytest.raises(LLMInfraError):
            complete_json_object(
                [{"role": "user", "content": "route"}],
                _Kept,
                role=Role.ROUTER,
            )
    finally:
        reset(token)
    assert _rows(92024) == []
    assert len(calls) == 2
