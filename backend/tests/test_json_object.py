from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.agents.harness.critique import critique_draft
from app.agents.router import RouterDecision, RouterReply
from app.core.config import settings
from app.core.llm import (
    LLMInfraError,
    LLMStructuredError,
    complete_json_object,
    llm_stats_reset,
    llm_stats_snapshot,
)
from app.core.prompts import ROUTER_SYSTEM, router_system

_ROUTER_EXAMPLE = (
    '{"reasoning":"","needs_kb":false,"needs_web":false,'
    '"needs_calendar":false,"needs_sandbox":false,"needs_freshness":false,"confidence":"high"}'
)
_CRITIQUE_EXAMPLE = '{"grounded":true,"addresses_question":true,"unsupported":[]}'
_VALID = {
    "reasoning": "查日程",
    "needs_kb": False,
    "needs_web": False,
    "needs_calendar": True,
    "needs_sandbox": False,
    "needs_freshness": False,
    "confidence": "low",
}


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]


class _RateLimitError(Exception):
    status_code = 429


class _BadRequestError(Exception):
    status_code = 400


def _install(monkeypatch, outputs):
    from app.core import llm as llm_mod

    calls: list[dict] = []
    sleeps: list[float] = []
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
    monkeypatch.setattr(llm_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    return calls, sleeps


def _assert_json_object_request(kwargs: dict) -> None:
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["stream"] is False
    assert kwargs["temperature"] == 0.0
    assert kwargs["timeout"] == min(settings.llm_timeout, 60.0)
    blob = json.dumps(kwargs)
    assert "json_schema" not in blob
    assert "strict" not in blob


def _valid_body() -> str:
    return json.dumps(_VALID, ensure_ascii=False)


def test_valid_json_returns_model_and_requests_json_object(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_Resp(_valid_body())])
    decision = complete_json_object(
        [{"role": "user", "content": "hi"}],
        RouterDecision,
        "router-test",
    )
    assert isinstance(decision, RouterDecision)
    assert decision.needs_calendar is True
    assert decision.confidence == "low"
    assert decision.reasoning == "查日程"
    assert len(calls) == 1
    assert calls[0]["model"] == "router-test"
    _assert_json_object_request(calls[0])
    assert sleeps == []


def test_empty_content_retries_once_then_raises(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_Resp(""), _Resp("")])
    with pytest.raises(LLMStructuredError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2
    assert sleeps == []
    _assert_json_object_request(calls[0])
    _assert_json_object_request(calls[1])


def test_whitespace_content_retries_once_then_raises(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_Resp("  \n\t"), _Resp("   ")])
    with pytest.raises(LLMStructuredError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2
    assert sleeps == []


def test_invalid_json_then_valid_returns_model(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_Resp("not-json"), _Resp(_valid_body())])
    decision = complete_json_object(
        [{"role": "user", "content": "hi"}],
        RouterDecision,
        "router-test",
    )
    assert decision.needs_calendar is True
    assert len(calls) == 2
    assert sleeps == []
    user = calls[1]["messages"][-1]
    assert user["role"] == "user"
    assert "json" in user["content"]
    assert _ROUTER_EXAMPLE in user["content"].splitlines()
    assert calls[1]["messages"][-2] == {"role": "assistant", "content": "not-json"}
    _assert_json_object_request(calls[1])


def test_both_responses_invalid_json_raises(monkeypatch):
    wrapped = (
        '说明 {"reasoning":"x","needs_kb":false,"needs_web":false,'
        '"needs_calendar":true,"needs_sandbox":false,"needs_freshness":false,"confidence":"high"}'
    )
    calls, sleeps = _install(monkeypatch, [_Resp(wrapped), _Resp(wrapped)])
    with pytest.raises(LLMStructuredError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2
    assert sleeps == []


def test_length_finish_is_failure_even_with_content(monkeypatch):
    body = _valid_body()
    calls, sleeps = _install(
        monkeypatch,
        [_Resp(body, finish_reason="length"), _Resp(body, finish_reason="length")],
    )
    with pytest.raises(LLMStructuredError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2
    assert sleeps == []


def test_rate_limit_is_infra_on_first_call_without_sleep(monkeypatch):
    llm_stats_reset()
    calls, sleeps = _install(monkeypatch, [_RateLimitError("slow down")])
    with pytest.raises(LLMInfraError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 1
    assert sleeps == []
    assert llm_stats_snapshot()["infra_errors"] == 1


def test_bad_request_retries_once_then_stays_structured(monkeypatch):
    calls, sleeps = _install(
        monkeypatch,
        [_BadRequestError("invalid request"), _BadRequestError("invalid request")],
    )
    with pytest.raises(LLMStructuredError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2
    assert sleeps == []
    user = calls[1]["messages"][-1]["content"]
    assert "json" in user
    assert _ROUTER_EXAMPLE in user.splitlines()
    _assert_json_object_request(calls[0])
    _assert_json_object_request(calls[1])


def test_second_call_infra_is_not_hidden_as_structured(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_Resp("not-json"), _RateLimitError("slow down")])
    with pytest.raises(LLMInfraError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2
    assert sleeps == []


def test_missing_api_key_raises_structured_without_a_call(monkeypatch):
    from app.core import llm as llm_mod

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "")

    def boom():
        raise AssertionError("client must not be built")

    monkeypatch.setattr(llm_mod, "_get_sync_client", boom)
    with pytest.raises(LLMStructuredError, match="未配置 LLM_API_KEY，无法进行工具规划"):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")


def test_critique_draft_rate_limit_propagates(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_RateLimitError("slow down")])
    with pytest.raises(LLMInfraError):
        critique_draft(
            question="标准？",
            draft="住宿上限 500 元。",
            evidence=[{"source_type": "kb", "index": 1, "content": "500 元"}],
        )
    assert len(calls) == 1
    assert sleeps == []
    by_role = {m["role"]: m["content"] for m in calls[0]["messages"]}
    assert "json" in by_role["user"]
    assert _CRITIQUE_EXAMPLE in by_role["system"]


def test_router_prompt_has_json_word_and_one_line_example():
    assert "json" in ROUTER_SYSTEM
    assert "符合 schema" not in ROUTER_SYSTEM
    text = router_system(has_kb_docs=False)
    assert _ROUTER_EXAMPLE in text
    assert "输入：明天下午留两小时写报告 | → needs_calendar=true" in text


def test_omitted_router_fields_fail_validation(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_Resp("{}"), _Resp(_valid_body())])
    decision = complete_json_object(
        [{"role": "user", "content": "hi"}],
        RouterReply,
        "router-test",
    )
    assert isinstance(decision, RouterReply)
    assert decision.needs_calendar is True
    assert decision.confidence == "low"
    assert len(calls) == 2
    assert sleeps == []
    retry = calls[1]["messages"][-1]["content"]
    assert "json" in retry
    assert _ROUTER_EXAMPLE in retry.splitlines()


def test_non_dict_json_is_not_accepted(monkeypatch):
    calls, _sleeps = _install(monkeypatch, [_Resp("[]"), _Resp("[]")])
    with pytest.raises(LLMStructuredError):
        complete_json_object([{"role": "user", "content": "hi"}], RouterDecision, "router-test")
    assert len(calls) == 2


class _Box(BaseModel):
    name: str
    ok: bool = False


def test_example_uses_defaults_for_a_small_model(monkeypatch):
    calls, _sleeps = _install(monkeypatch, [_Resp("nope"), _Resp('{"name":"a","ok":true}')])
    result = complete_json_object([{"role": "user", "content": "hi"}], _Box, "box")
    assert result.name == "a"
    assert result.ok is True
    example = calls[1]["messages"][-1]["content"].splitlines()[-1]
    assert example == '{"name":"","ok":false}'
    assert "\n" not in example
