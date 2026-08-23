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
from app.agents.router import RouterDecision, RouterError
from app.agents.tools.registry import ensure_builtin_tools
from app.core.config import settings
from app.eval import tool_routing
from app.eval.tool_routing import load_cases, run_tool_routing_eval


@pytest.fixture(autouse=True)
def _tools_ready():
    reload_constitution()
    ensure_builtin_tools()


_MIN_INTENT_ACC = 0.75
_MIN_TOOL_EXACT = 0.70


@pytest.mark.skipif(
    os.environ.get("RUN_ROUTER_EVAL") != "1" or not settings.llm_api_key,
    reason="set RUN_ROUTER_EVAL=1 (and LLM_API_KEY) to run the 500-case LLM router eval",
)
def test_routing_eval_meets_thresholds():
    report = run_tool_routing_eval(load_cases())
    assert report.n >= 500, f"expected >=500 golden cases, got {report.n}"
    assert report.intent_accuracy >= _MIN_INTENT_ACC, report.format_text()
    assert report.tools["exact_match_accuracy"] >= _MIN_TOOL_EXACT, report.format_text()


def test_routing_dataset_loads():
    cases = load_cases()
    assert len(cases) >= 500
    assert all("fast_path" not in c.gold for c in cases), "fast path metric is retired"
    d = run_tool_routing_eval(
        cases[:0],  # empty → structural smoke without LLM
    ).to_dict()
    assert "intent" in d and "tools" in d


def _oracle_route(tmp_path, monkeypatch, cases):
    """Bridge the eval pipeline to a mocked router (offline, no API key)."""
    by_message = {c.message: set(c.gold.get("tools") or []) for c in cases}

    def fake_route(message, history=None, **kwargs):
        tools = by_message.get(message, set())
        if kwargs.get("forced_kb") or kwargs.get("document_ids"):
            tools = tools | {"kb"}
        if not kwargs.get("has_kb_docs"):
            tools = tools - {"kb"}
        if not kwargs.get("has_tabular_docs"):
            tools = tools - {"sandbox"}
        return RouterDecision(
            reasoning="mock",
            needs_kb="kb" in tools,
            needs_web="web" in tools,
            needs_calendar="calendar" in tools,
            needs_sandbox="sandbox" in tools,
        )

    monkeypatch.setattr(tool_routing, "route", fake_route)
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

    monkeypatch.setattr(tool_routing, "route", boom)
    second = run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")
    assert second.cache_hits == len(cases)
    assert second.cache_misses == 0


def test_router_error_recorded_as_failure(tmp_path, monkeypatch):
    cases = [c for c in load_cases() if c.gold.get("tools")][:5]

    def failing(*_a, **_k):
        raise RouterError("工具规划失败：schema 校验失败")

    monkeypatch.setattr(tool_routing, "route", failing)
    report = run_tool_routing_eval(cases, cache_path=tmp_path / "router_cache.json")

    assert len(report.failures) == len(cases)
    assert all("工具规划失败" in (f.error or "") for f in report.failures)


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
