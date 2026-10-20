from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agents.router import router_cache_key
from app.core.config import settings
from app.core.llm import (
    AnswerDelta,
    LLMInfraError,
    LLMStructuredError,
    ReasoningDelta,
    Role,
    RoleConfigError,
    ROLE_SPECS,
    Spoken,
    ToolsRefused,
    cache_identity,
    complete_json,
    complete_json_object,
    complete_text,
    resolve,
    stream_chat,
)

_WORKSPACE = Path(__file__).resolve().parents[2]
_APP_ROOTS = (_WORKSPACE / "backend" / "app", _WORKSPACE / "frontend" / "src")
_REQUEST_FIELDS = ("extra_body", "reasoning_effort", "reasoning_content")
_ROLE_ENV_NAMES = [spec.model_env for spec in ROLE_SPECS.values()] + [
    spec.thinking_env for spec in ROLE_SPECS.values()
]
_SECRET_NAMES = [
    "LLM_API_KEY",
    "LLM_BASE_URL",
    *_ROLE_ENV_NAMES,
    "WEB_SEARCH_BASE_URL",
    "WEB_SEARCH_API_KEY",
]


class _Box(BaseModel):
    ok: bool = False


class _Msg:
    def __init__(self, content, reasoning_content=None, model_extra=None):
        self.content = content
        self.reasoning_content = reasoning_content
        if model_extra is not None:
            self.model_extra = model_extra


class _ExtraOnly:
    """reasoning_content lives only in model_extra, the way the SDK exposes it."""

    def __init__(self, content, reasoning):
        self.content = content
        self.model_extra = {"reasoning_content": reasoning}


class _Choice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens


class _Resp:
    def __init__(self, message, finish_reason="stop", prompt_tokens=None):
        self.choices = [_Choice(message, finish_reason)]
        self.usage = None if prompt_tokens is None else _Usage(prompt_tokens)


class _RateLimitError(Exception):
    status_code = 429


class _Delta:
    def __init__(self, content, reasoning_content=None):
        self.content = content
        self.reasoning_content = reasoning_content


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
            item = next(self._it)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        if isinstance(item, BaseException):
            raise item
        return item


def _install_sync(monkeypatch, outputs):
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
            self.chat = type("Chat", (), {})()
            self.chat.completions = Completions()

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_sync_client", lambda: Client())
    return calls


def _install_async(monkeypatch, outputs):
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

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: Client())
    return calls


def _assert_thinking_on(body: dict, model: str) -> None:
    assert body["model"] == model
    assert body["extra_body"] == {"thinking": {"type": "enabled"}}
    assert body["reasoning_effort"] == "high"
    assert "temperature" not in body
    assert "max_tokens" not in body


def _assert_thinking_off_json(body: dict, model: str) -> None:
    assert body["model"] == model
    assert body["extra_body"] == {"thinking": {"type": "disabled"}}
    assert body["reasoning_effort"] == "none"
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in body
    assert body["stream"] is False


def test_four_roles_send_their_own_request_bodies(monkeypatch):
    calls = _install_sync(
        monkeypatch,
        [
            _Resp(_Msg("  chat answer  ", "because"), prompt_tokens=11),
            _Resp(_Msg('{"ok": true}', None)),
            _Resp(_Msg('{"ok": false}', None)),
            _Resp(_Msg('{"ok": true}', "judge-thought")),
        ],
    )
    messages = [{"role": "user", "content": "hi", "id": "drop-me"}]
    spoken = complete_text(messages, role=Role.CHAT)
    planner = complete_json(messages, role=Role.PLANNER)
    router = complete_json(messages, role=Role.ROUTER)
    judge = complete_json(messages, role=Role.JUDGE)

    assert spoken == Spoken("chat answer", "because", 11)
    assert planner == {"ok": True}
    assert router == {"ok": False}
    assert judge == {"ok": True}
    assert len(calls) == 4
    _assert_thinking_on(calls[0], "deepseek-flash")
    assert calls[0]["stream"] is False
    assert calls[0]["messages"] == [{"role": "user", "content": "hi"}]
    _assert_thinking_off_json(calls[1], "deepseek-flash")
    _assert_thinking_off_json(calls[2], "deepseek-flash")
    _assert_thinking_on(calls[3], "deepseek-v4-pro")
    assert calls[3]["response_format"] == {"type": "json_object"}
    assert "timeout" not in calls[1]
    assert calls[3]["timeout"] == settings.llm_timeout
    assert calls[3]["stream"] is False
    assert resolve(Role.CHAT).model == "deepseek-flash"
    assert resolve(Role.JUDGE).model == "deepseek-v4-pro"
    assert resolve(Role.CHAT).thinking is True
    assert resolve(Role.PLANNER).thinking is False
    assert resolve(Role.ROUTER).thinking is False
    assert resolve(Role.JUDGE).thinking is True


def test_stream_request_omits_temperature_and_splits_deltas(monkeypatch):
    calls = _install_async(
        monkeypatch,
        [[
            _Chunk(_Delta(None, "think")),
            _Chunk(_Delta("ans", None), finish_reason="stop"),
        ]],
    )

    async def collect():
        return [item async for item in stream_chat([{"role": "user", "content": "q"}], role=Role.CHAT)]

    assert asyncio.run(collect()) == [ReasoningDelta("think"), AnswerDelta("ans")]
    _assert_thinking_on(calls[0], "deepseek-flash")
    assert calls[0]["stream"] is True
    assert "response_format" not in calls[0]


def test_none_content_is_not_concatenated(monkeypatch):
    calls = _install_sync(
        monkeypatch,
        [_Resp(_ExtraOnly(None, "from-extra"), prompt_tokens=3)],
    )
    spoken = complete_text([{"role": "user", "content": "q"}], role=Role.CHAT)
    assert spoken == Spoken("", "from-extra", 3)
    assert "None" not in spoken.content
    assert "None" not in spoken.reasoning
    assert len(calls) == 1


def test_tools_and_poisoned_messages_are_refused_before_send(monkeypatch):
    calls = _install_sync(monkeypatch, [])
    messages = [{"role": "user", "content": "hi"}]
    poisoned = [{"role": "assistant", "content": "hi", "reasoning_content": "secret"}]
    tool_turn = [{"role": "tool", "content": "ok"}]
    tool_calls = [{"role": "assistant", "content": "hi", "tool_calls": []}]

    with pytest.raises(ToolsRefused):
        complete_text(messages, role=Role.CHAT, tools=[])
    with pytest.raises(ToolsRefused):
        complete_json(messages, role=Role.PLANNER, tools={"type": "function"})
    with pytest.raises(ToolsRefused):
        complete_json_object(poisoned, _Box, role=Role.ROUTER)
    with pytest.raises(ToolsRefused):
        complete_text(poisoned, role=Role.CHAT)
    with pytest.raises(ToolsRefused):
        complete_text(tool_turn, role=Role.CHAT)
    with pytest.raises(ToolsRefused):
        complete_json(tool_calls, role=Role.JUDGE)
    assert calls == []


def test_stream_refuses_poisoned_reasoning_before_send(monkeypatch):
    calls = _install_async(monkeypatch, [])
    poisoned = [{"role": "user", "content": "hi", "reasoning_content": "secret"}]

    async def collect():
        return [item async for item in stream_chat(poisoned, role=Role.CHAT)]

    with pytest.raises(ToolsRefused):
        asyncio.run(collect())
    assert calls == []


def test_empty_api_key_does_not_call_http(monkeypatch):
    from app.core import llm as llm_mod

    def boom():
        raise AssertionError("client must not be built")

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "")
    monkeypatch.setattr(llm_mod, "_get_sync_client", boom)
    monkeypatch.setattr(llm_mod, "_get_async_client", boom)
    spoken = complete_text([{"role": "user", "content": "hello"}], role=Role.CHAT)
    assert spoken.reasoning == ""
    assert spoken.prompt_tokens is None
    assert "hello" in spoken.content
    assert complete_json([{"role": "user", "content": "hello"}], role=Role.PLANNER) is None

    async def collect():
        return [item async for item in stream_chat([{"role": "user", "content": "hello"}], role=Role.CHAT)]

    deltas = asyncio.run(collect())
    assert deltas
    assert all(isinstance(item, AnswerDelta) for item in deltas)
    assert "hello" in "".join(item.text for item in deltas)


def test_cache_key_changes_with_model_and_thinking(monkeypatch):
    kwargs = {
        "has_kb_docs": False,
        "has_tabular_docs": False,
        "prompt_sha": "abc",
        "today": "2026-10-20",
    }
    base = router_cache_key("明天开会", model="deepseek-flash", thinking="off", **kwargs)
    other_model = router_cache_key("明天开会", model="deepseek-v4-pro", thinking="off", **kwargs)
    other_thinking = router_cache_key("明天开会", model="deepseek-flash", thinking="on", **kwargs)
    assert len({base, other_model, other_thinking}) == 3

    monkeypatch.setattr(settings, "router_model", "deepseek-flash")
    monkeypatch.setattr(settings, "router_thinking", "off")
    off = cache_identity(Role.ROUTER)
    assert router_cache_key("明天开会", model=off.model, thinking=off.thinking, **kwargs) == base

    monkeypatch.setattr(settings, "router_model", "deepseek-v4-pro")
    changed_model = cache_identity(Role.ROUTER)
    assert changed_model.model == "deepseek-v4-pro"
    assert (
        router_cache_key(
            "明天开会",
            model=changed_model.model,
            thinking=changed_model.thinking,
            **kwargs,
        )
        == other_model
    )

    monkeypatch.setattr(settings, "router_thinking", "on")
    changed_thinking = cache_identity(Role.ROUTER)
    assert changed_thinking.thinking == "on"
    assert (
        router_cache_key(
            "明天开会",
            model=changed_thinking.model,
            thinking=changed_thinking.thinking,
            **kwargs,
        )
        != other_model
    )


def test_rate_limit_and_infra_finish_reasons_raise(monkeypatch):
    calls = _install_sync(monkeypatch, [_RateLimitError("slow down")])
    with pytest.raises(LLMInfraError):
        complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, raise_on_error=True)
    assert len(calls) == 1

    calls = _install_sync(monkeypatch, [_RateLimitError("slow down")])
    with pytest.raises(LLMInfraError):
        complete_json([{"role": "user", "content": "hi"}], role=Role.PLANNER, raise_on_error=True)
    assert len(calls) == 1


@pytest.mark.parametrize("reason", ["insufficient_system_resource", "aborted"])
def test_resource_and_aborted_finishes_are_infra(monkeypatch, reason):
    calls = _install_sync(monkeypatch, [_Resp(_Msg("partial", "thought"), finish_reason=reason)])
    with pytest.raises(LLMInfraError):
        complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, raise_on_error=True)
    assert len(calls) == 1

    calls = _install_sync(monkeypatch, [_Resp(_Msg('{"ok": true}', None), finish_reason=reason)])
    with pytest.raises(LLMInfraError):
        complete_json([{"role": "user", "content": "hi"}], role=Role.JUDGE, raise_on_error=True)
    assert len(calls) == 1


def test_length_with_thinking_on_is_not_a_successful_spoken(monkeypatch):
    _install_sync(monkeypatch, [_Resp(_Msg("partial answer", "thought"), finish_reason="length")])
    quiet = complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, raise_on_error=False)
    assert quiet == Spoken("", "", None)

    _install_sync(monkeypatch, [_Resp(_Msg("partial answer", "thought"), finish_reason="length")])
    with pytest.raises(LLMInfraError):
        complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, raise_on_error=True)


def test_judge_length_is_infra_even_when_thinking_is_off(monkeypatch):
    monkeypatch.setattr(settings, "judge_thinking", "off")
    calls = _install_sync(monkeypatch, [_Resp(_Msg('{"ok": true}', None), finish_reason="length")])
    with pytest.raises(LLMInfraError):
        complete_json([{"role": "user", "content": "hi"}], role=Role.JUDGE, raise_on_error=True)
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert calls[0]["temperature"] == 0.0

    _install_sync(monkeypatch, [_Resp(_Msg('{"ok": true}', None), finish_reason="length")])
    missed = complete_json([{"role": "user", "content": "hi"}], role=Role.JUDGE, raise_on_error=False)
    assert missed is None

    calls = _install_sync(monkeypatch, [_Resp(_Msg('{"ok": true}', None), finish_reason="length")])
    with pytest.raises(LLMInfraError):
        complete_json_object([{"role": "user", "content": "hi"}], _Box, role=Role.JUDGE)
    assert len(calls) == 1


class _RejectsStreamOptions(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def test_stream_rate_limit_is_infra_and_is_not_retried(monkeypatch):
    calls = _install_async(monkeypatch, [_RateLimitError("slow down")])
    usage: dict = {}

    async def collect():
        async for _item in stream_chat(
            [{"role": "user", "content": "q"}], role=Role.CHAT, usage_out=usage
        ):
            pass

    with pytest.raises(LLMInfraError):
        asyncio.run(collect())
    assert len(calls) == 1
    assert "stream_options" in calls[0]


def test_stream_retries_only_a_stream_options_rejection(monkeypatch):
    calls = _install_async(
        monkeypatch,
        [
            _RejectsStreamOptions("unknown parameter stream_options"),
            [_Chunk(_Delta("ans", None), finish_reason="stop")],
        ],
    )
    usage: dict = {}

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "q"}], role=Role.CHAT, usage_out=usage
            )
        ]

    assert asyncio.run(collect()) == [AnswerDelta("ans")]
    assert "stream_options" in calls[0]
    assert "stream_options" not in calls[1]


def test_stream_unrelated_client_error_is_not_retried(monkeypatch):
    calls = _install_async(monkeypatch, [_RejectsStreamOptions("bad request")])
    usage: dict = {}

    async def collect():
        async for _item in stream_chat(
            [{"role": "user", "content": "q"}], role=Role.CHAT, usage_out=usage
        ):
            pass

    with pytest.raises(_RejectsStreamOptions):
        asyncio.run(collect())
    assert len(calls) == 1


def test_stream_midway_rate_limit_is_infra(monkeypatch):
    _install_async(
        monkeypatch,
        [[_Chunk(_Delta("partial", "thought")), _RateLimitError("slow down")]],
    )
    got: list = []

    async def collect():
        async for item in stream_chat([{"role": "user", "content": "q"}], role=Role.CHAT):
            got.append(item)

    with pytest.raises(LLMInfraError):
        asyncio.run(collect())
    assert got == [ReasoningDelta("thought"), AnswerDelta("partial")]


def test_structured_finish_with_raise_on_error_is_not_an_empty_answer(monkeypatch):
    monkeypatch.setattr(settings, "chat_thinking", "off")
    _install_sync(monkeypatch, [_Resp(_Msg("partial", None), finish_reason="length")])
    with pytest.raises(LLMStructuredError):
        complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, raise_on_error=True)

    _install_sync(monkeypatch, [_Resp(_Msg("partial", None), finish_reason="content_filter")])
    quiet = complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, raise_on_error=False)
    assert quiet == Spoken("", "", None)


def test_ragas_raises_judge_infra_instead_of_scoring_it(monkeypatch):
    from app.eval.ragas_judge import run_ragas_rows

    def boom(*_args, **kwargs):
        assert kwargs.get("raise_exceptions") is True
        raise LLMInfraError("finish_reason=length")

    monkeypatch.setattr("app.eval.ragas_judge.settings.llm_api_key", "test-key")
    monkeypatch.setattr(
        "app.eval.ragas_judge.build_ragas_llm_embeddings",
        lambda: (object(), object()),
    )
    monkeypatch.setattr("ragas.evaluate", boom)
    outcome = run_ragas_rows(
        [{"question": "q", "answer": "a", "contexts": ["c"], "ground_truth": "g"}]
    )
    assert outcome.infra_error is True
    assert outcome.means == {}
    assert outcome.per_row == []


def test_stream_infra_finish_does_not_yield_the_failing_chunk(monkeypatch):
    _install_async(
        monkeypatch,
        [[
            _Chunk(_Delta(None, "kept")),
            _Chunk(_Delta("drop-me", "also-drop"), finish_reason="insufficient_system_resource"),
        ]],
    )
    got: list = []

    async def collect():
        async for item in stream_chat([{"role": "user", "content": "q"}], role=Role.CHAT):
            got.append(item)

    with pytest.raises(LLMInfraError):
        asyncio.run(collect())
    assert got == [ReasoningDelta("kept")]


def test_empty_model_does_not_fall_back_to_another_role(monkeypatch):
    monkeypatch.setattr(settings, "planner_model", "  ")
    with pytest.raises(RoleConfigError, match="PLANNER_MODEL"):
        resolve(Role.PLANNER)
    monkeypatch.setattr(settings, "planner_model", "deepseek-flash")
    monkeypatch.setattr(settings, "planner_thinking", "maybe")
    with pytest.raises(RoleConfigError, match="PLANNER_THINKING"):
        resolve(Role.PLANNER)


def test_request_fields_stay_inside_the_llm_module():
    hits: list[str] = []
    for root in _APP_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            if path.as_posix().endswith("backend/app/core/llm.py"):
                continue
            text = path.read_text(encoding="utf-8")
            for field in _REQUEST_FIELDS:
                if field in text:
                    hits.append(f"{path.relative_to(_WORKSPACE)}: {field}")
    assert hits == []


def test_role_env_names_are_documented_and_not_in_the_compose_map():
    example = (_WORKSPACE / ".env.example").read_text(encoding="utf-8")
    compose = (_WORKSPACE / "docker-compose.yml").read_text(encoding="utf-8")
    comment = compose.split("x-backend-env:", 1)[1].split("env_file:", 1)[0]
    for name in _SECRET_NAMES:
        assert f"{name}=" in example
        assert name in comment
        assert f"{name}:" not in compose


def test_disk_cache_unlinks_a_file_that_is_not_schema_3(tmp_path):
    from app.eval.disk_cache import load_or_drop_entries

    path = tmp_path / "router_cache.json"
    path.write_text(json.dumps({"glm-4-air": {"needs_kb": True}}), encoding="utf-8")
    assert load_or_drop_entries(path) == {}
    assert not path.exists()

    path.write_text(json.dumps({"schema": 2, "entries": {"a": 1}}), encoding="utf-8")
    assert load_or_drop_entries(path) == {}
    assert not path.exists()

    path.write_text(json.dumps({"schema": 3, "entries": {"a": 1}}), encoding="utf-8")
    assert load_or_drop_entries(path) == {"a": 1}


def test_committed_router_cache_is_absent():
    path = _WORKSPACE / "backend" / "data" / "eval" / "router_cache.json"
    assert not path.exists()
