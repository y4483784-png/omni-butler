"""Unit tests for strict LLM tool router (Tier0/Tier1 + schema path)."""

from __future__ import annotations

import pytest

from app.agents import router as router_mod
from app.agents.router import RouterError, classify_intent, plan_tools, route
from app.core.llm import LLMStructuredError


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
    def fake_schema(messages, *, schema, name, model=None, max_attempts=None):
        return {
            "reasoning": "以为要查库",
            "needs_kb": True,
            "needs_web": False,
            "needs_calendar": False,
            "needs_sandbox": True,
            "needs_freshness": False,
            "confidence": "high",
        }

    monkeypatch.setattr(router_mod, "complete_json_schema", fake_schema)
    d = route("文档里的制度", [], has_kb_docs=False, has_tabular_docs=False)
    assert d.needs_kb is False
    assert d.needs_sandbox is False


def test_llm_path_normal_work_hours(monkeypatch):
    def fake_schema(messages, *, schema, name, model=None, max_attempts=None):
        return {
            "reasoning": "制度事实问",
            "needs_kb": True,
            "needs_web": False,
            "needs_calendar": False,
            "needs_sandbox": False,
            "needs_freshness": False,
            "confidence": "high",
        }

    monkeypatch.setattr(router_mod, "complete_json_schema", fake_schema)
    d = route("正常工作时间", [], has_kb_docs=True)
    assert d.needs_kb is True
    assert classify_intent("正常工作时间", [], has_kb_docs=True) == "rag"


def test_llm_path_chat_lib(monkeypatch):
    def fake_schema(messages, *, schema, name, model=None, max_attempts=None):
        return {
            "reasoning": "写诗闲聊",
            "needs_kb": False,
            "needs_web": False,
            "needs_calendar": False,
            "needs_sandbox": False,
            "needs_freshness": False,
            "confidence": "high",
        }

    monkeypatch.setattr(router_mod, "complete_json_schema", fake_schema)
    assert classify_intent("写首诗", [], has_kb_docs=True) == "chat"


def test_router_error_after_failures(monkeypatch):
    def boom(*args, **kwargs):
        raise LLMStructuredError("schema 约束输出失败")

    monkeypatch.setattr(router_mod, "complete_json_schema", boom)
    monkeypatch.setattr(router_mod.settings, "router_max_attempts", 2)
    with pytest.raises(RouterError):
        route("公司几点上班", [], has_kb_docs=True)


def test_no_regex_fallback_branch_in_plan_tools():
    # plan_tools must not reference removed heuristic regex modules
    import inspect
    from app.agents import router

    src = inspect.getsource(router.plan_tools)
    assert "_RAG_HINTS" not in src
    assert "sandbox_hint" not in src
    assert "calendar_hint" not in src


def test_queue_order(monkeypatch):
    def fake_schema(messages, *, schema, name, model=None, max_attempts=None):
        return {
            "reasoning": "multi",
            "needs_kb": True,
            "needs_web": True,
            "needs_calendar": True,
            "needs_sandbox": True,
            "needs_freshness": False,
            "confidence": "high",
        }

    monkeypatch.setattr(router_mod, "complete_json_schema", fake_schema)
    p = plan_tools(
        "综合问题",
        [],
        has_kb_docs=True,
        forced_kb=False,
        has_tabular_docs=True,
    )
    assert p["pending_tools"] == ["kb", "web", "calendar", "sandbox"]
