"""
Office routing eval — golden JSONL + gateway/verify contracts.

Full routing metrics hit the real LLM 500 times, so they only run when
explicitly requested via RUN_ROUTER_EVAL=1 (plus LLM_API_KEY). Regular pytest
runs stay offline: pipeline behaviour is covered by mocked-router tests below.
"""

from __future__ import annotations

import os

import pytest

from app.agents.harness import gateway
from app.agents.harness.constitution import reload_constitution
from app.agents.harness.types import ToolContext
from app.agents.harness.verify import verify_state
from app.agents.router import RouterError, RouterInfraError
from app.agents.tools.registry import ensure_builtin_tools
from app.core.config import settings
from app.eval import tool_routing
from app.eval.tool_routing import load_cases, run_tool_routing_eval


@pytest.fixture(autouse=True)
def _tools_ready():
    reload_constitution()
    ensure_builtin_tools()


_MIN_INTENT_ACC = 0.87
_MIN_TOOL_EXACT = 0.87


@pytest.mark.skipif(
    os.environ.get("RUN_ROUTER_EVAL") != "1" or not settings.llm_api_key,
    reason="set RUN_ROUTER_EVAL=1 (and LLM_API_KEY) to run the 500-case LLM router eval",
)
def test_routing_eval_meets_thresholds():
    cases = load_cases()
    report = run_tool_routing_eval(cases)
    assert report.n == len(cases)
    assert report.intent_accuracy >= _MIN_INTENT_ACC, report.format_text()
    assert report.tools["exact_match_accuracy"] >= _MIN_TOOL_EXACT, report.format_text()


def test_routing_dataset_loads(tmp_path):
    cases = load_cases()
    assert cases
    assert all("fast_path" not in c.gold for c in cases), "fast path metric is retired"
    d = run_tool_routing_eval(
        cases[:0],  # empty → structural smoke without LLM
        cache_path=tmp_path / "router_cache.json",
    ).to_dict()
    assert "intent" in d and "tools" in d


def _oracle_route(tmp_path, monkeypatch, cases):
    """Bridge the eval pipeline to a mocked ``plan_tools`` (offline, no API key)."""
    by_message = {c.message: set(c.gold.get("tools") or []) for c in cases}

    def fake_plan_tools(message, history=None, **kwargs):
        tools = by_message.get(message, set())
        if kwargs.get("forced_kb") or kwargs.get("document_ids"):
            tools = tools | {"kb"}
        if not kwargs.get("has_kb_docs"):
            tools = tools - {"kb"}
        if not kwargs.get("has_tabular_docs"):
            tools = tools - {"sandbox"}
        pending = [t for t in ("kb", "web", "calendar", "sandbox") if t in tools]
        return {
            "pending_tools": pending,
            "needs_kb": "kb" in tools,
            "needs_web": "web" in tools,
            "needs_calendar": "calendar" in tools,
            "needs_sandbox": "sandbox" in tools,
            "needs_freshness": False,
            "confidence": "high",
            "reasoning": "mock",
        }

    monkeypatch.setattr(tool_routing, "plan_tools", fake_plan_tools)
    return run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")


def test_routing_pipeline_with_mocked_router(tmp_path, monkeypatch):
    cases = [c for c in load_cases() if not c.context.get("pending_calendar")][:60]
    report = _oracle_route(tmp_path, monkeypatch, cases)

    assert report.n == len(cases)
    assert report.cache_misses == len(cases)
    assert report.tools["exact_match_accuracy"] == pytest.approx(1.0)
    assert report.intent_accuracy == pytest.approx(1.0)
    assert not report.failures


def test_routing_cache_avoids_second_call(tmp_path, monkeypatch):
    cases = load_cases()[:10]
    _oracle_route(tmp_path, monkeypatch, cases)

    def boom(*_a, **_k):
        raise AssertionError("router must not be called on cache hit")

    monkeypatch.setattr(tool_routing, "plan_tools", boom)
    second = run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")
    assert second.cache_hits == len(cases)
    assert second.cache_misses == 0


def test_router_error_recorded_as_failure(tmp_path, monkeypatch):
    cases = [c for c in load_cases() if c.gold.get("tools")][:5]

    def failing(*_a, **_k):
        raise RouterError("工具规划失败：schema 校验失败")

    monkeypatch.setattr(tool_routing, "plan_tools", failing)
    report = run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")

    assert len(report.failures) == len(cases)
    assert all("工具规划失败" in (f.error or "") for f in report.failures)
    assert report.n_infra_error == 0
    assert report.n_scored == len(cases)
    assert report.intent_accuracy == pytest.approx(0.0)


def test_routing_infra_excluded_from_accuracy_denominator(tmp_path, monkeypatch):
    """Rate-limit rows leave the accuracy denominator; schema misses stay in it."""
    pool = [c for c in load_cases() if not c.context.get("pending_calendar")]
    with_tools = [c for c in pool if c.gold.get("tools")]
    chat = [c for c in pool if not c.gold.get("tools")]
    # Schema miss must have a non-empty gold set; empty-gold chat would still
    # exact-match the router's empty prediction and hide the denominator bug.
    cases = [with_tools[0], with_tools[1], chat[0]]
    infra_id = cases[0].id
    schema_id = cases[1].id
    by_message = {c.message: set(c.gold.get("tools") or []) for c in cases}

    def fake_plan_tools(message, history=None, **kwargs):
        case = next(c for c in cases if c.message == message)
        if case.id == infra_id:
            raise RouterInfraError("工具规划失败：APITimeoutError: timed out")
        if case.id == schema_id:
            raise RouterError("工具规划失败：schema 校验失败")
        tools = by_message.get(message, set())
        if kwargs.get("forced_kb") or kwargs.get("document_ids"):
            tools = tools | {"kb"}
        if not kwargs.get("has_kb_docs"):
            tools = tools - {"kb"}
        if not kwargs.get("has_tabular_docs"):
            tools = tools - {"sandbox"}
        pending = [t for t in ("kb", "web", "calendar", "sandbox") if t in tools]
        return {
            "pending_tools": pending,
            "needs_kb": "kb" in tools,
            "needs_web": "web" in tools,
            "needs_calendar": "calendar" in tools,
            "needs_sandbox": "sandbox" in tools,
            "needs_freshness": False,
            "confidence": "high",
            "reasoning": "mock",
        }

    monkeypatch.setattr(tool_routing, "plan_tools", fake_plan_tools)
    report = run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")
    # 1 correct + 1 schema miss scored; the timeout is not a miss.
    assert report.n == 3
    assert report.n_scored == 2
    assert report.n_error == 1
    assert report.n_infra_error == 1
    assert report.infra_error_rate == pytest.approx(1 / 3)
    assert report.intent_accuracy == pytest.approx(0.5)
    assert report.tools["exact_match_accuracy"] == pytest.approx(0.5)
    assert any(f.infra_error for f in report.failures)


def test_routing_all_infra_does_not_score_perfect(tmp_path, monkeypatch):
    cases = [c for c in load_cases() if c.gold.get("tools")][:2]

    def failing(*_a, **_k):
        raise RouterInfraError("工具规划失败：RateLimitError: 429")

    monkeypatch.setattr(tool_routing, "plan_tools", failing)
    report = run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")
    assert report.n_scored == 0
    assert report.infra_error_rate == pytest.approx(1.0)
    assert report.intent_accuracy == pytest.approx(0.0)
    assert report.tools["exact_match_accuracy"] == pytest.approx(0.0)


# Contract tests (gateway / verify) — not routing metrics, kept separate
GATEWAY_CASES = [
    {
        "id": "gateway_deny_sandbox_bridge",
        "message": "分析数据",
        "gateway_tool": "sandbox",
        "gateway_args": {"network": "bridge"},
        "expect_denied": True,
    },
    {
        "id": "gateway_deny_unknown",
        "message": "随便",
        "gateway_tool": "rm",
        "gateway_args": {},
        "expect_denied": True,
    },
]

VERIFY_CASES = [
    {
        "id": "sandbox_missing_evidence_verify",
        "verify_state": {
            "message": "这个表统计一下并画图",
            "evidence": [],
            "needs_sandbox": True,
            "iteration": 1,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "kb",
        },
        "expect_verify_next": "sandbox",
    },
]


@pytest.mark.parametrize("case", GATEWAY_CASES, ids=[c["id"] for c in GATEWAY_CASES])
def test_gateway_contract(case):
    ctx = ToolContext(db=None, message=case["message"], history=[], needs_sandbox=True)
    result = gateway.invoke(case["gateway_tool"], ctx, case.get("gateway_args") or {})
    assert result.denied is case.get("expect_denied", False)


@pytest.mark.parametrize("case", VERIFY_CASES, ids=[c["id"] for c in VERIFY_CASES])
def test_verify_contract(case):
    decision = verify_state(case["verify_state"])
    assert decision.ok is False
    assert decision.next_tool == case["expect_verify_next"]
