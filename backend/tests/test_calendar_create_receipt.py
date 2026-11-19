"""A connected create is success only after the stored event id is sealed."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.models.models import (
    CalendarEvent,
    EventReceipt,
    Message,
    ScheduleConfirmClaim,
    Session as ChatSession,
)
from app.services import task_run
from app.services.calendar_secrets import sign_oauth_state
from app.services.message_format import serialize_message
from app.services.schedule_confirm import phase_for_stored_card
from tests.test_calendar_google import CLIENT_SECRET, CODE, FERNET_KEY, FakeGoogle, _cleanup, _insert_calls


@pytest.fixture
def oauth_ready(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(
        settings,
        "google_oauth_redirect_uri",
        "http://localhost:5173/api/calendar/google/callback",
    )
    monkeypatch.setattr(settings, "calendar_token_key", FERNET_KEY)
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173")


@pytest.fixture
def fake_google(monkeypatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr("app.services.calendar_secrets._http_request", fake)
    return fake


def _lookup_calls(fake: FakeGoogle) -> list[dict]:
    return [
        call
        for call in fake.calls
        if call["method"] == "GET"
        and "/calendars/primary/events" in (call.get("url") or "")
        and "privateExtendedProperty" in (call.get("params") or {})
    ]


def _request_id(call: dict) -> str:
    body = call.get("json") or {}
    extended = body.get("extendedProperties") or {}
    private = extended.get("private") or {}
    return str(private.get("requestId") or "")


def _create_body(title: str, *, start: str, end: str) -> dict:
    return {
        "title": title,
        "start_at": start,
        "end_at": end,
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
    }


def _open(client: TestClient, body: dict) -> tuple[str, int, int]:
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="创建回执")
        db.add(row)
        db.commit()
        db.refresh(row)
        session_id = row.id
    finally:
        db.close()
    task_run.begin_turn(user_id=user_id, session_id=session_id)
    opened = client.post(
        "/api/calendar/confirm-claims",
        json={
            "session_id": session_id,
            "action": "create",
            "title": body.get("title"),
            "start_at": body.get("start_at"),
            "end_at": body.get("end_at"),
            "participants": body.get("participants") or [],
            "location": body.get("location") or "",
            "description": body.get("description") or "",
            "meet": bool(body.get("meet")),
        },
    )
    assert opened.status_code == 200, opened.text
    return opened.json()["confirm_token"], session_id, user_id


def _connect(client: TestClient, user_id: int) -> None:
    response = client.get(
        "/api/calendar/google/callback",
        params={"code": CODE, "state": sign_oauth_state(user_id)},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text


def _post(client: TestClient, body: dict, token: str):
    return client.post("/api/calendar", json={**body, "confirm_token": token})


def _titles(user_id: int, title: str) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(CalendarEvent)
            .filter(CalendarEvent.user_id == user_id, CalendarEvent.title == title)
            .all()
        )
        return [row.external_id or "" for row in rows]
    finally:
        db.close()


def _claim(token: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        if row is None:
            return None
        return {"status": row.status, "remote_request_id": row.remote_request_id or ""}
    finally:
        db.close()


def _receipt(user_id: int, token: str) -> dict | None:
    db = SessionLocal()
    try:
        rows = (
            db.query(EventReceipt)
            .filter(
                EventReceipt.user_id == user_id,
                EventReceipt.source == "internal",
                EventReceipt.delivery_id == f"cal:{token}",
            )
            .all()
        )
        if len(rows) != 1:
            return {"count": len(rows)}
        row = rows[0]
        return {
            "count": 1,
            "id": row.id,
            "state": row.state,
            "body": row.body,
            "external_request_id": row.external_request_id or "",
            "claim_token": row.claim_token or "",
        }
    finally:
        db.close()


def _drop_receipts(user_id: int) -> None:
    db = SessionLocal()
    try:
        (
            db.query(EventReceipt)
            .filter(EventReceipt.user_id == user_id, EventReceipt.delivery_id.like("cal:%"))
            .delete(synchronize_session=False)
        )
        db.commit()
    finally:
        db.close()


def test_connected_create_seals_one_event_and_one_receipt(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    title = f"回执创建-{uuid4().hex[:8]}"
    body = _create_body(title, start="2031-02-17T09:00:00", end="2031-02-17T10:00:00")
    token, _session_id, user_id = _open(auth_client, body)
    try:
        _connect(auth_client, user_id)
        fake_google.insert_id = "evt-created"
        created = _post(auth_client, body, token)
        assert created.status_code == 200, created.text
        stored_id = created.json()["external_id"]
        assert stored_id == "evt-created"
        assert _titles(user_id, title) == [stored_id]
        inserts = _insert_calls(fake_google)
        assert len(inserts) == 1
        request_id = _request_id(inserts[0])
        assert request_id
        assert request_id != stored_id
        claim = _claim(token)
        assert claim is not None
        assert claim["status"] == "written"
        assert claim["remote_request_id"] == request_id
        assert claim["remote_request_id"] != stored_id
        receipt = _receipt(user_id, token)
        assert receipt is not None
        assert receipt["count"] == 1
        assert receipt["state"] == "external_success"
        assert stored_id in receipt["body"]
        assert receipt["external_request_id"] == request_id
        assert receipt["claim_token"] == token
    finally:
        _drop_receipts(user_id)
        _cleanup(user_id)


def test_second_confirm_with_the_same_token_does_not_insert(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    title = f"回执再次-{uuid4().hex[:8]}"
    body = _create_body(title, start="2031-02-18T09:00:00", end="2031-02-18T10:00:00")
    token, _session_id, user_id = _open(auth_client, body)
    try:
        _connect(auth_client, user_id)
        fake_google.insert_id = "evt-once"
        first = _post(auth_client, body, token)
        assert first.status_code == 200, first.text
        again = _post(auth_client, body, token)
        assert again.status_code == 409, again.text
        assert _titles(user_id, title) == ["evt-once"]
        assert len(_insert_calls(fake_google)) == 1
    finally:
        _drop_receipts(user_id)
        _cleanup(user_id)


def test_a_new_confirm_token_posts_another_create(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    title = f"回执另开-{uuid4().hex[:8]}"
    first_body = _create_body(title, start="2031-02-19T09:00:00", end="2031-02-19T10:00:00")
    token, _session_id, user_id = _open(auth_client, first_body)
    try:
        _connect(auth_client, user_id)
        fake_google.insert_id = "evt-first"
        first = _post(auth_client, first_body, token)
        assert first.status_code == 200, first.text
        fake_google.insert_id = "evt-second"
        second = auth_client.post(
            "/api/calendar",
            json=_create_body(title, start="2031-02-19T14:00:00", end="2031-02-19T15:00:00"),
        )
        assert second.status_code == 200, second.text
        assert second.json()["external_id"] == "evt-second"
        assert second.json()["external_id"] != first.json()["external_id"]
        assert sorted(_titles(user_id, title)) == ["evt-first", "evt-second"]
        assert len(_insert_calls(fake_google)) == 2
    finally:
        _drop_receipts(user_id)
        _cleanup(user_id)


def test_missing_event_id_looks_up_once_and_stays_unknown(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    title = f"回执丢响应-{uuid4().hex[:8]}"
    body = _create_body(title, start="2031-02-20T09:00:00", end="2031-02-20T10:00:00")
    token, _session_id, user_id = _open(auth_client, body)
    try:
        _connect(auth_client, user_id)
        fake_google.insert_id = ""
        created = _post(auth_client, body, token)
        assert created.status_code == 409, created.text
        assert _titles(user_id, title) == []
        assert len(_insert_calls(fake_google)) == 1
        lookups = _lookup_calls(fake_google)
        assert len(lookups) == 1
        assert "privateExtendedProperty" in lookups[0]["params"]
        claim = _claim(token)
        assert claim is not None
        assert claim["status"] == "unknown"
        assert claim["remote_request_id"]
        assert lookups[0]["params"]["privateExtendedProperty"] == f"requestId={claim['remote_request_id']}"
        assert _receipt(user_id, token) == {"count": 0}
    finally:
        _drop_receipts(user_id)
        _cleanup(user_id)


def test_lookup_hit_stores_one_event_and_seals_without_another_insert(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    title = f"回执找回-{uuid4().hex[:8]}"
    body = _create_body(title, start="2031-02-21T09:00:00", end="2031-02-21T10:00:00")
    token, _session_id, user_id = _open(auth_client, body)
    found_id = "evt-looked-up"

    def on_event(method, url, payload):
        if method != "POST" or not str(url).rstrip("/").endswith("/calendars/primary/events"):
            return
        if not isinstance(payload, dict):
            return
        extended = payload.get("extendedProperties")
        private = extended.get("private") if isinstance(extended, dict) else None
        request_id = str(private.get("requestId") or "") if isinstance(private, dict) else ""
        if not request_id:
            return
        fake_google.events_items.append(
            {
                "id": found_id,
                "extendedProperties": {"private": {"requestId": request_id}},
            }
        )

    try:
        _connect(auth_client, user_id)
        fake_google.insert_id = ""
        fake_google.on_event = on_event
        created = _post(auth_client, body, token)
        assert created.status_code == 200, created.text
        assert created.json()["external_id"] == found_id
        assert _titles(user_id, title) == [found_id]
        assert len(_insert_calls(fake_google)) == 1
        lookups = _lookup_calls(fake_google)
        assert len(lookups) == 1
        assert "privateExtendedProperty" in lookups[0]["params"]
        inserts = _insert_calls(fake_google)
        request_id = _request_id(inserts[0])
        claim = _claim(token)
        assert claim is not None
        assert claim["status"] == "written"
        assert claim["remote_request_id"] == request_id
        assert claim["remote_request_id"] != found_id
        receipt = _receipt(user_id, token)
        assert receipt is not None
        assert receipt["count"] == 1
        assert receipt["state"] == "external_success"
        assert found_id in receipt["body"]
        assert receipt["external_request_id"] == request_id
    finally:
        _drop_receipts(user_id)
        _cleanup(user_id)


def test_repair_updates_one_unsealed_receipt_and_does_not_post(
    auth_client: TestClient, fake_google: FakeGoogle
):
    init_db()
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]
    token = str(uuid4())
    armed = f"req-{uuid4().hex}"
    event_id = f"evt-{uuid4().hex}"
    db = SessionLocal()
    try:
        session = ChatSession(user_id=user_id, title="创建回执")
        db.add(session)
        db.commit()
        db.refresh(session)
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=user_id,
                session_id=session.id,
                status="written",
                action="calendar.create",
                fingerprint=uuid4().hex,
                scope=f"calendar:user:{user_id}",
                generation_ids="[]",
                remote_request_id=armed,
            )
        )
        event = CalendarEvent(
            user_id=user_id,
            title="已写入未封口",
            start_at=datetime(2031, 3, 2, 9, 0),
            end_at=datetime(2031, 3, 2, 10, 0),
            status="active",
            external_id=event_id,
            provider="google",
        )
        db.add(event)
        db.flush()
        db.add(
            EventReceipt(
                user_id=user_id,
                session_id=session.id,
                source="internal",
                delivery_id=f"cal:{token}",
                fingerprint="",
                state="received",
                route="filed",
                claim_token=token,
                external_request_id="",
                body="",
            )
        )
        db.commit()
        event_row_id = event.id
        from app.api.calendar import record_create_success

        before = list(fake_google.calls)
        record_create_success(db, user_id, token, event_row_id)
        record_create_success(db, user_id, token, event_row_id)
        assert fake_google.calls == before
        rows = (
            db.query(EventReceipt)
            .filter(
                EventReceipt.user_id == user_id,
                EventReceipt.source == "internal",
                EventReceipt.delivery_id == f"cal:{token}",
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].state == "external_success"
        assert rows[0].body == event_id
        assert rows[0].external_request_id == armed
        assert rows[0].external_request_id != event_id
    finally:
        db.rollback()
        db.close()
        _drop_receipts(user_id)
        _cleanup(user_id)


def test_written_create_stays_incomplete_until_the_receipt_is_filed(
    auth_client: TestClient, fake_google: FakeGoogle
):
    init_db()
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]
    token = str(uuid4())
    armed = f"req-{uuid4().hex}"
    event_id = f"evt-{uuid4().hex}"
    title = f"未回执-{uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        session = ChatSession(user_id=user_id, title="创建回执")
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id
        db.add(
            ScheduleConfirmClaim(
                token=token,
                user_id=user_id,
                session_id=session_id,
                status="written",
                action="calendar.create",
                fingerprint=uuid4().hex,
                scope=f"calendar:user:{user_id}",
                generation_ids="[]",
                remote_request_id=armed,
            )
        )
        event = CalendarEvent(
            user_id=user_id,
            title=title,
            start_at=datetime(2031, 3, 3, 9, 0),
            end_at=datetime(2031, 3, 3, 10, 0),
            status="active",
            external_id=event_id,
            provider="google",
        )
        db.add(event)
        db.flush()
        event_row_id = event.id
        db.add(
            Message(
                session_id=session_id,
                role="assistant",
                content="请确认",
                schedule_card=json.dumps(
                    {
                        "phase": "confirmed",
                        "mode": "create",
                        "title": title,
                        "confirm_token": token,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()

        listed = auth_client.get(f"/api/sessions/{session_id}/messages")
        assert listed.status_code == 200, listed.text
        assert listed.json()[-1]["scheduleCard"]["phase"] == "unknown"
        stored = db.query(Message).filter(Message.session_id == session_id, Message.role == "assistant").one()
        db.refresh(stored)
        assert json.loads(stored.schedule_card)["phase"] == "confirmed"
        assert serialize_message(stored)["scheduleCard"]["phase"] == "unknown"
        assert json.loads(stored.schedule_card)["phase"] == "confirmed"
        draft = phase_for_stored_card(db, {"phase": "draft", "confirm_token": token, "title": title})
        assert draft is not None
        assert draft["phase"] == "draft"

        from app.api.calendar import record_create_success

        before = list(fake_google.calls)
        record_create_success(db, user_id, token, event_row_id)
        assert fake_google.calls == before
        receipt = _receipt(user_id, token)
        assert receipt is not None
        assert receipt["count"] == 1
        assert receipt["state"] == "external_success"
        assert event_id in receipt["body"]
        assert receipt["external_request_id"] == armed

        again = auth_client.get(f"/api/sessions/{session_id}/messages")
        assert again.status_code == 200, again.text
        assert again.json()[-1]["scheduleCard"]["phase"] == "confirmed"
        repaired = phase_for_stored_card(db, {"phase": "draft", "confirm_token": token, "title": title})
        assert repaired is not None
        assert repaired["phase"] == "confirmed"
    finally:
        db.rollback()
        db.close()
        _drop_receipts(user_id)
        _cleanup(user_id)
