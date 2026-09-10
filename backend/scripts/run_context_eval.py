#!/usr/bin/env python3
"""Run ContextManager replay eval (extractive CI / optional live LLM)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow `python scripts/run_context_eval.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Prefer in-memory sqlite for CLI isolation (same pattern as office_tabular).
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.eval.context_replay import (  # noqa: E402
    ARMS,
    DEFAULT_BASELINE,
    DEFAULT_DATASET,
    DEFAULT_REPORT,
    gate_report,
    load_cases,
    run_context_eval,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Omni-Butler ContextManager evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", choices=("smoke", "full", "all"), default="smoke")
    parser.add_argument(
        "--arms",
        default="legacy,full",
        help="Comma-separated arms: " + ",".join(ARMS),
    )
    parser.add_argument("--summarizer", choices=("extractive", "live"), default="extractive")
    parser.add_argument("--live", action="store_true", help="Call real LLM for answer+judge")
    parser.add_argument("--refresh-every", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.live and os.environ.get("RUN_CTX_EVAL") != "1":
        print("Refusing --live without RUN_CTX_EVAL=1", file=sys.stderr)
        return 2

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ARMS:
            print(f"unknown arm: {a}", file=sys.stderr)
            return 2

    cases = load_cases(args.dataset, split=args.split)
    if args.limit is not None:
        cases = cases[: max(0, args.limit)]

    report = run_context_eval(
        cases,
        arms=arms,
        summarizer=args.summarizer,
        live=bool(args.live),
        refresh_every=max(1, int(args.refresh_every)),
        split=args.split,
        refresh_cache=bool(args.refresh_cache),
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"updated baseline -> {args.baseline}")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report.format_text())
        print(f"\nreport -> {args.report}")

    if args.gate:
        baseline = None
        if args.baseline.exists():
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        fails = gate_report(report, baseline)
        if fails:
            print("\nGATE FAIL:", file=sys.stderr)
            for f in fails:
                print(f"  - {f}", file=sys.stderr)
            return 1
        print("\nGATE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
