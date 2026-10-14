"""Confirm-card calendar flow: the tool prepares a card and only the API writes."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.tools import calendar_tool
from app.agents.workflow import run_agent_workflow
from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, Message, Session as ChatSession
from app.services.calendar import EventDraft, extract_event, void_draft_schedule_cards
from app.services.calendar import _parse_time_from_message


def _plan():
    return {
        "pending_tools": ["calendar"],
        "needs_freshness": False,
        "needs_kb": False,
        "needs_web": False,
        "needs_calendar": True,
    }


def _cleanup(db, event_ids: list[int]) -> None:
    for event_id in event_ids:
        row = db.get(CalendarEvent, event_id)
        if row is not None:
            db.delete(row)
    db.commit()


def test_parse_tomorrow_afternoon_uses_injected_now():
    parsed = _parse_time_from_message("明天下午三点开会", now=datetime(2026, 8, 6, 23, 30))
    assert parsed == datetime(2026, 8, 7, 15, 0)


def test_extract_defaults_one_hour_and_optional_participants(monkeypatch):
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_a, **_k: {
            "title": "周会",
            "start_at": "2026-08-07T15:00:00",
            "end_at": "",
            "participants": [],
            "missing_fields": ["end_at", "participants"],
        },
    )
    draft = extract_event("明天三点开周会")
    assert draft.title == "周会"
    assert draft.start_at == datetime(2026, 8, 7, 15, 0)
    assert draft.end_at == datetime(2026, 8, 7, 16, 0)
    assert draft.participants == []
    assert "end_at" not in draft.missing_fields
    assert "participants" not in draft.missing_fields


def test_create_phrase_returns_draft_and_writes_nothing():
    draft = EventDraft(
        title="周会",
        start_at=datetime(2026, 8, 7, 15, 0),
        end_at=datetime(2026, 8, 7, 16, 0),
        participants=[],
    )
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch("app.agents.tools.calendar_tool.extract_event", return_value=draft),
        ):
            result = run_agent_workflow(db, message="明天三点开周会", history=[], user_id=1)
        assert result["pending_calendar"] is None
        card = result["schedule_card"]
        assert card["mode"] == "create"
        assert card["phase"] == "draft"
        assert card["title"] == "周会"
        assert card["start_at"].startswith("2026-08-07T15:00:00")
        assert card["end_at"].startswith("2026-08-07T16:00:00")
        assert card["participants"] == []
        assert card["expires_at"]
        assert db.query(CalendarEvent).count() == before
        assert "create_event" not in calendar_tool.execute_calendar.__code__.co_names
    finally:
        db.close()


def test_blank_draft_when_title_and_start_are_missing():
    with (
        patch("app.agents.workflow.plan_tools", return_value=_plan()),
        patch(
            "app.agents.tools.calendar_tool.extract_event",
            return_value=EventDraft(title="", participants=[]),
        ),
    ):
        result = run_agent_workflow(object(), message="帮我约一下", history=[], user_id=1)  # type: ignore[arg-type]
    card = result["schedule_card"]
    assert card["title"] == ""
    assert card["start_at"] == ""
    assert result["pending_calendar"] is None


def test_conflict_preview_offers_slots_without_insert():
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        existing = create_event(
            db,
            user_id=1,
            title="已有会",
            start_at=datetime(2026, 8, 7, 15, 0),
            end_at=datetime(2026, 8, 7, 16, 0),
            participants=[],
        )
        created.append(existing.id)
        draft = EventDraft(
            title="周会",
            start_at=datetime(2026, 8, 7, 15, 0),
            end_at=datetime(2026, 8, 7, 16, 0),
            participants=[],
        )
        before = db.query(CalendarEvent).count()
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch("app.agents.tools.calendar_tool.extract_event", return_value=draft),
        ):
            result = run_agent_workflow(db, message="明天三点开周会", history=[], user_id=1)
        slots = result["schedule_card"]["suggested_slots"]
        assert 1 <= len(slots) <= 3
        assert result["schedule_card"]["conflicts"]
        assert db.query(CalendarEvent).count() == before
    finally:
        _cleanup(db, created)
        db.close()


def test_cancel_one_match_is_a_card_not_a_delete():
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        event = create_event(
            db,
            user_id=1,
            title="面试",
            start_at=datetime(2026, 8, 7, 15, 0),
            end_at=datetime(2026, 8, 7, 16, 0),
            participants=[],
        )
        created.append(event.id)
        draft = EventDraft(title="面试", start_at=datetime(2026, 8, 7, 15, 0), end_at=datetime(2026, 8, 7, 16, 0))
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch("app.agents.tools.calendar_tool.extract_event", return_value=draft),
        ):
            result = run_agent_workflow(db, message="取消明天的面试", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["mode"] == "cancel"
        assert card["event_id"] == event.id
        db.refresh(event)
        assert event.status == "active"
    finally:
        _cleanup(db, created)
        db.close()


def test_cancel_multiple_matches_lists_choices():
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        for hour in (10, 15):
            event = create_event(
                db,
                user_id=1,
                title="周会",
                start_at=datetime(2026, 8, 7, hour, 0),
                end_at=datetime(2026, 8, 7, hour + 1, 0),
                participants=[],
            )
            created.append(event.id)
        draft = EventDraft(title="周会", start_at=datetime(2026, 8, 7, 9, 0))
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch("app.agents.tools.calendar_tool.extract_event", return_value=draft),
        ):
            result = run_agent_workflow(db, message="取消周会", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["mode"] == "choose"
        assert card["action"] == "cancel"
        assert len(card["candidates"]) == 2
        for event_id in created:
            row = db.get(CalendarEvent, event_id)
            assert row is not None and row.status == "active"
    finally:
        _cleanup(db, created)
        db.close()


def test_legacy_pending_still_routes_and_does_not_create(monkeypatch):
    called = {"n": 0}

    def plan_tools(*_a, **_k):
        called["n"] += 1
        return _plan()

    monkeypatch.setattr("app.agents.workflow.plan_tools", plan_tools)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.extract_event",
        lambda *_a, **_k: EventDraft(title="周会", start_at=datetime(2026, 8, 7, 15, 0), end_at=datetime(2026, 8, 7, 16, 0)),
    )
    monkeypatch.setattr("app.agents.tools.calendar_tool.check_conflict", lambda *_a, **_k: [])
    result = run_agent_workflow(
        object(),  # type: ignore[arg-type]
        message="明天三点开周会",
        history=[],
        user_id=1,
        pending_calendar={"title": "旧草稿", "missing_fields": ["end_at", "participants"]},
    )
    assert called["n"] == 1
    assert result["pending_calendar"] is None
    assert result["schedule_card"]["phase"] == "draft"


def test_confirm_api_creates_conflicts_and_clears_participants(auth_client: TestClient):
    init_db()
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200
    owner_id = me.json()["id"]
    missing = auth_client.post("/api/calendar", json={"start_at": "2026-09-02T10:00:00", "user_id": 99999})
    assert missing.status_code == 400

    created = auth_client.post(
        "/api/calendar",
        json={
            "title": "确认会",
            "start_at": "2026-09-02T10:00:00",
            "end_at": "2026-09-02T11:00:00",
            "participants": ["张三"],
            "user_id": 99999,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    db = SessionLocal()
    try:
        row = db.get(CalendarEvent, body["id"])
        assert row is not None
        assert row.user_id == owner_id
        assert row.user_id != 99999
        overlap = auth_client.post(
            "/api/calendar",
            json={
                "title": "撞车",
                "start_at": "2026-09-02T10:30:00",
                "end_at": "2026-09-02T11:30:00",
                "participants": [],
            },
        )
        assert overlap.status_code == 409
        detail = overlap.json()["detail"]
        assert detail["conflicts"]
        assert detail["suggested_slots"]
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "撞车").count() == 0

        renamed = auth_client.patch(f"/api/calendar/{body['id']}", json={"title": "改名会"})
        assert renamed.status_code == 200
        assert renamed.json()["participants"] == ["张三"]
        cleared = auth_client.patch(f"/api/calendar/{body['id']}", json={"participants": []})
        assert cleared.status_code == 200
        assert cleared.json()["participants"] == []
    finally:
        _cleanup(db, [body["id"]])
        db.close()


def test_cross_user_delete_is_404(auth_client: TestClient):
    from app.core.auth import hash_password
    from app.models.models import User

    init_db()
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.external_id == "cal-other").first()
        if other is None:
            other = User(
                external_id="cal-other",
                name="cal-other",
                password_hash=hash_password("x"),
                is_active=1,
            )
            db.add(other)
            db.commit()
            db.refresh(other)
        from app.services.calendar import create_event

        event = create_event(
            db,
            user_id=other.id,
            title="别人的会",
            start_at=datetime(2026, 9, 3, 10, 0),
            end_at=datetime(2026, 9, 3, 11, 0),
            participants=[],
        )
        event_id = event.id
    finally:
        db.close()
    response = auth_client.delete(f"/api/calendar/{event_id}")
    assert response.status_code == 404
    db = SessionLocal()
    try:
        _cleanup(db, [event_id])
    finally:
        db.close()


def test_update_finds_event_on_reference_day_not_target_day(monkeypatch):
    """把周三的周会改到周四三点 searches Wednesday and proposes Thursday."""
    frozen = datetime(2026, 10, 14, 9, 0)  # Wednesday
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_local_now", lambda _db=None: frozen)
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_a, **_k: {
            "title": "周会",
            "start_at": "2026-10-15T03:00:00",
            "end_at": "",
            "participants": [],
            "missing_fields": [],
        },
    )
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        wednesday = create_event(
            db,
            user_id=1,
            title="周会",
            start_at=datetime(2026, 10, 14, 10, 0),
            end_at=datetime(2026, 10, 14, 11, 0),
            participants=[],
        )
        created.append(wednesday.id)
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            result = run_agent_workflow(db, message="把周三的周会改到周四三点", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["mode"] == "update"
        assert card["event_id"] == wednesday.id
        assert card["start_at"].startswith("2026-10-15T03:00:00")
        assert card["end_at"].startswith("2026-10-15T04:00:00")
        db.refresh(wednesday)
        assert wednesday.start_at == datetime(2026, 10, 14, 10, 0)
    finally:
        _cleanup(db, created)
        db.close()


def test_title_only_update_skips_events_that_already_ended(monkeypatch):
    frozen = datetime(2026, 10, 14, 9, 0)
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_local_now", lambda _db=None: frozen)
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_a, **_k: {
            "title": "周会",
            "start_at": "",
            "end_at": "",
            "participants": [],
            "missing_fields": [],
        },
    )
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        past = create_event(
            db,
            user_id=1,
            title="周会",
            start_at=datetime(2026, 10, 13, 10, 0),
            end_at=datetime(2026, 10, 13, 11, 0),
            participants=[],
        )
        upcoming = create_event(
            db,
            user_id=1,
            title="周会",
            start_at=datetime(2026, 10, 15, 10, 0),
            end_at=datetime(2026, 10, 15, 11, 0),
            participants=[],
        )
        created.extend([past.id, upcoming.id])
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            result = run_agent_workflow(db, message="把周会改到四点", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["mode"] == "update"
        assert card["event_id"] == upcoming.id
        assert card["start_at"].startswith("2026-10-14T04:00:00")
    finally:
        _cleanup(db, created)
        db.close()


def test_draft_confirm_conflict_patch_and_delete(auth_client: TestClient):
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200
    owner_id = me.json()["id"]
    draft = EventDraft(
        title="往返会",
        start_at=datetime(2026, 11, 4, 10, 0),
        end_at=datetime(2026, 11, 4, 11, 0),
        participants=["张三"],
    )
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch("app.agents.tools.calendar_tool.extract_event", return_value=draft),
        ):
            prepared = run_agent_workflow(
                db, message="11月4日上午十点开往返会", history=[], user_id=owner_id
            )
        card = prepared["schedule_card"]
        assert card["phase"] == "draft"
        assert card["mode"] == "create"
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "往返会").count() == 0

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
        assert saved.json()["status"] == "active"

        conflict = auth_client.post(
            "/api/calendar",
            json={
                "title": "往返会撞车",
                "start_at": "2026-11-04T10:30:00",
                "end_at": "2026-11-04T11:30:00",
                "participants": [],
            },
        )
        assert conflict.status_code == 409
        detail = conflict.json()["detail"]
        assert detail["conflicts"]
        assert detail["suggested_slots"]
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "往返会撞车").count() == 0

        patched = auth_client.patch(
            f"/api/calendar/{event_id}",
            json={"title": "往返会改期", "start_at": "2026-11-04T14:00:00", "end_at": "2026-11-04T15:00:00"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["title"] == "往返会改期"
        assert patched.json()["participants"] == ["张三"]

        removed = auth_client.delete(f"/api/calendar/{event_id}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["status"] == "cancelled"
    finally:
        _cleanup(db, created)
        db.close()


def test_next_message_voids_draft_card():
    init_db()
    db = SessionLocal()
    try:
        session = ChatSession(user_id=1, title="卡片")
        db.add(session)
        db.commit()
        db.refresh(session)
        card = {"mode": "create", "phase": "draft", "title": "周会"}
        import json

        db.add(
            Message(
                session_id=session.id,
                role="assistant",
                content="请确认",
                schedule_card=json.dumps(card, ensure_ascii=False),
            )
        )
        db.commit()
        assert void_draft_schedule_cards(db, session.id) == 1
        row = db.query(Message).filter(Message.session_id == session.id).one()
        stored = json.loads(row.schedule_card)
        assert stored["phase"] == "cancelled"
        db.delete(row)
        db.delete(session)
        db.commit()
    finally:
        db.close()
