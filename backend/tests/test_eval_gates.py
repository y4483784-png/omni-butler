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


def _healthy_context_report(*, compaction_rate: float) -> dict:
    return {
        "n": 32,
        "invariants": {"violations": 0},
        "arms": {
            "full": {
                "overall": {
                    "pending_retention": 1.0,
                    "compaction_rate": compaction_rate,
                }
            }
        },
    }


@pytest.mark.unit
def test_context_smoke_does_not_hard_gate_compaction_rate():
    """Short-bucket smoke cannot compact; a zero rate must not fail a healthy run.

    Full still requires compaction_rate >= 0.05. That floor is the product check.
    """
    smoke_metrics = [g.metric for g in GATES[("context", "smoke")]]
    full_metrics = [g.metric for g in GATES[("context", "full")]]
    assert "arms.full.overall.compaction_rate" not in smoke_metrics
    assert "arms.full.overall.compaction_rate" in full_metrics

    healthy_smoke = evaluate_gates("context", "smoke", _healthy_context_report(compaction_rate=0.0))
    assert healthy_smoke["ok"] is True
    assert healthy_smoke["failed"] == []

    quiet_full = evaluate_gates("context", "full", _healthy_context_report(compaction_rate=0.0))
    assert quiet_full["ok"] is False
    assert any("compaction_rate" in item and "< min" in item for item in quiet_full["failed"])

    healthy_full = evaluate_gates("context", "full", _healthy_context_report(compaction_rate=0.0651))
    assert healthy_full["ok"] is True


@pytest.mark.unit
def test_context_smoke_still_fails_real_invariant_breaks():
    broken = _healthy_context_report(compaction_rate=0.0)
    broken["invariants"] = {"violations": 2}
    broken["arms"]["full"]["overall"]["pending_retention"] = 0.5
    out = evaluate_gates("context", "smoke", broken)
    assert out["ok"] is False
    assert any("violations" in item for item in out["failed"])
    assert any("pending_retention" in item for item in out["failed"])


@pytest.mark.unit
def test_grounding_smoke_skips_refusal_when_no_unanswerable_rows():
    """n_unanswerable=0 makes refusal_accuracy undefined. That skip is not a fail.

    A reported rate is still scored. Full still fails when the rate is missing,
    because the full set is supposed to contain unanswerable rows.
    """
    empty = evaluate_gates("grounding", "smoke", {"n_unanswerable": 0, "refusal_accuracy": None})
    assert empty["ok"] is True
    assert empty["failed"] == []
    assert empty["skipped"] == ["refusal_accuracy"]
    assert empty["checked"] == []

    present = evaluate_gates(
        "grounding", "smoke", {"n_unanswerable": 4, "refusal_accuracy": 0.75}
    )
    assert present["ok"] is True
    assert present["checked"][0]["metric"] == "refusal_accuracy"

    low = evaluate_gates("grounding", "smoke", {"n_unanswerable": 4, "refusal_accuracy": 0.25})
    assert low["ok"] is False
    assert any("refusal_accuracy" in item and "< min" in item for item in low["failed"])

    dropped = evaluate_gates("grounding", "smoke", {"n_unanswerable": 4})
    assert dropped["ok"] is False
    assert any("refusal_accuracy" in item and "missing" in item for item in dropped["failed"])

    unknown = evaluate_gates("grounding", "smoke", {})
    assert unknown["ok"] is False
    assert any("missing" in item for item in unknown["failed"])

    full_missing = evaluate_gates(
        "grounding",
        "full",
        {"faithfulness_mean": 0.9, "n_unanswerable": 24, "refusal_accuracy": None},
    )
    assert full_missing["ok"] is False
    assert any("refusal_accuracy" in item and "missing" in item for item in full_missing["failed"])

    full_low = evaluate_gates(
        "grounding",
        "full",
        {"faithfulness_mean": 0.9, "n_unanswerable": 24, "refusal_accuracy": 0.5},
    )
    assert full_low["ok"] is False
    assert any("refusal_accuracy=0.5000" in item for item in full_low["failed"])


def test_agent_gates_read_core_completion_not_headline():
    metrics = [g.metric for g in GATES[("agent", "full")]]
    assert "core_completion_rate" in metrics
    assert "task_completion_rate" not in metrics
    assert "infra_error_rate" in metrics
