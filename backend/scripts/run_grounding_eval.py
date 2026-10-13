#!/usr/bin/env python3
"""Run grounding faithfulness eval (fixed cases → ground_and_repair → ragas)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import ensure_bootstrap, resolve_target

ensure_bootstrap(isolate_app_db=False)

from app.eval.grounding import DEFAULT_DATASET, load_cases, run_grounding_eval  # noqa: E402
from app.eval.report import RunContext, SuiteResult, make_run_dir, write_run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omni-Butler grounding faithfulness evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-ragas", action="store_true")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--score-drafts", action="store_true")
    parser.add_argument("--no-relevancy", action="store_true")
    parser.add_argument("--min-faithfulness", type=float, default=None)
    return parser


def run(args: argparse.Namespace) -> SuiteResult:
    cases = load_cases(args.dataset)
    report = run_grounding_eval(
        cases,
        k=args.k,
        limit=args.limit,
        skip_ragas=args.skip_ragas,
        skip_ingest=args.skip_ingest,
        repair_enabled=not args.no_repair,
        score_drafts=args.score_drafts,
        include_relevancy=not args.no_relevancy,
    )
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report.format_text())

    exit_code = 0
    if args.min_faithfulness is not None:
        fm = report.faithfulness_mean
        if fm is None or fm < args.min_faithfulness:
            print(f"\nALERT: faithfulness {fm} < {args.min_faithfulness}", file=sys.stderr)
            exit_code = 1

    return SuiteResult(
        report=payload,
        samples=list(payload.get("failures") or []),
        exit_code=exit_code,
        dataset_path=Path(args.dataset),
    )


def main() -> int:
    target = resolve_target()
    args = build_parser().parse_args()
    run_dir = make_run_dir("grounding", "adhoc")
    result = run(args)
    write_run(
        RunContext(
            suite="grounding",
            profile="adhoc",
            target=target,
            argv=sys.argv,
            started_at=datetime.now(timezone.utc),
            run_dir=run_dir,
            dataset_path=Path(args.dataset),
            key_metrics=["raw.faithfulness_mean", "raw.repair_rate", "raw.disclaimer_rate"],
        ),
        result,
    )
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
