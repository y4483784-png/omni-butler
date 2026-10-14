"""Local calendar provider. Slice 1: storage facade only, same overlap rules."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

from app.agents.tools import calendar_tool
from app.core.auth import hash_password
from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, User
from app.services.calendar import check_conflict, create_event, serialize_event_card
from app.services.calendar_local import LocalCalendarProvider


def _user(db, *, username: str, user_id: int) -> User:
    row = db.query(User).filter(User.external_id == username).first()
    if row is None:
        row = User(
            id=user_id,
            external_id=username,
            name=username,
            password_hash=hash_password("provider-pass"),
            is_active=1,
            is_admin=0,
        )
        db.add(row)
        db.flush()
    return row


def _cleanup(db, event_ids: list[int]) -> None:
    for event_id in event_ids:
        row = db.get(CalendarEvent, event_id)
        if row is not None:
            db.delete(row)
    db.commit()


def test_tool_does_not_query_calendar_rows():
    src = inspect.getsource(calendar_tool)
    assert "CalendarEvent" not in src
    assert "db.query" not in src


def test_get_timezone_is_process_local():
    provider = LocalCalendarProvider(None)  # type: ignore[arg-type]
    zone = provider.get_timezone()
    now = datetime.now().astimezone()
    assert zone is not None
    assert zone.utcoffset(now) == now.utcoffset()


def test_provider_roundtrip_defaults_and_ownership():
    init_db()
    db = SessionLocal()
    created_ids: list[int] = []
    try:
        alice = _user(db, username="cal-alice", user_id=9701)
        bob = _user(db, username="cal-bob", user_id=9702)
        db.commit()
        provider = LocalCalendarProvider(db)
        start = datetime(2026, 8, 6, 10, 0)
        end = datetime(2026, 8, 6, 11, 0)
        created = provider.create_event(
            alice.id,
            title="周会",
            start_at=start,
            end_at=end,
            participants=["张三"],
        )
        created_ids.append(created.id)
        assert created.provider == "local"
        assert created.timezone == ""
        assert created.location == ""
        assert created.description == ""
        assert created.external_id == ""
        assert created.participants == ["张三"]
        assert created.status == "active"

        own = provider.get_event(alice.id, created.id)
        assert own is not None and own.title == "周会"
        assert provider.get_event(bob.id, created.id) is None

        listed = provider.list_events(alice.id, datetime(2026, 8, 6, 9, 0), datetime(2026, 8, 6, 12, 0))
        assert [row.id for row in listed] == [created.id]
        assert provider.list_events(alice.id, datetime(2026, 8, 7, 0, 0), datetime(2026, 8, 7, 1, 0)) == []
        assert provider.list_events(bob.id, datetime(2026, 8, 6, 9, 0), datetime(2026, 8, 6, 12, 0)) == []

        renamed = provider.update_event(alice.id, created.id, title="改名")
        assert renamed is not None and renamed.title == "改名"
        assert provider.update_event(bob.id, created.id, title="hacked") is None
        assert provider.get_event(alice.id, created.id).title == "改名"  # type: ignore[union-attr]

        cancelled = provider.cancel_event(alice.id, created.id)
        assert cancelled is not None and cancelled.status == "cancelled"
        assert provider.list_events(alice.id, datetime(2026, 8, 6, 9, 0), datetime(2026, 8, 6, 12, 0)) == []
        still = provider.get_event(alice.id, created.id)
        assert still is not None and still.status == "cancelled"
        assert provider.cancel_event(bob.id, created.id) is None
    finally:
        _cleanup(db, created_ids)
        db.close()


def test_overlap_ignores_touching_endpoints_and_other_users():
    init_db()
    db = SessionLocal()
    created_ids: list[int] = []
    try:
        alice = _user(db, username="cal-alice", user_id=9701)
        bob = _user(db, username="cal-bob", user_id=9702)
        db.commit()
        provider = LocalCalendarProvider(db)
        busy = provider.create_event(
            alice.id,
            title="已有",
            start_at=datetime(2026, 8, 6, 10, 0),
            end_at=datetime(2026, 8, 6, 11, 0),
            participants=[],
        )
        other = provider.create_event(
            bob.id,
            title="别人的",
            start_at=datetime(2026, 8, 6, 10, 0),
            end_at=datetime(2026, 8, 6, 11, 0),
            participants=[],
        )
        created_ids.extend([busy.id, other.id])

        touching = check_conflict(
            db, alice.id, datetime(2026, 8, 6, 11, 0), datetime(2026, 8, 6, 12, 0)
        )
        assert touching == []
        overlap = check_conflict(
            db, alice.id, datetime(2026, 8, 6, 10, 30), datetime(2026, 8, 6, 11, 30)
        )
        assert [row.id for row in overlap] == [busy.id]
        assert check_conflict(
            db, bob.id, datetime(2026, 8, 6, 10, 30), datetime(2026, 8, 6, 11, 30)
        )[0].id == other.id
    finally:
        _cleanup(db, created_ids)
        db.close()


def test_facade_create_event_keeps_orm_row_and_card_keys():
    init_db()
    db = SessionLocal()
    created_ids: list[int] = []
    try:
        alice = _user(db, username="cal-alice", user_id=9701)
        db.commit()
        event = create_event(
            db,
            user_id=alice.id,
            title="周会",
            start_at=datetime(2026, 8, 6, 10, 0),
            end_at=datetime(2026, 8, 6, 11, 0),
            participants=["张三"],
        )
        created_ids.append(event.id)
        assert isinstance(event, CalendarEvent)
        assert event.provider == "local"
        assert event.timezone == ""
        card = serialize_event_card(event)
        assert card["id"] == event.id
        assert card["participants"] == ["张三"]
        assert card["provider"] == "local"
        assert card["external_id"] == ""
    finally:
        _cleanup(db, created_ids)
        db.close()


def test_find_free_slots_uses_gaps_without_working_hours():
    init_db()
    db = SessionLocal()
    created_ids: list[int] = []
    try:
        alice = _user(db, username="cal-alice", user_id=9701)
        db.commit()
        provider = LocalCalendarProvider(db)
        morning = provider.create_event(
            alice.id,
            title="上午",
            start_at=datetime(2026, 8, 6, 10, 0),
            end_at=datetime(2026, 8, 6, 11, 0),
            participants=[],
        )
        afternoon = provider.create_event(
            alice.id,
            title="中午接着",
            start_at=datetime(2026, 8, 6, 11, 0),
            end_at=datetime(2026, 8, 6, 12, 0),
            participants=[],
        )
        created_ids.extend([morning.id, afternoon.id])
        window_start = datetime(2026, 8, 6, 9, 0)
        window_end = datetime(2026, 8, 6, 13, 0)
        duration = timedelta(minutes=30)
        slots = provider.find_free_slots(alice.id, window_start, window_end, duration)
        # 10:00-11:00 touches 11:00-12:00, so 11:00 is not a gap. Night hours are not removed.
        assert slots == [
            (datetime(2026, 8, 6, 9, 0), datetime(2026, 8, 6, 9, 30)),
            (datetime(2026, 8, 6, 12, 0), datetime(2026, 8, 6, 12, 30)),
        ]
        assert provider.find_free_slots(alice.id, window_start, window_end, duration, limit=1) == [
            (datetime(2026, 8, 6, 9, 0), datetime(2026, 8, 6, 9, 30))
        ]
        blocked = provider.find_free_slots(
            alice.id,
            datetime(2026, 8, 6, 10, 0),
            datetime(2026, 8, 6, 12, 0),
            timedelta(hours=1),
            exclude_event_id=afternoon.id,
        )
        assert blocked == [(datetime(2026, 8, 6, 11, 0), datetime(2026, 8, 6, 12, 0))]
        freed = provider.find_free_slots(
            alice.id,
            datetime(2026, 8, 6, 10, 0),
            datetime(2026, 8, 6, 12, 0),
            timedelta(hours=1),
            exclude_event_id=morning.id,
        )
        assert freed == [(datetime(2026, 8, 6, 10, 0), datetime(2026, 8, 6, 11, 0))]
    finally:
        _cleanup(db, created_ids)
        db.close()
