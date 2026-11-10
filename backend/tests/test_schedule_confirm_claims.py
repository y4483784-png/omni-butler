from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, ScheduleConfirmClaim, Session as ChatSession, User
from app.services import task_run
from app.services.calendar import void_draft_schedule_cards
from fastapi.testclient import TestClient


def _admin_id() -> int:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.external_id == "admin").one().id
    finally:
        db.close()


def _body(title: str, token: str | None = None, **extra) -> dict:
    payload = {
        "title": title,
        "start_at": extra.pop("start_at", "2026-11-04T10:00:00"),
        "end_at": extra.pop("end_at", "2026-11-04T11:00:00"),
        "participants": extra.pop("participants", []),
        **extra,
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
        row = ChatSession(user_id=_admin_id(), title="确认门禁")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _open(client: TestClient, body: dict, *, action: str = "create", session_id: int | None = None, **raw):
    if session_id is None:
        session_id = _session()
    turn = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    payload = {
        "session_id": session_id,
        "action": action,
        "title": body.get("title"),
        "start_at": body.get("start_at"),
        "end_at": body.get("end_at"),
        "participants": body.get("participants") or [],
        "location": body.get("location") or "",
        "description": body.get("description") or "",
        "meet": bool(body.get("meet")),
        "event_id": raw.get("event_id", body.get("event_id")),
        "external_id": raw.get("external_id", body.get("external_id")),
        "response": raw.get("response", body.get("response")),
    }
    opened = client.post("/api/calendar/confirm-claims", json=payload)
    assert opened.status_code == 200, opened.text
    return opened.json()["confirm_token"], session_id, turn


def test_create_without_a_token_does_not_write(plain_client: TestClient):
    title = "确认门禁直接写"
    saved = plain_client.post("/api/calendar", json=_body(title))
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "缺少确认，未写入。"
    assert _count(title) == 0


def test_closed_token_does_not_create(plain_client: TestClient):
    title = "确认门禁作废"
    saved = plain_client.post("/api/calendar", json=_body(title, "not-an-open-claim"))
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_void_cancels_an_open_claim(plain_client: TestClient):
    title = "确认门禁下一句"
    body = _body(title)
    token, session_id, _turn = _open(plain_client, body)
    db = SessionLocal()
    try:
        void_draft_schedule_cards(db, session_id)
        db.commit()
    finally:
        db.close()
    saved = plain_client.post("/api/calendar", json=_body(title, token))
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_one_claim_writes_once(plain_client: TestClient):
    title = "确认门禁一次"
    body = _body(title)
    token, _session_id, _turn = _open(plain_client, body)
    try:
        first = plain_client.post("/api/calendar", json=_body(title, token))
        assert first.status_code == 200, first.text
        second = plain_client.post("/api/calendar", json=_body(title, token))
        assert second.status_code == 409, second.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_expired_claim_does_not_create(plain_client: TestClient):
    title = "确认门禁过期"
    token = str(uuid4())
    session_id = _session()
    db = SessionLocal()
    try:
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=_admin_id(),
                session_id=session_id,
                status="open",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
                action="calendar.create",
                fingerprint=token,
                scope=f"calendar:user:{_admin_id()}",
                generation_ids="[]",
            )
        )
        db.commit()
    finally:
        db.close()
    saved = plain_client.post("/api/calendar", json=_body(title, token))
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_patch_and_delete_honor_a_closed_token(plain_client: TestClient):
    title = "确认门禁改期"
    body = _body(title)
    token, _session_id, _turn = _open(plain_client, body)
    try:
        saved = plain_client.post("/api/calendar", json=_body(title, token))
        assert saved.status_code == 200, saved.text
        event_id = saved.json()["id"]
        patched = plain_client.patch(
            f"/api/calendar/{event_id}",
            json={"title": "确认门禁改期后", "confirm_token": "not-an-open-claim"},
        )
        assert patched.status_code == 409, patched.text
        removed = plain_client.delete(f"/api/calendar/{event_id}?confirm_token=not-an-open-claim")
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
