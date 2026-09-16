#!/usr/bin/env python3
"""Run office tool-routing eval and print standard metrics (P/R/F1, etc.)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import bootstrap, resolve_target

bootstrap(resolve_target())

from app.eval.report import RunContext, SuiteResult, make_run_dir, write_run  # noqa: E402
from app.eval.tool_routing import DEFAULT_DATASET, load_cases, run_tool_routing_eval  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omni-Butler tool routing evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument("--min-intent-acc", type=float, default=None)
    parser.add_argument("--min-tool-exact", type=float, default=None)
    parser.add_argument("--refresh-cache", action="store_true")
    return parser


def run(args: argparse.Namespace) -> SuiteResult:
    cases = load_cases(args.dataset)
    report = run_tool_routing_eval(cases, refresh_cache=args.refresh_cache)
    payload = report.to_dict()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report.format_text())

    exit_code = 0
    if args.min_intent_acc is not None and report.intent_accuracy < args.min_intent_acc:
        print(
            f"\nFAIL: intent accuracy {report.intent_accuracy:.2%} < {args.min_intent_acc:.2%}",
            file=sys.stderr,
        )
        exit_code = 1
    if args.min_tool_exact is not None:
        exact = float(report.tools.get("exact_match_accuracy") or 0)
        if exact < args.min_tool_exact:
            print(
                f"\nFAIL: tool exact-match {exact:.2%} < {args.min_tool_exact:.2%}",
                file=sys.stderr,
            )
            exit_code = 1

    samples = []
    for f in payload.get("failures") or []:
        samples.append(f if isinstance(f, dict) else {"id": getattr(f, "id", None), "error": str(f)})

    return SuiteResult(
        report=payload,
        samples=samples,
        exit_code=exit_code,
        dataset_path=Path(args.dataset),
    )


def main() -> int:
    args = build_parser().parse_args()
    run_dir = make_run_dir("routing", "adhoc")
    result = run(args)
    write_run(
        RunContext(
            suite="routing",
            profile="adhoc",
            target="venv",
            argv=sys.argv,
            started_at=datetime.now(timezone.utc),
            run_dir=run_dir,
            dataset_path=Path(args.dataset),
            key_metrics=["raw.intent_accuracy", "raw.tools.exact_match_accuracy"],
        ),
        result,
    )
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
