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


def test_classifier_does_not_use_keywords_when_the_model_misses(monkeypatch):
    """A cancel sentence stays untouched when the model does not name an action."""
    from app.services.calendar import classify_calendar_action

    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.classify_calendar_action",
        classify_calendar_action,
    )
    seen: dict = {}

    def _json(messages, *_args, **_kwargs):
        seen["prompt"] = messages[0]["content"]
        return {"action": "maybe"}

    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: datetime(2026, 10, 15, 10, 0),
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
            ToolContext(db=db, message="取消这场会", history=[], user_id=1)
        )
    finally:
        db.close()
    assert result.schedule_card is None
    assert result.direct_answer == "没有听清要新建、改期、删除、查看、看空闲还是准备会议，这次没有改动日历。"
    for word in ("改期", "删除", "修改"):
        assert word in seen["prompt"]


def test_explicit_action_skips_classification(monkeypatch):
    called = {"n": 0}

    def _classify(*_args, **_kwargs):
        called["n"] += 1
        return "cancel"

    monkeypatch.setattr("app.agents.tools.calendar_tool.classify_calendar_action", _classify)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.extract_event",
        lambda *_args, **_kwargs: EventDraft(
            title="周会",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 16, 0),
        ),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: datetime(2026, 10, 15, 10, 0),
    )
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="取消这场会", history=[], user_id=1),
            {"action": "create"},
        )
    finally:
        db.close()
    assert called["n"] == 0
    assert result.schedule_card["mode"] == "create"
    assert result.schedule_card["title"] == "周会"


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
            patch(
                "app.agents.tools.calendar_tool.calendar_now",
                lambda *_a, **_k: datetime(2026, 8, 6, 9, 0),
            ),
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
            patch(
                "app.agents.tools.calendar_tool.calendar_now",
                lambda *_a, **_k: datetime(2026, 8, 6, 9, 0),
            ),
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
            patch(
                "app.agents.tools.calendar_tool.calendar_now",
                lambda *_a, **_k: datetime(2026, 8, 6, 9, 0),
            ),
        ):
            result = run_agent_workflow(db, message="取消周会", history=[], user_id=1)
        card = result["schedule_card"]
        assert card is None or card.get("mode") != "choose"
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
        card = result["schedule_card"]
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["items"][0]["title"] == "周会"
        assert card["items"][0]["start_at"].startswith("2026-10-15T10:00:00")
        assert result["pending_calendar"] is None
    finally:
        _cleanup(db, created)
        db.close()


def test_suggest_returns_slot_card_without_writing(monkeypatch):
    frozen = datetime(2026, 10, 14, 9, 0)
    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", lambda *_args, **_kwargs: frozen)

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            return {
                "slots": [{"start_at": "2026-10-15T15:00:00", "end_at": "2026-10-15T16:00:00"}],
                "note": "下午三点到四点可以。",
            }
        return {"day": "2026-10-15"}

    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        with patch("app.agents.workflow.plan_tools", return_value=_plan()):
            result = run_agent_workflow(db, message="明天下午有空吗", history=[], user_id=1)
        card = result["schedule_card"]
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-15T15:00:00", "end_at": "2026-10-15T16:00:00"}
        ]
        assert result["direct_answer"] == "下午三点到四点可以。"
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
        card = listed["schedule_card"]
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "已确认周会"
        assert "10:00" in card["items"][0]["start_at"]
    finally:
        _cleanup(db, created)
        db.close()


def _capture_extract(monkeypatch, payload: dict):
    captured: dict = {}

    def fake_json(messages, *args, **kwargs):
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
        assert "结束时间或时长" in prompt
        assert "改备注" in prompt
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
    """An empty day does not invent 00:00–01:00. The model's slot is the answer."""
    _freeze_suggest_now(monkeypatch)

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            return {
                "slots": [{"start_at": "2026-10-16T16:00:00", "end_at": "2026-10-16T17:00:00"}],
                "note": "明天下午四点可以。",
            }
        return {"day": "2026-10-16"}

    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    init_db()
    db = SessionLocal()
    try:
        owner_id = _suggest_owner(db)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == owner_id).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天下午四点有空吗", history=[], user_id=owner_id)
        )
        card = result.schedule_card
        assert card["items"] == []
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-16T16:00:00", "end_at": "2026-10-16T17:00:00"}
        ]
        assert "00:00–01:00" not in (result.direct_answer or "")
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == owner_id).count() == before
    finally:
        db.close()


def _planning_owner(db) -> int:
    user_id = 88077
    if db.get(User, user_id) is None:
        db.add(User(id=user_id, external_id="plan-88077", name="plan", is_active=1))
        db.commit()
    db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).delete()
    db.commit()
    return user_id


def _freeze_planning_clock(monkeypatch) -> None:
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


def test_personal_block_prompt_and_card(monkeypatch):
    captured = _capture_extract(
        monkeypatch,
        {
            "title": "写报告",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "2026-10-16T17:00:00",
            "participants": [],
            "missing_fields": [],
            "action": "create",
            "day": "",
        },
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天下午留两小时写报告", history=[], user_id=1)
        )
        card = result.schedule_card
        assert card["mode"] == "create"
        assert card["phase"] == "draft"
        assert card["title"] == "写报告"
        assert card["start_at"] == "2026-10-16T15:00:00"
        assert card["end_at"] == "2026-10-16T17:00:00"
        assert card["participants"] == []
        assert "+" not in card["start_at"] and "Z" not in card["start_at"]
        assert "+" not in card["end_at"] and "Z" not in card["end_at"]
        assert db.query(CalendarEvent).count() == before
        prompt = _prompt_text(captured)
        assert "2026-10-15" in prompt
        assert "Asia/Singapore" in prompt
        assert "说了时长就写 end_at" in prompt
        assert "参与人可空" in prompt
    finally:
        db.close()


def test_personal_block_blank_end_is_one_hour(monkeypatch):
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_args, **_kwargs: {
            "title": "写报告",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "",
            "participants": [],
            "action": "create",
        },
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天下午留两小时写报告", history=[], user_id=1)
        )
        assert result.schedule_card["end_at"] == "2026-10-16T16:00:00"
    finally:
        db.close()


def test_personal_block_conflict_stays_on_the_card(monkeypatch):
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_args, **_kwargs: {
            "title": "写报告",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "2026-10-16T17:00:00",
            "participants": [],
            "action": "create",
        },
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        owner_id = _planning_owner(db)
        existing = create_event(
            db,
            user_id=owner_id,
            title="已有会",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 16, 0),
            participants=[],
        )
        created.append(existing.id)
        before = db.query(CalendarEvent).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天下午留两小时写报告", history=[], user_id=owner_id)
        )
        card = result.schedule_card
        assert card["mode"] == "create"
        assert card["conflicts"]
        assert db.query(CalendarEvent).count() == before
    finally:
        _cleanup(db, created)
        db.close()


class _Listed:
    def __init__(self, rows, notice=""):
        self.rows = rows
        self.list_notice = notice
        self.calls: list[tuple[datetime, datetime]] = []

    def list_events(self, user_id, start, end, status="active"):
        self.calls.append((start, end))
        return self.rows


def test_named_day_list_appends_advice_and_writes_nothing(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    advice_messages: dict = {}

    def _advice(messages, *_args, **_kwargs):
        advice_messages["messages"] = messages
        return "会太密，留出缓冲。整段空闲适合专注。太满可以挪。"

    monkeypatch.setattr("app.services.calendar.complete_json", lambda *_a, **_k: {"day": "2026-10-16"})
    monkeypatch.setattr("app.agents.tools.calendar_tool.complete_text", _advice)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天有什么安排", history=[], user_id=1)
        )
        assert provider.calls == [(datetime(2026, 10, 15, 9, 0), datetime(2100, 1, 1))]
        answer = result.direct_answer or ""
        assert answer == "会太密，留出缓冲。整段空闲适合专注。太满可以挪。"
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["suggested_slots"] == []
        assert card["items"][0]["title"] == "周会"
        assert card["items"][0]["start_at"] == "2026-10-16T10:00:00"
        assert card["items"][0]["end_at"] == "2026-10-16T11:00:00"
        assert "+" not in card["items"][0]["start_at"]
        assert "Z" not in card["items"][0]["start_at"]
        assert db.query(CalendarEvent).count() == before
        prompt = "\n".join(str(item.get("content") or "") for item in advice_messages["messages"])
        assert "周会" in prompt
        assert "不要逐段填空" in prompt
        assert "密度" in prompt
        assert "缓冲" in prompt
        assert "整段空闲" in prompt
        assert "过载" in prompt
        assert "2026-10-15" in prompt
        assert "Asia/Singapore" in prompt
    finally:
        db.close()


def test_blank_list_day_uses_seven_day_window(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 18, 9, 0),
        end_at=datetime(2026, 10, 18, 10, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    monkeypatch.setattr("app.services.calendar.complete_json", lambda *_a, **_k: {"day": ""})
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.complete_text",
        lambda *_a, **_k: "这几天不算满。",
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="查一下日程", history=[], user_id=1)
        )
        assert provider.calls == [(datetime(2026, 10, 15, 9, 0), datetime(2100, 1, 1))]
        assert result.direct_answer == "这几天不算满。"
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "周会"
        assert card["suggested_slots"] == []
    finally:
        db.close()


def test_list_read_failure_skips_advice(monkeypatch):
    provider = _Listed([], notice="暂时读不到 Google 日历，以下只是这里保存的日程。")
    monkeypatch.setattr("app.services.calendar.complete_json", lambda *_a, **_k: {"day": "2026-10-16"})

    def _no_advice(*_args, **_kwargs):
        raise AssertionError("read failure must not ask for advice")

    monkeypatch.setattr("app.agents.tools.calendar_tool.complete_text", _no_advice)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有什么安排", history=[], user_id=1)
        )
        assert "暂时读不到 Google 日历" in (result.direct_answer or "")
        assert "暂时给不出建议" not in (result.direct_answer or "")
        assert result.schedule_card is None
    finally:
        db.close()


def test_advice_model_failure_keeps_the_list(monkeypatch):
    from types import SimpleNamespace

    from app.core.llm import LLMInfraError

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    monkeypatch.setattr("app.services.calendar.complete_json", lambda *_a, **_k: {"day": "2026-10-16"})
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.complete_text",
        lambda *_a, **_k: (_ for _ in ()).throw(LLMInfraError("down")),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有什么安排", history=[], user_id=1)
        )
        assert result.direct_answer == "暂时给不出建议"
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "周会"
        assert "2026-10-16T10:00:00" == card["items"][0]["start_at"]
    finally:
        db.close()


def test_unconnected_list_advises_from_local_rows(monkeypatch):
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: datetime(2026, 10, 15, 9, 0),
    )
    monkeypatch.setattr("app.services.calendar.complete_json", lambda *_a, **_k: {"day": ""})
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.complete_text",
        lambda *_a, **_k: "本地这几天还有空，适合专注。",
    )

    def _no_google(*_args, **_kwargs):
        raise AssertionError("unconnected list must not read Google")

    monkeypatch.setattr(
        "app.services.calendar_google.GoogleCalendarProvider.list_events",
        _no_google,
    )
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        owner_id = _planning_owner(db)
        event = create_event(
            db,
            user_id=owner_id,
            title="本地周会",
            start_at=datetime(2026, 10, 16, 10, 0),
            end_at=datetime(2026, 10, 16, 11, 0),
            participants=[],
        )
        created.append(event.id)
        before = db.query(CalendarEvent).count()
        result = execute_calendar(
            ToolContext(db=db, message="查一下日程", history=[], user_id=owner_id)
        )
        assert result.direct_answer == "本地这几天还有空，适合专注。"
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "本地周会"
        assert card["items"][0]["start_at"].startswith("2026-10-16T10:00:00")
        assert db.query(CalendarEvent).count() == before
    finally:
        _cleanup(db, created)
        db.close()


def test_list_comes_from_classification_not_extract_action(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        lambda *_a, **_k: {
            "title": "不该变成卡片",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "2026-10-16T17:00:00",
            "participants": ["张三"],
            "action": "list",
            "day": "2026-10-16",
        },
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.complete_text",
        lambda *_a, **_k: "会太密，留出缓冲。",
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        listed = execute_calendar(
            ToolContext(db=db, message="帮我看看明天怎么安排", history=[], user_id=1),
            {"action": "list"},
        )
        assert listed.schedule_card["mode"] == "list"
        assert listed.schedule_card["items"][0]["title"] == "周会"
        assert "不该变成卡片" not in (listed.direct_answer or "")
        assert listed.direct_answer == "会太密，留出缓冲。"
        assert provider.calls == [(datetime(2026, 10, 15, 9, 0), datetime(2100, 1, 1))]
        assert db.query(CalendarEvent).count() == before
        side_door = execute_calendar(
            ToolContext(db=db, message="帮我看看明天怎么安排", history=[], user_id=1),
            {"action": "create"},
        )
        assert side_door.schedule_card["mode"] == "create"
        assert side_door.schedule_card["title"] == "不该变成卡片"
        assert db.query(CalendarEvent).count() == before

        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "写报告",
                "start_at": "2026-10-16T15:00:00",
                "end_at": "2026-10-16T17:00:00",
                "participants": [],
                "action": "create",
            },
        )
        drafted = execute_calendar(
            ToolContext(db=db, message="明天下午留两小时写报告", history=[], user_id=1)
        )
        assert drafted.schedule_card["mode"] == "create"
        assert drafted.schedule_card["title"] == "写报告"
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


def test_free_slot_question_does_not_ask_for_planning_advice(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("suggest must not ask for planning advice")

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            return {
                "slots": [{"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"}],
                "note": "下午三点比较合适。",
            }
        return {"day": "2026-10-16"}

    monkeypatch.setattr("app.agents.tools.calendar_tool.complete_text", _boom)
    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    _freeze_suggest_now(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        owner_id = _suggest_owner(db)
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=owner_id)
        )
        assert result.schedule_card["mode"] == "list"
        assert result.schedule_card["items"] == []
        assert result.direct_answer == "下午三点比较合适。"
        assert "暂时给不出建议" not in (result.direct_answer or "")
        assert "密度" not in (result.direct_answer or "")
    finally:
        db.close()


def _json_day_then_slots(day, slots, note, *, fail_slots=False):
    from app.core.llm import LLMInfraError

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            if fail_slots:
                raise LLMInfraError("down")
            return {"slots": slots, "note": note}
        return {"day": day}

    return _json


def test_free_slot_lists_the_day_then_uses_model_slots(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    prompts: dict = {}

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            prompts["slot"] = blob
            return {
                "slots": [{"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"}],
                "note": "周会之后，下午三点到四点可以。",
            }
        return {"day": "2026-10-16"}

    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        assert provider.calls == [(datetime(2026, 10, 15, 9, 0), datetime(2100, 1, 1))]
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["items"][0]["title"] == "周会"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"}
        ]
        assert result.direct_answer == "周会之后，下午三点到四点可以。"
        assert "密度" not in (result.direct_answer or "")
        prompt = prompts["slot"]
        assert "周会" in prompt
        assert "2026-10-15" in prompt
        assert "Asia/Singapore" in prompt
        assert "不要推荐半夜" in prompt
        assert "不能说成有空" in prompt
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


def test_free_slot_keeps_a_model_midnight_slot(monkeypatch):
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        _json_day_then_slots(
            "2026-10-16",
            [{"start_at": "2026-10-16T00:00:00", "end_at": "2026-10-16T01:00:00"}],
            "模型仍给了午夜。",
        ),
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        assert result.schedule_card["suggested_slots"] == [
            {"start_at": "2026-10-16T00:00:00", "end_at": "2026-10-16T01:00:00"}
        ]
    finally:
        db.close()


def test_free_slot_read_failure_skips_the_slot_model(monkeypatch):
    provider = _Listed([], notice="暂时读不到 Google 日历，以下只是这里保存的日程。")

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            raise AssertionError("read failure must not ask for slots")
        return {"day": "2026-10-16"}

    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        assert result.direct_answer == "暂时读不到 Google 日历，不能建议空闲时段。"
        assert result.schedule_card is None
    finally:
        db.close()


def test_free_slot_model_failure_keeps_the_list(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        _json_day_then_slots("2026-10-16", [], "", fail_slots=True),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "周会"
        assert card["suggested_slots"] == []
        assert "暂时给不出建议" in (result.direct_answer or "")
    finally:
        db.close()


def test_create_kind_splits_meeting_and_block(monkeypatch):
    captured = _capture_extract(
        monkeypatch,
        {
            "title": "写报告",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "2026-10-16T17:00:00",
            "participants": ["小李"],
            "missing_fields": [],
            "action": "create",
            "kind": "block",
            "description": "",
            "location": "会议室",
        },
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        blocked = execute_calendar(
            ToolContext(db=db, message="明天下午留两小时写报告", history=[], user_id=1)
        )
        card = blocked.schedule_card
        assert card["kind"] == "block"
        assert card["mode"] == "create"
        assert card["phase"] == "draft"
        assert card["title"] == "写报告"
        assert card["start_at"] == "2026-10-16T15:00:00"
        assert card["end_at"] == "2026-10-16T17:00:00"
        assert card["participants"] == []
        assert card["location"] == ""
        assert "+" not in card["start_at"] and "Z" not in card["start_at"]
        assert "+" not in card["end_at"] and "Z" not in card["end_at"]
        assert db.query(CalendarEvent).count() == before
        prompt = _prompt_text(captured)
        assert "2026-10-15" in prompt
        assert "Asia/Singapore" in prompt
        assert "kind 为 meeting 或 block" in prompt
        assert "留给自己的一段时间是 block" in prompt

        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "周会",
                "start_at": "2026-10-16T10:00:00",
                "end_at": "2026-10-16T11:00:00",
                "participants": ["小李"],
                "action": "create",
                "kind": "meeting",
                "location": "会议室",
            },
        )
        meeting = execute_calendar(
            ToolContext(db=db, message="明早十点开周会，参与人小李", history=[], user_id=1)
        )
        assert meeting.schedule_card["kind"] == "meeting"
        assert meeting.schedule_card["participants"] == ["小李"]
        assert meeting.schedule_card["location"] == "会议室"

        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "写报告",
                "start_at": "2026-10-16T15:00:00",
                "end_at": "",
                "participants": [],
                "action": "create",
                "kind": "",
            },
        )
        blank = execute_calendar(
            ToolContext(db=db, message="明天下午留两小时写报告", history=[], user_id=1)
        )
        assert blank.schedule_card["kind"] == "meeting"
        assert blank.schedule_card["end_at"] == "2026-10-16T16:00:00"
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


def test_update_kind_follows_the_matched_event_and_cancel_does_not(monkeypatch):
    """A matched event decides the form. The model's kind does not override it."""
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        owner_id = _planning_owner(db)
        event = create_event(
            db,
            user_id=owner_id,
            title="写报告",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 17, 0),
            participants=["小李"],
        )
        created.append(event.id)
        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "写报告",
                "start_at": "2026-10-17T15:00:00",
                "end_at": "2026-10-17T17:00:00",
                "participants": ["小李"],
                "kind": "block",
            },
        )
        updated = execute_calendar(
            ToolContext(db=db, message="把写报告改到后天下午", history=[], user_id=owner_id)
        )
        assert updated.schedule_card["mode"] == "update"
        assert updated.schedule_card["kind"] == "meeting"
        assert updated.schedule_card["participants"] == ["小李"]
        db.refresh(event)
        assert event.start_at == datetime(2026, 10, 16, 15, 0)

        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "写报告",
                "start_at": "2026-10-17T15:00:00",
                "end_at": "2026-10-17T17:00:00",
                "participants": ["小李"],
            },
        )
        plain = execute_calendar(
            ToolContext(db=db, message="把写报告改到后天下午", history=[], user_id=owner_id)
        )
        assert plain.schedule_card["kind"] == "meeting"
        assert plain.schedule_card["participants"] == ["小李"]

        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "写报告",
                "start_at": "2026-10-16T09:00:00",
                "end_at": "2026-10-16T10:00:00",
                "participants": [],
                "kind": "block",
            },
        )
        cancelled = execute_calendar(
            ToolContext(db=db, message="取消写报告", history=[], user_id=owner_id)
        )
        assert cancelled.schedule_card["mode"] == "cancel"
        assert cancelled.schedule_card["kind"] == ""
        assert cancelled.schedule_card["start_at"] == "2026-10-16T15:00:00"
        assert cancelled.schedule_card["end_at"] == "2026-10-16T17:00:00"
        db.refresh(event)
        assert event.status == "active"
    finally:
        _cleanup(db, created)
        db.close()


def test_notes_prefill_description_and_stay_optional(monkeypatch):
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        for kind, people in (("meeting", ["小李"]), ("block", [])):
            monkeypatch.setattr(
                "app.services.calendar.complete_json",
                lambda *_a, kind=kind, people=people, **_k: {
                    "title": "写报告" if kind == "block" else "周会",
                    "start_at": "2026-10-16T15:00:00",
                    "end_at": "2026-10-16T17:00:00",
                    "participants": people,
                    "missing_fields": ["description"],
                    "action": "create",
                    "kind": kind,
                    "description": "带上大纲",
                },
            )
            result = execute_calendar(
                ToolContext(db=db, message="带上大纲", history=[], user_id=1)
            )
            assert result.schedule_card["kind"] == kind
            assert result.schedule_card["description"] == "带上大纲"
        draft = extract_event(
            "明天下午留两小时写报告",
            now=datetime(2026, 10, 15, 9, 0),
            timezone_name="Asia/Singapore",
        )
        assert draft.description == "带上大纲"
        assert "description" not in draft.missing_fields
        assert "kind" not in draft.missing_fields
    finally:
        db.close()


def test_update_keeps_matched_duration_and_notes_unless_asked(monkeypatch):
    """The model supplies the new start. Duration and notes stay unless it asked to change them."""
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        owner_id = _planning_owner(db)
        block = create_event(
            db,
            user_id=owner_id,
            title="写报告",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 17, 0),
            participants=[],
            location="",
            description="带上大纲",
        )
        meeting = create_event(
            db,
            user_id=owner_id,
            title="评审",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 17, 0),
            participants=["小李"],
            location="会议室",
            description="旧备注",
        )
        created.extend([block.id, meeting.id])

        def _json(messages, *_args, **_kwargs):
            blob = messages[-1]["content"] if messages else ""
            if "新备注" in blob:
                return {
                    "title": "评审",
                    "start_at": "2026-10-17T16:00:00",
                    "end_at": "2026-10-17T16:30:00",
                    "participants": ["别人"],
                    "kind": "block",
                    "description": "新备注",
                    "change_end": True,
                    "change_notes": True,
                }
            return {
                "title": "写报告",
                "start_at": "2026-10-17T16:00:00",
                "end_at": "",
                "participants": ["别人"],
                "kind": "meeting",
                "description": "",
            }

        monkeypatch.setattr("app.services.calendar.complete_json", _json)
        kept = execute_calendar(
            ToolContext(db=db, message="把写报告改到后天", history=[], user_id=owner_id)
        )
        card = kept.schedule_card
        assert card["mode"] == "update"
        assert card["phase"] == "draft"
        assert card["event_id"] == block.id
        assert card["kind"] == "block"
        assert card["title"] == "写报告"
        assert card["start_at"] == "2026-10-17T16:00:00"
        assert card["end_at"] == "2026-10-17T18:00:00"
        assert card["participants"] == []
        assert card["location"] == ""
        assert card["description"] == "带上大纲"
        assert "+" not in card["start_at"] and "Z" not in card["start_at"]
        assert "+" not in card["end_at"] and "Z" not in card["end_at"]
        db.refresh(block)
        assert block.start_at == datetime(2026, 10, 16, 15, 0)
        assert block.description == "带上大纲"

        changed = execute_calendar(
            ToolContext(
                db=db,
                message="把评审改到后天，备注改成新备注",
                history=[],
                user_id=owner_id,
            )
        )
        moved = changed.schedule_card
        assert moved["mode"] == "update"
        assert moved["phase"] == "draft"
        assert moved["event_id"] == meeting.id
        assert moved["kind"] == "meeting"
        assert moved["participants"] == ["小李"]
        assert moved["location"] == "会议室"
        assert moved["description"] == "新备注"
        assert moved["start_at"] == "2026-10-17T16:00:00"
        assert moved["end_at"] == "2026-10-17T16:30:00"
        db.refresh(meeting)
        assert meeting.start_at == datetime(2026, 10, 16, 15, 0)
        assert meeting.description == "旧备注"
        assert meeting.location == "会议室"
    finally:
        _cleanup(db, created)
        db.close()


def test_reschedule_ignores_filled_end_and_notes_without_change_flags(monkeypatch):
    """A 1h end and a shortened note are not a request. Flags are."""
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    prompts: list[str] = []
    try:
        from app.services.calendar import create_event

        owner_id = _planning_owner(db)
        block = create_event(
            db,
            user_id=owner_id,
            title="写报告",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 17, 0),
            participants=[],
            location="",
            description="带上大纲",
        )
        created.append(block.id)

        def _json(messages, *_args, **_kwargs):
            prompts.append("\n".join(str(item.get("content") or "") for item in messages))
            blob = messages[-1]["content"] if messages else ""
            if "新备注" in blob:
                return {
                    "title": "写报告",
                    "start_at": "2026-10-17T16:00:00",
                    "end_at": "2026-10-17T16:30:00",
                    "description": "新备注",
                    "change_end": True,
                    "change_notes": True,
                }
            return {
                "title": "写报告",
                "start_at": "2026-10-17T16:00:00",
                "end_at": "2026-10-17T17:00:00",
                "participants": ["别人"],
                "kind": "meeting",
                "description": "大纲",
            }

        monkeypatch.setattr("app.services.calendar.complete_json", _json)
        kept = execute_calendar(
            ToolContext(db=db, message="把写报告改到后天", history=[], user_id=owner_id)
        )
        card = kept.schedule_card
        assert card["mode"] == "update"
        assert card["phase"] == "draft"
        assert card["start_at"] == "2026-10-17T16:00:00"
        assert card["end_at"] == "2026-10-17T18:00:00"
        assert card["description"] == "带上大纲"
        assert "change_end" in prompts[0]
        assert "change_notes" in prompts[0]
        assert "不改" in prompts[0]
        db.refresh(block)
        assert block.start_at == datetime(2026, 10, 16, 15, 0)
        assert block.end_at == datetime(2026, 10, 16, 17, 0)
        assert block.description == "带上大纲"

        changed = execute_calendar(
            ToolContext(
                db=db,
                message="把写报告改到后天，备注改成新备注，时长改成半小时",
                history=[],
                user_id=owner_id,
            )
        )
        moved = changed.schedule_card
        assert moved["start_at"] == "2026-10-17T16:00:00"
        assert moved["end_at"] == "2026-10-17T16:30:00"
        assert moved["description"] == "新备注"
        db.refresh(block)
        assert block.description == "带上大纲"
    finally:
        _cleanup(db, created)
        db.close()


def test_cancel_card_uses_the_matched_event_times(monkeypatch):
    """A title match replaces the model's guessed cancel times with the real span."""
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    created: list[int] = []
    try:
        from app.services.calendar import create_event

        owner_id = _planning_owner(db)
        event = create_event(
            db,
            user_id=owner_id,
            title="写报告",
            start_at=datetime(2026, 10, 16, 15, 0),
            end_at=datetime(2026, 10, 16, 17, 0),
            participants=["小李"],
            location="会议室",
            description="带上大纲",
        )
        created.append(event.id)
        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_a, **_k: {
                "title": "写报告",
                "start_at": "2026-10-16T09:00:00",
                "end_at": "2026-10-16T10:00:00",
                "participants": [],
                "kind": "block",
                "description": "不要用这句",
            },
        )
        cancelled = execute_calendar(
            ToolContext(db=db, message="取消写报告", history=[], user_id=owner_id)
        )
        card = cancelled.schedule_card
        assert card["mode"] == "cancel"
        assert card["phase"] == "draft"
        assert card["kind"] == ""
        assert card["event_id"] == event.id
        assert card["start_at"] == "2026-10-16T15:00:00"
        assert card["end_at"] == "2026-10-16T17:00:00"
        assert card["participants"] == ["小李"]
        assert card["location"] == "会议室"
        assert card["description"] == "带上大纲"
        assert "+" not in card["start_at"] and "Z" not in card["end_at"]
        db.refresh(event)
        assert event.status == "active"
        assert event.start_at == datetime(2026, 10, 16, 15, 0)
    finally:
        _cleanup(db, created)
        db.close()


def test_free_slot_drops_overlapping_slots_but_not_by_hour(monkeypatch):
    """Busy overlap is dropped. A midnight slot that misses the event stays."""
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        _json_day_then_slots(
            "2026-10-16",
            [
                {"start_at": "2026-10-16T00:00:00", "end_at": "2026-10-16T01:00:00"},
                {"start_at": "2026-10-16T10:30:00", "end_at": "2026-10-16T11:30:00"},
                {"start_at": "2026-10-16T11:00:00", "end_at": "2026-10-16T12:00:00"},
                {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"},
            ],
            "午夜不和周会重叠。",
        ),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["items"][0]["title"] == "周会"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-16T00:00:00", "end_at": "2026-10-16T01:00:00"},
            {"start_at": "2026-10-16T11:00:00", "end_at": "2026-10-16T12:00:00"},
            {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"},
        ]
        assert result.direct_answer == "午夜不和周会重叠。"
    finally:
        db.close()


def test_free_slot_note_does_not_call_a_busy_hour_free(monkeypatch):
    """Overlap filtering drops the busy slot, and a note that calls it free is replaced."""
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    prompts: dict = {}

    def _json(messages, *_args, **_kwargs):
        blob = "\n".join(str(item.get("content") or "") for item in messages)
        if "不要推荐半夜" in blob:
            prompts["slot"] = blob
            return {
                "slots": [
                    {"start_at": "2026-10-16T10:00:00", "end_at": "2026-10-16T11:00:00"},
                    {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"},
                ],
                "note": "10点有空",
            }
        return {"day": "2026-10-16"}

    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "周会"
        assert card["items"][0]["start_at"] == "2026-10-16T10:00:00"
        assert card["items"][0]["end_at"] == "2026-10-16T11:00:00"
        assert card["suggested_slots"] == [
            {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"}
        ]
        assert result.direct_answer == "请以上面的列表为准，已有安排的时段不是空闲。"
        assert "10点有空" not in result.direct_answer
        assert "有空" not in result.direct_answer
        assert "不能说成有空" in prompts["slot"]
        assert "请用户看列表" in prompts["slot"]
    finally:
        db.close()


def test_free_slot_all_day_row_is_not_called_free(monkeypatch):
    """An all-day row covers the civil day, so a slot inside it is not free time."""
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="全天安排",
        start_at=datetime(2026, 10, 16, 0, 0),
        end_at=datetime(2026, 10, 17, 0, 0),
        all_day=True,
        readonly=False,
    )
    provider = _Listed([row])
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        _json_day_then_slots(
            "2026-10-16",
            [{"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"}],
            "下午有空",
        ),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        before = db.query(CalendarEvent).count()
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["items"][0]["title"] == "全天安排"
        assert card["items"][0]["all_day"] is True
        assert card["items"][0]["start_at"] == "2026-10-16T00:00:00"
        assert card["items"][0]["end_at"] == "2026-10-17T00:00:00"
        assert {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"} not in card[
            "suggested_slots"
        ]
        assert result.direct_answer == "请以上面的列表为准，已有安排的时段不是空闲。"
        assert result.direct_answer != "下午有空"
        assert "下午有空" not in (result.direct_answer or "")
        assert db.query(CalendarEvent).count() == before
    finally:
        db.close()


def test_free_slot_empty_when_every_slot_overlaps(monkeypatch):
    """Every suggested slot hits a listed event, so the list card keeps the note and no slots."""
    from types import SimpleNamespace

    row = SimpleNamespace(
        title="周会",
        start_at=datetime(2026, 10, 16, 10, 0),
        end_at=datetime(2026, 10, 16, 11, 0),
        all_day=False,
        readonly=False,
    )
    provider = _Listed([row])
    note = "这些时段都和周会重叠。"
    monkeypatch.setattr(
        "app.services.calendar.complete_json",
        _json_day_then_slots(
            "2026-10-16",
            [
                {"start_at": "2026-10-16T09:30:00", "end_at": "2026-10-16T10:30:00"},
                {"start_at": "2026-10-16T10:15:00", "end_at": "2026-10-16T10:45:00"},
            ],
            note,
        ),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_provider_for",
        lambda *_a, **_k: provider,
    )
    _freeze_planning_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    try:
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=1)
        )
        card = result.schedule_card
        assert card["mode"] == "list"
        assert card["phase"] == "info"
        assert card["items"][0]["title"] == "周会"
        assert card["suggested_slots"] == []
        assert result.direct_answer == note
    finally:
        db.close()
