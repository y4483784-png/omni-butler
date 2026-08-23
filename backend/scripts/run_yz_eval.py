#!/usr/bin/env python3
"""Run YZ full-chain eval (ingest → retrieve → answer → ragas + rules).

Report-only: failures are logged; this script does not modify product code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.yz_fullchain import (
    DEFAULT_DATASET,
    DEFAULT_REPORT,
    load_cases,
    run_yz_fullchain_eval,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Omni-Butler YZ full-chain evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true", help="Print full JSON report to stdout")
    parser.add_argument("--k", type=int, default=None, help="Retrieval top-k")
    parser.add_argument("--limit", type=int, default=None, help="Run first N cases only")
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="Skip ragas LLM judge (retrieval + rule metrics only)",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse existing YZ docs for eval user (dev only)",
    )
    parser.add_argument(
        "--zhipu-rerank",
        action="store_true",
        help="Use Zhipu rerank API (needs LLM_API_KEY)",
    )
    parser.add_argument("--min-recall", type=float, default=None, help="Alert if recall@k below")
    parser.add_argument(
        "--min-fact-containment",
        type=float,
        default=None,
        help="Alert if mean fact containment below",
    )
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    report = run_yz_fullchain_eval(
        cases,
        k=args.k,
        limit=args.limit,
        skip_ragas=args.skip_ragas,
        skip_ingest=args.skip_ingest,
        use_zhipu_rerank=args.zhipu_rerank,
    )
    report_path = write_report(report, args.report)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.format_text())
        print(f"\nReport written: {report_path}")

    exit_code = 0
    if args.min_recall is not None:
        recall = float(report.retrieval.get("recall_at_k") or 0)
        if recall < args.min_recall:
            print(f"\nALERT: recall@k {recall:.2%} < {args.min_recall:.2%}", file=sys.stderr)
            exit_code = 1
    if args.min_fact_containment is not None:
        fc = report.fact_containment_mean
        if fc < args.min_fact_containment:
            print(
                f"\nALERT: fact containment {fc:.2%} < {args.min_fact_containment:.2%}",
                file=sys.stderr,
            )
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
