"""Unified gate evaluator."""

from __future__ import annotations

import pytest

from app.eval.gates import GATES, evaluate_gates
from app.eval.suites import SUITES


@pytest.mark.unit
def test_eval_suites_declare_gates_for_full():
    eval_ids = {s.id for s in SUITES.values() if s.kind == "eval"}
    missing = [sid for sid in eval_ids if (sid, "full") not in GATES]
    assert missing == [], f"full profile missing gates: {missing}"


def _rag_metrics(**overrides):
    metrics = {
        "recall_at_k": 0.99,
        "mrr": 0.93,
        "slices": {
            "hard_negative": {"recall_at_k": 1.0, "mrr": 0.875, "n": 40},
        },
    }
    metrics.update(overrides)
    return {"metrics": metrics}


@pytest.mark.unit
def test_absolute_floor_fails():
    out = evaluate_gates("rag", "full", _rag_metrics(recall_at_k=0.5))
    assert out["ok"] is False
    assert any(m.startswith("metrics.recall_at_k=") for m in out["failed"])


@pytest.mark.unit
def test_hard_negative_slice_fails_when_overall_still_passes():
    """A hard-negative drop that the saturated mean can absorb must still fail."""
    collapsed = _rag_metrics()
    collapsed["metrics"]["slices"] = {
        "hard_negative": {"recall_at_k": 0.80, "mrr": 0.70, "n": 40},
    }
    out = evaluate_gates("rag", "smoke", collapsed)
    assert out["ok"] is False
    assert any("hard_negative.recall_at_k" in m for m in out["failed"])
    assert any("hard_negative.mrr" in m for m in out["failed"])

    healthy = evaluate_gates("rag", "full", _rag_metrics())
    assert healthy["ok"] is True


@pytest.mark.unit
def test_regression_vs_baseline():
    report = {"accuracy": 0.80, "join_success_accuracy": 0.80, "planner_error_rate": 0.0}
    baseline = {"raw": {"accuracy": 0.95}}
    out = evaluate_gates("tabular", "full", report, baseline)
    assert out["ok"] is False
    assert any("dropped" in m for m in out["failed"])


@pytest.mark.unit
def test_missing_metric_fails_by_default():
    """A declared gate whose metric vanished must not read as "passed"."""
    out = evaluate_gates("rag", "full", {"metrics": {"recall_at_k": 0.99}})
    assert out["ok"] is False
    assert any("mrr" in m and "missing" in m for m in out["failed"])
    assert "metrics.mrr" in out["skipped"]


@pytest.mark.unit
def test_allow_missing_can_be_opted_out(monkeypatch):
    import app.eval.gates as gates_mod

    monkeypatch.setitem(
        gates_mod.GATES, ("rag", "full"), [gates_mod.Gate("metrics.mrr", min=0.5, allow_missing=True)]
    )
    out = evaluate_gates("rag", "full", {"metrics": {"recall_at_k": 0.99}})
    assert out["ok"] is True
    assert out["skipped"] == ["metrics.mrr"]


@pytest.mark.unit
def test_infra_and_planner_error_rates_are_gated():
    ok = evaluate_gates(
        "agent",
        "full",
        {
            "core_completion_rate": 0.6,
            "routing": {"tool_exact_match": 0.9},
            "infra_error_rate": 0.0,
        },
    )
    assert ok["ok"] is True
    bad = evaluate_gates(
        "agent",
        "full",
        {
            "core_completion_rate": 0.6,
            "routing": {"tool_exact_match": 0.9},
            "infra_error_rate": 0.4,
        },
    )
    assert bad["ok"] is False

    tab_ok = evaluate_gates(
        "tabular",
        "full",
        {"accuracy": 0.85, "join_success_accuracy": 0.80, "planner_error_rate": 0.0},
    )
    assert tab_ok["ok"] is True
    tab_bad = evaluate_gates(
        "tabular",
        "full",
        {"accuracy": 0.85, "join_success_accuracy": 0.80, "planner_error_rate": 0.1},
    )
    assert tab_bad["ok"] is False
    watered = evaluate_gates(
        "tabular",
        "full",
        {"accuracy": 0.50, "join_success_accuracy": 0.50, "planner_error_rate": 0.0},
    )
    assert watered["ok"] is False
    assert any("accuracy" in m for m in watered["failed"])


@pytest.mark.unit
def test_retired_yz_suite_has_no_gates():
    assert ("yz", "smoke") not in GATES
    assert ("yz", "full") not in GATES


def test_agent_gates_read_core_completion_not_headline():
    metrics = [g.metric for g in GATES[("agent", "full")]]
    assert "core_completion_rate" in metrics
    assert "task_completion_rate" not in metrics
    assert "infra_error_rate" in metrics
