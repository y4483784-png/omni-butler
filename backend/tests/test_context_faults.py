"""Fault-injection tests for ContextManager degradation paths (mocked, no live LLM)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.context.assembler import ContextBundle, build_context, compose_answer_messages
from app.agents.context.budget import estimate_tokens
from app.agents.context.summary import invalidate_summary_if_needed, refresh_session_context, summarize_incremental
from app.agents.context.working_state import load_working_state
from app.models.models import Message, Session as ChatSession


def _db_with_messages(n: int = 8, *, summary: str = "旧摘要", upto: int = 0):
    db = MagicMock()
    session = ChatSession(id=1, user_id=1, title="t")
    session.context_summary = summary
    session.context_summary_upto_message_id = upto
    session.working_state = ""
    db.get.return_value = session
    rows = []
    for i in range(1, n + 1):
        role = "user" if i % 2 == 1 else "assistant"
        rows.append(Message(id=i, session_id=1, role=role, content=f"c{i}"))
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = rows
    db.query.return_value = q
    return db, session


def test_summary_llm_exception_keeps_old(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("llm timeout")

    monkeypatch.setattr("app.agents.context.summary.complete_json", boom)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_enabled", True)
    out = summarize_incremental("保留", [{"role": "user", "content": "新"}])
    assert out is None

    db, session = _db_with_messages(summary="保留我", upto=0)
    monkeypatch.setattr("app.agents.context.summary.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.max_context_turns", 1)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_min_overflow_turns", 1)
    refresh_session_context(db, session_id=1, user_id=1)
    assert session.context_summary == "保留我"
    assert session.context_summary_upto_message_id == 0


def test_summary_bad_json_and_empty(monkeypatch):
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.complete_json", lambda *a, **k: "not-a-dict")
    assert summarize_incremental("", [{"role": "user", "content": "x"}]) is None
    monkeypatch.setattr("app.agents.context.summary.complete_json", lambda *a, **k: {"summary": ""})
    assert summarize_incremental("", [{"role": "user", "content": "x"}]) is None


def test_estimate_drift_still_composes(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    hist = []
    for i in range(10):
        hist.append({"role": "user", "content": "问" * 50})
        hist.append({"role": "assistant", "content": "答" * 80})
    message = "当前问题"

    for scale in (0.8, 1.2):
        real = estimate_tokens

        def scaled(text, _s=scale):
            return max(1, int(real(text) * _s)) if text else 0

        monkeypatch.setattr("app.agents.context.budget.estimate_tokens", scaled)
        monkeypatch.setattr("app.agents.context.assembler.estimate_messages_tokens", lambda msgs: sum(scaled(str(m.get("content") or "")) + 4 for m in msgs))
        monkeypatch.setattr("app.agents.context.budget.settings.context_max_tokens", 2500)
        monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", 2500)
        bundle = ContextBundle(answer_history=hist, summary_block="摘要", working_block="工作", stats={})
        messages, _, _ = compose_answer_messages(
            bundle=bundle,
            hist=hist,
            message=message,
            pool_text="【证据池】" + ("证" * 200),
            has_evidence_system=True,
        )
        assert messages[-1]["content"] == message
        assert messages[0]["role"] == "system"


def test_async_summary_lag_placeholder_then_catchup(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.max_context_turns", 2)
    hist = []
    for i in range(15):
        hist.append({"role": "user", "content": f"u{i}"})
        hist.append({"role": "assistant", "content": f"a{i}"})
    session = SimpleNamespace(context_summary="", context_summary_upto_message_id=0, working_state="")
    bundle = build_context(session=session, history=hist, message="q15")
    assert "另有" in bundle.summary_block
    assert bundle.stats["dropped_turns"] >= 1

    # catch-up: set summary coverage
    session.context_summary = "已追赶摘要"
    session.context_summary_upto_message_id = 20
    bundle2 = build_context(session=session, history=hist, message="q16")
    assert "【会话摘要】" in bundle2.summary_block
    assert "已追赶摘要" in bundle2.summary_block


def test_working_state_bad_json():
    session = SimpleNamespace(working_state="{not-json")
    assert load_working_state(session) == {}
    session.working_state = '["list"]'
    assert load_working_state(session) == {}


def test_regenerate_invalidates_and_rebuild_excludes_deleted(monkeypatch):
    session = ChatSession(id=1, user_id=1, title="t")
    session.context_summary = "包含秘密针：预算八万元"
    session.context_summary_upto_message_id = 10
    assert invalidate_summary_if_needed(session, max_message_id=4) is True
    assert session.context_summary == ""
    assert session.context_summary_upto_message_id == 0

    # rebuild with extractive-like summarizer on remaining messages only
    monkeypatch.setattr(
        "app.agents.context.summary.complete_json",
        lambda messages, **_kwargs: {"summary": "仅剩普通闲聊"},
    )
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.max_context_turns", 1)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_min_overflow_turns", 1)
    db, sess = _db_with_messages(n=4, summary="", upto=0)
    refresh_session_context(db, session_id=1, user_id=1)
    assert "八万" not in (sess.context_summary or "")


def test_mid_session_toggle_enabled(monkeypatch):
    hist = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}] * 12
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.max_context_turns", 3)
    on = build_context(history=hist, message="q")
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", False)
    monkeypatch.setattr("app.agents.context.assembler.settings.max_context_turns", 10)
    off = build_context(history=hist, message="q")
    assert len(on.answer_history) <= 6
    assert off.answer_history == hist[-20:]


@pytest.mark.xfail(strict=True, reason="summarize_incremental clips each message to 600 chars")
def test_needle_beyond_600_char_clip_is_known_gap(monkeypatch):
    needle = "SECRET_NEEDLE_TOKEN_XYZ"
    long_msg = ("前缀填充。" * 80) + needle  # > 600 chars before needle
    assert len(long_msg) > 700
    captured = {}

    def fake_complete_json(messages, *args, **kwargs):
        captured["user"] = messages[-1]["content"]
        return {"summary": "ok"}

    monkeypatch.setattr("app.agents.context.summary.complete_json", fake_complete_json)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_enabled", True)
    summarize_incremental("", [{"role": "user", "content": long_msg}])
    # Desired behavior (not yet true): needle reaches summarizer
    assert needle in captured.get("user", "")
