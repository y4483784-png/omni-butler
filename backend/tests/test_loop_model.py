from __future__ import annotations

import asyncio
import base64

import pytest

from app.core.config import settings
from app.core.llm import (
    AnswerDelta,
    LLMStructuredError,
    ModelTurn,
    ReasoningDelta,
    Role,
    RoleConfigError,
    ROLE_SPECS,
    ToolCallDelta,
    ToolCallSpec,
    ToolRoundEnd,
    ToolsRefused,
    complete_text,
    provider_payload,
    resolve_executor,
    stream_chat,
)

PNG = b"\x89PNG\r\n\x1a\nrest"
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kb",
            "description": "read",
            "parameters": {"type": "object"},
        },
    }
]


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content, reasoning_content="", calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = calls


class _Choice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens


class _Resp:
    def __init__(self, message, finish_reason="stop", prompt_tokens=4):
        self.choices = [_Choice(message, finish_reason)]
        self.usage = _Usage(prompt_tokens)


class _ToolPiece:
    def __init__(self, index, call_id, name, arguments):
        self.index = index
        self.id = call_id
        self.function = _Fn(name, arguments)


class _Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
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
            item = next(self._it)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
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


def _clear_executor(monkeypatch):
    monkeypatch.delenv("EXECUTOR_MODEL", raising=False)
    monkeypatch.delenv("EXECUTOR_THINKING", raising=False)


def _job_messages():
    url = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "goal"},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        },
        {
            "role": "assistant",
            "content": "looking",
            "reasoning_content": "",
            "tool_calls": [
                {
                    "id": "call-a",
                    "type": "function",
                    "function": {"name": "kb", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "pages"},
    ]


def test_a_job_request_keeps_tools_and_reasoning(monkeypatch):
    _clear_executor(monkeypatch)
    calls = _install_sync(
        monkeypatch,
        [_Resp(_Msg("done", "because"), finish_reason="stop")],
    )
    turn = complete_text(_job_messages(), role=Role.CHAT, tools=_TOOLS, job=True)
    assert isinstance(turn, ModelTurn)
    assert turn.finish == "stop"
    assert turn.tool_calls == ()
    assert turn.content == "done"
    body = calls[0]
    assert body["model"] == settings.chat_model
    assert body["tools"] == _TOOLS
    assert "tool_choice" not in body
    assert "temperature" not in body
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body
    assert body["messages"][0]["content"][1]["type"] == "image_url"
    assistant = body["messages"][1]
    assert assistant["reasoning_content"] == ""
    assert assistant["tool_calls"][0]["id"] == "call-a"
    assert body["messages"][2] == {"role": "tool", "tool_call_id": "call-a", "content": "pages"}
    payload = provider_payload(body)
    assert payload["tools"] == _TOOLS
    assert "tool_choice" not in payload
    assert len(ROLE_SPECS) == 4


def test_tool_calls_finish_is_a_turn_and_stop_can_keep_calls(monkeypatch):
    _clear_executor(monkeypatch)
    calls = _install_sync(
        monkeypatch,
        [
            _Resp(_Msg("looking", "because", [_Call("call-a", "kb", "{}")]), finish_reason="tool_calls"),
            _Resp(_Msg("looking", "", [_Call("call-b", "web", "{}")]), finish_reason="stop"),
            _Resp(_Msg("nope", ""), finish_reason="length"),
        ],
    )
    monkeypatch.setenv("EXECUTOR_THINKING", "off")
    first = complete_text([{"role": "user", "content": "goal"}], role=Role.PLANNER, tools=_TOOLS, job=True)
    assert first == ModelTurn(
        "looking",
        "because",
        (ToolCallSpec("call-a", "kb", "{}"),),
        "tool_calls",
        4,
    )
    assert calls[0]["model"] == settings.chat_model
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "temperature" not in calls[0]
    second = complete_text([{"role": "user", "content": "goal"}], role=Role.CHAT, tools=_TOOLS, job=True)
    assert second.finish == "stop"
    assert second.tool_calls == (ToolCallSpec("call-b", "web", "{}"),)
    with pytest.raises(LLMStructuredError):
        complete_text([{"role": "user", "content": "goal"}], role=Role.CHAT, tools=_TOOLS, job=True)


def test_executor_env_falls_back_one_side_at_a_time(monkeypatch):
    _clear_executor(monkeypatch)
    monkeypatch.setattr(settings, "chat_model", "chat-fallback")
    monkeypatch.setattr(settings, "chat_thinking", "on")
    assert resolve_executor().model == "chat-fallback"
    assert resolve_executor().thinking is True
    monkeypatch.setenv("EXECUTOR_MODEL", "executor-a")
    monkeypatch.setattr(settings, "chat_thinking", "off")
    chosen = resolve_executor()
    assert chosen.model == "executor-a"
    assert chosen.thinking is False
    assert chosen.role is Role.CHAT
    monkeypatch.delenv("EXECUTOR_MODEL")
    monkeypatch.setenv("EXECUTOR_THINKING", "on")
    monkeypatch.setattr(settings, "chat_model", "from-chat")
    chosen = resolve_executor()
    assert chosen.model == "from-chat"
    assert chosen.thinking is True
    monkeypatch.setenv("EXECUTOR_MODEL", "executor-b")
    monkeypatch.setenv("EXECUTOR_THINKING", "off")
    chosen = resolve_executor()
    assert chosen.model == "executor-b"
    assert chosen.thinking is False
    monkeypatch.setenv("EXECUTOR_THINKING", "sideways")
    with pytest.raises(RoleConfigError):
        resolve_executor()
    monkeypatch.delenv("EXECUTOR_MODEL")
    monkeypatch.delenv("EXECUTOR_THINKING")
    monkeypatch.setattr(settings, "chat_model", "")
    with pytest.raises(RoleConfigError):
        resolve_executor()


def test_a_job_stream_ends_after_the_tool_deltas(monkeypatch):
    _clear_executor(monkeypatch)
    monkeypatch.setenv("EXECUTOR_MODEL", "executor-stream")
    monkeypatch.setenv("EXECUTOR_THINKING", "on")
    calls = _install_async(
        monkeypatch,
        [[
            _Chunk(_Delta(reasoning_content="because")),
            _Chunk(_Delta(content="looking")),
            _Chunk(_Delta(tool_calls=[_ToolPiece(0, "call-a", "kb", "{")])),
            _Chunk(_Delta(tool_calls=[_ToolPiece(0, None, None, "}")]), finish_reason="tool_calls"),
        ]],
    )

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "goal"}],
                role=Role.CHAT,
                tools=_TOOLS,
                job=True,
            )
        ]

    assert asyncio.run(collect()) == [
        ReasoningDelta("because"),
        AnswerDelta("looking"),
        ToolCallDelta(0, "call-a", "kb", "{"),
        ToolCallDelta(0, None, None, "}"),
        ToolRoundEnd("tool_calls"),
    ]
    assert calls[0]["model"] == "executor-stream"
    assert calls[0]["tools"] == _TOOLS
    assert "tool_choice" not in calls[0]
    assert "temperature" not in calls[0]


def test_ordinary_chat_still_refuses_tools(monkeypatch):
    calls = _install_sync(monkeypatch, [])
    with pytest.raises(ToolsRefused):
        complete_text(
            [{"role": "assistant", "content": "hi", "reasoning_content": "secret"}],
            role=Role.CHAT,
        )
    with pytest.raises(ToolsRefused):
        complete_text([{"role": "user", "content": "hi"}], role=Role.CHAT, tools=_TOOLS)
    assert calls == []
