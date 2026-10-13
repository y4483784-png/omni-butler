"""ContextManager structural invariants (L0, no LLM)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents.context.assembler import ContextBundle, build_context, compose_answer_messages
from app.agents.context.budget import TokenBudget, estimate_messages_tokens, estimate_tokens
from app.agents.context.window import to_logical_turns

CALIBRATION = Path(__file__).resolve().parents[1] / "data" / "eval" / "token_calibration.jsonl"
_CITATION_RE = re.compile(r"\[(\d+)\]")


def _history(n_turns: int, *, prefix: str = "h") -> list[dict]:
    out: list[dict] = []
    for i in range(n_turns):
        out.append({"role": "user", "content": f"{prefix}-u{i} " + ("问" * 8)})
        out.append({"role": "assistant", "content": f"{prefix}-a{i} " + ("答" * 12)})
    return out


def _assert_no_half_turns(messages: list[dict], *, message: str) -> None:
    assert messages, "messages must not be empty"
    assert messages[-1].get("role") == "user"
    assert messages[-1].get("content") == message
    body = [m for m in messages if m.get("role") in ("user", "assistant")]
    # drop trailing current user for turn structure check
    hist = body[:-1]
    if not hist:
        return
    turns = to_logical_turns(hist)
    flat = [m for t in turns for m in t]
    assert flat == hist or hist[0].get("role") == "user"
    for turn in turns:
        assert turn and turn[0].get("role") == "user"


def test_compose_keeps_system_and_latest_user_at_all_levels(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    hist = _history(12)
    message = "现在追问最早的会议主题是什么？"
    pool = "【证据池】\n[1] 差旅制度片段\n[2] 会议纪要片段\n" + ("长证据" * 400)

    def rebuild(n: int) -> tuple[str, list[dict]]:
        text = pool[: max(20, n)]
        rows = [{"index": 1, "snippet": "a"}, {"index": 2, "snippet": "b"}]
        return text, rows

    for max_tok in (32000, 4000, 1800, 900):
        monkeypatch.setattr("app.agents.context.budget.settings.context_max_tokens", max_tok)
        monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", max_tok)
        bundle = ContextBundle(
            answer_history=hist,
            summary_block="【会话摘要】曾约定与王总开会",
            working_block="【当前工作状态】\n- 未完成日程草稿：周会（待补：地点）",
            stats={},
        )
        messages, _pool, included = compose_answer_messages(
            bundle=bundle,
            hist=hist,
            message=message,
            pool_text=pool,
            rebuild_pool=rebuild,
            has_evidence_system=True,
        )
        assert messages[0]["role"] == "system"
        assert messages[-1] == {"role": "user", "content": message}
        _assert_no_half_turns(messages, message=message)
        assert "compose_level" in bundle.stats
        assert "compose_tokens" in bundle.stats
        # citations in pool text that remain must be subset of included indices
        cites = {int(m.group(1)) for m in _CITATION_RE.finditer(_pool)}
        if included:
            allowed = {int(r.get("index") or 0) for r in included}
            assert cites <= allowed or not cites


def test_citation_indices_subset_of_included(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.budget.settings.context_max_tokens", 2000)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", 2000)
    hist = _history(6)
    message = "根据证据回答"

    def rebuild(n: int) -> tuple[str, list[dict]]:
        # Only keep first row under tight budget
        rows = [{"index": 1, "snippet": "only-one"}]
        return f"【证据池】\n[1] only-one\n" + ("x" * min(n, 40)), rows

    bundle = ContextBundle(answer_history=hist, summary_block="摘要", stats={})
    messages, pool_used, included = compose_answer_messages(
        bundle=bundle,
        hist=hist,
        message=message,
        pool_text="【证据池】\n[1] a\n[2] b\n[3] c\n" + ("证据" * 800),
        rebuild_pool=rebuild,
        has_evidence_system=True,
    )
    assert included
    cites = {int(m.group(1)) for m in _CITATION_RE.finditer(pool_used)}
    allowed = {int(r["index"]) for r in included}
    assert cites <= allowed
    assert messages[-1]["content"] == message


def test_legacy_disabled_matches_last_twenty_messages(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", False)
    monkeypatch.setattr("app.agents.context.assembler.settings.max_context_turns", 10)
    hist = _history(20)
    bundle = build_context(history=hist, message="q")
    assert bundle.answer_history == hist[-20:]
    # content equality byte-for-byte
    assert [m["content"] for m in bundle.answer_history] == [m["content"] for m in hist[-20:]]


def test_budget_grid_never_explodes_or_emergency_keeps_one_turn(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    turn_counts = (2, 8, 20)
    msg_lens = (20, 200, 2000)
    max_tokens_list = (32000, 4000, 1200)

    for turns in turn_counts:
        for ml in msg_lens:
            for max_tok in max_tokens_list:
                monkeypatch.setattr("app.agents.context.budget.settings.context_max_tokens", max_tok)
                monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", max_tok)
                hist = []
                for i in range(turns):
                    hist.append({"role": "user", "content": ("用" * ml)})
                    hist.append({"role": "assistant", "content": ("助" * ml)})
                message = "当前问题" + ("问" * min(ml, 100))
                bundle = ContextBundle(
                    answer_history=hist,
                    summary_block="摘要块" * 5,
                    working_block="工作状态" * 5,
                    stats={},
                )
                messages, _, _ = compose_answer_messages(
                    bundle=bundle,
                    hist=hist,
                    message=message,
                    pool_text="【证据池】\n" + ("证" * ml),
                    has_evidence_system=True,
                )
                used = estimate_messages_tokens(messages)
                budget = TokenBudget.from_settings()
                level = budget.level(used)
                hist_msgs = [m for m in messages if m.get("role") in ("user", "assistant")][:-1]
                hist_turns = to_logical_turns(hist_msgs)
                if level == "emergency":
                    assert len(hist_turns) <= 1
                else:
                    assert used <= int(budget.usable * budget.emergency_ratio) + 64
                assert messages[-1]["content"] == message


def test_token_calibration_mape_under_25_percent():
    rows = [
        json.loads(line)
        for line in CALIBRATION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 25
    abs_pct = []
    for row in rows:
        truth = int(row.get("prompt_tokens") or 0)
        est = estimate_tokens(str(row.get("text") or ""))
        if truth <= 0:
            assert est == 0
            continue
        abs_pct.append(abs(est - truth) / truth)
    assert abs_pct
    mape = sum(abs_pct) / len(abs_pct)
    assert mape < 0.25, f"MAPE={mape:.3f} exceeds 25%"


def test_compose_stats_written_on_bundle(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    hist = _history(3)
    bundle = ContextBundle(answer_history=hist, stats={"tokens": 1, "level": "safe"})
    messages, _, _ = compose_answer_messages(
        bundle=bundle,
        hist=hist,
        message="hi",
        pool_text="",
        has_evidence_system=False,
    )
    assert messages[-1]["content"] == "hi"
    assert bundle.stats.get("compose_level") in ("safe", "warn", "compact", "emergency")
    assert isinstance(bundle.stats.get("compose_tokens"), int)
    assert isinstance(bundle.stats.get("compose_dropped_turns"), int)
