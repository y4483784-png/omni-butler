"""Confirm-card calendar flow: the tool prepares a card and only the API writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.harness.types import ToolContext
from app.agents.tools import calendar_tool
from app.agents.tools.calendar_tool import execute_calendar
from app.agents.workflow import run_agent_workflow
from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, Message, Session as ChatSession, User
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


def test_draft_expires_at_emits_utc_offset_for_naive_now():
    """Naive now is the container clock. The stamp must still carry +00:00."""
    from app.services.calendar import draft_expires_at

    naive = datetime(2026, 10, 15, 6, 12, 45, 123456)
    assert draft_expires_at(naive) == "2026-10-15T06:42:45+00:00"

    shanghai = timezone(timedelta(hours=8))
    aware = datetime(2026, 10, 15, 14, 12, 45, tzinfo=shanghai)
    assert draft_expires_at(aware) == "2026-10-15T06:42:45+00:00"

    stamped = datetime.fromisoformat(draft_expires_at())
    assert stamped.utcoffset() == timedelta(0)
    remaining = stamped - datetime.now(timezone.utc)
    assert timedelta(minutes=29) < remaining < timedelta(minutes=31)


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
        expires = datetime.fromisoformat(card["expires_at"])
        assert expires.utcoffset() == timedelta(0)
        remaining = expires - datetime.now(timezone.utc)
        assert timedelta(minutes=29) < remaining < timedelta(minutes=31)
        assert db.query(CalendarEvent).count() == before
        assert "create_event" not in calendar_tool.execute_calendar.__code__.co_names
    finally:
        db.close()


def test_blank_draft_when_title_and_start_are_missing():
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch(
                "app.agents.tools.calendar_tool.extract_event",
                return_value=EventDraft(title="", participants=[]),
            ),
        ):
            result = run_agent_workflow(db, message="帮我约一下", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["title"] == ""
        assert card["start_at"] == ""
        assert result["pending_calendar"] is None
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


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
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        result = run_agent_workflow(
            db,
            message="明天三点开周会",
            history=[],
            user_id=1,
            pending_calendar={"title": "旧草稿", "missing_fields": ["end_at", "participants"]},
        )
        assert called["n"] == 1
        assert result["pending_calendar"] is None
        assert result["schedule_card"]["phase"] == "draft"
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


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
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)
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
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)
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


def test_list_is_read_only_text(monkeypatch):
    frozen = datetime(2026, 10, 14, 9, 0)
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        event = create_event(
            db,
            user_id=1,
            title="周会",
            start_at=datetime(2026, 10, 15, 10, 0),
            end_at=datetime(2026, 10, 15, 11, 0),
            participants=[],
        )
        created.append(event.id)
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            result = run_agent_workflow(db, message="这周有哪些会", history=[], user_id=1)
        assert "周会" in result["direct_answer"]
        assert "2026-10-15 10:00" in result["direct_answer"]
        assert result["schedule_card"] is None
        assert result["pending_calendar"] is None
    finally:
        _cleanup(db, created)
        db.close()


def test_suggest_returns_slot_card_without_writing(monkeypatch):
    frozen = datetime(2026, 10, 14, 9, 0)
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            result = run_agent_workflow(db, message="明天下午有空吗", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["mode"] == "suggest"
        assert card["phase"] == "info"
        assert card["suggested_slots"]
        assert "2026-10-15" in result["direct_answer"]
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


def test_unconfirmed_create_then_reschedule_does_not_move_the_event(monkeypatch):
    frozen = datetime(2026, 10, 14, 9, 0)
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)
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

        event = create_event(
            db,
            user_id=1,
            title="周会",
            start_at=datetime(2026, 10, 16, 10, 0),
            end_at=datetime(2026, 10, 16, 11, 0),
            participants=[],
        )
        created.append(event.id)
        with (
            patch("app.agents.workflow.plan_tools", return_value=_plan()),
            patch(
                "app.agents.tools.calendar_tool.extract_event",
                return_value=EventDraft(
                    title="新会",
                    start_at=datetime(2026, 10, 16, 15, 0),
                    end_at=datetime(2026, 10, 16, 16, 0),
                ),
            ),
        ):
            drafted = run_agent_workflow(db, message="明天下午三点开新会", history=[], user_id=1)
        assert drafted["schedule_card"]["phase"] == "draft"
        session = ChatSession(user_id=1, title="改期")
        db.add(session)
        db.commit()
        db.refresh(session)
        import json

        db.add(
            Message(
                session_id=session.id,
                role="assistant",
                content=drafted["direct_answer"],
                schedule_card=json.dumps(drafted["schedule_card"], ensure_ascii=False),
            )
        )
        db.commit()
        assert void_draft_schedule_cards(db, session.id) == 1
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            moved = run_agent_workflow(db, message="把周会改到四点", history=[], user_id=1)
        assert moved["schedule_card"]["mode"] == "update"
        assert moved["schedule_card"]["event_id"] == event.id
        db.refresh(event)
        assert event.start_at == datetime(2026, 10, 16, 10, 0)
        db.query(Message).filter(Message.session_id == session.id).delete()
        db.delete(session)
        db.commit()
    finally:
        _cleanup(db, created)
        db.close()


def test_confirmed_event_shows_up_in_list(auth_client: TestClient, monkeypatch):
    frozen = datetime(2026, 10, 14, 9, 0)
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)
    me = auth_client.get("/api/auth/me")
    owner_id = me.json()["id"]
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        saved = auth_client.post(
            "/api/calendar",
            json={
                "title": "已确认周会",
                "start_at": "2026-10-15T10:00:00",
                "end_at": "2026-10-15T11:00:00",
                "participants": [],
            },
        )
        assert saved.status_code == 200, saved.text
        created.append(saved.json()["id"])
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            listed = run_agent_workflow(db, message="这周有哪些会", history=[], user_id=owner_id)
        assert "已确认周会" in listed["direct_answer"]
        assert "10:00" in listed["direct_answer"]
    finally:
        _cleanup(db, created)
        db.close()


def _capture_extract(monkeypatch, payload: dict):
    captured: dict = {}

    def fake_json(messages, model=None):
        captured["messages"] = messages
        return payload

    monkeypatch.setattr("app.services.calendar.complete_json", fake_json)
    return captured


def _prompt_text(captured: dict) -> str:
    return "\n".join(str(item.get("content") or "") for item in captured["messages"])


def test_connected_extract_prompt_names_today_and_iana_zone(monkeypatch):
    """The model sees the account day and IANA zone, and its JSON is the card."""
    captured = _capture_extract(
        monkeypatch,
        {
            "title": "测试会议",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "",
            "participants": ["Grok测试"],
            "missing_fields": [],
        },
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: datetime(2026, 10, 15, 9, 0),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.account_timezone_name",
        lambda *_args, **_kwargs: "Asia/Singapore",
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_writes_remote",
        lambda *_args, **_kwargs: False,
    )
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(
                db=db,
                message="明天下午三点开个测试会议 Grok测试",
                history=[],
                user_id=1,
            )
        )
    finally:
        db.close()
    prompt = _prompt_text(captured)
    assert "2026-10-15" in prompt
    assert "Asia/Singapore" in prompt
    card = result.schedule_card
    assert card["title"] == "测试会议"
    assert card["participants"] == ["Grok测试"]
    assert card["start_at"] == "2026-10-16T15:00:00"
    assert card["end_at"] == "2026-10-16T16:00:00"
    assert "+" not in card["start_at"] and "Z" not in card["start_at"]
    assert "+" not in card["end_at"] and "Z" not in card["end_at"]
    assert "已有日程的名称" not in prompt


def test_unconnected_extract_prompt_has_date_and_no_invented_zone(monkeypatch):
    captured = _capture_extract(
        monkeypatch,
        {
            "title": "",
            "start_at": "",
            "end_at": "",
            "participants": [],
            "missing_fields": ["start_at"],
        },
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: datetime(2026, 10, 15, 9, 0),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.account_timezone_name",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_writes_remote",
        lambda *_args, **_kwargs: False,
    )
    init_db()
    db = SessionLocal()
    try:
        execute_calendar(
            ToolContext(
                db=db,
                message="明天下午三点开会",
                history=[],
                user_id=1,
            )
        )
    finally:
        db.close()
    prompt = _prompt_text(captured)
    assert "2026-10-15" in prompt
    assert "时区是" not in prompt
    assert "Asia/Singapore" not in prompt
    assert "Etc/" not in prompt
    scrubbed = prompt.replace("start_at/end_at", "")
    assert "/" not in scrubbed


def test_update_and_cancel_prompt_asks_for_the_existing_title(monkeypatch):
    """Update and cancel ask for the event's name. Create does not get that sentence."""
    captured = _capture_extract(
        monkeypatch,
        {
            "title": "Grok测试会议",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "",
            "participants": ["Grok测试"],
            "missing_fields": [],
        },
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: datetime(2026, 10, 15, 9, 0),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.account_timezone_name",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_writes_remote",
        lambda *_args, **_kwargs: False,
    )
    init_db()
    db = SessionLocal()
    try:
        execute_calendar(
            ToolContext(db=db, message="取消Grok测试会议", history=[], user_id=1)
        )
        cancel_prompt = _prompt_text(captured)
        execute_calendar(
            ToolContext(db=db, message="把测试会议改到后天下午四点", history=[], user_id=1)
        )
        update_prompt = _prompt_text(captured)
    finally:
        db.close()
    for prompt in (cancel_prompt, update_prompt):
        assert "已有日程的名称" in prompt
        assert "不要把参与人昵称或其他词拼进标题" in prompt
        assert "/" not in prompt.replace("start_at/end_at", "")


def test_model_far_date_stays_on_the_draft(monkeypatch):
    """A distant model date is the user's to correct. The code does not replace it."""
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_args, **_kwargs: {
            "title": "测试会议",
            "start_at": "2023-04-05T15:00:00",
            "end_at": "",
            "participants": ["Grok测试"],
            "missing_fields": [],
        },
    )
    draft = extract_event(
        "明天下午三点开个测试会议 Grok测试",
        now=datetime(2026, 10, 15, 9, 0),
        timezone_name="Asia/Singapore",
    )
    assert draft.title == "测试会议"
    assert draft.participants == ["Grok测试"]
    assert draft.start_at == datetime(2023, 4, 5, 15, 0)
    assert draft.end_at == datetime(2023, 4, 5, 16, 0)
    assert "+" not in draft.start_at.isoformat()
    assert "Z" not in draft.start_at.isoformat()


def _freeze_suggest_now(monkeypatch) -> None:
    frozen = datetime(2026, 10, 15, 9, 0)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: frozen,
    )


def _suggest_owner(db) -> int:
    user_id = 88041
    if db.get(User, user_id) is None:
        db.add(User(id=user_id, external_id="suggest-88041", name="suggest", is_active=1))
        db.commit()
    db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).delete()
    db.commit()
    return user_id


def test_suggest_named_hour_when_tomorrow_is_empty(monkeypatch):
    _freeze_suggest_now(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        owner_id = _suggest_owner(db)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == owner_id).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天下午四点有空吗", history=[], user_id=owner_id)
        )
        answer = result.direct_answer or ""
        assert "2026-10-16 16:00" in answer
        assert "12:00–13:00" not in answer
        card = result.schedule_card
        assert card["mode"] == "suggest"
        assert card["phase"] == "info"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-16T16:00:00", "end_at": "2026-10-16T17:00:00"}
        ]
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == owner_id).count() == before
    finally:
        db.close()


def test_suggest_named_hour_offers_another_afternoon_slot_when_busy(monkeypatch):
    _freeze_suggest_now(monkeypatch)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        owner_id = _suggest_owner(db)
        busy = CalendarEvent(
            user_id=owner_id,
            title="已占",
            start_at=datetime(2026, 10, 16, 16, 0),
            end_at=datetime(2026, 10, 16, 17, 0),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(busy)
        db.commit()
        db.refresh(busy)
        created.append(busy.id)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == owner_id).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天下午四点有空吗", history=[], user_id=owner_id)
        )
        answer = result.direct_answer or ""
        assert "这个时间可以" not in answer
        assert "16:00–17:00" not in answer
        card = result.schedule_card
        assert card is not None
        assert card["mode"] == "suggest"
        assert card["phase"] == "info"
        assert card["suggested_slots"]
        assert all(slot["start_at"] != "2026-10-16T16:00:00" for slot in card["suggested_slots"])
        assert any(
            "T12:00" in slot["start_at"] or "T17:00" in slot["start_at"]
            for slot in card["suggested_slots"]
        )
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == owner_id).count() == before
    finally:
        _cleanup(db, created)
        db.close()


def test_suggest_open_afternoon_is_not_only_the_first_hour(monkeypatch):
    _freeze_suggest_now(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        owner_id = _suggest_owner(db)
        result = execute_calendar(
            ToolContext(db=db, message="明天下午有空吗", history=[], user_id=owner_id)
        )
        answer = result.direct_answer or ""
        assert "这段时间都有空" in answer
        assert "12:00–13:00" not in answer
        assert "2026-10-16 12:00" in answer
        card = result.schedule_card
        assert card["mode"] == "suggest"
        assert card["phase"] == "info"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-16T12:00:00", "end_at": "2026-10-17T00:00:00"}
        ]
    finally:
        db.close()
