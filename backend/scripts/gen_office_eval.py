#!/usr/bin/env python3
"""Build the office routing dataset from reviewed, frozen utterance seeds."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.contracts import RoutingCase, normalize_text
from app.eval.jsonl_io import write_jsonl

SEED = ROOT / "data" / "eval" / "seeds" / "routing_utterances.jsonl"
SEED_EXTRA = ROOT / "data" / "eval" / "seeds" / "routing_multiturn_extra.jsonl"
OUT = ROOT / "data" / "eval" / "office_tool_routing.jsonl"


def build_cases() -> list[dict]:
    def _load(path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    base = _load(SEED)
    extra = _load(SEED_EXTRA)  # real anaphora multi-turn cases (history is required to answer)
    if len(base) != 400 or len(extra) != 20:
        raise ValueError(f"expected 400 base + 20 multiturn seeds, got {len(base)} + {len(extra)}")
    seeds = base + extra
    # Availability flags must match the gold tools, otherwise route() zeroes the tool
    # (router._apply_availability) and the case can never be answered correctly.
    for seed in seeds:
        tools = set(seed.get("gold", {}).get("tools") or [])
        ctx = dict(seed.get("context") or {})
        if "sandbox" in tools:
            ctx["has_tabular_docs"] = True
        if "kb" in tools and not ctx.get("forced_kb") and not ctx.get("use_kb"):
            ctx["has_kb_docs"] = True
        seed["context"] = ctx
    seen: set[str] = set()
    intents: Counter[str] = Counter()
    multi = history = pending = 0
    rows: list[dict] = []
    for index, seed in enumerate(seeds, start=1):
        key = normalize_text(str(seed.get("message") or ""))
        if not key or key in seen:
            raise ValueError(f"duplicate/empty normalized message: {seed.get('message')}")
        seen.add(key)
        row = {"id": f"route_{index:04d}", **seed}
        parsed = RoutingCase.model_validate(row).model_dump(mode="python")
        rows.append(parsed)
        intents[parsed["gold"]["intent"]] += 1
        multi += int(len(parsed["gold"]["tools"]) > 1)
        history += int(bool(parsed["history"]))
        pending += int(bool(parsed["context"].get("pending_calendar")))
    if multi != 50 or history < 80 or pending != 24:
        raise ValueError(
            f"routing coverage multi={multi}, history={history}, pending={pending}; "
            "expected 50, >=80, 24"
        )
    print(f"intent distribution: {dict(intents)}")
    return rows


def main() -> int:
    rows = build_cases()
    write_jsonl(OUT, rows)
    print(f"wrote {len(rows)} office routing cases -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
