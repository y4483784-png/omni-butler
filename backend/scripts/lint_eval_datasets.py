#!/usr/bin/env python3
"""Validate all frozen eval datasets and optionally refresh their dataset card."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.contracts import (
    DATA_ROOT,
    CONTRACTS,
    cross_dataset_checks,
    render_dataset_card,
    validate_all,
    yz_label_conflicts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Omni-Butler eval datasets")
    parser.add_argument(
        "--card",
        action="store_true",
        help="write data/eval/DATASETS.md after validation",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = validate_all()
    for result in results:
        label = "OK" if result.ok else "FAIL"
        print(f"[{label}] {result.contract.name}: {len(result.rows)} rows")
        for error in result.errors:
            print(f"  - {error}")

    cross = cross_dataset_checks()
    print(f"[{'OK' if not cross else 'FAIL'}] cross-dataset label conventions")
    for error in cross[:20]:
        print(f"  - {error}")
    conflicts = yz_label_conflicts()
    if conflicts:
        print(
            f"[WARN] yz (deprecated): {len(conflicts)} aggregate rows labelled kb-only, "
            "contradicting the routing convention -> use the `agent` suite"
        )

    if args.card:
        card = DATA_ROOT / "DATASETS.md"
        card.write_text(render_dataset_card(results), encoding="utf-8")
        print(f"dataset card -> {card}")

    return 0 if all(result.ok for result in results) and not cross else 1


if __name__ == "__main__":
    raise SystemExit(main())
