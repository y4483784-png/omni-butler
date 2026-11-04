from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, ScheduleConfirmClaim, Session as ChatSession
from app.services.calendar import void_draft_schedule_cards
from fastapi.testclient import TestClient


def _body(title: str, token: str | None = None) -> dict:
    payload = {
        "title": title,
        "start_at": "2026-11-04T10:00:00",
        "end_at": "2026-11-04T11:00:00",
        "participants": [],
    }
    if token is not None:
        payload["confirm_token"] = token
    return payload


def _count(title: str) -> int:
    db = SessionLocal()
    try:
        return db.query(CalendarEvent).filter(CalendarEvent.title == title).count()
    finally:
        db.close()


def _delete_title(title: str) -> None:
    db = SessionLocal()
    try:
        db.query(CalendarEvent).filter(CalendarEvent.title == title).delete()
        db.commit()
    finally:
        db.close()


def _session() -> int:
    init_db()
    db = SessionLocal()
    try:
        row = ChatSession(user_id=1, title="确认门禁")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def test_create_without_a_token_still_writes(auth_client: TestClient):
    title = "确认门禁直接写"
    try:
        saved = auth_client.post("/api/calendar", json=_body(title))
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_closed_token_does_not_create(auth_client: TestClient):
    title = "确认门禁作废"
    saved = auth_client.post("/api/calendar", json=_body(title, "not-an-open-claim"))
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_void_cancels_an_open_claim(auth_client: TestClient):
    title = "确认门禁下一句"
    session_id = _session()
    opened = auth_client.post("/api/calendar/confirm-claims", json={"session_id": session_id})
    assert opened.status_code == 200, opened.text
    token = opened.json()["confirm_token"]
    db = SessionLocal()
    try:
        void_draft_schedule_cards(db, session_id)
    finally:
        db.close()
    saved = auth_client.post("/api/calendar", json=_body(title, token))
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_one_claim_writes_once(auth_client: TestClient):
    title = "确认门禁一次"
    session_id = _session()
    opened = auth_client.post("/api/calendar/confirm-claims", json={"session_id": session_id})
    assert opened.status_code == 200, opened.text
    token = opened.json()["confirm_token"]
    try:
        first = auth_client.post("/api/calendar", json=_body(title, token))
        assert first.status_code == 200, first.text
        second = auth_client.post("/api/calendar", json=_body(title, token))
        assert second.status_code == 409, second.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_expired_claim_does_not_create(auth_client: TestClient):
    title = "确认门禁过期"
    token = str(uuid4())
    session_id = _session()
    db = SessionLocal()
    try:
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=1,
                session_id=session_id,
                status="open",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
            )
        )
        db.commit()
    finally:
        db.close()
    saved = auth_client.post("/api/calendar", json=_body(title, token))
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_patch_and_delete_honor_a_closed_token(auth_client: TestClient):
    title = "确认门禁改期"
    try:
        saved = auth_client.post("/api/calendar", json=_body(title))
        assert saved.status_code == 200, saved.text
        event_id = saved.json()["id"]
        patched = auth_client.patch(
            f"/api/calendar/{event_id}",
            json={"title": "确认门禁改期后", "confirm_token": "not-an-open-claim"},
        )
        assert patched.status_code == 409, patched.text
        removed = auth_client.delete(f"/api/calendar/{event_id}?confirm_token=not-an-open-claim")
        assert removed.status_code == 409, removed.text
        db = SessionLocal()
        try:
            row = db.get(CalendarEvent, event_id)
            assert row is not None
            assert row.title == title
            assert row.status == "active"
        finally:
            db.close()
    finally:
        _delete_title(title)
        _delete_title("确认门禁改期后")
