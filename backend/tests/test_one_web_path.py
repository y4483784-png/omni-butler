from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents.harness.constitution import load_constitution
from app.agents.router import RouterDecision, _decision_to_plan
from app.agents.tools.registry import get_tool
from app.agents.tools.web import execute_web
from app.core.llm import Role, RoleCall, ToolsRefused, _build_request, complete_text, resolve
from app.services.web_search import SearchOptions, search_api


_TEXT_KEYS = {"model", "messages", "stream", "extra_body", "reasoning_effort"}
_JSON_OFF_KEYS = _TEXT_KEYS | {"response_format", "temperature"}


class _Msg:
    def __init__(self, content, reasoning_content=None, model_extra=None):
        self.content = content
        self.reasoning_content = reasoning_content
        if model_extra is not None:
            self.model_extra = model_extra


class _Choice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, message, finish_reason="stop", prompt_tokens=None):
        self.choices = [_Choice(message, finish_reason)]
        self.usage = None if prompt_tokens is None else prompt_tokens


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


def test_chat_body_stays_inside_the_keys_build_request_writes():
    model = resolve(Role.CHAT).model
    messages = [{"role": "user", "content": "今天的新闻", "web_search": {"search_query": "新闻"}}]
    wired = [{"role": "user", "content": "今天的新闻"}]

    enabled = _build_request(
        RoleCall(role=Role.CHAT, model=model, thinking=True),
        messages,
        kind="text",
        stream=False,
    )
    assert set(enabled) == _TEXT_KEYS
    assert enabled["model"] == model
    assert enabled["messages"] == wired
    assert enabled["stream"] is False
    assert enabled["extra_body"] == {"thinking": {"type": "enabled"}}
    assert enabled["reasoning_effort"] == "high"

    disabled = _build_request(
        RoleCall(role=Role.CHAT, model=model, thinking=False),
        messages,
        kind="text",
        stream=False,
    )
    assert set(disabled) == _TEXT_KEYS
    assert disabled["model"] == model
    assert disabled["messages"] == wired
    assert disabled["stream"] is False
    assert disabled["extra_body"] == {"thinking": {"type": "disabled"}}
    assert disabled["reasoning_effort"] == "none"

    json_off = _build_request(
        RoleCall(role=Role.CHAT, model=model, thinking=False),
        messages,
        kind="json",
        stream=False,
    )
    assert set(json_off) == _JSON_OFF_KEYS
    assert json_off["model"] == model
    assert json_off["messages"] == wired
    assert json_off["stream"] is False
    assert json_off["extra_body"] == {"thinking": {"type": "disabled"}}
    assert json_off["reasoning_effort"] == "none"
    assert json_off["response_format"] == {"type": "json_object"}
    assert json_off["temperature"] == 0


def test_complete_text_refuses_tools_before_the_client_is_called(monkeypatch):
    calls = _install_sync(
        monkeypatch,
        [_Resp(_Msg("unused")), _Resp(_Msg("unused")), _Resp(_Msg("unused"))],
    )
    user = [{"role": "user", "content": "hi"}]
    tool_turn = [{"role": "tool", "content": "ok"}]
    tool_calls = [{"role": "assistant", "content": "hi", "tool_calls": [{"id": "call_1"}]}]

    with pytest.raises(ToolsRefused):
        complete_text(user, role=Role.CHAT, tools=[{"type": "function", "function": {"name": "web"}}])
    with pytest.raises(ToolsRefused):
        complete_text(tool_turn, role=Role.CHAT)
    with pytest.raises(ToolsRefused):
        complete_text(tool_calls, role=Role.CHAT)
    assert calls == []


def test_web_remains_the_only_queued_web_tool():
    allowed = load_constitution()["allowed_tools"]
    assert allowed == ["kb", "web", "calendar", "sandbox"]
    assert "上网" not in allowed

    plan = _decision_to_plan(RouterDecision(needs_web=True))
    assert plan["pending_tools"] == ["web"]
    assert plan["needs_web"] is True
    assert plan["needs_kb"] is False
    assert plan["needs_calendar"] is False
    assert plan["needs_sandbox"] is False
    assert plan["needs_freshness"] is False

    assert get_tool("web").execute is execute_web
    assert get_tool("上网") is None


def test_search_api_posts_the_search_body_when_base_url_is_empty(monkeypatch):
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
    post.assert_called_once()
    assert post.call_args.args[0] == "https://open.bigmodel.cn/api/paas/v4/web_search"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer web-key"
    assert post.call_args.kwargs["json"] == {
        "search_query": "天气",
        "search_engine": "search_pro_quark",
        "search_intent": False,
        "count": 5,
        "content_size": "medium",
        "search_recency_filter": "noLimit",
    }
