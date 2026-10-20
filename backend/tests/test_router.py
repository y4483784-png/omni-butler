"""Unit tests for strict LLM tool router (Tier0/Tier1 + schema path)."""

from __future__ import annotations

import pytest

from app.agents import router as router_mod
from app.agents.router import (
    RouterError,
    RouterInfraError,
    classify_intent,
    plan_tools,
    route,
    router_cache_key,
    router_prompt_sha,
)
from app.core.llm import (
    LLMInfraError,
    LLMStructuredError,
    is_llm_infra_exception,
    llm_infra_backoff_seconds,
)


def _decision(result_model, **overrides):
    payload = {
        "reasoning": "制度事实问",
        "needs_kb": False,
        "needs_web": False,
        "needs_calendar": False,
        "needs_sandbox": False,
        "needs_freshness": False,
        "confidence": "high",
    }
    payload.update(overrides)
    return result_model.model_validate(payload)


def test_router_prompt_names_personal_block_and_planning():
    from app.core.prompts import router_system

    text = router_system(has_kb_docs=False)
    assert "输入：明天下午留两小时写报告 | → needs_calendar=true" in text
    assert "输入：帮我看看明天怎么安排 | → needs_calendar=true" in text
    assert "输入：什么是时间规划 | → 全部 false" in text


def test_tier1_certain_chat_exact():
    d = route("你好", [], has_kb_docs=True)
    assert d.needs_kb is False
    assert d.needs_web is False
    assert plan_tools("你好", [], has_kb_docs=True, forced_kb=False)["pending_tools"] == []


def test_tier1_certain_kb_deixis():
    d = route("文档里怎么说全勤奖", [], has_kb_docs=True)
    assert d.needs_kb is True
    assert "kb" in plan_tools("文档里怎么说全勤奖", [], has_kb_docs=True, forced_kb=False)["pending_tools"]


def test_tier0_forced_kb():
    d = route("随便问问", [], has_kb_docs=True, forced_kb=True)
    assert d.needs_kb is True


def test_tier0_document_ids_force_kb():
    d = route("随便问问", [], has_kb_docs=True, document_ids=[1, 2])
    assert d.needs_kb is True


def test_availability_clamp_no_docs(monkeypatch):
    def fake_schema(messages, result_model, model_name):
        return _decision(result_model, reasoning="以为要查库", needs_kb=True, needs_sandbox=True)

    monkeypatch.setattr(router_mod, "complete_json_object", fake_schema)
    d = route("文档里的制度", [], has_kb_docs=False, has_tabular_docs=False)
    assert d.needs_kb is False
    assert d.needs_sandbox is False


def test_llm_path_normal_work_hours(monkeypatch):
    def fake_schema(messages, result_model, model_name):
        return _decision(result_model, reasoning="制度事实问", needs_kb=True)

    monkeypatch.setattr(router_mod, "complete_json_object", fake_schema)
    d = route("正常工作时间", [], has_kb_docs=True)
    assert d.needs_kb is True
    assert classify_intent("正常工作时间", [], has_kb_docs=True) == "rag"


def test_llm_path_chat_lib(monkeypatch):
    def fake_schema(messages, result_model, model_name):
        return _decision(result_model, reasoning="写诗闲聊")

    monkeypatch.setattr(router_mod, "complete_json_object", fake_schema)
    assert classify_intent("写首诗", [], has_kb_docs=True) == "chat"


def test_router_error_after_failures(monkeypatch):
    calls = {"n": 0}

    def boom(messages, result_model, model_name):
        calls["n"] += 1
        raise LLMStructuredError("json 对象校验失败")

    monkeypatch.setattr(router_mod, "complete_json_object", boom)
    monkeypatch.setattr(router_mod.settings, "router_max_attempts", 2)
    with pytest.raises(RouterError):
        route("公司几点上班", [], has_kb_docs=True)
    assert calls["n"] == 1


def test_no_regex_fallback_branch_in_plan_tools():
    # plan_tools must not reference removed heuristic regex modules
    import inspect
    from app.agents import router

    src = inspect.getsource(router.plan_tools)
    assert "_RAG_HINTS" not in src
    assert "sandbox_hint" not in src
    assert "calendar_hint" not in src


def _clear_eval_cache_flags(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_SKIP_ROUTER_REDIS", raising=False)
    monkeypatch.delenv("EVAL_BOOTSTRAPPED", raising=False)


def test_router_cache_key_includes_prompt_fingerprint():
    base = dict(has_kb_docs=True, has_tabular_docs=False, model="glm-test", today="2026-09-19")
    same = router_cache_key("计划文本", prompt_sha="abc123", **base)
    assert same == router_cache_key("计划文本", prompt_sha="abc123", **base)
    assert same != router_cache_key("计划文本", prompt_sha="def456", **base)
    assert same != router_cache_key("另一段计划", prompt_sha="abc123", **base)
    live = router_cache_key("计划文本", **base)
    assert live == router_cache_key("计划文本", prompt_sha=router_prompt_sha(), **base)
    assert len(router_prompt_sha()) == 12


def test_route_redis_key_uses_live_prompt_sha(monkeypatch):
    _clear_eval_cache_flags(monkeypatch)
    seen: dict = {}

    def capture(namespace, payload):
        seen["namespace"] = namespace
        seen["payload"] = payload
        return "route-key"

    monkeypatch.setattr(router_mod, "stable_key", capture)
    monkeypatch.setattr(router_mod, "json_get", lambda _key: None)
    monkeypatch.setattr(router_mod, "json_set", lambda *_a, **_k: True)

    def fake_schema(messages, result_model, model_name):
        return _decision(result_model, reasoning="制度事实问", needs_kb=True)

    monkeypatch.setattr(router_mod, "complete_json_object", fake_schema)
    route("正常工作时间", [], has_kb_docs=True)
    assert seen["namespace"] == "route"
    assert seen["payload"]["prompt_sha"] == router_prompt_sha()
    assert seen["payload"]["v"] == router_mod._ROUTER_CACHE_VERSION


def test_eval_bootstrap_bypasses_router_redis(monkeypatch):
    monkeypatch.setenv("EVAL_SKIP_ROUTER_REDIS", "1")

    def boom_get(*_a, **_k):
        raise AssertionError("eval must not read the router Redis cache")

    def boom_set(*_a, **_k):
        raise AssertionError("eval must not write the router Redis cache")

    monkeypatch.setattr(router_mod, "json_get", boom_get)
    monkeypatch.setattr(router_mod, "json_set", boom_set)
    calls = {"n": 0}

    def fake_schema(messages, result_model, model_name):
        calls["n"] += 1
        return _decision(result_model, reasoning="制度事实问", needs_kb=True)

    monkeypatch.setattr(router_mod, "complete_json_object", fake_schema)
    decision = route("正常工作时间", [], has_kb_docs=True)
    assert calls["n"] == 1
    assert decision.needs_kb is True

    monkeypatch.delenv("EVAL_SKIP_ROUTER_REDIS", raising=False)
    monkeypatch.setenv("EVAL_BOOTSTRAPPED", "1")
    decision = route("正常工作时间", [], has_kb_docs=True)
    assert calls["n"] == 2
    assert decision.needs_kb is True


def test_router_rate_limit_backs_off_then_raises_infra(monkeypatch):
    _clear_eval_cache_flags(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr(router_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(router_mod.settings, "router_max_attempts", 3)
    calls = {"n": 0}

    def boom(messages, result_model, model_name):
        calls["n"] += 1
        raise LLMInfraError("RateLimitError: 429")

    monkeypatch.setattr(router_mod, "complete_json_object", boom)
    with pytest.raises(RouterInfraError) as raised:
        route("公司几点上班", [], has_kb_docs=True)
    assert calls["n"] == 3
    assert sleeps == [llm_infra_backoff_seconds(1), llm_infra_backoff_seconds(2)]
    assert isinstance(raised.value, RouterError)
    assert router_mod.router_failure_is_infra(raised.value)


def test_router_schema_retry_is_not_infra_and_does_not_sleep(monkeypatch):
    _clear_eval_cache_flags(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr(router_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(router_mod.settings, "router_max_attempts", 2)

    calls = {"n": 0}

    def boom(messages, result_model, model_name):
        calls["n"] += 1
        raise LLMStructuredError("json 对象校验失败")

    monkeypatch.setattr(router_mod, "complete_json_object", boom)
    with pytest.raises(RouterError) as raised:
        route("公司几点上班", [], has_kb_docs=True)
    assert calls["n"] == 1
    assert not isinstance(raised.value, RouterInfraError)
    assert sleeps == []
    assert not router_mod.router_failure_is_infra(raised.value)


def test_infra_classification_uses_type_not_message_text():
    class APITimeoutError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self):
            super().__init__("nope")
            self.status_code = 503

    assert is_llm_infra_exception(APITimeoutError("timed out"))
    assert is_llm_infra_exception(APIStatusError())
    assert is_llm_infra_exception(LLMInfraError("RateLimitError: slow down"))
    assert not is_llm_infra_exception(LLMStructuredError("schema failed after timeout text"))
    assert not is_llm_infra_exception(ValueError("RateLimitError mentioned in a parse error"))


def test_queue_order(monkeypatch):
    def fake_schema(messages, result_model, model_name):
        return _decision(
            result_model,
            reasoning="multi",
            needs_kb=True,
            needs_web=True,
            needs_calendar=True,
            needs_sandbox=True,
        )

    monkeypatch.setattr(router_mod, "complete_json_object", fake_schema)
    p = plan_tools(
        "综合问题",
        [],
        has_kb_docs=True,
        forced_kb=False,
        has_tabular_docs=True,
    )
    assert p["pending_tools"] == ["kb", "web", "calendar", "sandbox"]
