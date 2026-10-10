#!/usr/bin/env python3
"""Build grounding cases from reviewed frozen evidence/query seeds."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.contracts import GroundingCase, normalize_text

SEED = ROOT / "data" / "eval" / "seeds" / "grounding_cases.jsonl"
OUT = ROOT / "data" / "eval" / "grounding_faithfulness.jsonl"


def build_cases() -> list[dict]:
    seeds = [
        json.loads(line)
        for line in SEED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(seeds) != 120:
        raise ValueError(f"expected 120 reviewed grounding seeds, got {len(seeds)}")
    seen: set[str] = set()
    source_index: Counter[str] = Counter()
    rows: list[dict] = []
    for seed in seeds:
        key = normalize_text(str(seed.get("query") or ""))
        if not key or key in seen:
            raise ValueError(f"duplicate/empty normalized query: {seed.get('query')}")
        seen.add(key)
        source = str(seed.get("source") or "")
        source_index[source] += 1
        row = {"id": f"gf_{source}_{source_index[source]:03d}", **seed}
        rows.append(GroundingCase.model_validate(row).model_dump(mode="python"))
    if source_index != {"kb": 40, "web": 40, "sandbox": 40}:
        raise ValueError(f"unexpected grounding source counts: {dict(source_index)}")
    return rows


def main() -> int:
    rows = build_cases()
    OUT.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} grounding cases -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
