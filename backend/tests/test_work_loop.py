from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.agents.dispatch import TurnAssignment, assign_turn
from app.agents.harness.critique import CritiqueVerdict
from app.agents.harness.effects import decide_replay as real_decide_replay
from app.agents.harness.types import ToolResult, UnknownWrite
from app.agents.workflow import run_agent_workflow
from app.core.db import SessionLocal, init_db
from app.core.llm import AnswerDelta
from app.models.models import CalendarEvent
from app.models.models import Session as ChatSession
from app.models.models import User
from app.services.schedule_confirm import CONFIRM_DETAIL

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

_CALENDAR_PLAN = {
    "pending_tools": ["calendar", "calendar"],
    "needs_freshness": False,
    "needs_kb": False,
    "needs_web": False,
    "needs_calendar": True,
    "needs_sandbox": False,
}

_KB_PLAN = {
    "pending_tools": ["kb"],
    "needs_freshness": False,
    "needs_kb": True,
    "needs_web": False,
    "needs_calendar": False,
    "needs_sandbox": False,
}

_KB_TOKEN = "库内片段已核对"

_GROUNDING_PASS = CritiqueVerdict.model_validate(
    {"grounded": True, "addresses_question": True, "unsupported": []}
)


def _forbid(label: str):
    return MagicMock(side_effect=AssertionError(label))


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
        row = ChatSession(user_id=user_id, title="工作循环")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def test_stop_ends_the_loop_without_a_tool():
    invoke = _forbid("gateway.invoke")
    replay = _forbid("decide_replay")
    init_db()
    db = SessionLocal()
    try:
        with (
            patch("app.agents.workflow.is_stopped", return_value=True),
            patch("app.agents.harness.gateway.invoke", invoke),
            patch("app.agents.workflow.decide_replay", replay),
        ):
            result = run_agent_workflow(
                db,
                message="今天有什么 AI 新闻",
                history=[],
                assignment=TurnAssignment.from_plan(_WEB_PLAN, forced=False),
            )
    finally:
        db.close()
    assert result == _HALTED
    assert invoke.call_count == 0
    assert replay.call_count == 0


def test_stop_after_the_graph_starts_does_not_call_a_tool():
    seen = {"n": 0}

    def stopped() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    invoke = _forbid("gateway.invoke")
    init_db()
    db = SessionLocal()
    try:
        with (
            patch("app.agents.workflow.is_stopped", side_effect=stopped),
            patch("app.agents.harness.gateway.invoke", invoke),
        ):
            result = run_agent_workflow(
                db,
                message="今天有什么 AI 新闻",
                history=[],
                assignment=TurnAssignment.from_plan(_WEB_PLAN, forced=False),
            )
    finally:
        db.close()
    assert result == _HALTED
    assert invoke.call_count == 0
    assert seen["n"] > 1


def test_unknown_write_does_not_insert_again():
    card = {
        "mode": "create",
        "phase": "draft",
        "title": "周会",
        "start_at": "2026-08-06T15:00:00",
    }
    payload = ToolResult(
        ok=True,
        schedule_card=card,
        direct_answer="请确认日程「周会」，时间 15:00。未确认不会写入。",
        unknown_write=UnknownWrite(
            effect="non_replayable_write",
            external_id="req-1",
            prior_external_id="req-1",
            attempt=1,
        ),
    )
    calls = {"n": 0}

    def invoke(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("gateway.invoke")
        return payload

    seen: list[tuple] = []

    def spy(effect, *, attempt, external_id, prior_external_id):
        seen.append((effect, attempt, external_id, prior_external_id))
        return real_decide_replay(
            effect,
            attempt=attempt,
            external_id=external_id,
            prior_external_id=prior_external_id,
        )

    class LookupOnly:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def lookup_inserted_event(self, external_id: str):
            self.calls.append(external_id)
            return {"id": "evt-1"}

    lookup = LookupOnly()
    create_event = _forbid("create_event")
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        with (
            patch("app.agents.harness.gateway.invoke", invoke),
            patch("app.agents.workflow.decide_replay", spy),
            patch("app.agents.workflow.calendar_provider_for", lambda *_a, **_k: lookup),
            patch("app.services.calendar.create_event", create_event),
        ):
            result = run_agent_workflow(
                db,
                message="明天三点开周会",
                history=[],
                assignment=TurnAssignment.from_plan(_CALENDAR_PLAN, forced=False),
            )
        after = db.query(CalendarEvent).count()
    finally:
        db.close()
    assert calls["n"] == 1
    assert lookup.calls == ["req-1"]
    assert seen == [("non_replayable_write", 1, "req-1", "req-1")]
    assert seen[0][1] > 0
    assert create_event.call_count == 0
    assert after == before
    assert result["schedule_card"] is None
    assert result["direct_answer"] == CONFIRM_DETAIL["unknown"]
    assert result["llm_messages"] == []
    assert any("不再写入" in step for step in result["thinking_steps"])
    assert "step_outcome" not in result


def test_send_choice_does_not_look_up_or_insert():
    payload = ToolResult(
        ok=True,
        unknown_write=UnknownWrite(
            effect="non_replayable_write",
            external_id="req-send",
            prior_external_id="req-send",
            attempt=1,
        ),
    )
    calls = {"n": 0}

    def invoke(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("gateway.invoke")
        return payload

    lookup = _forbid("lookup_inserted_event")
    create_event = _forbid("create_event")
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        with (
            patch("app.agents.harness.gateway.invoke", invoke),
            patch("app.agents.workflow.decide_replay", return_value="send"),
            patch("app.agents.workflow.calendar_provider_for", lambda *_a, **_k: lookup),
            patch("app.services.calendar.create_event", create_event),
        ):
            result = run_agent_workflow(
                db,
                message="明天三点开周会",
                history=[],
                assignment=TurnAssignment.from_plan(
                    {**_CALENDAR_PLAN, "pending_tools": ["calendar", "calendar"]},
                    forced=False,
                ),
            )
        after = db.query(CalendarEvent).count()
    finally:
        db.close()
    assert calls["n"] == 1
    assert lookup.call_count == 0
    assert create_event.call_count == 0
    assert after == before
    assert result["schedule_card"] is None
    assert result["direct_answer"] == CONFIRM_DETAIL["unknown"]
    assert result["llm_messages"] == []


def test_assign_turn_does_not_invoke_or_publish():
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


def test_kb_answer_has_no_confirm_card(plain_client: TestClient):
    class Chunk:
        filename = "方案.md"
        snippet = "流程"
        content = "流程说明"
        heading = ""
        page = None

    def kb_assignment(*_args, **_kwargs):
        return TurnAssignment.from_plan(_KB_PLAN, forced=False)

    async def one_token(*_args, **_kwargs):
        yield AnswerDelta(_KB_TOKEN)

    claim = _forbid("publish_schedule_claim")
    with (
        patch("app.api.chat.assign_turn", kb_assignment),
        patch("app.agents.tools.kb.retrieve", return_value=[Chunk()]),
        patch("app.agents.workflow.complete_text", return_value=_KB_TOKEN),
        patch("app.agents.harness.critique.complete_json_object", return_value=_GROUNDING_PASS),
        patch("app.api.chat.stream_chat", side_effect=one_token),
        patch("app.api.chat.publish_schedule_claim", claim),
        patch("app.services.schedule_confirm.publish_schedule_claim", claim),
        patch("app.api.chat.generate_session_title", return_value=""),
    ):
        response = plain_client.post(
            "/api/chat",
            json={"session_id": _session(_admin_id()), "message": "方案里的流程是什么"},
        )
    assert response.status_code == 200
    assert '"type": "token"' in response.text
    assert _KB_TOKEN in response.text
    assert '"type": "schedule_card"' not in response.text
    assert claim.call_count == 0
