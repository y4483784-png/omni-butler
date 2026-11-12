from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.harness.constitution import load_constitution
from app.agents.harness.types import ToolContext
from app.agents.router import RouterDecision, _decision_to_plan
from app.agents.tools.registry import get_tool
from app.agents.tools.web import execute_web
from app.core.config import settings
from app.core.llm import (
    AnswerDelta,
    CitationDelta,
    LLMInfraError,
    ReasoningDelta,
    Role,
    RoleCall,
    SearchDelta,
    ToolsRefused,
    _build_request,
    complete_text,
    resolve,
    stream_chat,
)
from app.core.responses_api import NATIVE_WEB_MODEL, citations_from
from app.services.web_search import SearchOptions, search_api


_TEXT_KEYS = {"model", "messages", "stream", "extra_body", "reasoning_effort"}


class _Msg:
    def __init__(self, content, reasoning_content=None):
        self.content = content
        self.reasoning_content = reasoning_content


class _Choice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, message):
        self.choices = [_Choice(message)]
        self.usage = None


class _Ev:
    def __init__(self, type, **kwargs):
        self.type = type
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Done:
    def __init__(self, input_tokens=3, output_tokens=2, output=None, error=None):
        self.usage = _Usage(input_tokens, output_tokens)
        self.output = output or []
        self.error = error


class _Chunks:
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


class _Status(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _install_responses(monkeypatch, outputs):
    from app.core import llm as llm_mod

    calls: list[dict] = []
    queue = list(outputs)

    class Responses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Chunks(item)

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: SimpleNamespace(responses=Responses()))
    return calls


def _text_round(text, *, search=None, item=None, usage=(4, 1)):
    events = []
    if search is not None:
        events.append(_Ev(f"response.web_search_call.{search}", action={"query": "新闻"}))
    events.append(_Ev("response.output_text.delta", delta=text))
    if item is not None:
        events.append(_Ev("response.output_item.done", item=item))
    events.append(_Ev("response.completed", response=_Done(*usage)))
    return events


def test_chat_completions_body_does_not_grow_a_search_field():
    model = resolve(Role.CHAT).model
    body = _build_request(
        RoleCall(role=Role.CHAT, model=model, thinking=True),
        [{"role": "user", "content": "今天的新闻"}],
        kind="text",
        stream=False,
    )
    assert set(body) == _TEXT_KEYS
    assert "web_search" not in body
    assert "enable_search" not in body
    assert body["model"] == "deepseek-flash"


def test_complete_text_still_refuses_tools_before_the_client(monkeypatch):
    from app.core import llm as llm_mod

    def boom():
        raise AssertionError("client must not be built")

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_sync_client", boom)
    with pytest.raises(ToolsRefused):
        complete_text(
            [{"role": "user", "content": "hi"}],
            role=Role.CHAT,
            tools=[{"type": "function", "name": "lookup"}],
        )


def test_default_answer_offers_native_search_on_pro(monkeypatch):
    calls = _install_responses(monkeypatch, [_text_round("有结果")])

    async def collect():
        return [
            item
            async for item in stream_chat([{"role": "user", "content": "今天的新闻"}], role=Role.CHAT)
        ]

    assert asyncio.run(collect()) == [AnswerDelta("有结果")]
    body = calls[0]
    assert resolve(Role.CHAT).model == "deepseek-flash"
    assert settings.chat_model == "deepseek-flash"
    assert body["model"] == NATIVE_WEB_MODEL
    assert body["tools"] == [{"type": "web_search"}]
    assert "tool_choice" not in body
    assert "extra_body" not in body
    assert "enable_search" not in body
    assert body["stream"] is True
    assert body["instructions"]


def test_explicit_deny_omits_the_tool_and_stays_on_the_chat_model(monkeypatch):
    calls = _install_responses(monkeypatch, [_text_round("不开")])

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "今天的新闻"}],
                role=Role.CHAT,
                offer_web=False,
            )
        ]

    assert asyncio.run(collect()) == [AnswerDelta("不开")]
    body = calls[0]
    assert body["model"] == "deepseek-flash"
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "instructions" not in body


def test_user_ask_to_search_does_not_force_the_tool(monkeypatch):
    calls = _install_responses(monkeypatch, [_text_round("好")])

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "搜一下今天的新闻"}],
                role=Role.CHAT,
            )
        ]

    asyncio.run(collect())
    assert calls[0]["tools"] == [{"type": "web_search"}]
    assert "tool_choice" not in calls[0]


def test_router_does_not_queue_a_second_web_plug():
    allowed = load_constitution()["allowed_tools"]
    assert allowed == ["kb", "web", "calendar", "sandbox"]
    assert web_offered(allowed) is True
    assert web_offered(("kb", "calendar")) is False

    plan = _decision_to_plan(RouterDecision(needs_web=True, needs_kb=True))
    assert plan["pending_tools"] == ["kb"]
    assert plan["needs_web"] is True
    assert get_tool("web").execute is execute_web
    assert get_tool("上网") is None


def test_execute_web_does_not_post(monkeypatch):
    from app.services import web_search as web_search_mod

    monkeypatch.setattr(web_search_mod.settings, "web_search_api_key", "web-key")
    ctx = ToolContext(db=object(), message="天气", history=[], user_id=1, iteration=1)
    with patch("app.services.web_search.httpx.Client") as client_cls:
        result = execute_web(ctx, {})
    client_cls.assert_not_called()
    assert result.evidence == []
    assert result.thinking_steps == ["网页搜索改由回答请求附带，这里不再另发"]


def test_dormant_zhipu_client_is_not_the_answer_path(monkeypatch):
    from app.services import web_search as web_search_mod

    monkeypatch.setattr(web_search_mod.settings, "web_search_api_key", "web-key")
    monkeypatch.setattr(web_search_mod.settings, "web_search_base_url", "")
    monkeypatch.setattr(web_search_mod.settings, "web_search_engine", "search_pro_quark")
    with patch("app.services.web_search.httpx.Client") as client_cls:
        post = client_cls.return_value.__enter__.return_value.post
        post.return_value.json.return_value = {"search_result": []}
        post.return_value.raise_for_status = lambda: None
        search_api(
            "天气",
            options=SearchOptions(
                query="天气",
                count=5,
                content_size="medium",
                recency_filter="noLimit",
            ),
        )
    assert post.call_args.args[0] == "https://open.bigmodel.cn/api/paas/v4/web_search"


def test_responses_error_is_surfaced_and_does_not_search_elsewhere(monkeypatch):
    calls = _install_responses(monkeypatch, [_Status("nope", 503)])

    async def collect():
        async for _item in stream_chat([{"role": "user", "content": "搜"}], role=Role.CHAT):
            pass

    with pytest.raises(LLMInfraError):
        asyncio.run(collect())
    assert len(calls) == 1
    assert calls[0]["model"] == NATIVE_WEB_MODEL


def test_failed_response_is_an_error_without_a_made_up_answer(monkeypatch):
    failed = _Done(error=SimpleNamespace(message="model does not support web_search"))
    calls = _install_responses(
        monkeypatch,
        [[
            _Ev("response.output_text.delta", delta="先别信"),
            _Ev("response.failed", response=failed),
        ]],
    )
    got = []

    async def collect():
        async for item in stream_chat([{"role": "user", "content": "搜"}], role=Role.CHAT):
            got.append(item)

    with pytest.raises(LLMInfraError, match="does not support"):
        asyncio.run(collect())
    assert got == [AnswerDelta("先别信")]
    assert len(calls) == 1


def test_stream_reports_usage_and_a_finished_search_without_a_citation(monkeypatch):
    calls = _install_responses(
        monkeypatch,
        [_text_round("正文", search="completed", usage=(11, 4))],
    )
    usage: dict = {}

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "新闻"}],
                role=Role.CHAT,
                usage_out=usage,
            )
        ]

    assert asyncio.run(collect()) == [
        SearchDelta("网页搜索已结束"),
        AnswerDelta("正文"),
    ]
    assert usage == {"prompt_tokens": 11, "completion_tokens": 4}
    assert "stream_options" not in calls[0]
    assert citations_from({"status": "completed"}) == []


def test_citation_uses_only_a_url_the_payload_actually_has(monkeypatch):
    item = {
        "type": "web_search_call",
        "status": "completed",
        "action": {"sources": [{"title": "官网", "url": "https://example.com/a"}]},
    }
    _install_responses(monkeypatch, [_text_round("见链接", search="searching", item=item)])

    async def collect():
        return [
            item
            async for item in stream_chat([{"role": "user", "content": "官网"}], role=Role.CHAT)
        ]

    deltas = asyncio.run(collect())
    citations = [delta for delta in deltas if isinstance(delta, CitationDelta)]
    assert len(citations) == 1
    assert citations[0].citations == (
        {
            "title": "官网",
            "url": "https://example.com/a",
            "filename": "官网",
            "source_type": "web",
            "snippet": "官网",
        },
    )


def test_function_round_replays_the_call_and_does_not_add_a_web_plug(monkeypatch):
    calls = _install_responses(
        monkeypatch,
        [
            [
                _Ev("response.output_text.delta", delta="先查"),
                _Ev(
                    "response.output_item.done",
                    item={
                        "type": "function_call",
                        "call_id": "fc1",
                        "name": "lookup",
                        "arguments": "{\"q\":1}",
                    },
                ),
                _Ev("response.completed", response=_Done()),
            ],
            [
                _Ev("response.output_text.delta", delta="查完"),
                _Ev("response.completed", response=_Done(8, 3)),
            ],
        ],
    )
    seen: list[tuple[str, str]] = []

    def execute(name, arguments):
        seen.append((name, arguments))
        return "找到了"

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "查日程"}],
                role=Role.CHAT,
                tools=[{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
                tool_executor=execute,
            )
        ]

    deltas = asyncio.run(collect())
    assert [delta for delta in deltas if isinstance(delta, AnswerDelta)] == [
        AnswerDelta("先查"),
        AnswerDelta("查完"),
    ]
    assert seen == [("lookup", "{\"q\":1}")]
    assert len(calls) == 2
    assert calls[0]["tools"] == [
        {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
        {"type": "web_search"},
    ]
    replay = calls[1]["input"]
    assert {"type": "function_call", "call_id": "fc1", "name": "lookup", "arguments": "{\"q\":1}"} in replay
    assert {"type": "function_call_output", "call_id": "fc1", "output": "找到了"} in replay
    assert all(item.get("name") != "web" for item in calls[1]["tools"] if item.get("type") == "function")


def test_stop_before_the_tool_followup_sends_no_second_request(monkeypatch):
    from app.core import llm as llm_mod

    calls = _install_responses(
        monkeypatch,
        [[
            _Ev("response.output_text.delta", delta="先查"),
            _Ev(
                "response.output_item.done",
                item={
                    "type": "function_call",
                    "call_id": "fc1",
                    "name": "lookup",
                    "arguments": "{}",
                },
            ),
            _Ev("response.completed", response=_Done()),
        ]],
    )
    state = {"stop": False}

    def stopped():
        return state["stop"]

    def execute(_name, _arguments):
        state["stop"] = True
        return "晚了"

    monkeypatch.setattr(llm_mod, "is_stopped", stopped)

    async def collect():
        return [
            item
            async for item in stream_chat(
                [{"role": "user", "content": "查"}],
                role=Role.CHAT,
                tools=[{"type": "function", "name": "lookup"}],
                tool_executor=execute,
            )
        ]

    deltas = asyncio.run(collect())
    assert AnswerDelta("先查") in deltas
    assert len(calls) == 1


def web_offered(allowed):
    from app.core.responses_api import web_offered as offer

    return offer(allowed)
