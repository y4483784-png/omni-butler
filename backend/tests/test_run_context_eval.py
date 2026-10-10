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


def test_gate_flags_degraded_compaction(monkeypatch):
    """The compaction floor is part of the context gates, not just a report field."""
    from app.eval.gates import GATES

    metrics = [g.metric for g in GATES[("context", "smoke")]]
    assert "arms.full.overall.compaction_rate" in metrics


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
