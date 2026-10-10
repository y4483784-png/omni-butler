#!/usr/bin/env python3
"""Run ContextManager replay eval (extractive CI / optional live LLM)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import ensure_bootstrap

ensure_bootstrap(isolate_app_db=True)

from app.eval.context_replay import (  # noqa: E402
    ARMS,
    DEFAULT_BASELINE,
    DEFAULT_DATASET,
    gate_report,
    load_cases,
    run_context_eval,
)
from app.eval.report import RunContext, SuiteResult, make_run_dir, write_run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omni-Butler ContextManager evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", choices=("smoke", "full", "all"), default="smoke")
    parser.add_argument("--arms", default="legacy,full", help="Comma-separated arms: " + ",".join(ARMS))
    parser.add_argument("--summarizer", choices=("extractive", "live"), default="extractive")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--refresh-every", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    return parser


def _stratified_live(cases: list, limit: int) -> list:
    """Round-robin by case type so a 64-row live slice covers every probe kind."""
    if limit >= len(cases):
        return list(cases)
    buckets: dict[str, list] = {}
    for c in cases:
        buckets.setdefault(str(c.get("type") or "other"), []).append(c)
    out: list = []
    queues = [list(v) for _, v in sorted(buckets.items())]
    while len(out) < limit and any(queues):
        for q in queues:
            if q and len(out) < limit:
                out.append(q.pop(0))
    return out


def run(args: argparse.Namespace) -> SuiteResult:
    if args.live and os.environ.get("RUN_CTX_EVAL") != "1":
        print("Refusing --live without RUN_CTX_EVAL=1", file=sys.stderr)
        return SuiteResult(report={"error": "RUN_CTX_EVAL required"}, exit_code=2)
    if args.update_baseline and args.gate:
        print("Refusing --update-baseline together with --gate", file=sys.stderr)
        return SuiteResult(report={"error": "cannot update baseline and gate in one run"}, exit_code=2)

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ARMS:
            print(f"unknown arm: {a}", file=sys.stderr)
            return SuiteResult(report={"error": f"unknown arm {a}"}, exit_code=2)

    cases = load_cases(args.dataset, split=args.split)
    if args.live and args.limit is None:
        cases = _stratified_live(cases, 64)
    elif args.limit is not None:
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
    payload = report.to_dict()

    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"updated baseline -> {args.baseline}")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report.format_text())

    exit_code = 0
    gate_fails: list[str] = []
    if args.gate:
        baseline = None
        if args.baseline.exists():
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        gate_fails = gate_report(report, baseline)
        if gate_fails:
            print("\nGATE FAIL:", file=sys.stderr)
            for f in gate_fails:
                print(f"  - {f}", file=sys.stderr)
            exit_code = 1
        else:
            print("\nGATE PASS")

    payload["gate"] = {"failed": gate_fails, "ok": not gate_fails} if args.gate else None
    return SuiteResult(
        report=payload,
        samples=list(payload.get("failures") or []),
        exit_code=exit_code,
        dataset_path=Path(args.dataset),
    )


def main() -> int:
    args = build_parser().parse_args()
    run_dir = make_run_dir("context", "adhoc")
    result = run(args)
    write_run(
        RunContext(
            suite="context",
            profile="adhoc",
            target="venv",
            argv=sys.argv,
            started_at=datetime.now(timezone.utc),
            run_dir=run_dir,
            dataset_path=Path(args.dataset),
            key_metrics=["raw.n", "raw.invariants.violations"],
        ),
        result,
    )
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
