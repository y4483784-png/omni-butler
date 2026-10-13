#!/usr/bin/env python3
"""Build the RAG retrieval dataset from reviewed, frozen query seeds."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.contracts import RagCase, normalize_text
from app.eval.jsonl_io import write_jsonl

SEED = ROOT / "data" / "eval" / "seeds" / "rag_queries.jsonl"
CORPUS = ROOT / "data" / "eval" / "corpus" / "rag"
OUT = ROOT / "data" / "eval" / "rag_retrieval.jsonl"


def build_cases() -> list[dict]:
    seeds = [
        json.loads(line)
        for line in SEED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(seeds) != 400:
        raise ValueError(f"expected 400 reviewed RAG seeds, got {len(seeds)}")
    corpus_keys = {
        path.stem for path in CORPUS.glob("*.md") if path.name.casefold() != "readme.md"
    }
    seen: set[str] = set()
    categories: Counter[str] = Counter()
    rows: list[dict] = []
    for index, seed in enumerate(seeds, start=1):
        key = normalize_text(str(seed.get("query") or ""))
        if not key or key in seen:
            raise ValueError(f"duplicate/empty normalized query at seed {index}: {seed.get('query')}")
        seen.add(key)
        unknown = set(seed.get("gold_doc_keys") or []) - corpus_keys
        if unknown:
            raise ValueError(f"seed {index} references unknown corpus keys: {sorted(unknown)}")
        category = {"single_doc", "multi_doc", "hard_negative"}.intersection(
            seed.get("tags") or []
        )
        if len(category) != 1:
            raise ValueError(f"seed {index} needs one category tag, got {sorted(category)}")
        categories.update(category)
        row = {"id": f"rag_{index:04d}", **seed}
        rows.append(RagCase.model_validate(row).model_dump(mode="python"))
    if categories != {"single_doc": 300, "multi_doc": 60, "hard_negative": 40}:
        raise ValueError(f"unexpected RAG category counts: {dict(categories)}")
    return rows


def main() -> int:
    rows = build_cases()
    write_jsonl(OUT, rows)
    print(f"wrote {len(rows)} reviewed RAG cases -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
