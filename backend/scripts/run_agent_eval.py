#!/usr/bin/env python3
"""End-to-end agent eval (real run_agent_workflow). Replaces the YZ side-path suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import ensure_bootstrap

ensure_bootstrap(isolate_app_db=False)

from app.eval.agent_e2e import DEFAULT_DATASET, format_text, run_agent_eval  # noqa: E402
from app.eval.report import SuiteResult  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Omni-Butler end-to-end agent evaluation")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--skip-ingest", action="store_true")
    p.add_argument("--seed", type=int, default=0, help="bootstrap CI seed")
    p.add_argument("--json", action="store_true")
    p.add_argument("--min-completion", type=float, default=None)
    return p


def run(args: argparse.Namespace) -> SuiteResult:
    report, results = run_agent_eval(
        dataset_path=args.dataset,
        limit=args.limit,
        skip_ingest=args.skip_ingest,
        seed=args.seed,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_text(report))
    exit_code = 0
    if args.min_completion is not None and report["task_completion_rate"] < args.min_completion:
        print(f"\nFAIL: completion {report['task_completion_rate']:.2%} < {args.min_completion:.2%}", file=sys.stderr)
        exit_code = 1
    return SuiteResult(
        report=report,
        samples=[r.to_dict() for r in results],
        exit_code=exit_code,
        dataset_path=Path(args.dataset),
        run_stats={
            "n_infra_error": report["n_infra_error"],
            "infra_error_rate": report["infra_error_rate"],
            "latency_s_mean": report["latency_s_mean"],
            "latency_s_p50": report["latency_s_p50"],
            "latency_s_max": report["latency_s_max"],
            "prompt_tokens_total": report["prompt_tokens_total"],
            "tokens_measured_n": report["tokens_measured_n"],
        },
    )


def main() -> int:
    return int(run(build_parser().parse_args()).exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
