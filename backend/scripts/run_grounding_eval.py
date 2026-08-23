#!/usr/bin/env python3
"""Run grounding faithfulness eval (fixed cases → ground_and_repair → ragas).

Report-only: failures are logged; this script does not modify product code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.grounding import (
    DEFAULT_DATASET,
    DEFAULT_REPORT,
    load_cases,
    run_grounding_eval,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Omni-Butler grounding faithfulness evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true", help="Print full JSON report to stdout")
    parser.add_argument("--k", type=int, default=None, help="KB retrieval top-k")
    parser.add_argument("--limit", type=int, default=None, help="Run first N cases only")
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="Skip ragas LLM judge (rule metrics only)",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse existing YZ docs for eval user (kb cases)",
    )
    parser.add_argument(
        "--no-repair",
        action="store_true",
        help="Disable Reflexion-style rewrite (disclaimer only on fail)",
    )
    parser.add_argument(
        "--score-drafts",
        action="store_true",
        help="Also score first draft faithfulness for repaired cases (extra cost)",
    )
    parser.add_argument(
        "--no-relevancy",
        action="store_true",
        help="Only run faithfulness (skip answer_relevancy)",
    )
    parser.add_argument(
        "--min-faithfulness",
        type=float,
        default=None,
        help="Exit 1 if mean faithfulness below threshold",
    )
    args = parser.parse_args()

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
    report_path = write_report(report, args.report)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.format_text())
        print(f"\nReport written: {report_path}")

    exit_code = 0
    if args.min_faithfulness is not None:
        fm = report.faithfulness_mean
        if fm is None or fm < args.min_faithfulness:
            print(
                f"\nALERT: faithfulness {fm} < {args.min_faithfulness}",
                file=sys.stderr,
            )
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
