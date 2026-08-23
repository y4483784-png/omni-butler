#!/usr/bin/env python3
"""Run office tool-routing eval and print standard metrics (P/R/F1, etc.)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scripts/run_office_eval.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.tool_routing import DEFAULT_DATASET, load_cases, run_tool_routing_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Omni-Butler tool routing evaluation")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="JSONL golden set path",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument(
        "--min-intent-acc",
        type=float,
        default=None,
        help="Exit 1 if intent accuracy below threshold (CI gate)",
    )
    parser.add_argument(
        "--min-tool-exact",
        type=float,
        default=None,
        help="Exit 1 if tool exact-match below threshold",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore data/eval/router_cache.json and re-call the LLM router",
    )
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    report = run_tool_routing_eval(cases, refresh_cache=args.refresh_cache)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.format_text())

    if args.min_intent_acc is not None and report.intent_accuracy < args.min_intent_acc:
        print(f"\nFAIL: intent accuracy {report.intent_accuracy:.2%} < {args.min_intent_acc:.2%}", file=sys.stderr)
        return 1
    if args.min_tool_exact is not None:
        exact = float(report.tools.get("exact_match_accuracy") or 0)
        if exact < args.min_tool_exact:
            print(f"\nFAIL: tool exact-match {exact:.2%} < {args.min_tool_exact:.2%}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
