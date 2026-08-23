"""Office tabular eval: scoring, plan matching, report aggregation."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from app.services.analysis_ir import numbers_equivalent
from app.services.data_analysis import AnalysisSpec, refuse_multitable_without_join


def case_filenames(case: dict) -> list[str]:
    extra = case.get("fixtures")
    if isinstance(extra, list) and extra:
        return [str(x) for x in extra]
    name = str(case.get("fixture") or "")
    return [name] if name else []


_TAG_TO_CONCEPTS: dict[str, str] = {
    "sum": "aggregation",
    "count": "aggregation",
    "median": "aggregation",
    "max": "aggregation",
    "min": "aggregation",
    "mean": "aggregation",
    "count_distinct": "aggregation",
    "group": "aggregation",
    "filter": "filter",
    "date": "filter",
    "rate": "derive",
    "dirty": "dirty_parse",
    "join_ok": "join",
    "join_refuse": "refusal",
    "missing": "refusal",
    "followup": "followup",
}


def concepts_from_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        c = _TAG_TO_CONCEPTS.get(tag)
        if c and c not in out:
            out.append(c)
    return out or ["aggregation"]


def compare_numeric_soft(actual: float, expected: float, *, rel_tol: float = 0.01) -> bool:
    if numbers_equivalent(actual, expected):
        return True
    if expected == 0:
        return abs(actual) <= rel_tol
    return abs(actual - expected) / abs(expected) <= rel_tol


def soft_exec_matches(case: dict, payload: dict, *, tol: float) -> bool:
    gold = case.get("gold")
    if gold is None:
        return False
    tags = set(case.get("tags") or [])
    if "dirty" not in tags and "rate" not in tags:
        return False
    for m in payload.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        try:
            val = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if compare_numeric_soft(val, float(gold), rel_tol=max(tol, 0.01)):
            return True
        if case.get("accept_percent") and compare_numeric_soft(val, float(gold) * 100, rel_tol=0.05):
            return True
    return False


def gold_plan(case: dict) -> dict[str, Any]:
    g = case.get("gold_plan")
    if isinstance(g, dict):
        return g
    # legacy cases
    tags = list(case.get("tags") or [])
    op = "count"
    if "median" in tags:
        op = "median"
    elif "sum" in tags:
        op = "sum"
    elif "rate" in tags:
        op = "rate"
    return {
        "operation": op,
        "filters": [],
        "derive": bool(case.get("require_derive")),
        "join_key": "",
        "join_left": "",
        "join_right": "",
        "uncomputable": bool(case.get("expect_uncomputable")),
    }


def _filters_match(spec_filters: list, gold_filters: list) -> bool:
    if len(spec_filters or []) != len(gold_filters or []):
        return False
    for sf, gf in zip(spec_filters or [], gold_filters or []):
        if not isinstance(sf, dict) or not isinstance(gf, dict):
            return False
        if str(sf.get("column")) != str(gf.get("column")):
            return False
        if str(sf.get("op")) != str(gf.get("op")):
            return False
        try:
            if float(sf.get("value")) != float(gf.get("value")):
                return False
        except (TypeError, ValueError):
            svs, gvs = str(sf.get("value")), str(gf.get("value"))
            if svs != gvs and not (svs.startswith(gvs) or gvs.startswith(svs)):
                return False
    return True


def plan_matches(spec: AnalysisSpec, case: dict) -> bool:
    gp = gold_plan(case)
    if gp.get("uncomputable") or case.get("expect_uncomputable"):
        if "join_refuse" in (case.get("tags") or []):
            payload = refuse_multitable_without_join(case["query"], case_filenames(case)) or {}
            return bool(payload.get("missing"))
        if not spec.uncomputable:
            return False
        tokens = list(case.get("missing_tokens") or [])
        blob = json.dumps(spec.uncomputable, ensure_ascii=False)
        return all(t in blob for t in tokens) if tokens else bool(spec.uncomputable)

    op_ok = str(spec.operation) == str(gp.get("operation") or spec.operation)
    if gp.get("operation") == "rate":
        op_ok = bool(spec.derive) or spec.operation in ("none", "avg")
        if any(
            isinstance(m, dict) and str(m.get("kind")) == "mean"
            for m in (spec.metrics or [])
        ):
            op_ok = True
    if gp.get("operation") == "max" and spec.operation in ("max", "none"):
        op_ok = True
    if gp.get("operation") == "min" and spec.operation in ("min", "none"):
        op_ok = True
    if gp.get("operation") == "avg" and spec.operation in ("avg", "mean", "none", "profile"):
        op_ok = True
    if not op_ok:
        return False

    if gp.get("join_key"):
        if str(spec.join_key) != str(gp.get("join_key")):
            return False
        if gp.get("join_left") and str(spec.join_left) != str(gp.get("join_left")):
            return False
        if gp.get("join_right") and str(spec.join_right) != str(gp.get("join_right")):
            return False
        if "join_ok" in (case.get("tags") or []):
            if str(spec.operation) != str(gp.get("operation")):
                if gp.get("operation") == "avg" and spec.operation in ("avg", "mean", "none", "profile"):
                    pass
                elif gp.get("operation") == "count_distinct" and spec.operation == "count":
                    pass
                else:
                    return False
            return True
    elif "join_ok" in (case.get("tags") or []):
        return False

    if gp.get("filters") is not None and not _filters_match(spec.filters, gp.get("filters") or []):
        return False

    if gp.get("derive") and not spec.derive:
        if gp.get("operation") != "rate" or not any(
            isinstance(m, dict) and str(m.get("kind")) == "mean" for m in (spec.metrics or [])
        ):
            return False
    if case.get("require_derive") and not spec.derive:
        if gp.get("operation") != "rate" or not any(
            isinstance(m, dict) and str(m.get("kind")) == "mean" for m in (spec.metrics or [])
        ):
            return False

    if "median" in (case.get("tags") or []) and spec.operation != "median":
        return False
    if "filter" in (case.get("tags") or []) and "count" in (case.get("tags") or []):
        if spec.operation != "count" or not spec.filters:
            if not (gp.get("filters") and spec.operation == "count"):
                return False

    return True


def metric_hit(payload: dict, gold: float, *, tol: float, accept_percent: bool) -> bool:
    for m in payload.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        try:
            val = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if abs(val - gold) <= tol or numbers_equivalent(val, gold, tol=tol):
            return True
        if accept_percent and (
            numbers_equivalent(val, gold * 100, tol=max(tol, 0.05))
            or numbers_equivalent(val * 100, gold, tol=max(tol, 0.05))
        ):
            return True
    return False


def missing_has_tokens(payload: dict, tokens: list[str]) -> bool:
    blob = json.dumps(payload.get("missing") or [], ensure_ascii=False)
    return bool(tokens) and all(t in blob for t in tokens)


def exec_matches(
    *,
    case: dict,
    payload: dict,
    spec: AnalysisSpec,
    sandbox_error: str = "",
) -> tuple[bool, str]:
    """Return (ok, failure_kind)."""
    if sandbox_error:
        return False, "sandbox_error"

    if case.get("expect_uncomputable"):
        if "join_refuse" in (case.get("tags") or []):
            payload = refuse_multitable_without_join(case["query"], case_filenames(case)) or {}
            ok = missing_has_tokens(payload, list(case.get("missing_tokens") or ["join"]))
            return ok, "" if ok else "join_skipped"
        ok = bool(spec.uncomputable) and missing_has_tokens(
            {"missing": spec.uncomputable},
            list(case.get("missing_tokens") or []),
        )
        return ok, "" if ok else "silent_substitute"

    gold = case.get("gold")
    if gold is None:
        return False, "wrong_number"
    tol = float(case.get("tolerance") or 0)
    ok = metric_hit(
        payload,
        float(gold),
        tol=tol,
        accept_percent=bool(case.get("accept_percent")),
    )
    if not ok:
        if "join_ok" in (case.get("tags") or []) and payload.get("missing"):
            return False, "join_empty"
        if "join_ok" in (case.get("tags") or []):
            return False, "join_wrong_measure"
        return False, "wrong_number"
    return True, ""


def aggregate_report(results: list[dict], *, mode: str) -> dict[str, Any]:
    total = len(results)
    exec_ok = sum(1 for r in results if r.get("exec_ok"))
    plan_ok = sum(1 for r in results if r.get("plan_ok"))
    both_ok = sum(1 for r in results if r.get("exec_ok") and r.get("plan_ok"))

    by_tag: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "exec_ok": 0, "plan_ok": 0})
    by_diff: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "exec_ok": 0, "plan_ok": 0})
    failures: dict[str, int] = defaultdict(int)

    refuse_expected = [r for r in results if r.get("expect_uncomputable")]
    refuse_hit = sum(1 for r in refuse_expected if r.get("exec_ok"))
    false_refuse = sum(1 for r in results if not r.get("expect_uncomputable") and r.get("failure_kind") == "silent_substitute")

    for r in results:
        for tag in r.get("tags") or []:
            by_tag[tag]["total"] += 1
            if r.get("exec_ok"):
                by_tag[tag]["exec_ok"] += 1
            if r.get("plan_ok"):
                by_tag[tag]["plan_ok"] += 1
        diff = r.get("difficulty") or "easy"
        by_diff[diff]["total"] += 1
        if r.get("exec_ok"):
            by_diff[diff]["exec_ok"] += 1
        if r.get("plan_ok"):
            by_diff[diff]["plan_ok"] += 1
        fk = r.get("failure_kind") or ""
        if fk and not r.get("exec_ok"):
            failures[fk] += 1
        if r.get("soft_exec_ok"):
            soft_exec_ok += 1

    smoke = [r for r in results if r.get("split") == "smoke"]
    full = results

    def _acc(rows: list[dict], key: str) -> float:
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get(key)) / len(rows)

    join_ok_rows = [r for r in results if "join_ok" in (r.get("tags") or [])]
    join_ref_rows = [r for r in results if "join_refuse" in (r.get("tags") or [])]

    by_concept: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "exec_ok": 0, "plan_ok": 0})
    plan_exec_gap_by_tag: dict[str, float] = {}
    top_failures: list[dict[str, Any]] = []
    soft_exec_ok = 0

    for r in results:
        case_id = r.get("id")
        for concept in r.get("concepts") or []:
            by_concept[concept]["total"] += 1
            if r.get("exec_ok"):
                by_concept[concept]["exec_ok"] += 1
            if r.get("plan_ok"):
                by_concept[concept]["plan_ok"] += 1
        if not r.get("exec_ok"):
            payload = r.get("payload") or {}
            metrics = payload.get("metrics") or []
            actual = metrics[0].get("value") if metrics and isinstance(metrics[0], dict) else None
            top_failures.append(
                {
                    "id": case_id,
                    "failure_kind": r.get("failure_kind"),
                    "tags": r.get("tags"),
                    "expected": r.get("expected"),
                    "actual": actual,
                    "error": r.get("error"),
                }
            )

    for tag, stats in by_tag.items():
        if stats["total"]:
            plan_exec_gap_by_tag[tag] = (stats["plan_ok"] - stats["exec_ok"]) / stats["total"]

    top_failures = top_failures[:15]

    return {
        "mode": mode,
        "total": total,
        "accuracy": exec_ok / total if total else 0.0,
        "plan_accuracy": plan_ok / total if total else 0.0,
        "plan_and_exec_accuracy": both_ok / total if total else 0.0,
        "smoke_accuracy": _acc(smoke, "exec_ok"),
        "smoke_plan_accuracy": _acc(smoke, "plan_ok"),
        "full_accuracy": _acc(full, "exec_ok"),
        "easy_accuracy": _acc([r for r in results if r.get("difficulty") == "easy"], "exec_ok"),
        "hard_accuracy": _acc([r for r in results if r.get("difficulty") == "hard"], "exec_ok"),
        "join_success_accuracy": _acc(join_ok_rows, "exec_ok"),
        "join_refuse_accuracy": _acc(join_ref_rows, "exec_ok"),
        "refuse_recall": refuse_hit / len(refuse_expected) if refuse_expected else 1.0,
        "refuse_precision": 1.0 - (false_refuse / max(1, total - len(refuse_expected))),
        "by_tag": {k: {**v, "accuracy": v["exec_ok"] / v["total"] if v["total"] else 0} for k, v in by_tag.items()},
        "by_difficulty": {
            k: {**v, "accuracy": v["exec_ok"] / v["total"] if v["total"] else 0} for k, v in by_diff.items()
        },
        "failure_counts": dict(failures),
        "capability_breakdown": {
            k: {**v, "accuracy": v["exec_ok"] / v["total"] if v["total"] else 0} for k, v in by_concept.items()
        },
        "plan_exec_gap_by_tag": plan_exec_gap_by_tag,
        "top_failures": top_failures,
        "soft_exec_accuracy": soft_exec_ok / total if total else 0.0,
        "results": results,
    }
