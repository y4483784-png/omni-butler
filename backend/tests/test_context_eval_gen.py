"""Generator contracts for context_recall dataset."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.gen_context_eval import FILLERS, build_cases, write_dataset

DATA = Path(__file__).resolve().parents[1] / "data" / "eval" / "context_recall.jsonl"
_DIGIT_OR_DATE = re.compile(r"\d|月|日|周[一二三四五六日天]")


def test_generator_idempotent(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    write_dataset(a)
    write_dataset(b)
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_build_cases_smoke_and_coverage():
    cases = build_cases()
    assert len(cases) == 8 * 3 * 4 * 2  # types * buckets * depths * seeds
    smoke = [c for c in cases if c["split"] == "smoke"]
    assert len(smoke) == 24
    types = {c["type"] for c in smoke}
    assert len(types) == 8
    for c in cases:
        assert c["bucket_turns"] in (12, 30, 60)
        assert len(c["history"]) == c["bucket_turns"] * 2
        assert c["probe"]["gold_keys"]
        assert c["probe"]["question"]


def test_fillers_avoid_digit_date_collision():
    assert len(FILLERS) >= 60
    for u, a in FILLERS:
        assert not _DIGIT_OR_DATE.search(u), u
        assert not _DIGIT_OR_DATE.search(a), a


def test_written_dataset_matches_builder():
    if not DATA.exists():
        write_dataset(DATA)
    on_disk = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
    built = build_cases()
    assert [c["id"] for c in on_disk] == [c["id"] for c in built]
