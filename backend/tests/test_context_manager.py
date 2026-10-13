"""ContextManager unit tests (budget, window, assemble, summary, router/calendar clips)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.context.assembler import ContextBundle, build_context, compose_answer_messages
from app.agents.context.budget import TokenBudget, estimate_tokens
from app.agents.context.summary import invalidate_summary_if_needed, refresh_session_context
from app.agents.context.window import (
    clip_history_for_prompt,
    flatten_turns,
    recent_turns,
    to_logical_turns,
)
from app.core.prompts import plan_user
from app.models.models import Message, Session as ChatSession
from app.services import calendar as calendar_svc


def test_estimate_tokens_cjk_vs_latin(monkeypatch):
    monkeypatch.setattr("app.agents.context.budget.settings.context_cjk_chars_per_token", 1.6)
    monkeypatch.setattr("app.agents.context.budget.settings.context_latin_chars_per_token", 4.0)
    cjk = estimate_tokens("你好世界")  # 4 chars / 1.6 → 3
    latin = estimate_tokens("abcd")  # 4 / 4 → 1
    assert cjk >= latin
    assert cjk >= 2
    assert latin == 1


def test_budget_levels(monkeypatch):
    monkeypatch.setattr("app.agents.context.budget.settings.context_max_tokens", 1000)
    monkeypatch.setattr("app.agents.context.budget.settings.context_reserve_ratio", 0.25)
    monkeypatch.setattr("app.agents.context.budget.settings.context_warn_ratio", 0.70)
    monkeypatch.setattr("app.agents.context.budget.settings.context_compact_ratio", 0.85)
    monkeypatch.setattr("app.agents.context.budget.settings.context_emergency_ratio", 0.95)
    b = TokenBudget.from_settings()
    usable = b.usable  # 750
    assert b.level(0) == "safe"
    assert b.level(int(usable * 0.71)) == "warn"
    assert b.level(int(usable * 0.86)) == "compact"
    assert b.level(int(usable * 0.96)) == "emergency"


def test_logical_turns_start_on_user_and_drop_orphan_assistant():
    hist = [
        {"role": "assistant", "content": "orphan"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    turns = to_logical_turns(hist)
    assert len(turns) == 2
    assert turns[0][0]["content"] == "u1"
    assert turns[1][0]["content"] == "u2"


def test_recent_turns_overflow_split():
    turns = [[{"role": "user", "content": f"u{i}"}] for i in range(5)]
    kept, overflow = recent_turns(turns, 2)
    assert len(kept) == 2
    assert len(overflow) == 3
    assert kept[0][0]["content"] == "u3"


def test_plain_chat_without_blocks_keeps_no_system(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", 32000)
    hist = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]
    msgs, _, _ = compose_answer_messages(
        bundle=ContextBundle(answer_history=hist),
        hist=hist,
        message="今天怎么样",
        pool_text="",
        memory_block="",
        has_evidence_system=False,
    )
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["content"] == "今天怎么样"
    assert all(m["role"] != "system" for m in msgs)


def test_summary_and_working_blocks_go_into_system_before_history(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", 32000)
    hist = [{"role": "user", "content": "继续"}, {"role": "assistant", "content": "好"}]
    bundle = ContextBundle(
        answer_history=hist,
        summary_block="【会话摘要】曾约王总开会",
        working_block="【当前工作状态】\n- 待补日程",
    )
    msgs, _, _ = compose_answer_messages(
        bundle=bundle,
        hist=hist,
        message="改到周五",
        pool_text="规则\n\n【证据池】\nx",
        memory_block="",
        has_evidence_system=True,
    )
    assert msgs[0]["role"] == "system"
    assert "【会话摘要】" in msgs[0]["content"]
    assert "【当前工作状态】" in msgs[0]["content"]
    assert msgs[-1]["role"] == "user"


def test_memory_still_injected_via_inject_system_memory(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", 32000)
    hist = [{"role": "user", "content": "hi"}]
    msgs, _, _ = compose_answer_messages(
        bundle=ContextBundle(answer_history=hist, summary_block="【会话摘要】x"),
        hist=hist,
        message="ok",
        pool_text="pool",
        memory_block="【长期记忆】叫我小陈",
        has_evidence_system=True,
    )
    assert msgs[0]["role"] == "system"
    assert "【长期记忆】" in msgs[0]["content"]


def test_compose_emergency_keeps_system_and_latest_user(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_max_tokens", 80)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_reserve_ratio", 0.0)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_warn_ratio", 0.1)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_compact_ratio", 0.2)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_emergency_ratio", 0.3)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_pool_chars_warn", 50)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_pool_chars_emergency", 20)

    hist = []
    for i in range(6):
        hist.append({"role": "user", "content": f"用户问题很长{i}" + ("内容" * 20)})
        hist.append({"role": "assistant", "content": f"助手回答很长{i}" + ("答案" * 20)})

    def rebuild(n: int):
        return f"pool-{n}", [{"index": 1, "source_type": "kb", "filename": "a"}]

    msgs, pool, included = compose_answer_messages(
        bundle=ContextBundle(
            answer_history=hist,
            summary_block="【会话摘要】旧事",
            working_block="【当前工作状态】\n- x",
        ),
        hist=hist,
        message="最新问题",
        pool_text="巨大证据池" * 50,
        memory_block="【长期记忆】" + ("偏好" * 40),
        rebuild_pool=rebuild,
        has_evidence_system=True,
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "最新问题"
    user_turns = [m for m in msgs if m["role"] == "user" and m is not msgs[-1]]
    assert len(user_turns) <= 1
    assert included or pool.startswith("pool-")


def test_build_context_views(monkeypatch):
    monkeypatch.setattr("app.agents.context.assembler.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.assembler.settings.max_context_turns", 2)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_router_turns", 1)
    monkeypatch.setattr("app.agents.context.assembler.settings.context_tool_turns", 2)
    hist = []
    for i in range(4):
        hist.append({"role": "user", "content": f"u{i}"})
        hist.append({"role": "assistant", "content": f"a{i}"})
    session = SimpleNamespace(context_summary="旧摘要", working_state="")
    bundle = build_context(
        session=session,
        history=hist,
        message="now",
        pending_calendar={"title": "周会", "missing_fields": ["end_at"]},
        memory_block="",
    )
    assert "【会话摘要】" in bundle.summary_block
    assert "旧摘要" in bundle.summary_block
    assert bundle.context_line
    assert len(to_logical_turns(bundle.answer_history)) == 2
    assert len(to_logical_turns(bundle.router_history)) == 1
    assert bundle.stats["dropped_turns"] == 2


def test_invalidate_summary_if_needed():
    session = SimpleNamespace(context_summary="x", context_summary_upto_message_id=50)
    assert invalidate_summary_if_needed(session, 40) is True
    assert session.context_summary == ""
    assert session.context_summary_upto_message_id == 0
    session.context_summary = "y"
    session.context_summary_upto_message_id = 10
    assert invalidate_summary_if_needed(session, 20) is False
    assert session.context_summary == "y"


def test_refresh_only_summarizes_overflow_beyond_upto_id(monkeypatch):
    calls = {"n": 0}

    def fake_complete_json(messages, model=None):
        calls["n"] += 1
        return {"summary": f"摘要{calls['n']}"}

    monkeypatch.setattr("app.agents.context.summary.complete_json", fake_complete_json)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.max_context_turns", 2)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_min_overflow_turns", 2)

    db = MagicMock()
    session = ChatSession(id=1, user_id=1, title="t")
    session.context_summary = ""
    session.context_summary_upto_message_id = 0
    session.working_state = ""
    db.get.return_value = session

    rows = []
    for i in range(1, 9):
        role = "user" if i % 2 == 1 else "assistant"
        m = Message(id=i, session_id=1, role=role, content=f"c{i}")
        rows.append(m)
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = rows
    db.query.return_value = q

    refresh_session_context(db, session_id=1, user_id=1, citations=[{"source_type": "kb", "filename": "a.pdf"}])
    assert calls["n"] == 1
    assert session.context_summary.startswith("摘要")
    first_upto = session.context_summary_upto_message_id

    # Second call with same messages: no new overflow beyond upto → no LLM
    refresh_session_context(db, session_id=1, user_id=1)
    assert calls["n"] == 1
    assert session.context_summary_upto_message_id == first_upto


def test_summary_llm_failure_keeps_previous_summary(monkeypatch):
    monkeypatch.setattr("app.agents.context.summary.complete_json", lambda *a, **k: None)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.context_manager_enabled", True)
    monkeypatch.setattr("app.agents.context.summary.settings.max_context_turns", 1)
    monkeypatch.setattr("app.agents.context.summary.settings.context_summary_min_overflow_turns", 1)

    db = MagicMock()
    session = ChatSession(id=1, user_id=1, title="t")
    session.context_summary = "保留我"
    session.context_summary_upto_message_id = 0
    session.working_state = ""
    db.get.return_value = session
    rows = [
        Message(id=1, session_id=1, role="user", content="旧"),
        Message(id=2, session_id=1, role="assistant", content="旧答"),
        Message(id=3, session_id=1, role="user", content="新"),
        Message(id=4, session_id=1, role="assistant", content="新答"),
    ]
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = rows
    db.query.return_value = q

    refresh_session_context(db, session_id=1, user_id=1)
    assert session.context_summary == "保留我"


def test_plan_user_context_line_and_cache_key_follow_prompt_text():
    text = plan_user(
        "再算中位数",
        [
            {"role": "user", "content": "分析这张表"},
            {"role": "assistant", "content": "已算完"},
        ],
        turns=2,
        max_chars=120,
        context_line="刚分析过「销售.xlsx」",
    )
    assert text.startswith("工作状态：刚分析过")
    assert "当前提问：再算中位数" in text


def test_extract_event_history_clipped(monkeypatch):
    captured = {}

    def fake_json(messages, model=None):
        captured["messages"] = messages
        return {
            "title": "会",
            "start_at": "2026-09-06T10:00:00",
            "end_at": "2026-09-06T11:00:00",
            "participants": [],
            "missing_fields": [],
        }

    monkeypatch.setattr(calendar_svc, "complete_json", fake_json)
    long_hist = [
        {"role": "user", "content": ("很长" * 200)},
        {"role": "assistant", "content": ("回复" * 200)},
    ] * 6
    calendar_svc.extract_event("明天下午三点开会", long_hist)
    user_prompt = captured["messages"][1]["content"]
    assert "最近对话：" in user_prompt
    # clipped dump should not contain full 200*2*「很长」 repeats unbounded
    import json
    import re

    m = re.search(r"最近对话：(.*?)\n当前消息：", user_prompt, re.S)
    assert m
    dumped = json.loads(m.group(1))
    assert len(dumped) <= 8
    for item in dumped:
        assert len(item["content"]) <= 300


def test_clip_history_for_prompt_limits():
    hist = [{"role": "user", "content": "x" * 500} for _ in range(20)]
    out = clip_history_for_prompt(hist, max_msgs=8, max_chars=300)
    assert len(out) == 8
    assert all(len(m["content"]) <= 300 for m in out)


def test_flatten_turns_roundtrip():
    turns = [[{"role": "user", "content": "a"}], [{"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}]]
    flat = flatten_turns(turns)
    assert [m["content"] for m in flat] == ["a", "b", "c"]
