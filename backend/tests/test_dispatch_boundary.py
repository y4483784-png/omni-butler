from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.agents.dispatch import TurnAssignment, assign_turn
from app.agents.workflow import run_agent_workflow
from app.core.db import SessionLocal, init_db
from app.models.models import Session as ChatSession
from app.models.models import User
from app.services.web_search import SearchHit, SearchOutcome

_HALTED = {
    "intent": "chat",
    "forced": False,
    "citations": [],
    "thinking_steps": [],
    "llm_messages": [],
    "schedule_card": None,
    "direct_answer": None,
    "answer_reasoning": None,
    "pending_calendar": None,
    "artifact": None,
    "analysis_summary": None,
    "context_stats": None,
}

_WEB_PLAN = {
    "pending_tools": ["web"],
    "needs_freshness": True,
    "needs_kb": False,
    "needs_web": True,
    "needs_calendar": False,
    "needs_sandbox": False,
}

_CHAT_PLAN = {
    "pending_tools": [],
    "needs_freshness": False,
    "needs_kb": False,
    "needs_web": False,
    "needs_calendar": False,
    "needs_sandbox": False,
}


def _forbid(label: str):
    return MagicMock(side_effect=AssertionError(label))


def _web_assignment() -> TurnAssignment:
    return TurnAssignment.from_plan(_WEB_PLAN, forced=False)


def test_assign_turn_writes_a_calendar_assignment_and_does_not_act():
    def planned(*_args, **_kwargs):
        return {
            "pending_tools": ["calendar"],
            "needs_freshness": False,
            "needs_kb": False,
            "needs_web": False,
            "needs_calendar": True,
            "confidence": "low",
            "reasoning": "可能是在约一个会",
        }

    invoke = _forbid("gateway.invoke")
    claim = _forbid("publish_schedule_claim")
    with (
        patch("app.agents.dispatch.plan_tools", planned),
        patch("app.agents.harness.gateway.invoke", invoke),
        patch("app.services.schedule_confirm.publish_schedule_claim", claim),
    ):
        assignment = assign_turn("帮我约一下", [])
    assert assignment.next_tool == "calendar"
    assert not hasattr(assignment, "confidence")
    assert not hasattr(assignment, "schedule_card")
    assert invoke.call_count == 0
    assert claim.call_count == 0


def test_work_graph_runs_web_once_without_planning():
    from app.agents.harness.critique import CritiqueVerdict

    fake = SearchOutcome(
        results=[SearchHit(title="新闻", url="https://n.example/1", snippet="摘要", media="站")],
        query_used="q",
        count=8,
        recency_filter="oneDay",
        query_rewritten=True,
    )
    verdict = CritiqueVerdict.model_validate(
        {"grounded": True, "addresses_question": True, "unsupported": []}
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("planner ran on the work path")

    init_db()
    db = SessionLocal()
    try:
        with (
            patch("app.services.web_search.httpx.Client") as search,
            patch("app.agents.dispatch.plan_tools", side_effect=boom),
            patch("app.agents.router.plan_tools", side_effect=boom),
            patch("app.agents.workflow.complete_text", return_value="根据证据的简要说明。[1]"),
            patch("app.agents.harness.critique.complete_json_object", return_value=verdict),
            patch("app.agents.harness.critique.settings.llm_api_key", "fake"),
        ):
            result = run_agent_workflow(
                db,
                message="今天有什么 AI 新闻",
                history=[],
                has_kb_docs=False,
                assignment=_web_assignment(),
            )
    finally:
        db.close()
    assert search.call_count == 0
    assert result["intent"] == "web_search"
    assert result["llm_messages"]
    assert result["citations"] == []


def test_stopped_turn_returns_halted_result_without_a_tool():
    invoke = _forbid("gateway.invoke")
    init_db()
    db = SessionLocal()
    try:
        with (
            patch("app.agents.workflow.is_stopped", return_value=True),
            patch("app.agents.harness.gateway.invoke", invoke),
        ):
            result = run_agent_workflow(
                db,
                message="今天有什么 AI 新闻",
                history=[],
                assignment=_web_assignment(),
            )
    finally:
        db.close()
    assert result == _HALTED
    assert invoke.call_count == 0


def _admin_id() -> int:
    init_db()
    db = SessionLocal()
    try:
        return db.query(User).filter(User.external_id == "admin").one().id
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="边界")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def test_chat_reply_uses_the_assignment_and_skips_tools(plain_client: TestClient):
    from app.core.llm import AnswerDelta

    def chat_assignment(*_args, **_kwargs):
        return TurnAssignment.from_plan(_CHAT_PLAN, forced=False)

    async def one_token(*_args, **_kwargs):
        yield AnswerDelta("ZXQOK")

    invoke = _forbid("gateway.invoke")
    claim = _forbid("publish_schedule_claim")
    with (
        patch("app.api.chat.assign_turn", chat_assignment),
        patch("app.api.chat.stream_chat", side_effect=one_token),
        patch("app.agents.harness.gateway.invoke", invoke),
        patch("app.api.chat.publish_schedule_claim", claim),
        patch("app.services.schedule_confirm.publish_schedule_claim", claim),
    ):
        response = plain_client.post(
            "/api/chat",
            json={"session_id": _session(_admin_id()), "message": "hello"},
        )
    assert response.status_code == 200
    assert "ZXQOK" in response.text
    assert invoke.call_count == 0
    assert claim.call_count == 0


def test_assign_turn_prefers_router_history_and_passes_context_line():
    history = [{"role": "user", "content": "完整历史"}]
    router_history = [{"role": "user", "content": "路由切片"}]
    seen: dict = {}

    def planned(message, hist, **kwargs):
        seen["message"] = message
        seen["history"] = list(hist)
        seen["context_line"] = kwargs.get("context_line")
        return dict(_CHAT_PLAN)

    with patch("app.agents.dispatch.plan_tools", planned):
        assign_turn(
            "现在呢",
            history,
            context={"router_history": router_history, "context_line": "摘要：预算"},
        )
    assert seen["history"] == router_history
    assert seen["history"] != history
    assert seen["context_line"] == "摘要：预算"


def test_confidence_and_reasoning_are_not_part_of_the_assignment():
    shared = {
        "pending_tools": ["calendar", "web"],
        "needs_freshness": False,
        "needs_kb": False,
        "needs_web": True,
        "needs_calendar": True,
        "needs_sandbox": False,
    }
    low = {**shared, "confidence": "low", "reasoning": "也许要查日程再搜"}
    high = {**shared, "confidence": "high", "reasoning": "先日程后搜索"}
    left = TurnAssignment.from_plan(low, forced=False)
    right = TurnAssignment.from_plan(high, forced=False)
    assert left == right
    assert not hasattr(left, "schedule_card")
    assert not hasattr(right, "schedule_card")
    assert not hasattr(left, "confidence")
    assert not hasattr(right, "reasoning")
