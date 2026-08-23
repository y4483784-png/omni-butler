#!/usr/bin/env python3
"""Run RAG retrieval eval (P@k / R@k / MRR) on seeded mini corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.rag_retrieval import DEFAULT_DATASET, load_cases, run_rag_retrieval_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Omni-Butler RAG retrieval evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument(
        "--zhipu-rerank",
        action="store_true",
        help="Use Zhipu /paas/v4/rerank (needs API key); default heuristic",
    )
    parser.add_argument("--min-recall", type=float, default=None)
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    report = run_rag_retrieval_eval(
        cases, k=args.k, use_zhipu_rerank=args.zhipu_rerank
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.format_text())

    if args.min_recall is not None:
        recall = float(report.metrics.get("recall_at_k") or 0)
        if recall < args.min_recall:
            print(
                f"\nFAIL: recall@k {recall:.2%} < {args.min_recall:.2%}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
