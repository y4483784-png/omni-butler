#!/usr/bin/env python3
"""Run RAG retrieval eval (P@k / R@k / MRR) on seeded mini corpus."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import bootstrap, resolve_target

bootstrap(resolve_target())

from app.eval.rag_retrieval import DEFAULT_DATASET, load_cases, run_rag_retrieval_eval  # noqa: E402
from app.eval.report import RunContext, SuiteResult, make_run_dir, write_run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omni-Butler RAG retrieval evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--zhipu-rerank", action="store_true")
    parser.add_argument("--min-recall", type=float, default=None)
    return parser


def run(args: argparse.Namespace) -> SuiteResult:
    cases = load_cases(args.dataset)
    report = run_rag_retrieval_eval(cases, k=args.k, use_zhipu_rerank=args.zhipu_rerank)
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report.format_text())

    exit_code = 0
    if args.min_recall is not None:
        recall = float(report.metrics.get("recall_at_k") or 0)
        if recall < args.min_recall:
            print(
                f"\nFAIL: recall@k {recall:.2%} < {args.min_recall:.2%}",
                file=sys.stderr,
            )
            exit_code = 1

    return SuiteResult(
        report=payload,
        samples=list(payload.get("failures") or []),
        exit_code=exit_code,
        dataset_path=Path(args.dataset),
    )


def main() -> int:
    args = build_parser().parse_args()
    run_dir = make_run_dir("rag", "adhoc")
    result = run(args)
    write_run(
        RunContext(
            suite="rag",
            profile="adhoc",
            target="venv",
            argv=sys.argv,
            started_at=datetime.now(timezone.utc),
            run_dir=run_dir,
            dataset_path=Path(args.dataset),
            key_metrics=[
                "raw.metrics.precision_at_k",
                "raw.metrics.recall_at_k",
                "raw.metrics.mrr",
            ],
        ),
        result,
    )
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
