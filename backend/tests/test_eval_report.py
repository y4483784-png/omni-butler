"""Report writer contracts: a failing run must not become "the latest result"."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.eval import report as report_mod
from app.eval.report import RunContext, SuiteResult, read_baseline, write_run


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_REPORTS_DIR", str(tmp_path))
    return tmp_path


def _run(suite: str, profile: str, exit_code: int, *, metric: float) -> Path:
    run_dir = report_mod.make_run_dir(suite, profile)
    ctx = RunContext(
        suite=suite,
        profile=profile,
        target="venv",
        argv=["eval.py", "run", suite],
        started_at=datetime.now(timezone.utc),
        run_dir=run_dir,
        key_metrics=["raw.accuracy"],
    )
    return write_run(ctx, SuiteResult(report={"accuracy": metric}, exit_code=exit_code))


def test_failing_run_does_not_become_latest(reports_dir):
    _run("tabular", "smoke", 0, metric=0.9)
    latest = reports_dir / "tabular" / "latest-smoke.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["metrics"]["accuracy"] == 0.9

    _run("tabular", "smoke", 1, metric=0.1)
    # latest still shows the passing run, but the bad run is archived.
    assert json.loads(latest.read_text(encoding="utf-8"))["metrics"]["accuracy"] == 0.9
    runs = sorted((reports_dir / "tabular" / "runs").iterdir())
    assert len(runs) == 2
    assert json.loads((runs[-1] / "report.json").read_text(encoding="utf-8"))["metrics"]["accuracy"] == 0.1


def test_first_run_is_promoted_even_if_failing(reports_dir):
    """With nothing to fall back on, a failing run is still the latest result."""
    _run("tabular", "smoke", 1, metric=0.2)
    latest = reports_dir / "tabular" / "latest-smoke.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["metrics"]["accuracy"] == 0.2


def test_smoke_never_overwrites_passing_full_legacy(reports_dir):
    _run("rag", "full", 0, metric=0.99)
    legacy = reports_dir / "rag" / "latest.json"
    assert json.loads(legacy.read_text(encoding="utf-8"))["meta"]["profile"] == "full"
    _run("rag", "smoke", 0, metric=0.5)
    assert json.loads(legacy.read_text(encoding="utf-8"))["meta"]["profile"] == "full"


def test_failing_full_does_not_replace_passing_full_legacy(reports_dir):
    _run("rag", "full", 0, metric=0.99)
    _run("rag", "full", 1, metric=0.10)
    legacy = reports_dir / "rag" / "latest.json"
    assert json.loads(legacy.read_text(encoding="utf-8"))["metrics"]["accuracy"] == 0.99


def test_baseline_skips_failing_runs(reports_dir):
    _run("rag", "full", 0, metric=0.99)
    _run("rag", "full", 1, metric=0.10)
    baseline = read_baseline("rag", "full")
    assert baseline is not None
    assert baseline["metrics"]["accuracy"] == 0.99
    assert baseline["meta"]["exit_code"] == 0


def test_baseline_falls_back_to_newest_when_nothing_passed(reports_dir):
    _run("rag", "full", 1, metric=0.10)
    baseline = read_baseline("rag", "full")
    assert baseline is not None and baseline["metrics"]["accuracy"] == 0.10


def test_corrupt_legacy_file_is_survivable(reports_dir):
    _run("rag", "full", 0, metric=0.99)
    (reports_dir / "rag" / "latest.json").write_text("{not json", encoding="utf-8")
    # Must not raise; a failing run simply replaces the unreadable legacy file.
    _run("rag", "full", 1, metric=0.10)
    assert (reports_dir / "rag" / "latest.json").is_file()


def test_index_records_profile_latest_and_promotion(reports_dir):
    _run("rag", "full", 1, metric=0.10)
    rows = [
        json.loads(line)
        for line in (reports_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["latest"].endswith("latest-full.json")
    assert rows[-1]["promoted_to_latest"] is True
