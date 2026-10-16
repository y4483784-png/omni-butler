"""Unified per-suite / per-profile gates: absolute floors + regression vs previous run.

A gate reads dotted metric paths from the suite report (``SuiteResult.report``).
``eval.py`` evaluates gates after every run; any failure forces exit code 1, so
CI no longer depends on each suite remembering its own ``--min-*`` flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Gate:
    metric: str  # dotted path inside the suite report
    min: float | None = None  # absolute floor
    max: float | None = None  # absolute ceiling (error-like metrics)
    max_drop: float | None = 0.05  # allowed absolute drop vs previous same-profile run
    note: str = ""
    # A declared gate whose metric is absent means the suite stopped reporting it.
    # Silence must not read as "passed", so missing metrics fail by default.
    allow_missing: bool = False
    # Dotted path of a denominator. When this gate's metric is absent AND that
    # denominator is numeric 0, the rate is undefined for this sample — skip it
    # instead of failing. A present metric is still scored against min/max.
    # If the denominator is missing or non-zero, a missing metric still fails.
    missing_ok_if_zero: str | None = None


# (suite, profile) -> gates. Floors sit ~0.05 under the first calibrated baseline.
GATES: dict[tuple[str, str], list[Gate]] = {
    ("routing", "smoke"): [
        Gate("intent.accuracy", min=0.87),
        Gate("tools.exact_match_accuracy", min=0.87),
    ],
    ("routing", "full"): [
        Gate("intent.accuracy", min=0.87),
        Gate("tools.exact_match_accuracy", min=0.87),
    ],
    # Overall floors stay ~0.05 under the 2026-09-17 keyword baseline
    # (recall 0.9888 / MRR 0.9322). That mean can absorb a hard-negative
    # collapse: the slice is 40/400. Keyword hard_negative on the same run is
    # recall 1.0 / MRR 0.875, so the slice floors sit ~0.05 under those numbers.
    ("rag", "smoke"): [
        Gate("metrics.recall_at_k", min=0.93),
        Gate("metrics.mrr", min=0.88),
        Gate("metrics.slices.hard_negative.recall_at_k", min=0.95),
        Gate("metrics.slices.hard_negative.mrr", min=0.82),
    ],
    ("rag", "full"): [
        Gate("metrics.recall_at_k", min=0.93),
        Gate("metrics.mrr", min=0.88),
        Gate("metrics.slices.hard_negative.recall_at_k", min=0.95),
        Gate("metrics.slices.hard_negative.mrr", min=0.82),
    ],
    # "yz" is retired (see suites.py). Do not keep dead floors that nothing evaluates.
    ("agent", "smoke"): [
        Gate("core_completion_rate", min=0.30),
        Gate("infra_error_rate", max=0.05, max_drop=None),
    ],
    ("agent", "full"): [
        Gate("core_completion_rate", min=0.45),
        Gate("routing.tool_exact_match", min=0.55),
        Gate("infra_error_rate", max=0.05, max_drop=None),
    ],
    # Smoke takes the first 24 rows (8 kb / 8 web / 8 sandbox), all answerable.
    # refusal_accuracy is undefined when n_unanswerable is 0; that is a sample
    # fact, not a dropped metric. The floor still applies once the rate exists.
    ("grounding", "smoke"): [
        Gate(
            "refusal_accuracy",
            min=0.40,
            missing_ok_if_zero="n_unanswerable",
            note="undefined when the smoke prefix has no expect_unanswerable rows",
        ),
    ],
    ("grounding", "full"): [
        Gate("faithfulness_mean", min=0.70),
        Gate("refusal_accuracy", min=0.60, note="expect_unanswerable handled correctly"),
    ],
    ("tabular", "smoke"): [
        Gate("smoke_plan_accuracy", min=0.90),
    ],
    ("tabular", "full"): [
        # README target: full exec ≥ 80%, join_ok ≥ 10/14. Floors used to be 0.30,
        # which let a weak run pass. A miss here is a real fail, not a reason to lower the bar.
        Gate("accuracy", min=0.80),
        Gate("join_success_accuracy", min=10 / 14, note="README join_ok ≥ 10/14"),
        Gate("planner_error_rate", max=0.0, max_drop=None, note="LLM planner failures must be zero"),
    ],
    # Smoke is a short-bucket probe (12/30 turns only) and is not comparable to
    # full. Those histories stay under the compact threshold even when
    # ContextManager is healthy, so compaction_rate=0 is the sample, not a
    # product miss. Invariants and pending_retention stay hard. The compaction
    # floor lives on the full profile, whose 60/120-turn buckets can actually
    # compact (healthy runs land near 0.065).
    ("context", "smoke"): [
        Gate("invariants.violations", max=0.0, max_drop=None),
        Gate("arms.full.overall.pending_retention", min=1.0, max_drop=None),
    ],
    ("context", "full"): [
        Gate("invariants.violations", max=0.0, max_drop=None),
        Gate("arms.full.overall.pending_retention", min=1.0, max_drop=None),
        Gate(
            "arms.full.overall.compaction_rate",
            min=0.05,
            max_drop=None,
            note="compose must actually compact on some cases",
        ),
    ],
    # Clock equality is not a soft score. A wrong wall time stays in the
    # denominator, so the floor is 1.0 with no drop budget. Transport failures
    # are the only rows that leave it, and those are capped on their own.
    ("calendar", "smoke"): [
        Gate("pass_rate", min=1.0, max_drop=None),
        Gate("hard_pass_rate", min=1.0, max_drop=None),
        Gate("clock_exact_rate", min=1.0, max_drop=None),
        Gate("slices.confirm_before_write.pass_rate", min=1.0, max_drop=None),
        Gate("slices.meeting_block.pass_rate", min=1.0, max_drop=None),
        Gate("slices.id_mutate.pass_rate", min=1.0, max_drop=None),
        Gate("slices.free_busy.pass_rate", min=1.0, max_drop=None),
        Gate("slices.notes_description.pass_rate", min=1.0, max_drop=None),
        Gate("slices.wall_clock.pass_rate", min=1.0, max_drop=None),
        Gate("slices.error_paths.pass_rate", min=1.0, max_drop=None),
        Gate("infra_error_rate", max=0.05, max_drop=None),
    ],
    ("calendar", "full"): [
        Gate("pass_rate", min=1.0, max_drop=None),
        Gate("hard_pass_rate", min=1.0, max_drop=None),
        Gate("clock_exact_rate", min=1.0, max_drop=None),
        Gate("slices.confirm_before_write.pass_rate", min=1.0, max_drop=None),
        Gate("slices.meeting_block.pass_rate", min=1.0, max_drop=None),
        Gate("slices.id_mutate.pass_rate", min=1.0, max_drop=None),
        Gate("slices.free_busy.pass_rate", min=1.0, max_drop=None),
        Gate("slices.notes_description.pass_rate", min=1.0, max_drop=None),
        Gate("slices.wall_clock.pass_rate", min=1.0, max_drop=None),
        Gate("slices.error_paths.pass_rate", min=1.0, max_drop=None),
        Gate("infra_error_rate", max=0.05, max_drop=None),
    ],
}


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _metrics_root(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("metrics") if isinstance(report.get("metrics"), dict) and "n" not in report else report


def _metric_value(report: dict[str, Any], path: str) -> float | None:
    cur = _num(_dig(report, path))
    if cur is None:
        cur = _num(_dig(_metrics_root(report), path))
    return cur


def _missing_metric_ok(gate: Gate, report: dict[str, Any]) -> bool:
    """True when an absent metric must not fail the run.

    ``allow_missing`` is the blanket opt-out. ``missing_ok_if_zero`` is narrower:
    the rate cannot be computed because its denominator is 0. Any other absence
    still fails, including a zero-denominator field that the report forgot.
    """
    if gate.allow_missing:
        return True
    if not gate.missing_ok_if_zero:
        return False
    return _metric_value(report, gate.missing_ok_if_zero) == 0


def evaluate_gates(
    suite: str,
    profile: str,
    report: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{"ok", "failed": [...], "checked": [...]}``.

    ``baseline`` is the previous same-profile ``report.json`` payload (its ``raw``
    section is the suite report). A gate whose metric is missing FAILS unless the
    gate explicitly sets ``allow_missing``, or ``missing_ok_if_zero`` names a
    denominator that is 0 (the rate is undefined for this sample). A silently
    dropped metric from a refactor must not read as "passed".
    """
    gates = GATES.get((suite, profile), [])
    base_raw = (baseline or {}).get("raw") if baseline else None
    failed: list[str] = []
    checked: list[dict[str, Any]] = []
    skipped: list[str] = []
    for g in gates:
        cur = _metric_value(report, g.metric)
        if cur is None:
            if _missing_metric_ok(g, report):
                skipped.append(g.metric)
                continue
            failed.append(f"{g.metric} missing from report")
            skipped.append(g.metric)
            continue
        row: dict[str, Any] = {"metric": g.metric, "value": round(cur, 4)}
        if g.min is not None:
            row["min"] = g.min
            if cur + 1e-9 < g.min:
                failed.append(f"{g.metric}={cur:.4f} < min {g.min}")
        if g.max is not None:
            row["max"] = g.max
            if cur - 1e-9 > g.max:
                failed.append(f"{g.metric}={cur:.4f} > max {g.max}")
        if g.max_drop is not None and isinstance(base_raw, dict):
            prev = _num(_dig(base_raw, g.metric))
            if prev is None:
                prev = _num(_dig(_metrics_root(base_raw), g.metric))
            if prev is not None:
                row["baseline"] = round(prev, 4)
                if cur + 1e-9 < prev - g.max_drop:
                    failed.append(f"{g.metric}={cur:.4f} dropped > {g.max_drop} vs baseline {prev:.4f}")
        checked.append(row)
    return {"ok": not failed, "failed": failed, "checked": checked, "skipped": skipped}
