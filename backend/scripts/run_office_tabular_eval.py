"""Office tabular objective short-answer eval (live sandbox smoke by default).

Usage:
  python scripts/gen_office_tabular_eval.py          # regenerate fixtures + jsonl
  python scripts/run_office_tabular_eval.py          # live smoke (20 cases)
  python scripts/run_office_tabular_eval.py --split full
  python scripts/run_office_tabular_eval.py --dry-compile --split full
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.eval.office_tabular import (  # noqa: E402
    aggregate_report,
    case_filenames,
    concepts_from_tags,
    exec_matches,
    plan_matches,
    soft_exec_matches,
)
from app.services.data_analysis import (  # noqa: E402
    AnalysisSpec,
    _heuristic_spec,
    analyze_local_tables,
)
from app.services.tabular_inspect import infer_tabular_schema  # noqa: E402

DATASET = ROOT / "data" / "eval" / "office_tabular.jsonl"
FIXTURES = ROOT / "data" / "eval" / "fixtures"
REPORT = ROOT / "reports" / "office_tabular_eval_latest.json"


def _load_cases(*, split: str) -> list[dict]:
    rows = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        s = row.get("split") or "full"
        if split == "all" or split == s or (split == "full" and s in ("full", "smoke")):
            rows.append(row)
    return rows


def _fixture_paths(case: dict) -> list[Path]:
    return [FIXTURES / name for name in case_filenames(case)]


def _plan_spec(case: dict) -> AnalysisSpec:
    paths = _fixture_paths(case)
    schemas = [(p.name, infer_tabular_schema(p)) for p in paths]
    primary = paths[0]
    schema = schemas[0][1]
    if len(schemas) > 1:
        # join_ok: plan on merged-equivalent primary when join inferable
        from app.services.analysis_ir import infer_join_from_message

        jl, jr, jk = infer_join_from_message(case["query"], schemas)
        if jk and jl and jr:
            left = next(p for p in paths if p.name == jl)
            right = next(p for p in paths if p.name == jr)
            import pandas as pd

            from app.core.tmpdir import ephemeral_dir

            ldf = pd.read_csv(left)
            rdf = pd.read_csv(right)
            merged = ldf.merge(rdf, on=jk, how="inner")
            tmp_root = ephemeral_dir() or primary.parent
            tmp = tmp_root / f"_plan_join_{case['id']}.csv"
            merged.to_csv(tmp, index=False)
            schema = infer_tabular_schema(tmp)
    spec = _heuristic_spec(
        case["query"],
        schema,
        file_schemas=schemas,
        prior_ir=case.get("prior_ir"),
    )
    from app.services.analysis_ir import infer_join_from_message

    jl, jr, jk = infer_join_from_message(case["query"], schemas)
    if jk:
        spec.join_left = jl
        spec.join_right = jr
        spec.join_key = jk
    return spec


def _result_meta(case: dict) -> dict:
    return {
        "concepts": case.get("concepts") or concepts_from_tags(list(case.get("tags") or [])),
        "expected": case.get("gold"),
    }


def eval_plan(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        spec = _plan_spec(case)
        if case.get("expect_uncomputable") and "join_refuse" in (case.get("tags") or []):
            from app.services.data_analysis import refuse_multitable_without_join

            payload = refuse_multitable_without_join(
                case["query"],
                case_filenames(case),
                file_schemas=[(p.name, infer_tabular_schema(p)) for p in _fixture_paths(case)],
            ) or {}
            ok = bool(payload.get("missing"))
        else:
            ok = plan_matches(spec, case)
        rows.append(
            {
                "id": case["id"],
                "split": case.get("split"),
                "difficulty": case.get("difficulty"),
                "tags": case.get("tags"),
                "expect_uncomputable": case.get("expect_uncomputable"),
                "plan_ok": ok,
                "exec_ok": ok,
                "operation": spec.operation,
                "join_key": spec.join_key,
                **_result_meta(case),
            }
        )
    return rows


def eval_exec(cases: list[dict]) -> list[dict]:
    from app.sandbox.runner import docker_available

    if not docker_available():
        return [
            {
                "id": c["id"],
                "split": c.get("split"),
                "difficulty": c.get("difficulty"),
                "tags": c.get("tags"),
                "expect_uncomputable": c.get("expect_uncomputable"),
                "plan_ok": False,
                "exec_ok": False,
                "failure_kind": "sandbox_error",
                "error": "docker unavailable",
            }
            for c in cases
        ]

    rows = []
    for case in cases:
        spec = _plan_spec(case)
        plan_ok = plan_matches(spec, case)

        if case.get("expect_uncomputable"):
            if "join_refuse" in (case.get("tags") or []):
                from app.services.data_analysis import refuse_multitable_without_join

                payload = refuse_multitable_without_join(
                    case["query"],
                    case_filenames(case),
                    file_schemas=[(p.name, infer_tabular_schema(p)) for p in _fixture_paths(case)],
                ) or {}
                exec_ok, fk = exec_matches(case=case, payload=payload, spec=spec)
                rows.append(
                    {
                        "id": case["id"],
                        "split": case.get("split"),
                        "difficulty": case.get("difficulty"),
                        "tags": case.get("tags"),
                        "expect_uncomputable": True,
                        "plan_ok": plan_ok,
                        "exec_ok": exec_ok,
                        "failure_kind": fk,
                        "payload": payload,
                    }
                )
                continue
            exec_ok, fk = exec_matches(
                case=case,
                payload={"missing": spec.uncomputable, "metrics": []},
                spec=spec,
            )
            rows.append(
                {
                    "id": case["id"],
                    "split": case.get("split"),
                    "difficulty": case.get("difficulty"),
                    "tags": case.get("tags"),
                    "expect_uncomputable": True,
                    "plan_ok": plan_ok,
                    "exec_ok": exec_ok,
                    "failure_kind": fk,
                }
            )
            continue

        outcome = analyze_local_tables(
            case["query"],
            _fixture_paths(case),
            prior_ir=case.get("prior_ir"),
            stage_copy=True,
        )
        payload = outcome.summary or {}
        exec_ok, fk = exec_matches(
            case=case,
            payload=payload,
            spec=spec,
            sandbox_error=outcome.error,
        )
        if not plan_ok and exec_ok:
            fk = fk or "wrong_op"
        soft_ok = (not exec_ok) and soft_exec_matches(
            case, payload, tol=float(case.get("tolerance") or 0)
        )
        rows.append(
            {
                "id": case["id"],
                "split": case.get("split"),
                "difficulty": case.get("difficulty"),
                "tags": case.get("tags"),
                "expect_uncomputable": False,
                "plan_ok": plan_ok,
                "exec_ok": exec_ok,
                "soft_exec_ok": soft_ok,
                "failure_kind": fk,
                "error": outcome.error,
                "operation": spec.operation,
                "payload": payload,
                **_result_meta(case),
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-compile", action="store_true", help="Plan accuracy only (no Docker exec)")
    ap.add_argument(
        "--split",
        choices=("smoke", "full", "all"),
        default=None,
        help="smoke=20 cases; full=all cases; default smoke for live, full for dry-compile",
    )
    args = ap.parse_args()

    split = args.split or ("full" if args.dry_compile else "smoke")
    cases = _load_cases(split=split)
    if not cases:
        print(json.dumps({"error": "no cases", "split": split}), file=sys.stderr)
        return 2

    mode = "dry_compile" if args.dry_compile else "live"
    results = eval_plan(cases) if args.dry_compile else eval_exec(cases)
    report = aggregate_report(results, mode=mode)
    report["split"] = split
    report["passed"] = sum(1 for r in results if r.get("exec_ok"))
    report["total"] = len(results)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "mode": mode,
        "split": split,
        "total": report["total"],
        "accuracy": report["accuracy"],
        "plan_accuracy": report["plan_accuracy"],
        "passed": report["passed"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
