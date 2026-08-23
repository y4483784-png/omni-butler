"""Tests for harness gateway (AutoHarness when present, local fallback)."""

from __future__ import annotations

from app.agents.harness import gateway
from app.agents.harness.constitution import (
    autoharness_available,
    get_autoharness_pipeline,
    reload_constitution,
)
from app.agents.harness.types import ToolContext
from app.agents.harness.verify import verify_state
from app.agents.tools.registry import ensure_builtin_tools, get_tool


def test_builtin_tools_registered():
    ensure_builtin_tools()
    for name in ("kb", "web", "calendar", "sandbox"):
        assert get_tool(name) is not None


def test_gateway_denies_unknown_tool():
    reload_constitution()
    ctx = ToolContext(db=None, message="hi", history=[])
    result = gateway.invoke("shell", ctx, {})
    assert result.denied is True
    assert result.deny_reason


def test_gateway_denies_sandbox_network_escape():
    reload_constitution()
    ctx = ToolContext(db=None, message="画图", history=[])
    result = gateway.invoke("sandbox", ctx, {"network": "bridge"})
    assert result.denied is True
    assert "network" in (result.deny_reason or "").lower() or "禁止" in (result.deny_reason or "")


def test_gateway_allows_sandbox_network_none_without_executing_docker(monkeypatch):
    reload_constitution()
    ensure_builtin_tools()
    from app.agents.tools import sandbox_tool

    class _Out:
        ok = True
        steps = ["stub"]
        filename = "t.csv"
        stdout = "ok"
        error = ""
        evidence_text = "===SUMMARY===\nrows=1"
        artifact = None
        summary = {"metrics": [], "missing": [], "asked_ids": []}
        asked_ids = []
        analysis_uncomputable = False
        ir = {}

    monkeypatch.setattr(sandbox_tool, "run_analysis", lambda *a, **k: _Out())
    ctx = ToolContext(db=None, message="汇总均值", history=[], needs_sandbox=True)
    result = gateway.invoke("sandbox", ctx, {"network": "none", "read_only": True})
    assert result.denied is False
    assert result.ok is True
    assert any(e.get("source_type") == "sandbox" for e in result.evidence)


def test_verify_requests_sandbox_when_missing():
    decision = verify_state(
        {
            "message": "请对表格画柱状图并汇总",
            "evidence": [],
            "needs_sandbox": True,
            "iteration": 1,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "kb",
        }
    )
    assert decision.ok is False
    assert decision.next_tool == "sandbox"


def test_verify_ignores_analysis_hint_without_tabular_docs():
    decision = verify_state(
        {
            "message": "分析一下这个问题",
            "evidence": [],
            "needs_sandbox": False,
            "has_tabular_docs": False,
            "iteration": 1,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "kb",
        }
    )
    assert decision.next_tool != "sandbox"


def test_verify_requests_sandbox_when_hint_and_tabular_docs():
    decision = verify_state(
        {
            "message": "分析一下这个问题",
            "evidence": [],
            "needs_sandbox": False,
            "has_tabular_docs": True,
            "iteration": 1,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "kb",
        }
    )
    assert decision.ok is False
    assert decision.next_tool == "sandbox"


def test_verify_accepts_sandbox_round():
    decision = verify_state(
        {
            "message": "画图",
            "evidence": [{"source_type": "sandbox", "content": "===SUMMARY===\nrows=1"}],
            "needs_sandbox": True,
            "iteration": 2,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "sandbox",
        }
    )
    assert decision.ok is True


def test_verify_metric_coverage_replans_once():
    decision = verify_state(
        {
            "message": "中位数",
            "evidence": [
                {
                    "source_type": "sandbox",
                    "content": "===SUMMARY===\n===SUMMARY_JSON===\n"
                    '{"metrics":[{"id":"sum_x","value":1}],"missing":[],"asked_ids":["median_x"]}',
                }
            ],
            "analysis_asked_ids": ["median_x"],
            "analysis_summary": {
                "metrics": [{"id": "sum_x", "value": 1}],
                "missing": [],
                "asked_ids": ["median_x"],
            },
            "needs_sandbox": True,
            "iteration": 2,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "sandbox",
            "sandbox_replanned": False,
        }
    )
    assert decision.ok is False
    assert decision.next_tool == "sandbox"
    assert decision.mark_sandbox_replanned is True


def test_verify_uncomputable_passes():
    decision = verify_state(
        {
            "message": "年终奖",
            "evidence": [
                {
                    "source_type": "sandbox",
                    "content": "===SUMMARY===\n===SUMMARY_JSON===\n"
                    '{"metrics":[],"missing":[{"reason":"列不存在","missing_column":"年终奖"}],"asked_ids":["m"]}',
                }
            ],
            "analysis_uncomputable": True,
            "analysis_asked_ids": ["m"],
            "needs_sandbox": True,
            "iteration": 2,
            "max_iterations": 4,
            "pending_tools": [],
            "next_tool": "sandbox",
        }
    )
    assert decision.ok is True
    assert "uncomputable" in decision.reason


def test_autoharness_pipeline_loads_when_installed():
    reload_constitution()
    if not autoharness_available():
        # Windows/dev without git install — skip soft
        return
    pipe = get_autoharness_pipeline()
    assert pipe is not None
