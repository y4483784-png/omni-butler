"""Multi-turn calendar dialogue: draft, confirm, list, move, conflict, cancel, void."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.workflow import run_agent_workflow
from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, Message, Session as ChatSession
from app.services.calendar import create_event, void_draft_schedule_cards

_FROZEN = datetime(2026, 10, 14, 9, 0)  # Wednesday
_PLAN = {
    "pending_tools": ["calendar"],
    "needs_freshness": False,
    "needs_kb": False,
    "needs_web": False,
    "needs_calendar": True,
}


def _fake_extract(messages, *args, **kwargs):
    text = messages[-1]["content"]
    if "改到周五三点" in text:
        return {
            "title": "对话评审",
            "start_at": "2026-10-16T03:00:00",
            "end_at": "",
            "participants": [],
            "missing_fields": [],
        }
    if "取消周五" in text or "取消周四" in text:
        start = "2026-10-16T03:00:00" if "周五" in text else "2026-10-15T10:00:00"
        return {
            "title": "对话评审",
            "start_at": start,
            "end_at": "",
            "participants": [],
            "missing_fields": [],
        }
    if "明天三点开对话评审" in text:
        return {
            "title": "对话评审",
            "start_at": "2026-10-15T15:00:00",
            "end_at": "",
            "participants": [],
            "missing_fields": [],
        }
    return {"title": "", "start_at": "", "end_at": "", "participants": [], "missing_fields": []}


def _cleanup(db, event_ids: list[int]) -> None:
    for event_id in event_ids:
        row = db.get(CalendarEvent, event_id)
        if row is not None:
            db.delete(row)
    db.commit()


def _say(db, user_id: int, message: str) -> dict:
    with patch("app.agents.workflow.plan_tools", return_value=_PLAN):
        return run_agent_workflow(db, message=message, history=[], user_id=user_id)


def test_dialogue_create_list_move_conflict_and_cancel(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: _FROZEN)
    monkeypatch.setattr("app.services.calendar.complete_json", _fake_extract)
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200
    owner_id = me.json()["id"]
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        drafted = _say(db, owner_id, "明天三点开对话评审")
        card = drafted["schedule_card"]
        assert card["mode"] == "create"
        assert card["phase"] == "draft"
        assert card["title"] == "对话评审"
        assert card["start_at"].startswith("2026-10-15T15:00:00")
        assert card["end_at"].startswith("2026-10-15T16:00:00")
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "对话评审").count() == 0

        saved = auth_client.post(
            "/api/calendar",
            json={
                "title": card["title"],
                "start_at": card["start_at"],
                "end_at": card["end_at"],
                "participants": card["participants"],
            },
        )
        assert saved.status_code == 200, saved.text
        event_id = saved.json()["id"]
        created.append(event_id)

        listed = _say(db, owner_id, "这周有哪些会")
        assert listed["schedule_card"]["mode"] == "list"
        listed_row = listed["schedule_card"]["items"][0]
        assert listed_row["title"] == "对话评审"
        assert listed_row["start_at"].startswith("2026-10-15T15:00:00")

        moved = _say(db, owner_id, "把周四的对话评审改到周五三点")
        move_card = moved["schedule_card"]
        assert move_card["mode"] == "update"
        assert move_card["event_id"] == event_id
        assert move_card["start_at"].startswith("2026-10-16T03:00:00")
        row = db.get(CalendarEvent, event_id)
        assert row is not None and row.start_at == datetime(2026, 10, 15, 15, 0)

        patched = auth_client.patch(
            f"/api/calendar/{event_id}",
            json={"start_at": move_card["start_at"], "end_at": move_card["end_at"]},
        )
        assert patched.status_code == 200, patched.text
        db.refresh(row)
        assert row.start_at == datetime(2026, 10, 16, 3, 0)

        conflict = auth_client.post(
            "/api/calendar",
            json={
                "title": "对话撞车",
                "start_at": "2026-10-16T03:30:00",
                "end_at": "2026-10-16T04:30:00",
                "participants": [],
            },
        )
        assert conflict.status_code == 409
        detail = conflict.json()["detail"]
        assert detail["conflicts"]
        assert 1 <= len(detail["suggested_slots"]) <= 3
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "对话撞车").count() == 0

        cancel = _say(db, owner_id, "取消周五的对话评审")
        assert cancel["schedule_card"]["mode"] == "cancel"
        assert cancel["schedule_card"]["event_id"] == event_id
        db.refresh(row)
        assert row.status == "active"

        removed = auth_client.delete(f"/api/calendar/{event_id}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["status"] == "cancelled"
        after = _say(db, owner_id, "这周有哪些会")
        assert "对话评审" not in after["direct_answer"]
    finally:
        _cleanup(db, created)
        db.close()


def test_dialogue_duplicate_titles_and_void_on_next_message(monkeypatch):
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: _FROZEN)
    monkeypatch.setattr("app.services.calendar.complete_json", _fake_extract)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        for hour in (10, 15):
            event = create_event(
                db,
                user_id=1,
                title="对话评审",
                start_at=datetime(2026, 10, 15, hour, 0),
                end_at=datetime(2026, 10, 15, hour + 1, 0),
                participants=[],
            )
            created.append(event.id)
        choose = _say(db, 1, "取消周四的对话评审")
        card = choose["schedule_card"]
        assert card is None or card.get("mode") != "choose"
        for event_id in created:
            row = db.get(CalendarEvent, event_id)
            assert row is not None and row.status == "active"

        before = db.query(CalendarEvent).count()
        drafted = _say(db, 1, "明天三点开对话评审")
        assert drafted["schedule_card"]["phase"] == "draft"
        session = ChatSession(user_id=1, title="对话")
        db.add(session)
        db.commit()
        db.refresh(session)
        message = Message(
            session_id=session.id,
            role="assistant",
            content=drafted["direct_answer"],
            schedule_card=json.dumps(drafted["schedule_card"], ensure_ascii=False),
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        assert void_draft_schedule_cards(db, session.id) == 1
        db.refresh(message)
        stored = json.loads(message.schedule_card)
        assert stored["phase"] == "cancelled"
        assert db.query(CalendarEvent).count() == before
        db.delete(message)
        db.delete(session)
        db.commit()
    finally:
        _cleanup(db, created)
        db.close()
