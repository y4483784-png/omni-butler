#!/usr/bin/env python3
"""Run office tool-routing eval and print standard metrics (P/R/F1, etc.)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import ensure_bootstrap

ensure_bootstrap(isolate_app_db=True)

from app.eval.report import RunContext, SuiteResult, make_run_dir, write_run  # noqa: E402
from app.eval.tool_routing import (  # noqa: E402
    DEFAULT_DATASET,
    CacheMissError,
    load_cases,
    run_tool_routing_eval,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omni-Butler tool routing evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument("--min-intent-acc", type=float, default=None)
    parser.add_argument("--min-tool-exact", type=float, default=None)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument(
        "--require-cache",
        action="store_true",
        help="Smoke replay: fail if the cache lacks an entry for the current model/prompt",
    )
    return parser


def run(args: argparse.Namespace) -> SuiteResult:
    cases = load_cases(args.dataset)
    try:
        report = run_tool_routing_eval(
            cases,
            refresh_cache=args.refresh_cache,
            require_cache=getattr(args, "require_cache", False),
        )
    except CacheMissError as exc:
        # Smoke replays a frozen cache; a stale fingerprint is a setup problem, not
        # a model failure. Report it readably instead of crashing without a report.
        print(
            f"\nBLOCKED: {exc}\n"
            "提示：请先运行完整档刷新缓存："
            "python scripts/eval.py run routing --profile full",
            file=sys.stderr,
        )
        return SuiteResult(
            report={
                "error": str(exc),
                "blocked": "router_cache_miss",
                "n": len(cases),
                "failures": [
                    {
                        "id": "router_cache",
                        "status": "blocked",
                        "message": "router cache missing/stale; run --profile full to refresh",
                    }
                ],
            },
            samples=[{"id": "router_cache", "status": "blocked", "message": str(exc)}],
            exit_code=2,
            dataset_path=Path(args.dataset),
        )
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
