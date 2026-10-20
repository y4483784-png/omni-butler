"""Generator contracts for context_recall dataset."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.gen_context_eval import FILLERS, NEEDLE_BANK, build_cases, write_dataset

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
    smoke = [c for c in cases if c["split"] == "smoke"]
    types = {c["type"] for c in smoke}
    assert len(types) == 8
    # Smoke stays on the cheap buckets. Those histories do not reach the compact
    # threshold, so the smoke profile must not hard-gate compaction_rate.
    assert {c["bucket_turns"] for c in smoke} <= {12, 30}
    assert any(c["bucket_turns"] >= 60 for c in cases if c["split"] == "full")
    for c in cases:
        assert c["bucket_turns"] in (12, 30, 60, 120)
        assert len(c["history"]) == c["bucket_turns"] * 2
        assert c["probe"]["gold_keys"]
        assert c["probe"]["question"]


def test_fillers_avoid_digit_date_collision():
    assert len(FILLERS) >= 120
    assert len(FILLERS) == len(set(FILLERS))
    for u, a in FILLERS:
        assert not _DIGIT_OR_DATE.search(u), u
        assert not _DIGIT_OR_DATE.search(a), a


def test_fillers_do_not_contain_any_needle_gold_key():
    filler_text = "\n".join(part for pair in FILLERS for part in pair)
    for variants in NEEDLE_BANK.values():
        for variant in variants:
            for key in variant["gold_keys"]:
                assert str(key) not in filler_text, key


def test_written_dataset_matches_builder():
    if not DATA.exists():
        write_dataset(DATA)
    on_disk = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
    built = build_cases()
    assert [c["id"] for c in on_disk] == [c["id"] for c in built]


def test_judge_model_is_separate_from_answer_model(monkeypatch):
    from app.core.config import settings
    from app.core.llm import Role, RoleConfigError, resolve

    assert resolve(Role.JUDGE).model == "deepseek-v4-pro"
    assert resolve(Role.JUDGE).model != resolve(Role.CHAT).model
    monkeypatch.setattr(settings, "judge_model", "judge-other")
    assert resolve(Role.JUDGE).model == "judge-other"
    monkeypatch.setattr(settings, "judge_model", "")
    with pytest.raises(RoleConfigError):
        resolve(Role.JUDGE)


def test_context_report_records_judge_identity():
    from app.eval.context_replay import EvalReport

    rep = EvalReport(
        n=1,
        split="smoke",
        mode="extractive",
        answer_model="m-a",
        judge_model="glm-4-air",
        self_judged=False,
    )
    payload = rep.to_dict()
    assert payload["judge_model"] == "glm-4-air"
    assert payload["answer_model"] == "m-a"
    assert payload["self_judged"] is False


def test_context_cache_key_includes_judge_model(monkeypatch):
    """A verdict cached under one judge must not be replayed under another."""
    from app.core.config import settings
    from app.eval.context_replay import _cache_key

    msgs = [{"role": "user", "content": "x"}]
    monkeypatch.setattr(settings, "judge_model", "glm-4-air")
    a = _cache_key("full", msgs, "q")
    monkeypatch.setattr(settings, "judge_model", "glm-4-flashx")
    b = _cache_key("full", msgs, "q")
    assert a != b
