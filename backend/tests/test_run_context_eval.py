"""Context eval CLI guards (no LLM, no Compose middleware)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = ROOT / "scripts" / "run_context_eval.py"
    spec = importlib.util.spec_from_file_location("omni_run_context_eval", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["omni_run_context_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_update_baseline_and_gate_are_mutually_exclusive(monkeypatch, capsys):
    """Updating the baseline and gating against it in one run proves nothing."""
    runner = _load_runner()
    args = runner.build_parser().parse_args(["--update-baseline", "--gate"])
    result = runner.run(args)
    assert result.exit_code == 2
    assert "cannot update baseline and gate in one run" in (result.report.get("error") or "")


def test_live_requires_explicit_opt_in(monkeypatch):
    """--live spends real API calls; it must not run by accident."""
    runner = _load_runner()
    monkeypatch.delenv("RUN_CTX_EVAL", raising=False)
    args = runner.build_parser().parse_args(["--live"])
    result = runner.run(args)
    assert result.exit_code == 2
    assert "RUN_CTX_EVAL" in (result.report.get("error") or "")


def test_live_infra_is_not_a_zero_fact_recall(monkeypatch):
    from app.core.llm import LLMInfraError
    from app.eval.context_replay import score_live_answer

    def boom(*_a, **_k):
        raise LLMInfraError("RateLimitError: slow down")

    # Imported inside score_live_answer, so patch the defining module.
    monkeypatch.setattr("app.core.llm.complete_text", boom)
    answer, _tokens, fact, _f1, infra, reason = score_live_answer(
        [{"role": "user", "content": "hi"}],
        question="预算是多少",
        gold="80000",
        judge_mode="strict",
    )
    assert infra is True
    assert fact is None
    assert answer == ""
    assert "RateLimit" in reason


def test_gate_report_rejects_split_mismatch():
    from app.eval.context_replay import EvalReport, gate_report

    report = EvalReport(
        n=32,
        split="smoke",
        mode="extractive",
        arms={"full": {"overall": {"pending_retention": 1.0}}},
        invariants={"violations": 0},
    )
    fails = gate_report(report, {"split": "full", "arms": {"full": {"overall": {"fact_recall": 0.9}}}})
    assert any("split mismatch" in item for item in fails)
    # Same split does not add that failure.
    report.split = "full"
    assert not any("split mismatch" in item for item in gate_report(report, {"split": "full", "n": 1}))


def test_gate_flags_degraded_compaction(monkeypatch):
    """Compaction is a full-profile floor. Smoke is a short-bucket probe."""
    from app.eval.gates import GATES

    smoke = [g.metric for g in GATES[("context", "smoke")]]
    full = [g.metric for g in GATES[("context", "full")]]
    assert "arms.full.overall.compaction_rate" not in smoke
    assert "arms.full.overall.compaction_rate" in full
    assert "arms.full.overall.pending_retention" in smoke


@pytest.mark.parametrize("profile", ["smoke", "full"])
def test_context_arm_settings_keep_a_tight_budget(profile):
    """The full arm must lower the token budget, otherwise compaction never runs."""
    from app.eval.context_replay import arm_settings, ARMS

    assert "full" in ARMS
    from app.core.config import settings

    before = int(settings.context_max_tokens)
    with arm_settings("full"):
        assert int(settings.context_max_tokens) <= 4096
    assert int(settings.context_max_tokens) == before
