"""Google Calendar connection, tokens, factory, and account timezone. No live Google calls."""

from __future__ import annotations

import base64
import inspect
import json
import logging
import time as unix_time
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.models.models import CalendarConnection, CalendarEvent, ToolAuditLog, User
from app.agents.harness.types import ToolContext
from app.agents.tools import calendar_tool
from app.agents.tools.calendar_tool import execute_calendar
from app.services.calendar import (
    EventDraft,
    calendar_now,
    create_event,
    extract_event,
    find_event_candidates,
    titles_match,
)
from app.services.calendar_factory import calendar_provider_for
from app.services.calendar_google import (
    GoogleCalendarProvider,
    WorkingHoursWindow,
    parse_working_hours,
)
from app.services.calendar_local import LocalCalendarProvider
from app.services.calendar_secrets import (
    GOOGLE_CALENDAR_SCOPES,
    GOOGLE_IDENTITY_SCOPES,
    CalendarTokenError,
    CalendarTransientError,
    decrypt_token,
    encrypt_token,
    install_log_redaction,
    refresh_access_token,
    sign_oauth_state,
)

ACCESS = "ya29.access-token-do-not-log"
REFRESH = "1//refresh-token-do-not-log"
CODE = "auth-code-do-not-log"
CLIENT_SECRET = "google-client-secret-do-not-log"
FERNET_KEY = Fernet.generate_key().decode("ascii")
SECRETS = (ACCESS, REFRESH, CODE, CLIENT_SECRET)
EVENT_COLUMNS = {
    "id",
    "user_id",
    "title",
    "start_at",
    "end_at",
    "participants",
    "status",
    "timezone",
    "location",
    "description",
    "external_id",
    "provider",
    "created_at",
}


class FakeGoogle:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.token_status = 200
        self.tokeninfo_status = 200
        self.primary_status = 200
        self.sub = "google-sub-1"
        self.email = "owner@example.com"
        self.access_token = ACCESS
        self.refresh_token = REFRESH
        self.expires_in = 3600
        self.scope = " ".join(GOOGLE_CALENDAR_SCOPES)
        self.id_token = ""
        self.tokeninfo_payload = None
        self.include_refresh = True
        self.timezone_status = 404
        self.timezone_value = ""
        self.events_status = 200
        self.events_items: list[dict] = []
        self.always_next_page = False
        self.pages_served = 0
        self.insert_status = 200
        self.insert_id = "evt-created"
        self.patch_status = 200
        self.delete_status = 204
        self.deleted_ids: list[str] = []
        self.network_on: set[str] = set()
        self.on_event = None
        self.settings_items: list[dict] | None = None
        self.settings_status = 200

    def __call__(self, method, url, *, data=None, params=None, headers=None, json=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "data": data,
                "params": params,
                "headers": headers,
                "json": json,
            }
        )
        if method in self.network_on and "/calendars/primary/events" in (url or ""):
            raise httpx.ConnectError("down")
        event_response = self._events_response(method, url, json)
        if event_response is not None:
            return event_response
        if url.endswith("/token"):
            body = {
                "access_token": self.access_token,
                "expires_in": self.expires_in,
                "scope": self.scope,
                "token_type": "Bearer",
            }
            if self.include_refresh:
                body["refresh_token"] = self.refresh_token
            if self.id_token:
                body["id_token"] = self.id_token
            grant = (data or {}).get("grant_type")
            if grant == "refresh_token" and self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(self.token_status, json=body)
        if url.endswith("/tokeninfo"):
            payload = (
                {"sub": self.sub, "email": self.email, "scope": self.scope}
                if self.tokeninfo_payload is None
                else self.tokeninfo_payload
            )
            return httpx.Response(self.tokeninfo_status, json=payload)
        if url.endswith("/calendars/primary"):
            return httpx.Response(self.primary_status, json={"id": self.email})
        if url.endswith("/users/me/settings/timezone"):
            if self.timezone_status == 404:
                return httpx.Response(404, json={"error": {"message": "not found"}})
            return httpx.Response(
                self.timezone_status,
                json={"id": "timezone", "value": self.timezone_value},
            )
        if url.rstrip("/").endswith("/users/me/settings"):
            if self.settings_items is None:
                return httpx.Response(404, json={"error": {"message": "not found"}})
            if self.settings_status != 200:
                return httpx.Response(self.settings_status, json={"error": {"message": "settings failed"}})
            return httpx.Response(200, json={"items": list(self.settings_items)})
        return httpx.Response(404, json={"error": "not-found"})

    def _events_response(self, method, url, body):
        path = url.split("?", 1)[0]
        collection = path.rstrip("/").endswith("/calendars/primary/events")
        item = "/calendars/primary/events/" in path
        if not collection and not item:
            return None
        if self.on_event is not None:
            self.on_event(method, url, body)
        if method == "GET" and collection:
            if self.events_status != 200:
                return httpx.Response(self.events_status, json={"error": {"message": "list failed"}})
            self.pages_served += 1
            payload = {"items": list(self.events_items)}
            if self.always_next_page:
                payload["nextPageToken"] = "next"
            return httpx.Response(200, json=payload)
        if method == "POST" and collection:
            if self.insert_status not in (200, 201):
                return httpx.Response(self.insert_status, json={"error": {"message": "insert failed"}})
            return httpx.Response(
                self.insert_status, json={"id": self.insert_id, "status": "confirmed"}
            )
        if method == "PATCH" and item:
            if self.patch_status not in (200, 201):
                return httpx.Response(self.patch_status, json={"error": {"message": "patch failed"}})
            event_id = path.rstrip("/").rsplit("/", 1)[-1]
            payload = {"id": event_id, "status": "confirmed"}
            if isinstance(body, dict):
                payload.update({key: value for key, value in body.items() if key != "id"})
            return httpx.Response(self.patch_status, json=payload)
        if method == "DELETE" and item:
            self.deleted_ids.append(path.rstrip("/").rsplit("/", 1)[-1])
            if self.delete_status in (200, 204):
                return httpx.Response(self.delete_status)
            return httpx.Response(self.delete_status, json={"error": {"message": "delete failed"}})
        return httpx.Response(404, json={"error": "not-found"})


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


def _me(client: TestClient) -> dict:
    response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()


def _callback(client: TestClient, user_id: int, *, state: str | None = None, code: str = CODE, **params):
    query = {"code": code, "state": state if state is not None else sign_oauth_state(user_id)}
    query.update(params)
    return client.get("/api/calendar/google/callback", params=query, follow_redirects=False)


def _cleanup(*user_ids: int) -> None:
    db = SessionLocal()
    try:
        if user_ids:
            db.query(CalendarConnection).filter(CalendarConnection.user_id.in_(user_ids)).delete(
                synchronize_session=False
            )
            db.query(CalendarEvent).filter(CalendarEvent.user_id.in_(user_ids)).delete(
                synchronize_session=False
            )
        db.commit()
    finally:
        db.close()


def _assert_secrets_absent(blob: str) -> None:
    for secret in SECRETS:
        assert secret not in blob


def test_only_factory_chooses_calendar_provider():
    root = Path(__file__).resolve().parents[1] / "app"
    factory = (root / "services/calendar_factory.py").read_text(encoding="utf-8")
    google = (root / "services/calendar_google.py").read_text(encoding="utf-8")
    api = (root / "api/calendar.py").read_text(encoding="utf-8")
    service = (root / "services/calendar.py").read_text(encoding="utf-8")
    tool = (root / "agents/tools/calendar_tool.py").read_text(encoding="utf-8")
    assert "return LocalCalendarProvider" in factory
    assert "return GoogleCalendarProvider" in factory
    for src in (api, service, tool):
        assert "calendar_provider_for" in src
        assert "LocalCalendarProvider" not in src
        assert "GoogleCalendarProvider(" not in src
    assert "GoogleCalendarProvider(" not in google
    assert "/calendars/primary/events" in google
    assert "/calendars/primary/events" not in service
    assert "/calendars/primary/events" not in api
    assert "/calendars/primary/events" not in tool
    assert "events.insert" not in service
    assert "events.list" not in api


def test_missing_client_id_returns_clear_error(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "calendar_token_key", FERNET_KEY)
    response = auth_client.get("/api/calendar/google/connect", follow_redirects=False)
    assert response.status_code == 400
    assert "GOOGLE_OAUTH_CLIENT_ID" in response.json()["detail"]
    assert "accounts.google.com" not in response.text


def test_connect_redirects_with_offline_consent_and_exact_scopes(
    auth_client: TestClient, oauth_ready
):
    response = auth_client.get("/api/calendar/google/connect", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    query = parse_qs(parsed.query)
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["include_granted_scopes"] == ["false"]
    assert query["response_type"] == ["code"]
    granted = set(query["scope"][0].split())
    assert granted == set(GOOGLE_CALENDAR_SCOPES) | set(GOOGLE_IDENTITY_SCOPES)
    assert set(GOOGLE_CALENDAR_SCOPES) < granted
    assert CLIENT_SECRET not in location
    assert "calendar.events.freebusy" not in location
    assert "https://www.googleapis.com/auth/calendar" not in set(query["scope"][0].split())
    assert "https://www.googleapis.com/auth/calendar.readonly" not in set(query["scope"][0].split())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _google_id_token(
    *,
    sub: str,
    email: str,
    aud: str = "google-client-id",
    iss: str = "https://accounts.google.com",
    exp: int | None = None,
) -> str:
    if exp is None:
        exp = int(unix_time.time()) + 3600
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url(
        json.dumps(
            {"sub": sub, "email": email, "aud": aud, "iss": iss, "exp": exp},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{header}.{payload}.signature"


def test_connect_uses_id_token_when_tokeninfo_lacks_sub(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, caplog
):
    caplog.set_level(logging.DEBUG)
    init_db()
    me = _me(auth_client)
    fake_google.tokeninfo_payload = {"scope": fake_google.scope, "expires_in": 3599}
    fake_google.primary_status = 404
    fake_google.id_token = _google_id_token(sub="sub-from-id-token", email="id-token@example.com")
    try:
        response = _callback(auth_client, me["id"])
        assert response.status_code == 302, response.text
        assert any(call["url"].endswith("/tokeninfo") for call in fake_google.calls)
        assert all("access_token=" not in call["url"] for call in fake_google.calls)
        assert "missing-sub" not in caplog.text
        assert fake_google.id_token not in caplog.text
        db = SessionLocal()
        try:
            row = db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).one()
            assert row.google_sub == "sub-from-id-token"
            assert row.account_email == "id-token@example.com"
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_connect_uses_id_token_when_tokeninfo_errors(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    me = _me(auth_client)
    fake_google.tokeninfo_status = 500
    fake_google.tokeninfo_payload = {"error": "backendError"}
    fake_google.primary_status = 404
    fake_google.id_token = _google_id_token(sub="sub-despite-tokeninfo", email="still@example.com")
    try:
        response = _callback(auth_client, me["id"])
        assert response.status_code == 302, response.text
        db = SessionLocal()
        try:
            row = db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).one()
            assert row.google_sub == "sub-despite-tokeninfo"
            assert row.account_email == "still@example.com"
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_bad_id_token_falls_back_to_tokeninfo_sub(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, caplog
):
    caplog.set_level(logging.DEBUG)
    init_db()
    me = _me(auth_client)
    fake_google.id_token = _google_id_token(
        sub="sub-other-client", email="other@example.com", aud="other-client"
    )
    try:
        response = _callback(auth_client, me["id"])
        assert response.status_code == 302, response.text
        assert "status=bad-aud" in caplog.text
        assert fake_google.id_token not in caplog.text
        db = SessionLocal()
        try:
            row = db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).one()
            assert row.google_sub == "google-sub-1"
            assert row.account_email == "owner@example.com"
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


@pytest.mark.parametrize(
    "kind",
    ["absent", "malformed", "bad-aud", "expired", "blank-sub"],
)
def test_connect_fails_when_identity_cannot_be_established(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, caplog, kind
):
    caplog.set_level(logging.DEBUG)
    init_db()
    me = _me(auth_client)
    fake_google.tokeninfo_payload = {
        "scope": fake_google.scope,
        "expires_in": 3599,
        "aud": "google-client-id",
    }
    if kind == "malformed":
        fake_google.id_token = "not-a-jwt"
    elif kind == "bad-aud":
        fake_google.id_token = _google_id_token(
            sub="sub-x", email="a@example.com", aud="other-client"
        )
    elif kind == "expired":
        fake_google.id_token = _google_id_token(
            sub="sub-x", email="a@example.com", exp=int(unix_time.time()) - 3600
        )
    elif kind == "blank-sub":
        fake_google.id_token = _google_id_token(sub="", email="a@example.com")
    try:
        response = _callback(auth_client, me["id"])
        assert response.status_code == 502
        assert response.json()["detail"] == "无法确认 Google 账户，未保存连接。"
        assert "status=missing-sub" in caplog.text
        if fake_google.id_token:
            assert fake_google.id_token not in caplog.text
        assert ACCESS not in caplog.text
        db = SessionLocal()
        try:
            assert (
                db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).count()
                == 0
            )
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_anonymous_callback_and_mismatched_state_write_nothing(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    from app.main import app

    init_db()
    me = _me(auth_client)
    anon = TestClient(app)
    anon_response = anon.get(
        "/api/calendar/google/callback",
        params={"code": CODE, "state": sign_oauth_state(me["id"])},
        follow_redirects=False,
    )
    assert anon_response.status_code == 401

    mismatch = _callback(auth_client, me["id"], state=sign_oauth_state(me["id"] + 50))
    assert mismatch.status_code == 400
    assert "不一致" in mismatch.json()["detail"]
    assert fake_google.calls == []

    bad = _callback(auth_client, me["id"], state="not-a-signed-state")
    assert bad.status_code == 400
    assert fake_google.calls == []

    expired = URLSafeTimedSerializer(settings.secret_key, salt="omni-calendar-google-oauth").dumps(
        {"user_id": me["id"], "exp": int(datetime.now(timezone.utc).timestamp()) - 30}
    )
    stale = _callback(auth_client, me["id"], state=expired)
    assert stale.status_code == 400
    assert fake_google.calls == []

    db = SessionLocal()
    try:
        assert db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).count() == 0
        assert EVENT_COLUMNS == {column.name for column in CalendarEvent.__table__.columns}
        assert "access_token" not in EVENT_COLUMNS
    finally:
        db.close()


def test_callback_stores_ciphertext_and_not_on_events(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, caplog
):
    caplog.set_level(logging.DEBUG)
    init_db()
    me = _me(auth_client)
    try:
        response = _callback(auth_client, me["id"])
        assert response.status_code == 302
        assert response.headers["location"].startswith("http://localhost:5173/")
        assert len(fake_google.calls) == 3
        assert all(CLIENT_SECRET not in call["url"] for call in fake_google.calls)
        assert all("access_token=" not in call["url"] for call in fake_google.calls)

        db = SessionLocal()
        try:
            row = db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).one()
            assert row.provider == "google"
            assert row.google_sub == "google-sub-1"
            assert row.account_email == "owner@example.com"
            assert row.access_token != ACCESS
            assert row.refresh_token != REFRESH
            assert ACCESS not in row.access_token
            assert REFRESH not in row.refresh_token
            assert decrypt_token(row.access_token) == ACCESS
            assert decrypt_token(row.refresh_token) == REFRESH
            assert row.access_token.startswith("gAAAA")
            for event in db.query(CalendarEvent).all():
                blob = " ".join(str(getattr(event, name) or "") for name in EVENT_COLUMNS)
                _assert_secrets_absent(blob)
        finally:
            db.close()

        created = auth_client.post(
            "/api/calendar",
            json={
                "title": "连接后的日程",
                "start_at": "2026-11-03T09:00:00",
                "end_at": "2026-11-03T10:00:00",
                "participants": [],
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["provider"] == "google"
        assert body["external_id"] == "evt-created"
        assert body["timezone"] == "Etc/GMT"
        assert body["start_at"].startswith("2026-11-03T09:00:00")
        event_calls = [call for call in fake_google.calls if "/calendars/primary/events" in call["url"]]
        assert any(call["method"] == "GET" for call in event_calls)
        assert any(call["method"] == "POST" for call in event_calls)
        assert all("syncToken" not in (call.get("params") or {}) for call in event_calls)
        _assert_secrets_absent(caplog.text)
        db = SessionLocal()
        try:
            audit = " ".join((item.args or "") for item in db.query(ToolAuditLog).all())
            _assert_secrets_absent(audit)
            stored = db.get(CalendarEvent, body["id"])
            assert stored is not None
            assert ACCESS not in (stored.description or "")
            assert REFRESH not in (stored.title or "")
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_second_row_and_different_sub_rejected_disconnect_clears_status(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    me = _me(auth_client)
    other_id = 77041
    db = SessionLocal()
    try:
        if db.get(User, other_id) is None:
            db.add(User(id=other_id, external_id="cal-google-other", name="other", is_active=1))
            db.commit()
        first = CalendarConnection(
            user_id=other_id,
            provider="google",
            google_sub="sub-a",
            account_email="a@example.com",
            access_token="cipher-a",
            refresh_token="cipher-b",
            scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
        )
        db.add(first)
        db.commit()
        db.add(
            CalendarConnection(
                user_id=other_id,
                provider="google",
                google_sub="sub-b",
                account_email="b@example.com",
                access_token="cipher-c",
                refresh_token="cipher-d",
                scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()
        _cleanup(other_id)

    try:
        assert _callback(auth_client, me["id"]).status_code == 302
        fake_google.sub = "google-sub-other"
        fake_google.email = "other@example.com"
        fake_google.access_token = "ya29.second-access-do-not-log"
        rejected = _callback(auth_client, me["id"])
        assert rejected.status_code == 409
        assert "断开" in rejected.json()["detail"]
        db = SessionLocal()
        try:
            rows = db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).all()
            assert len(rows) == 1
            assert rows[0].google_sub == "google-sub-1"
            assert decrypt_token(rows[0].access_token) == ACCESS
        finally:
            db.close()

        status = auth_client.get("/api/calendar/google/status")
        assert status.status_code == 200
        assert status.json() == {
            "connected": True,
            "account_email": "owner@example.com",
            "timezone": "Etc/GMT",
        }
        assert any(call["url"].endswith("/users/me/settings/timezone") for call in fake_google.calls)
        assert all("/events" not in call["url"] for call in fake_google.calls)
        gone = auth_client.delete("/api/calendar/google/connection")
        assert gone.status_code == 200
        assert gone.json()["connected"] is False
        again = auth_client.get("/api/calendar/google/status")
        assert again.json()["connected"] is False
        assert again.json()["account_email"] == ""
        db = SessionLocal()
        try:
            assert db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).count() == 0
        finally:
            db.close()
    finally:
        _cleanup(me["id"], other_id)


def test_disconnect_keeps_local_events(auth_client: TestClient, oauth_ready, fake_google: FakeGoogle):
    init_db()
    me = _me(auth_client)
    try:
        assert _callback(auth_client, me["id"]).status_code == 302
        created = auth_client.post(
            "/api/calendar",
            json={
                "title": "断开后还在",
                "start_at": "2026-11-04T09:00:00",
                "end_at": "2026-11-04T10:00:00",
                "participants": ["甲"],
            },
        )
        assert created.status_code == 200, created.text
        event_id = created.json()["id"]
        assert auth_client.delete("/api/calendar/google/connection").status_code == 200
        db = SessionLocal()
        try:
            event = db.get(CalendarEvent, event_id)
            assert event is not None
            assert event.status == "active"
            assert event.provider == "google"
            assert event.external_id == "evt-created"
            assert event.title == "断开后还在"
            assert db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).count() == 0
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_same_sub_reconnect_updates_one_row(auth_client: TestClient, oauth_ready, fake_google: FakeGoogle):
    init_db()
    me = _me(auth_client)
    try:
        assert _callback(auth_client, me["id"]).status_code == 302
        fake_google.access_token = "ya29.refreshed-access-do-not-log"
        fake_google.email = "owner@example.com"
        assert _callback(auth_client, me["id"]).status_code == 302
        db = SessionLocal()
        try:
            rows = db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).all()
            assert len(rows) == 1
            assert rows[0].google_sub == "google-sub-1"
            assert decrypt_token(rows[0].access_token) == "ya29.refreshed-access-do-not-log"
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_exchange_failure_and_denied_consent_write_nothing(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    me = _me(auth_client)
    try:
        denied = auth_client.get(
            "/api/calendar/google/callback",
            params={"error": "access_denied", "state": sign_oauth_state(me["id"])},
            follow_redirects=False,
        )
        assert denied.status_code == 400
        assert fake_google.calls == []
        fake_google.token_status = 500
        failed = _callback(auth_client, me["id"])
        assert failed.status_code == 502
        db = SessionLocal()
        try:
            assert db.query(CalendarConnection).filter(CalendarConnection.user_id == me["id"]).count() == 0
        finally:
            db.close()
    finally:
        _cleanup(me["id"])


def test_partial_unique_external_id_allows_blank_local_rows():
    init_db()
    user_a = 77051
    user_b = 77052
    db = SessionLocal()
    try:
        for user_id, name in ((user_a, "ext-a"), (user_b, "ext-b")):
            if db.get(User, user_id) is None:
                db.add(User(id=user_id, external_id=name, name=name, is_active=1))
        db.commit()
        start = datetime(2026, 11, 5, 9, 0)
        db.add(
            CalendarEvent(
                user_id=user_a,
                title="one",
                start_at=start,
                end_at=start + timedelta(hours=1),
                external_id="",
                provider="local",
            )
        )
        db.add(
            CalendarEvent(
                user_id=user_a,
                title="two",
                start_at=start,
                end_at=start + timedelta(hours=1),
                external_id="",
                provider="local",
            )
        )
        db.commit()
        db.add(
            CalendarEvent(
                user_id=user_a,
                title="google",
                start_at=start,
                end_at=start + timedelta(hours=1),
                external_id="evt-shared",
                provider="google",
            )
        )
        db.commit()
        db.add(
            CalendarEvent(
                user_id=user_b,
                title="other-user",
                start_at=start,
                end_at=start + timedelta(hours=1),
                external_id="evt-shared",
                provider="google",
            )
        )
        db.commit()
        db.add(
            CalendarEvent(
                user_id=user_a,
                title="dup",
                start_at=start,
                end_at=start + timedelta(hours=1),
                external_id="evt-shared",
                provider="google",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        _cleanup(user_a, user_b)
        db.close()


def test_reassign_moves_calendar_connection():
    init_db()
    src_id = 77061
    dst_id = 77062
    db = SessionLocal()
    try:
        for user_id, name in ((src_id, "reassign-src"), (dst_id, "reassign-dst")):
            if db.get(User, user_id) is None:
                db.add(User(id=user_id, external_id=name, name=name, is_active=1))
        db.commit()
        db.add(
            CalendarConnection(
                user_id=src_id,
                provider="google",
                google_sub="sub-move",
                account_email="move@example.com",
                access_token="cipher",
                refresh_token="cipher",
                scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
            )
        )
        db.commit()
        from app.core.bootstrap import _reassign_user_rows

        _reassign_user_rows(db, src_id=src_id, dst_id=dst_id)
        db.commit()
        moved = db.query(CalendarConnection).filter(CalendarConnection.user_id == dst_id).one()
        assert moved.google_sub == "sub-move"
        assert db.query(CalendarConnection).filter(CalendarConnection.user_id == src_id).count() == 0
    finally:
        _cleanup(src_id, dst_id)
        db.close()


def test_secrets_check_requires_token_key_when_client_id_set(monkeypatch):
    from app.core import secrets_check

    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "calendar_token_key", "")
    monkeypatch.setattr(settings, "secret_key", "already-changed")
    monkeypatch.setattr(settings, "s3_access_key", "not-default")
    monkeypatch.setattr(settings, "s3_secret_key", "not-default")
    monkeypatch.setattr(settings, "database_url", "sqlite:///./omni_butler.db")
    hits = secrets_check.insecure_secret_findings()
    assert "CALENDAR_TOKEN_KEY" in hits

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    assert "CALENDAR_TOKEN_KEY" not in secrets_check.insecure_secret_findings()

    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "calendar_token_key", "")
    monkeypatch.setattr(settings, "enforce_secure_secrets", True)
    monkeypatch.setattr(secrets_check, "_in_pytest", lambda: False)
    with pytest.raises(RuntimeError) as raised:
        secrets_check.check_insecure_secrets()
    assert "CALENDAR_TOKEN_KEY" in str(raised.value)


def test_refresh_failure_does_not_log_tokens_or_touch_events(
    monkeypatch, caplog, oauth_ready, fake_google: FakeGoogle
):
    caplog.set_level(logging.DEBUG)
    init_db()
    user_id = 77071
    db = SessionLocal()
    try:
        if db.get(User, user_id) is None:
            db.add(User(id=user_id, external_id="refresh-user", name="refresh", is_active=1))
            db.commit()
        start = datetime(2026, 11, 6, 9, 0)
        event = CalendarEvent(
            user_id=user_id,
            title="保持不动",
            start_at=start,
            end_at=start + timedelta(hours=1),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(event)
        stored_access = encrypt_token(ACCESS)
        stored_refresh = encrypt_token(REFRESH)
        connection = CalendarConnection(
            user_id=user_id,
            provider="google",
            google_sub="google-sub-1",
            account_email="owner@example.com",
            access_token=stored_access,
            refresh_token=stored_refresh,
            token_expiry=datetime.now(timezone.utc) - timedelta(minutes=5),
            scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        db.refresh(event)
        fake_google.token_status = 400
        with pytest.raises(CalendarTokenError):
            refresh_access_token(db, connection)
        db.refresh(connection)
        db.refresh(event)
        assert connection.access_token == stored_access
        assert connection.refresh_token == stored_refresh
        assert event.status == "active"
        assert event.title == "保持不动"
        _assert_secrets_absent(caplog.text)
        assert "status=400" in caplog.text
    finally:
        _cleanup(user_id)
        db.close()


def test_httpx_log_filter_redacts_tokens():
    install_log_redaction()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="HTTP Request: %s %s",
        args=("GET", "https://oauth2.googleapis.com/tokeninfo?access_token=" + ACCESS),
        exc_info=None,
    )
    assert logging.getLogger("httpx").filter(record)
    rendered = record.getMessage()
    assert ACCESS not in rendered
    assert "access_token=[redacted]" in rendered


_FIXED_UTC = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FIXED_UTC.astimezone().replace(tzinfo=None)
        return _FIXED_UTC.astimezone(tz)


def _freeze_calendar_clock(monkeypatch) -> None:
    monkeypatch.setattr("app.services.calendar.datetime", _FrozenDateTime)


def _ensure_user(db, user_id: int, name: str) -> None:
    if db.get(User, user_id) is None:
        db.add(User(id=user_id, external_id=name, name=name, is_active=1))
        db.commit()


def _store_connection(db, user_id: int) -> None:
    expiry = datetime.now(timezone.utc) + timedelta(hours=2)
    row = db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).one_or_none()
    fields = dict(
        provider="google",
        google_sub=f"sub-{user_id}",
        account_email="owner@example.com",
        access_token=encrypt_token(ACCESS),
        refresh_token=encrypt_token(REFRESH),
        token_expiry=expiry,
        scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
    )
    if row is None:
        db.add(CalendarConnection(user_id=user_id, **fields))
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    db.commit()


def _empty_slots(*_args, **_kwargs):
    return {
        "title": "会",
        "start_at": "",
        "end_at": "",
        "participants": [],
        "missing_fields": [],
    }


def _refuse_process_zone(self):
    raise AssertionError("process timezone")


def test_unconnected_provider_matches_local_and_skips_google(oauth_ready, fake_google: FakeGoogle):
    init_db()
    user_id = 77111
    db = SessionLocal()
    try:
        _ensure_user(db, user_id, "unconnected-cal")
        before = len(fake_google.calls)
        provider = calendar_provider_for(db, user_id)
        local = LocalCalendarProvider(db)
        assert type(provider) is LocalCalendarProvider
        start = datetime(2026, 8, 6, 15, 0)
        end = start + timedelta(hours=1)
        created = provider.create_event(
            user_id,
            title="本地会",
            start_at=start,
            end_at=end,
            participants=["甲"],
            location="会议室",
            description="说明",
        )
        assert created.provider == "local"
        assert created.external_id == ""
        assert created.timezone == ""
        assert local.get_event(user_id, created.id) == created
        listed = provider.list_events(user_id, start - timedelta(minutes=1), end + timedelta(minutes=1))
        assert [row.id for row in listed] == [created.id]
        assert provider.get_event(user_id, created.id).title == "本地会"
        renamed = provider.update_event(user_id, created.id, title="改名")
        assert renamed is not None
        assert renamed.title == "改名"
        assert renamed.provider == "local"
        assert renamed.external_id == ""
        slots = provider.find_free_slots(user_id, start, start + timedelta(days=1), timedelta(hours=1))
        assert slots == local.find_free_slots(user_id, start, start + timedelta(days=1), timedelta(hours=1))
        cancelled = provider.cancel_event(user_id, created.id)
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert cancelled.provider == "local"
        assert provider.list_events(user_id, start, end + timedelta(hours=1)) == []
        process = datetime.now().astimezone()
        assert provider.get_timezone().utcoffset(process) == process.tzinfo.utcoffset(process)
        stored = create_event(
            db,
            user_id=user_id,
            title="服务仍本地",
            start_at=start,
            end_at=end,
            participants=[],
        )
        assert stored.provider == "local"
        assert stored.external_id == ""
        assert len(fake_google.calls) == before
        assert calendar_now(db, user_id).tzinfo is None
    finally:
        _cleanup(user_id)
        db.close()


def test_account_timezone_drives_now_window_and_open_search(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Settings Asia/Shanghai: tomorrow 3:00, the default list, and title search use that zone."""
    _freeze_calendar_clock(monkeypatch)
    monkeypatch.setattr(LocalCalendarProvider, "get_timezone", _refuse_process_zone)
    monkeypatch.setattr("app.services.calendar.complete_json", _empty_slots)
    fake_google.timezone_status = 200
    fake_google.timezone_value = "Asia/Shanghai"
    init_db()
    me = _me(auth_client)
    user_id = me["id"]
    db = SessionLocal()
    try:
        _store_connection(db, user_id)
        before_events = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        provider = calendar_provider_for(db, user_id)
        assert type(provider) is GoogleCalendarProvider
        assert provider.timezone_name() == "Asia/Shanghai"
        zone = provider.get_timezone()
        assert isinstance(zone, ZoneInfo)
        assert zone.key == "Asia/Shanghai"

        now = calendar_now(db, user_id)
        assert now == datetime(2026, 6, 2, 4, 0)
        assert now != datetime(2026, 6, 1, 20, 0)
        draft = extract_event("明天三点开会", now=now)
        assert draft.start_at == datetime(2026, 6, 3, 3, 0)
        assert draft.start_at != datetime(2026, 6, 2, 3, 0)

        drafted = execute_calendar(
            ToolContext(db=db, message="明天三点开会", history=[], user_id=user_id)
        )
        assert drafted.schedule_card["start_at"].startswith("2026-06-03T03:00:00")
        assert drafted.schedule_card["phase"] == "draft"
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before_events

        night = CalendarEvent(
            user_id=user_id,
            title="账户时区昨夜",
            start_at=datetime(2026, 6, 1, 21, 0),
            end_at=datetime(2026, 6, 1, 22, 0),
            status="active",
            provider="local",
            external_id="",
        )
        day = CalendarEvent(
            user_id=user_id,
            title="账户时区白天",
            start_at=datetime(2026, 6, 3, 10, 0),
            end_at=datetime(2026, 6, 3, 11, 0),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(night)
        db.add(day)
        db.commit()
        found = find_event_candidates(db, user_id, title="账户时区", start_at=None)
        assert [row.id for row in found] == [day.id]

        listed = auth_client.get("/api/calendar")
        assert listed.status_code == 200, listed.text
        titles = {row["title"] for row in listed.json()}
        assert "账户时区白天" in titles
        assert "账户时区昨夜" not in titles

        calls_before_write = len(fake_google.calls)
        saved = create_event(
            db,
            user_id=user_id,
            title="连接后写入 Google",
            start_at=datetime(2026, 6, 4, 9, 0),
            end_at=datetime(2026, 6, 4, 10, 0),
            participants=[],
        )
        assert saved.provider == "google"
        assert saved.external_id == "evt-created"
        assert saved.timezone == "Asia/Shanghai"
        assert saved.start_at == datetime(2026, 6, 4, 9, 0)
        assert saved.end_at == datetime(2026, 6, 4, 10, 0)
        assert len(fake_google.calls) > calls_before_write

        status = auth_client.get("/api/calendar/google/status")
        assert status.status_code == 200, status.text
        assert status.json()["connected"] is True
        assert status.json()["timezone"] == "Asia/Shanghai"
        zone_calls = [call for call in fake_google.calls if call["url"].endswith("/settings/timezone")]
        assert zone_calls
        assert all(call["method"] == "GET" for call in zone_calls)
        assert all(ACCESS not in call["url"] for call in zone_calls)
        assert all(not (call.get("params") or {}) for call in zone_calls)
        event_calls = [call for call in fake_google.calls if "/calendars/primary/events" in call["url"]]
        assert event_calls
        listed = [call for call in event_calls if call["method"] == "GET"]
        assert listed
        assert all((call.get("params") or {}).get("singleEvents") == "true" for call in listed)
        assert all("syncToken" not in (call.get("params") or {}) for call in event_calls)
    finally:
        _cleanup(user_id)
        db.close()


def test_missing_timezone_is_etc_gmt_not_process_zone(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    _freeze_calendar_clock(monkeypatch)
    monkeypatch.setattr(LocalCalendarProvider, "get_timezone", _refuse_process_zone)
    init_db()
    me = _me(auth_client)
    user_id = me["id"]
    db = SessionLocal()
    try:
        _store_connection(db, user_id)
        cases = (
            (404, "", "missing"),
            (200, "", "empty"),
            (200, "   ", "blank"),
            (200, "Not/AZone", "invalid"),
        )
        for status_code, value, _label in cases:
            fake_google.timezone_status = status_code
            fake_google.timezone_value = value
            provider = calendar_provider_for(db, user_id)
            assert provider.timezone_name() == "Etc/GMT"
            zone = provider.get_timezone()
            assert isinstance(zone, ZoneInfo)
            assert zone.key == "Etc/GMT"
            assert calendar_now(db, user_id) == datetime(2026, 6, 1, 20, 0)
            body = auth_client.get("/api/calendar/google/status").json()
            assert body["connected"] is True
            assert body["timezone"] == "Etc/GMT"

        fake_google.timezone_status = 500
        fake_google.timezone_value = "Asia/Shanghai"
        zone_calls_before = [
            call for call in fake_google.calls if call["url"].endswith("/settings/timezone")
        ]
        with pytest.raises(CalendarTransientError) as raised:
            calendar_now(db, user_id)
        assert "重新连接" not in str(raised.value)
        zone_calls_after = [
            call for call in fake_google.calls if call["url"].endswith("/settings/timezone")
        ]
        assert len(zone_calls_after) == len(zone_calls_before) + 1
        failed = auth_client.get("/api/calendar/google/status")
        assert failed.status_code == 200
        assert failed.json()["connected"] is True
        assert failed.json()["timezone"] == ""
        assert all("/events" not in call["url"] for call in fake_google.calls)
    finally:
        _cleanup(user_id)
        db.close()


def _event_calls(fake: FakeGoogle) -> list[dict]:
    return [call for call in fake.calls if "/calendars/primary/events" in call["url"]]


def _insert_calls(fake: FakeGoogle) -> list[dict]:
    return [
        call
        for call in _event_calls(fake)
        if call["method"] == "POST" and call["url"].rstrip("/").endswith("/calendars/primary/events")
    ]


def _patch_calls(fake: FakeGoogle) -> list[dict]:
    return [call for call in _event_calls(fake) if call["method"] == "PATCH"]


def _delete_calls(fake: FakeGoogle) -> list[dict]:
    return [call for call in _event_calls(fake) if call["method"] == "DELETE"]


def _list_calls(fake: FakeGoogle) -> list[dict]:
    return [
        call
        for call in _event_calls(fake)
        if call["method"] == "GET" and call["url"].rstrip("/").endswith("/calendars/primary/events")
    ]


def _google_item(
    event_id: str,
    start: str,
    end: str,
    *,
    summary: str = "已有安排",
    transparency: str = "",
    event_type: str = "",
    status: str = "confirmed",
    declined: bool = False,
    all_day: bool = False,
) -> dict:
    if all_day:
        start_body = {"date": start}
        end_body = {"date": end}
    else:
        start_body = {"dateTime": start, "timeZone": "Asia/Shanghai"}
        end_body = {"dateTime": end, "timeZone": "Asia/Shanghai"}
    item = {
        "id": event_id,
        "status": status,
        "summary": summary,
        "start": start_body,
        "end": end_body,
    }
    if transparency:
        item["transparency"] = transparency
    if event_type:
        item["eventType"] = event_type
    if declined:
        item["attendees"] = [{"email": "owner@example.com", "self": True, "responseStatus": "declined"}]
    return item


def _connect_admin(auth_client: TestClient, db, fake: FakeGoogle, zone: str = "Asia/Shanghai") -> int:
    fake.timezone_status = 200
    fake.timezone_value = zone
    user_id = _me(auth_client)["id"]
    _store_connection(db, user_id)
    return user_id


def test_provider_signatures_include_location_and_description():
    import inspect

    from app.services.calendar_provider import CalendarProvider

    for name in ("create_event", "update_event"):
        params = inspect.signature(getattr(CalendarProvider, name)).parameters
        assert "location" in params
        assert "description" in params


def test_unconnected_confirm_stays_local(auth_client: TestClient, oauth_ready, fake_google: FakeGoogle):
    init_db()
    user_id = _me(auth_client)["id"]
    before = len(fake_google.calls)
    try:
        created = auth_client.post(
            "/api/calendar",
            json={
                "title": "未连接",
                "start_at": "2026-08-06T15:00:00",
                "end_at": "2026-08-06T16:00:00",
                "participants": ["甲"],
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["provider"] == "local"
        assert body["external_id"] == ""
        assert _event_calls(fake_google) == []
        assert len(fake_google.calls) == before
    finally:
        _cleanup(user_id)


def test_connected_create_writes_google_then_local_wall_clock(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        created = auth_client.post(
            "/api/calendar",
            json={
                "title": "产品评审",
                "start_at": "2026-08-06T15:00:00+08:00",
                "end_at": "2026-08-06T16:00:00+08:00",
                "participants": ["张三"],
                "location": "会议室",
                "description": "只留在本地的说明",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["provider"] == "google"
        assert body["external_id"] == "evt-created"
        assert body["timezone"] == "Asia/Shanghai"
        assert body["start_at"].startswith("2026-08-06T15:00:00")
        assert body["end_at"].startswith("2026-08-06T16:00:00")
        assert "+08:00" not in body["start_at"]
        assert body["participants"] == ["张三"]
        assert body["location"] == "会议室"
        assert body["description"] == "只留在本地的说明"

        row = db.get(CalendarEvent, body["id"])
        assert row is not None
        assert row.provider == "google"
        assert row.external_id == "evt-created"
        assert row.timezone == "Asia/Shanghai"
        assert row.start_at == datetime(2026, 8, 6, 15, 0)
        assert row.end_at == datetime(2026, 8, 6, 16, 0)
        assert row.start_at.tzinfo is None
        assert "张三" in (row.participants or "")
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before + 1

        inserts = _insert_calls(fake_google)
        assert len(inserts) == 1
        payload = inserts[0]["json"]
        assert payload["summary"] == "产品评审"
        assert payload["location"] == "会议室"
        assert payload["description"] == "只留在本地的说明"
        assert payload["start"] == {"dateTime": "2026-08-06T15:00:00", "timeZone": "Asia/Shanghai"}
        assert payload["end"] == {"dateTime": "2026-08-06T16:00:00", "timeZone": "Asia/Shanghai"}
        assert "Z" not in payload["start"]["dateTime"]
        assert "attendees" not in payload
        assert "sendUpdates" not in (inserts[0].get("params") or {})
        assert "sendUpdates" not in inserts[0]["url"]
        assert "张三" not in str(payload)

        listed = [call for call in _event_calls(fake_google) if call["method"] == "GET"]
        assert listed
        params = listed[0]["params"]
        assert params["singleEvents"] == "true"
        assert params["orderBy"] == "startTime"
        assert params["showDeleted"] == "false"
        assert params["timeZone"] == "Asia/Shanghai"
        assert params["timeMin"] == "2026-08-06T15:00:00+08:00"
        assert params["timeMax"] == "2026-08-06T16:00:00+08:00"
        assert "syncToken" not in params
        assert fake_google.calls.index(listed[0]) < fake_google.calls.index(inserts[0])

        provider = calendar_provider_for(db, user_id)
        assert provider.get_event(user_id, body["id"]) is not None
        assert provider.get_event(user_id + 99, body["id"]) is None
    finally:
        _cleanup(user_id)
        db.close()


def test_google_insert_failure_writes_no_local_row(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.insert_status = 500
        failed = auth_client.post(
            "/api/calendar",
            json={
                "title": "写失败",
                "start_at": "2026-08-06T15:00:00",
                "end_at": "2026-08-06T16:00:00",
            },
        )
        assert failed.status_code == 502
        assert failed.status_code != 200
        assert "未保存" in failed.json()["detail"]
        assert _insert_calls(fake_google)
        assert fake_google.deleted_ids == []
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "写失败").count() == 0
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
    finally:
        _cleanup(user_id)
        db.close()


def test_google_overlap_is_409_and_does_not_insert(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        local = CalendarEvent(
            user_id=user_id,
            title="本地会",
            start_at=datetime(2026, 8, 6, 16, 0),
            end_at=datetime(2026, 8, 6, 17, 0),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(local)
        db.commit()
        db.refresh(local)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_items = [
            _google_item("evt-google", "2026-08-06T15:00:00+08:00", "2026-08-06T16:00:00+08:00", summary="外面的会")
        ]
        overlap = auth_client.post(
            "/api/calendar",
            json={
                "title": "撞车",
                "start_at": "2026-08-06T15:30:00",
                "end_at": "2026-08-06T16:30:00",
            },
        )
        assert overlap.status_code == 409
        detail = overlap.json()["detail"]
        titles = {item["title"] for item in detail["conflicts"]}
        assert "Google 日历上已有安排" in titles
        assert "本地会" in titles
        google_rows = [item for item in detail["conflicts"] if item["title"] == "Google 日历上已有安排"]
        assert google_rows[0]["id"] is None
        assert google_rows[0]["readonly"] is True
        local_rows = [item for item in detail["conflicts"] if item["title"] == "本地会"]
        assert local_rows[0]["id"] == local.id
        assert detail["suggested_slots"]
        slot_start = datetime.fromisoformat(detail["suggested_slots"][0]["start_at"])
        assert slot_start >= datetime(2026, 8, 6, 17, 0)
        assert _insert_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "撞车").count() == 0
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
    finally:
        _cleanup(user_id)
        db.close()


def test_events_list_failure_on_confirm_is_502(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_status = 500
        failed = auth_client.post(
            "/api/calendar",
            json={
                "title": "读失败",
                "start_at": "2026-08-06T15:00:00",
                "end_at": "2026-08-06T16:00:00",
            },
        )
        assert failed.status_code == 502
        assert failed.status_code != 200
        assert failed.json()["detail"] == "暂时读不到 Google 日历，未写入。"
        assert _insert_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        fake_google.events_status = 200
        fake_google.always_next_page = True
        fake_google.pages_served = 0
        truncated = auth_client.post(
            "/api/calendar",
            json={
                "title": "没列完",
                "start_at": "2026-08-07T15:00:00",
                "end_at": "2026-08-07T16:00:00",
            },
        )
        assert truncated.status_code == 502
        assert truncated.json()["detail"] == "暂时读不到 Google 日历，未写入。"
        assert fake_google.pages_served == 5
        assert _insert_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "没列完").count() == 0
    finally:
        _cleanup(user_id)
        db.close()


def test_local_insert_failure_deletes_google_event(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch, caplog
):
    from sqlalchemy.orm import Session as OrmSession

    caplog.set_level(logging.ERROR)
    init_db()
    db = SessionLocal()
    user_id = 0
    original = OrmSession.commit

    def fail_new_google_row(self):
        pending = [
            obj
            for obj in self.new
            if isinstance(obj, CalendarEvent) and getattr(obj, "provider", "") == "google"
        ]
        if pending:
            raise RuntimeError("local insert failed")
        return original(self)

    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        monkeypatch.setattr(OrmSession, "commit", fail_new_google_row)
        failed = auth_client.post(
            "/api/calendar",
            json={
                "title": "本地失败",
                "start_at": "2026-08-06T15:00:00",
                "end_at": "2026-08-06T16:00:00",
            },
        )
        assert failed.status_code == 502
        assert failed.status_code != 200
        assert "本地保存失败" in failed.json()["detail"]
        assert fake_google.deleted_ids == ["evt-created"]
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "本地失败").count() == 0

        fake_google.deleted_ids.clear()
        fake_google.delete_status = 500
        again = auth_client.post(
            "/api/calendar",
            json={
                "title": "补偿也失败",
                "start_at": "2026-08-08T15:00:00",
                "end_at": "2026-08-08T16:00:00",
            },
        )
        assert again.status_code == 502
        assert again.status_code != 200
        assert fake_google.deleted_ids == ["evt-created"]
        assert "event_id=evt-created" in caplog.text
        assert ACCESS not in caplog.text
        assert REFRESH not in caplog.text
        assert CLIENT_SECRET not in caplog.text
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "补偿也失败").count() == 0
    finally:
        _cleanup(user_id)
        db.close()


def test_duplicate_external_id_deletes_google_event_and_fails_confirm(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        existing = CalendarEvent(
            user_id=user_id,
            title="早已同步",
            start_at=datetime(2026, 1, 1, 9, 0),
            end_at=datetime(2026, 1, 1, 10, 0),
            status="active",
            provider="google",
            external_id="evt-created",
            timezone="Asia/Shanghai",
        )
        db.add(existing)
        db.commit()
        failed = auth_client.post(
            "/api/calendar",
            json={
                "title": "重复外部 id",
                "start_at": "2026-08-06T15:00:00",
                "end_at": "2026-08-06T16:00:00",
            },
        )
        assert failed.status_code == 502
        assert failed.status_code != 200
        assert fake_google.deleted_ids == ["evt-created"]
        rows = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).all()
        assert len(rows) == 1
        assert rows[0].id == existing.id
        assert rows[0].title == "早已同步"
        provider = GoogleCalendarProvider(db, user_id)
        assert provider.get_event(user_id + 50, existing.id) is None
        assert provider.get_event(user_id, existing.id) is not None
    finally:
        _cleanup(user_id)
        db.close()


def test_matched_google_event_conflicts_as_the_local_row(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        synced = CalendarEvent(
            user_id=user_id,
            title="已同步会",
            start_at=datetime(2026, 8, 6, 15, 0),
            end_at=datetime(2026, 8, 6, 16, 0),
            status="active",
            provider="google",
            external_id="evt-live",
        )
        db.add(synced)
        db.commit()
        db.refresh(synced)
        fake_google.events_items = [
            _google_item(
                "evt-live",
                "2026-08-06T15:00:00+08:00",
                "2026-08-06T16:00:00+08:00",
                summary="Google 上的新标题",
            )
        ]
        overlap = auth_client.post(
            "/api/calendar",
            json={
                "title": "再约",
                "start_at": "2026-08-06T15:15:00",
                "end_at": "2026-08-06T15:45:00",
            },
        )
        assert overlap.status_code == 409
        conflicts = overlap.json()["detail"]["conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["id"] == synced.id
        assert conflicts[0]["title"] == "已同步会"
        assert _insert_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_non_busy_google_events_do_not_block_create(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        provider = calendar_provider_for(db, user_id)
        start = datetime(2026, 8, 6, 15, 0)
        end = datetime(2026, 8, 6, 16, 0)
        cases = [
            _google_item("clear", "2026-08-06T15:00:00+08:00", "2026-08-06T16:00:00+08:00", transparency="transparent"),
            _google_item("declined", "2026-08-06T15:00:00+08:00", "2026-08-06T16:00:00+08:00", declined=True),
            _google_item("office", "2026-08-06T15:00:00+08:00", "2026-08-06T16:00:00+08:00", event_type="workingLocation"),
            _google_item("gone", "2026-08-06T15:00:00+08:00", "2026-08-06T16:00:00+08:00", status="cancelled"),
            _google_item("touch", "2026-08-06T14:00:00+08:00", "2026-08-06T15:00:00+08:00"),
        ]
        for item in cases:
            fake_google.events_items = [item]
            assert provider.conflicts_for_create(user_id, start, end) == []

        fake_google.events_items = [
            _google_item("day", "2026-08-06", "2026-08-07", all_day=True, summary="全天")
        ]
        all_day = provider.conflicts_for_create(user_id, start, end)
        assert len(all_day) == 1
        assert all_day[0].id is None
        assert all_day[0].title == "Google 日历上已有安排"
        assert all_day[0].start_at == datetime(2026, 8, 6, 0, 0)
        assert all_day[0].end_at == datetime(2026, 8, 7, 0, 0)

        fake_google.events_items = [
            _google_item("touch", "2026-08-06T14:00:00+08:00", "2026-08-06T15:00:00+08:00")
        ]
        created = auth_client.post(
            "/api/calendar",
            json={"title": "紧挨着", "start_at": "2026-08-06T15:00:00", "end_at": "2026-08-06T16:00:00"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["provider"] == "google"
        assert len(_insert_calls(fake_google)) == 1
    finally:
        _cleanup(user_id)
        db.close()


def test_preview_shows_google_conflict_and_survives_list_failure(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    user_id = 0
    start = datetime(2026, 8, 6, 15, 0)
    end = datetime(2026, 8, 6, 16, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        local = CalendarEvent(
            user_id=user_id,
            title="已有会",
            start_at=start,
            end_at=end,
            status="active",
            provider="local",
            external_id="",
        )
        db.add(local)
        db.commit()
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        monkeypatch.setattr(
            "app.agents.tools.calendar_tool.extract_event",
            lambda *_args, **_kwargs: EventDraft(title="周会", start_at=start, end_at=end),
        )
        fake_google.events_status = 500
        missed = execute_calendar(
            ToolContext(db=db, message="明天三点开周会", history=[], user_id=user_id)
        )
        card = missed.schedule_card
        assert card["phase"] == "draft"
        assert card["mode"] == "create"
        assert card["suggested_slots"] == []
        assert any(item["title"] == "已有会" and item["id"] == local.id for item in card["conflicts"])
        assert "没读到 Google" in (missed.direct_answer or "")
        assert "确认时会再查" in (missed.direct_answer or "")
        assert "不会写入" in (missed.direct_answer or "")
        assert "建议空隙" not in (missed.direct_answer or "")
        assert _insert_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        fake_google.events_status = 200
        fake_google.events_items = [
            _google_item("evt-only", "2026-08-06T18:00:00+08:00", "2026-08-06T19:00:00+08:00", summary="外面的会")
        ]
        db.delete(local)
        db.commit()
        later = datetime(2026, 8, 6, 18, 0)
        monkeypatch.setattr(
            "app.agents.tools.calendar_tool.extract_event",
            lambda *_args, **_kwargs: EventDraft(
                title="周会", start_at=later, end_at=later + timedelta(hours=1)
            ),
        )
        preview = execute_calendar(
            ToolContext(db=db, message="晚上六点开会", history=[], user_id=user_id)
        )
        conflicts = preview.schedule_card["conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["id"] is None
        assert conflicts[0]["title"] == "Google 日历上已有安排"
        assert conflicts[0]["readonly"] is True
        assert preview.schedule_card["candidates"] == []
        assert preview.schedule_card["suggested_slots"]
        slot_start = datetime.fromisoformat(preview.schedule_card["suggested_slots"][0]["start_at"])
        assert slot_start >= datetime(2026, 8, 6, 19, 0)
        assert _insert_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.title == "周会").count() == 0
    finally:
        _cleanup(user_id)
        db.close()


def test_find_free_slots_unions_google_busy_without_working_hours(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        provider = calendar_provider_for(db, user_id)
        night = datetime(2026, 8, 6, 20, 0)
        window_end = datetime(2026, 8, 6, 23, 0)
        fake_google.events_items = []
        open_night = provider.find_free_slots(user_id, night, window_end, timedelta(hours=1))
        assert open_night[0] == (night, night + timedelta(hours=1))

        db.add(
            CalendarEvent(
                user_id=user_id,
                title="本地夜会",
                start_at=datetime(2026, 8, 6, 20, 0),
                end_at=datetime(2026, 8, 6, 21, 0),
                status="active",
                provider="local",
                external_id="",
            )
        )
        db.commit()
        fake_google.events_items = [
            _google_item("night", "2026-08-06T21:00:00+08:00", "2026-08-06T22:00:00+08:00"),
            _google_item(
                "free",
                "2026-08-06T20:00:00+08:00",
                "2026-08-06T23:00:00+08:00",
                transparency="transparent",
            ),
        ]
        slots = provider.find_free_slots(user_id, night, window_end, timedelta(hours=1))
        assert slots == [(datetime(2026, 8, 6, 22, 0), datetime(2026, 8, 6, 23, 0))]
    finally:
        _cleanup(user_id)
        db.close()


def _watch_row(fake: FakeGoogle, event_id: int, methods: set[str]) -> dict:
    """Read the committed row from another session while a Google write is in flight."""
    seen: dict = {}

    def on_event(method, _url, _body):
        if method not in methods:
            return
        fresh = SessionLocal()
        try:
            row = fresh.get(CalendarEvent, event_id)
            assert row is not None
            seen["title"] = row.title
            seen["start_at"] = row.start_at
            seen["provider"] = row.provider
            seen["external_id"] = row.external_id
            seen["status"] = row.status
            seen["timezone"] = row.timezone
        finally:
            fresh.close()

    fake.on_event = on_event
    return seen


def _google_row(db, user_id: int, *, title: str, start: datetime, external_id: str, **extra) -> CalendarEvent:
    row = CalendarEvent(
        user_id=user_id,
        title=title,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="active",
        provider=extra.pop("provider", "google"),
        external_id=external_id,
        timezone=extra.pop("timezone", "America/New_York"),
        participants=extra.pop("participants", "[]"),
        location=extra.pop("location", ""),
        description=extra.pop("description", ""),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_suggest_list_failure_has_no_slot_card(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        fake_google.events_status = 500
        result = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=user_id)
        )
        assert result.schedule_card is None
        assert result.direct_answer == "暂时读不到 Google 日历，不能建议空闲时段。"
        assert _insert_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_update_patches_google_before_local_and_failure_keeps_row(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    """Item 7: patch is visible before the local row changes. A failed patch writes nothing."""
    init_db()
    db = SessionLocal()
    user_id = 0
    start = datetime(2026, 12, 2, 15, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _google_row(db, user_id, title="产品评审", start=start, external_id="evt-self", participants='["甲"]')
        fake_google.calls.clear()
        participants = auth_client.patch(f"/api/calendar/{row.id}", json={"participants": ["李四"]})
        assert participants.status_code == 200, participants.text
        assert participants.json()["participants"] == ["李四"]
        assert _event_calls(fake_google) == []
        db.refresh(row)
        assert row.title == "产品评审"
        assert row.timezone == "America/New_York"
        assert row.provider == "google"

        fake_google.calls.clear()
        seen = _watch_row(fake_google, row.id, {"PATCH"})
        renamed = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={
                "title": "改过的评审",
                "location": "会议室",
                "description": "只改说明",
                "participants": ["王五"],
            },
        )
        assert renamed.status_code == 200, renamed.text
        body = renamed.json()
        assert seen["title"] == "产品评审"
        assert seen["timezone"] == "America/New_York"
        assert body["title"] == "改过的评审"
        assert body["location"] == "会议室"
        assert body["description"] == "只改说明"
        assert body["participants"] == ["王五"]
        assert body["timezone"] == "Asia/Shanghai"
        assert body["start_at"].startswith("2026-12-02T15:00:00")
        db.refresh(row)
        assert row.timezone == "Asia/Shanghai"
        assert row.start_at == start
        patches = _patch_calls(fake_google)
        assert len(patches) == 1
        payload = patches[0]["json"]
        assert payload == {"summary": "改过的评审", "location": "会议室", "description": "只改说明"}
        assert "start" not in payload
        assert "attendees" not in payload
        assert "sendUpdates" not in (patches[0].get("params") or {})
        assert "sendUpdates" not in patches[0]["url"]
        assert "王五" not in str(payload)
        assert "甲" not in str(payload)
        assert _list_calls(fake_google) == []
        assert _insert_calls(fake_google) == []
        assert patches[0]["url"].endswith("/evt-self")

        fake_google.calls.clear()
        fake_google.patch_status = 500
        failed = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={
                "title": "失败标题",
                "start_at": "2026-12-02T17:00:00",
                "end_at": "2026-12-02T18:00:00",
            },
        )
        assert failed.status_code == 502
        assert failed.status_code != 200
        assert "未保存" in failed.json()["detail"]
        assert _patch_calls(fake_google)
        assert _list_calls(fake_google)
        assert fake_google.calls.index(_list_calls(fake_google)[0]) < fake_google.calls.index(
            _patch_calls(fake_google)[0]
        )
        db.refresh(row)
        assert row.title == "改过的评审"
        assert row.start_at == start
        assert row.end_at == start + timedelta(hours=1)
        assert row.status == "active"
    finally:
        _cleanup(user_id)
        db.close()


def test_local_row_inserts_on_first_confirmed_update(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    """Item 7: a pre-connect local row is inserted on Google before the local row changes."""
    init_db()
    db = SessionLocal()
    user_id = 0
    start = datetime(2026, 12, 2, 15, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _google_row(
            db,
            user_id,
            title="旧本地会",
            start=start,
            external_id="",
            provider="local",
            timezone="",
            participants='["甲"]',
        )
        taken = _google_row(
            db,
            user_id,
            title="已经占用",
            start=datetime(2026, 1, 1, 9, 0),
            external_id="evt-created",
        )
        fake_google.events_status = 500
        unread = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"start_at": "2026-12-02T21:00:00", "end_at": "2026-12-02T22:00:00"},
        )
        assert unread.status_code == 502
        assert unread.json()["detail"] == "暂时读不到 Google 日历，未写入。"
        assert _insert_calls(fake_google) == []
        db.refresh(row)
        assert row.provider == "local"
        assert row.external_id == ""
        assert row.start_at == start

        fake_google.events_status = 200
        fake_google.events_items = [
            _google_item("evt-other", "2026-12-02T18:00:00+08:00", "2026-12-02T19:00:00+08:00", summary="外面的会")
        ]
        fake_google.calls.clear()
        overlap = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"start_at": "2026-12-02T18:30:00", "end_at": "2026-12-02T19:30:00"},
        )
        assert overlap.status_code == 409
        assert overlap.json()["detail"]["conflicts"][0]["title"] == "Google 日历上已有安排"
        assert _insert_calls(fake_google) == []
        db.refresh(row)
        assert row.start_at == start
        assert row.provider == "local"

        fake_google.insert_status = 500
        fake_google.calls.clear()
        broken = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"title": "不该写上", "start_at": "2026-12-02T19:30:00", "end_at": "2026-12-02T20:30:00"},
        )
        assert broken.status_code == 502
        assert "未保存" in broken.json()["detail"]
        assert _insert_calls(fake_google)
        db.refresh(row)
        assert row.title == "旧本地会"
        assert row.provider == "local"
        assert row.external_id == ""
        assert row.start_at == start

        fake_google.insert_status = 200
        fake_google.insert_id = "evt-created"
        fake_google.deleted_ids.clear()
        fake_google.calls.clear()
        duplicate = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"start_at": "2026-12-02T19:30:00", "end_at": "2026-12-02T20:30:00"},
        )
        assert duplicate.status_code == 502
        assert duplicate.status_code != 200
        assert fake_google.deleted_ids == ["evt-created"]
        db.refresh(row)
        assert row.provider == "local"
        assert row.external_id == ""
        assert row.title == "旧本地会"
        db.refresh(taken)
        assert taken.external_id == "evt-created"

        fake_google.insert_id = "evt-migrated"
        fake_google.calls.clear()
        seen = _watch_row(fake_google, row.id, {"POST"})
        moved = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={
                "title": "迁到 Google",
                "start_at": "2026-12-02T19:30:00",
                "end_at": "2026-12-02T20:30:00",
            },
        )
        assert moved.status_code == 200, moved.text
        assert seen["provider"] == "local"
        assert seen["external_id"] == ""
        assert seen["title"] == "旧本地会"
        assert seen["start_at"] == start
        body = moved.json()
        assert body["provider"] == "google"
        assert body["external_id"] == "evt-migrated"
        assert body["timezone"] == "Asia/Shanghai"
        assert body["title"] == "迁到 Google"
        assert body["start_at"].startswith("2026-12-02T19:30:00")
        assert body["participants"] == ["甲"]
        listed = _list_calls(fake_google)
        inserts = _insert_calls(fake_google)
        assert listed and inserts
        assert fake_google.calls.index(listed[0]) < fake_google.calls.index(inserts[0])
        payload = inserts[0]["json"]
        assert payload["summary"] == "迁到 Google"
        assert payload["start"] == {"dateTime": "2026-12-02T19:30:00", "timeZone": "Asia/Shanghai"}
        assert "attendees" not in payload
        assert "sendUpdates" not in (inserts[0].get("params") or {})
        assert "甲" not in str(payload)
        assert _patch_calls(fake_google) == []
        db.refresh(row)
        assert row.provider == "google"
        assert row.external_id == "evt-migrated"
    finally:
        _cleanup(user_id)
        db.close()


def test_cancel_deletes_google_first_and_disconnect_refuses_linked_rows(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    """Item 8: soft-cancel only after Google succeeds or is already gone. Disconnect refuses."""
    init_db()
    db = SessionLocal()
    user_id = 0
    start = datetime(2026, 12, 2, 15, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _google_row(db, user_id, title="要取消的会", start=start, external_id="evt-cancel")

        def _still_active(status_code: int) -> None:
            failed = auth_client.delete(f"/api/calendar/{row.id}")
            assert failed.status_code == status_code
            assert failed.status_code != 200
            assert "未改" in failed.json()["detail"]
            assert _list_calls(fake_google) == []
            db.refresh(row)
            assert row.status == "active"
            assert db.get(CalendarEvent, row.id) is not None

        fake_google.calls.clear()
        fake_google.delete_status = 500
        _still_active(502)
        fake_google.calls.clear()
        fake_google.delete_status = 403
        _still_active(502)
        fake_google.calls.clear()
        fake_google.delete_status = 204
        fake_google.network_on.add("DELETE")
        _still_active(502)
        fake_google.network_on.clear()

        fake_google.calls.clear()
        fake_google.delete_status = 404
        missing = auth_client.delete(f"/api/calendar/{row.id}")
        assert missing.status_code == 200, missing.text
        assert missing.json()["status"] == "cancelled"
        assert missing.json()["id"] == row.id
        assert _list_calls(fake_google) == []
        assert [call["method"] for call in _event_calls(fake_google)] == ["DELETE"]
        db.refresh(row)
        assert row.status == "cancelled"

        row.status = "active"
        db.commit()
        fake_google.calls.clear()
        fake_google.delete_status = 410
        gone = auth_client.delete(f"/api/calendar/{row.id}")
        assert gone.status_code == 200, gone.text
        assert gone.json()["status"] == "cancelled"
        db.refresh(row)
        assert row.status == "cancelled"
        assert _list_calls(fake_google) == []

        other = _google_row(db, user_id, title="成功取消", start=start + timedelta(days=1), external_id="evt-gone")
        fake_google.calls.clear()
        fake_google.delete_status = 204
        removed = auth_client.delete(f"/api/calendar/{other.id}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["status"] == "cancelled"
        assert fake_google.deleted_ids[-1] == "evt-gone"
        assert [call["method"] for call in _event_calls(fake_google)] == ["DELETE"]
        db.refresh(other)
        assert other.status == "cancelled"

        local = _google_row(
            db,
            user_id,
            title="纯本地",
            start=start + timedelta(days=2),
            external_id="",
            provider="local",
            timezone="",
        )
        fake_google.calls.clear()
        fake_google.deleted_ids.clear()
        local_cancel = auth_client.delete(f"/api/calendar/{local.id}")
        assert local_cancel.status_code == 200, local_cancel.text
        assert local_cancel.json()["status"] == "cancelled"
        assert local_cancel.json()["provider"] == "local"
        assert _event_calls(fake_google) == []
        assert fake_google.deleted_ids == []

        linked = _google_row(db, user_id, title="断开后不能改", start=start + timedelta(days=3), external_id="evt-linked")
        assert auth_client.delete("/api/calendar/google/connection").status_code == 200
        fake_google.calls.clear()
        refused = auth_client.patch(f"/api/calendar/{linked.id}", json={"title": "不该改"})
        assert refused.status_code == 409
        assert "重新连接" in refused.json()["detail"]
        db.refresh(linked)
        assert linked.title == "断开后不能改"
        assert linked.status == "active"
        refused_delete = auth_client.delete(f"/api/calendar/{linked.id}")
        assert refused_delete.status_code == 409
        assert "重新连接" in refused_delete.json()["detail"]
        db.refresh(linked)
        assert linked.status == "active"
        assert linked.title == "断开后不能改"
        assert _event_calls(fake_google) == []

        plain = _google_row(
            db,
            user_id,
            title="断开后的本地",
            start=start + timedelta(days=4),
            external_id="",
            provider="local",
            timezone="",
        )
        renamed = auth_client.patch(f"/api/calendar/{plain.id}", json={"title": "本地仍可改"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["provider"] == "local"
        assert renamed.json()["title"] == "本地仍可改"
        removed_local = auth_client.delete(f"/api/calendar/{plain.id}")
        assert removed_local.status_code == 200, removed_local.text
        assert removed_local.json()["status"] == "cancelled"
        assert _event_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_update_excludes_self_by_external_id_and_list_failure_writes_nothing(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    """Item 11: own Google event is not a conflict. Another event is. A failed list does not patch."""
    init_db()
    db = SessionLocal()
    user_id = 0
    start = datetime(2026, 12, 2, 15, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _google_row(db, user_id, title="已同步会", start=start, external_id="evt-self")
        stale = _google_row(db, user_id, title="库里的旧时间", start=start, external_id="evt-stale")
        fake_google.events_items = [
            _google_item("evt-self", "2026-12-02T15:00:00+08:00", "2026-12-02T16:00:00+08:00", summary="Google 上的自己"),
            _google_item("evt-stale", "2026-12-02T20:00:00+08:00", "2026-12-02T21:00:00+08:00", summary="已经挪走"),
            _google_item("evt-other", "2026-12-02T15:45:00+08:00", "2026-12-02T16:45:00+08:00", summary="外面的会"),
        ]
        overlap = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"start_at": "2026-12-02T15:30:00", "end_at": "2026-12-02T16:30:00"},
        )
        assert overlap.status_code == 409
        detail = overlap.json()["detail"]
        assert len(detail["conflicts"]) == 1
        conflict = detail["conflicts"][0]
        assert conflict["id"] is None
        assert conflict["readonly"] is True
        assert conflict["title"] == "Google 日历上已有安排"
        assert conflict["start_at"].startswith("2026-12-02T15:45:00")
        assert conflict["end_at"].startswith("2026-12-02T16:45:00")
        assert "07:45" not in conflict["start_at"]
        assert "Z" not in conflict["start_at"]
        assert detail["suggested_slots"]
        assert detail["suggested_slots"][0]["start_at"].startswith("2026-12-02T16:45:00")
        assert _patch_calls(fake_google) == []
        listed = _list_calls(fake_google)
        assert listed
        assert listed[0]["params"]["timeMin"] == "2026-12-02T15:30:00+08:00"
        assert listed[0]["params"]["timeMax"] == "2026-12-02T16:30:00+08:00"
        assert listed[0]["params"]["timeZone"] == "Asia/Shanghai"
        assert "syncToken" not in listed[0]["params"]
        db.refresh(row)
        assert row.start_at == start
        assert row.title == "已同步会"
        db.refresh(stale)
        assert stale.start_at == start

        fake_google.calls.clear()
        fake_google.events_status = 500
        unread = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"start_at": "2026-12-02T15:30:00", "end_at": "2026-12-02T16:30:00"},
        )
        assert unread.status_code == 502
        assert unread.status_code != 200
        assert unread.json()["detail"] == "暂时读不到 Google 日历，未写入。"
        assert _patch_calls(fake_google) == []
        db.refresh(row)
        assert row.start_at == start
        assert row.title == "已同步会"

        fake_google.events_status = 200
        fake_google.events_items = [
            _google_item("evt-self", "2026-12-02T15:00:00+08:00", "2026-12-02T16:00:00+08:00"),
            _google_item("evt-stale", "2026-12-02T20:00:00+08:00", "2026-12-02T21:00:00+08:00"),
        ]
        fake_google.calls.clear()
        seen = _watch_row(fake_google, row.id, {"PATCH"})
        moved = auth_client.patch(
            f"/api/calendar/{row.id}",
            json={"start_at": "2026-12-02T15:30:00", "end_at": "2026-12-02T16:30:00"},
        )
        assert moved.status_code == 200, moved.text
        assert seen["start_at"] == start
        assert seen["title"] == "已同步会"
        assert moved.json()["start_at"].startswith("2026-12-02T15:30:00")
        assert moved.json()["end_at"].startswith("2026-12-02T16:30:00")
        assert moved.json()["timezone"] == "Asia/Shanghai"
        listed = _list_calls(fake_google)
        patches = _patch_calls(fake_google)
        assert listed and patches
        assert fake_google.calls.index(listed[0]) < fake_google.calls.index(patches[0])
        assert patches[0]["json"]["start"] == {
            "dateTime": "2026-12-02T15:30:00",
            "timeZone": "Asia/Shanghai",
        }
        assert "attendees" not in patches[0]["json"]
        db.refresh(row)
        assert row.start_at == datetime(2026, 12, 2, 15, 30)
        db.refresh(stale)
        assert stale.start_at == start
        assert stale.title == "库里的旧时间"
    finally:
        _cleanup(user_id)
        db.close()


def test_update_preview_merges_google_and_survives_list_failure(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    user_id = 0
    start = datetime(2026, 12, 2, 15, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _google_row(db, user_id, title="已同步会", start=start, external_id="evt-self")
        stale = _google_row(db, user_id, title="库里的旧时间", start=start, external_id="evt-stale")
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_items = [
            _google_item("evt-self", "2026-12-02T15:00:00+08:00", "2026-12-02T16:00:00+08:00"),
            _google_item("evt-stale", "2026-12-02T20:00:00+08:00", "2026-12-02T21:00:00+08:00"),
            _google_item("evt-other", "2026-12-02T15:45:00+08:00", "2026-12-02T16:45:00+08:00", summary="外面的会"),
        ]

        def _extract(*_args, **_kwargs):
            return EventDraft(
                title="已同步会",
                start_at=datetime(2026, 12, 2, 15, 30),
                end_at=datetime(2026, 12, 2, 16, 30),
            )

        def _apply(draft, _message, *, now=None):
            draft.title = "已同步会"
            draft.reference_start_at = start
            draft.start_at = datetime(2026, 12, 2, 15, 30)
            draft.end_at = datetime(2026, 12, 2, 16, 30)
            return draft

        monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
        monkeypatch.setattr("app.agents.tools.calendar_tool.apply_update_times", _apply)
        preview = execute_calendar(
            ToolContext(db=db, message="把已同步会改到三点半", history=[], user_id=user_id)
        )
        card = preview.schedule_card
        assert card["mode"] == "update"
        assert card["phase"] == "draft"
        assert card["event_id"] is None
        assert card["title"] == "已同步会"
        assert card["start_at"].startswith("2026-12-02T15:30:00")
        other = [item for item in card["conflicts"] if str(item["start_at"]).startswith("2026-12-02T15:45:00")]
        assert len(other) == 1
        assert other[0]["id"] is None
        assert other[0]["readonly"] is True
        assert other[0]["title"] == "Google 日历上已有安排"
        assert "07:45" not in other[0]["start_at"]
        assert card["suggested_slots"][0]["start_at"].startswith("2026-12-02T16:45:00")
        assert _patch_calls(fake_google) == []
        assert _insert_calls(fake_google) == []
        db.refresh(row)
        assert row.start_at == start

        fake_google.events_status = 500
        missed = execute_calendar(
            ToolContext(db=db, message="把已同步会改到三点半", history=[], user_id=user_id)
        )
        missed_card = missed.schedule_card
        assert missed_card["mode"] == "update"
        assert missed_card["phase"] == "draft"
        assert missed_card["event_id"] is None
        assert missed_card["suggested_slots"] == []
        assert any(item["id"] == stale.id for item in missed_card["conflicts"])
        assert "没读到 Google" in (missed.direct_answer or "")
        assert "确认时会再查" in (missed.direct_answer or "")
        assert "不会写入" in (missed.direct_answer or "")
        assert _patch_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
    finally:
        _cleanup(user_id)
        db.close()


def test_readonly_google_item_has_no_update_or_cancel_card(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Connected update and cancel prefill a confirm card. Google is written only on confirm."""
    init_db()
    db = SessionLocal()
    user_id = 0
    day = datetime(2026, 12, 2, 15, 0)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_items = [
            _google_item("evt-only", "2026-12-02T15:00:00+08:00", "2026-12-02T16:00:00+08:00", summary="外面的会")
        ]

        def _extract(*_args, **_kwargs):
            return EventDraft(title="外面的会", start_at=day, end_at=day + timedelta(hours=1))

        def _apply(draft, _message, *, now=None):
            draft.title = "外面的会"
            draft.reference_start_at = day
            return draft

        monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
        monkeypatch.setattr("app.agents.tools.calendar_tool.apply_update_times", _apply)

        cancel = execute_calendar(
            ToolContext(db=db, message="取消外面的会", history=[], user_id=user_id)
        )
        assert cancel.schedule_card["mode"] == "cancel"
        assert cancel.schedule_card["phase"] == "draft"
        assert cancel.schedule_card["title"] == "外面的会"
        assert cancel.schedule_card["event_id"] is None
        assert cancel.schedule_card["start_at"].startswith("2026-12-02T15:00:00")
        assert "请在 Google 日历里改" not in (cancel.direct_answer or "")
        assert "没有找到对应日程" not in (cancel.direct_answer or "")
        assert _insert_calls(fake_google) == []
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        update = execute_calendar(
            ToolContext(db=db, message="把外面的会改到五点", history=[], user_id=user_id)
        )
        assert update.schedule_card["mode"] == "update"
        assert update.schedule_card["title"] == "外面的会"
        assert update.schedule_card["event_id"] is None
        assert update.schedule_card["start_at"].startswith("2026-12-02T15:00:00")
        assert "请在 Google 日历里改" not in (update.direct_answer or "")
        assert _insert_calls(fake_google) == []
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        local = _google_row(
            db,
            user_id,
            title="外面的会",
            start=day,
            external_id="",
            provider="local",
            timezone="",
        )
        owned = execute_calendar(
            ToolContext(db=db, message="取消外面的会", history=[], user_id=user_id)
        )
        assert owned.schedule_card["mode"] == "cancel"
        assert owned.schedule_card["event_id"] is None
        assert owned.schedule_card["title"] == "外面的会"
        db.refresh(local)
        assert local.status == "active"
        db.delete(local)
        db.commit()

        linked = _google_row(db, user_id, title="别的会", start=day, external_id="evt-only")
        missed_title = execute_calendar(
            ToolContext(db=db, message="取消外面的会", history=[], user_id=user_id)
        )
        assert missed_title.schedule_card["mode"] == "cancel"
        assert missed_title.schedule_card["title"] == "外面的会"
        assert "没有找到对应日程" not in (missed_title.direct_answer or "")
        db.refresh(linked)
        assert linked.status == "active"
        db.delete(linked)
        db.commit()

        fake_google.events_status = 500
        unread = execute_calendar(
            ToolContext(db=db, message="取消外面的会", history=[], user_id=user_id)
        )
        assert unread.schedule_card["mode"] == "cancel"
        assert unread.schedule_card["title"] == "外面的会"
        assert "没有找到对应日程" not in (unread.direct_answer or "")
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
        assert (
            db.query(CalendarEvent)
            .filter(CalendarEvent.user_id == user_id, CalendarEvent.status == "cancelled")
            .count()
            == 0
        )
    finally:
        _cleanup(user_id)
        db.close()


def test_connected_list_overlays_google_without_writing(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Item 10: dedupe by external_id, show Google times, leave the stored row alone."""
    _freeze_calendar_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        matched = CalendarEvent(
            user_id=user_id,
            title="旧标题",
            start_at=datetime(2026, 6, 3, 10, 0),
            end_at=datetime(2026, 6, 3, 11, 0),
            participants='["甲"]',
            status="active",
            provider="google",
            external_id="evt-live",
            timezone="Asia/Shanghai",
            location="旧地点",
        )
        local_only = CalendarEvent(
            user_id=user_id,
            title="本地会",
            start_at=datetime(2026, 6, 3, 9, 0),
            end_at=datetime(2026, 6, 3, 9, 30),
            status="active",
            provider="local",
            external_id="",
        )
        hidden = CalendarEvent(
            user_id=user_id,
            title="已取消的同步",
            start_at=datetime(2026, 6, 3, 8, 0),
            end_at=datetime(2026, 6, 3, 8, 30),
            status="active",
            provider="google",
            external_id="evt-cancelled",
            timezone="Asia/Shanghai",
        )
        db.add(matched)
        db.add(local_only)
        db.add(hidden)
        db.commit()
        db.refresh(matched)
        db.refresh(local_only)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_items = [
            _google_item(
                "evt-live",
                "2026-06-03T14:00:00+08:00",
                "2026-06-03T15:30:00+08:00",
                summary="新标题",
            ),
            _google_item(
                "evt-only",
                "2026-06-03T18:00:00+08:00",
                "2026-06-03T19:00:00+08:00",
                summary="外面的会",
            ),
            _google_item("evt-day", "2026-06-04", "2026-06-05", all_day=True, summary="休息"),
            _google_item(
                "evt-blank",
                "2026-06-03T12:00:00+08:00",
                "2026-06-03T12:30:00+08:00",
                summary="",
            ),
            _google_item(
                "evt-clear",
                "2026-06-03T13:00:00+08:00",
                "2026-06-03T13:30:00+08:00",
                summary="透明安排",
                transparency="transparent",
            ),
            _google_item(
                "evt-cancelled",
                "2026-06-03T08:00:00+08:00",
                "2026-06-03T08:30:00+08:00",
                summary="已取消的同步",
                status="cancelled",
            ),
            _google_item(
                "evt-gone",
                "2026-06-03T07:00:00+08:00",
                "2026-06-03T07:30:00+08:00",
                summary="不该出现",
                status="cancelled",
            ),
            _google_item(
                "evt-office",
                "2026-06-03T09:00:00+08:00",
                "2026-06-03T18:00:00+08:00",
                summary="在办公室",
                event_type="workingLocation",
            ),
            _google_item(
                "evt-declined",
                "2026-06-03T16:00:00+08:00",
                "2026-06-03T17:00:00+08:00",
                summary="已拒绝",
                declined=True,
            ),
        ]
        listed = auth_client.get(
            "/api/calendar",
            params={"start": "2026-06-02T00:00:00", "end": "2026-06-09T00:00:00"},
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert isinstance(body, list)
        assert [row["title"] for row in body] == [
            "本地会",
            "（无标题）",
            "透明安排",
            "新标题",
            "外面的会",
            "休息",
        ]
        by_title = {row["title"]: row for row in body}
        assert by_title["本地会"]["id"] == local_only.id
        assert by_title["本地会"].get("readonly") is not True
        live = by_title["新标题"]
        assert live["id"] == matched.id
        assert live.get("readonly") is not True
        assert live["start_at"].startswith("2026-06-03T14:00:00")
        assert live["end_at"].startswith("2026-06-03T15:30:00")
        assert live["participants"] == ["甲"]
        assert live["location"] == "旧地点"
        assert "10:00" not in live["start_at"]
        google_only = by_title["外面的会"]
        assert google_only["id"] is None
        assert google_only["readonly"] is True
        assert google_only["provider"] == "google"
        untitled = by_title["（无标题）"]
        assert untitled["id"] is None
        assert untitled["readonly"] is True
        rest = by_title["休息"]
        assert rest["id"] is None
        assert rest["readonly"] is True
        assert rest["all_day"] is True
        assert by_title["透明安排"]["readonly"] is True

        for call in _list_calls(fake_google):
            params = call["params"] or {}
            assert params.get("singleEvents") == "true"
            assert params.get("orderBy") == "startTime"
            assert params.get("showDeleted") == "false"
            assert "syncToken" not in params

        db.refresh(matched)
        db.refresh(hidden)
        assert matched.title == "旧标题"
        assert matched.start_at == datetime(2026, 6, 3, 10, 0)
        assert matched.end_at == datetime(2026, 6, 3, 11, 0)
        assert hidden.status == "active"
        assert hidden.title == "已取消的同步"
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
        assert _insert_calls(fake_google) == []

        provider = calendar_provider_for(db, user_id)
        stored = provider.get_event(user_id, matched.id)
        assert stored is not None
        assert stored.title == "旧标题"
        assert stored.start_at == datetime(2026, 6, 3, 10, 0)
        assert stored.readonly is False
        assert provider.get_event(user_id, 10**9) is None
        lists_before = len(_list_calls(fake_google))
        found = find_event_candidates(
            db, user_id, title="旧标题", start_at=datetime(2026, 6, 3, 10, 0)
        )
        assert [row.id for row in found] == [matched.id]
        assert found[0].title == "旧标题"
        assert found[0].start_at == datetime(2026, 6, 3, 10, 0)
        assert (
            find_event_candidates(
                db, user_id, title="外面的会", start_at=datetime(2026, 6, 3, 18, 0)
            )
            == []
        )
        assert (
            find_event_candidates(
                db, user_id, title="新标题", start_at=datetime(2026, 6, 3, 14, 0)
            )
            == []
        )
        assert len(_list_calls(fake_google)) == lists_before

        advice = "会有点密，中间留一点缓冲。"
        monkeypatch.setattr(
            "app.agents.tools.calendar_tool.complete_text",
            lambda *_args, **_kwargs: advice,
        )
        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_args, **_kwargs: {"day": ""},
        )
        chat = execute_calendar(
            ToolContext(db=db, message="这周有哪些会", history=[], user_id=user_id)
        )
        assert chat.direct_answer == advice
        assert chat.schedule_card["mode"] == "list"
        assert chat.schedule_card["phase"] == "info"
        titles = [item["title"] for item in chat.schedule_card["items"]]
        assert titles == ["本地会", "（无标题）", "透明安排", "新标题", "外面的会", "休息"]
        rest = next(item for item in chat.schedule_card["items"] if item["title"] == "休息")
        assert rest["all_day"] is True
        assert "00:00" not in (chat.direct_answer or "")
        for hidden in ("旧标题", "不该出现", "在办公室", "已拒绝", "已取消的同步"):
            assert hidden not in titles
            assert hidden not in (chat.direct_answer or "")
    finally:
        _cleanup(user_id)
        db.close()


def test_list_read_failure_keeps_local_rows_and_says_so(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    _freeze_calendar_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        local_only = CalendarEvent(
            user_id=user_id,
            title="本地会",
            start_at=datetime(2026, 6, 3, 9, 0),
            end_at=datetime(2026, 6, 3, 9, 30),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(local_only)
        db.commit()
        db.refresh(local_only)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_status = 500
        listed = auth_client.get(
            "/api/calendar",
            params={"start": "2026-06-02T00:00:00", "end": "2026-06-09T00:00:00"},
        )
        assert listed.status_code == 200, listed.text
        assert listed.status_code != 500
        body = listed.json()
        assert body["notice"] == "暂时读不到 Google 日历，以下只是这里保存的日程。"
        assert len(body["events"]) == 1
        assert body["events"][0]["id"] == local_only.id
        assert body["events"][0]["title"] == "本地会"
        assert body["events"][0]["start_at"].startswith("2026-06-03T09:00:00")

        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_args, **_kwargs: {"day": ""},
        )

        def _no_advice(*_args, **_kwargs):
            raise AssertionError("read failure must not ask for advice")

        monkeypatch.setattr("app.agents.tools.calendar_tool.complete_text", _no_advice)
        chat = execute_calendar(
            ToolContext(db=db, message="这周有哪些会", history=[], user_id=user_id)
        )
        assert chat.schedule_card["mode"] == "list"
        assert chat.schedule_card["items"][0]["title"] == "本地会"
        assert chat.direct_answer == "暂时读不到 Google 日历，以下只是这里保存的日程。"
        db.refresh(local_only)
        assert local_only.title == "本地会"
        assert local_only.start_at == datetime(2026, 6, 3, 9, 0)
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
        assert _insert_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_list_truncated_google_page_still_returns_rows(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    _freeze_calendar_clock(monkeypatch)
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        local_only = CalendarEvent(
            user_id=user_id,
            title="本地会",
            start_at=datetime(2026, 6, 3, 9, 0),
            end_at=datetime(2026, 6, 3, 9, 30),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(local_only)
        db.commit()
        fake_google.always_next_page = True
        fake_google.events_items = [
            _google_item(
                "evt-only",
                "2026-06-03T18:00:00+08:00",
                "2026-06-03T19:00:00+08:00",
                summary="外面的会",
            )
        ]
        listed = auth_client.get(
            "/api/calendar",
            params={"start": "2026-06-02T00:00:00", "end": "2026-06-09T00:00:00"},
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["notice"] == "这次没有列完。"
        titles = [row["title"] for row in body["events"]]
        assert titles.count("外面的会") == 1
        assert "本地会" in titles
        assert fake_google.pages_served == 5
        assert _insert_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_parse_working_hours_ignores_a_payload_without_the_field():
    assert parse_working_hours(
        [
            {"id": "timezone", "value": "Asia/Shanghai"},
            {"id": "weekStart", "value": "0"},
            {"id": "workingLocation", "value": "office"},
        ]
    ) == []
    parsed = parse_working_hours(
        [
            {"id": "timezone", "value": "Asia/Shanghai"},
            {
                "id": "workingHours",
                "value": json.dumps([{"weekday": 3, "start": "10:00", "end": "16:00"}]),
            },
        ]
    )
    assert parsed == [WorkingHoursWindow(weekday=3, start=time(10, 0), end=time(16, 0))]


def test_find_free_slots_clips_injected_hours_and_leaves_nights_closed(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    """No working hours matches Local. Injected hours do not treat night as free."""
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        day = datetime(2026, 8, 6, 8, 0)
        window_end = datetime(2026, 8, 6, 20, 0)
        night = datetime(2026, 8, 6, 20, 0)
        night_end = datetime(2026, 8, 6, 23, 0)
        duration = timedelta(hours=1)
        local = LocalCalendarProvider(db)
        open_provider = calendar_provider_for(db, user_id)
        fake_google.events_items = []
        assert open_provider.find_free_slots(user_id, night, night_end, duration) == (
            local.find_free_slots(user_id, night, night_end, duration)
        )
        assert open_provider.find_free_slots(user_id, day, window_end, duration)[0] == (
            day,
            day + duration,
        )

        fake_google.events_items = [
            _google_item(
                "office",
                "2026-08-06T10:00:00+08:00",
                "2026-08-06T16:00:00+08:00",
                summary="在办公室",
                event_type="workingLocation",
            )
        ]
        assert open_provider.find_free_slots(user_id, night, night_end, duration) == [
            (night, night + duration)
        ]
        assert open_provider.find_free_slots(user_id, day, window_end, duration)[0] == (
            day,
            day + duration,
        )

        hours = [
            WorkingHoursWindow(weekday=day.weekday(), start=time(10, 0), end=time(16, 0))
        ]
        clipped = GoogleCalendarProvider(db, user_id, working_hours=hours)
        fake_google.events_items = []
        assert clipped.find_free_slots(user_id, night, night_end, duration) == []
        daytime = clipped.find_free_slots(user_id, day, window_end, duration)
        assert daytime[0] == (datetime(2026, 8, 6, 10, 0), datetime(2026, 8, 6, 11, 0))
        assert daytime[0][0].hour != 9

        fake_google.events_items = [
            _google_item("a", "2026-08-06T10:00:00+08:00", "2026-08-06T11:00:00+08:00"),
            _google_item("b", "2026-08-06T11:00:00+08:00", "2026-08-06T12:00:00+08:00"),
            _google_item(
                "clear",
                "2026-08-06T12:00:00+08:00",
                "2026-08-06T13:00:00+08:00",
                transparency="transparent",
            ),
        ]
        adjacent = clipped.find_free_slots(user_id, day, window_end, duration)
        assert adjacent[0] == (datetime(2026, 8, 6, 12, 0), datetime(2026, 8, 6, 13, 0))

        fake_google.events_items = []
        fake_google.settings_items = [
            {"id": "timezone", "value": "Asia/Shanghai"},
            {"id": "weekStart", "value": "0"},
        ]
        from_settings = calendar_provider_for(db, user_id)
        assert from_settings.working_hours is None
        assert from_settings.find_free_slots(user_id, night, night_end, duration) == [
            (night, night + duration)
        ]
        fake_google.settings_items = [
            {"id": "timezone", "value": "Asia/Shanghai"},
            {
                "id": "workingHours",
                "value": json.dumps(
                    [{"weekday": day.weekday(), "start": "10:00", "end": "16:00"}]
                ),
            },
        ]
        parsed = calendar_provider_for(db, user_id)
        assert parsed.find_free_slots(user_id, day, window_end, duration)[0] == (
            datetime(2026, 8, 6, 10, 0),
            datetime(2026, 8, 6, 11, 0),
        )
        assert parsed.find_free_slots(user_id, night, night_end, duration) == []

        fake_google.events_items = []
        fake_google.settings_items = []
        fake_google.settings_status = 500
        suggested = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=user_id)
        )
        assert "工作时间" not in (suggested.direct_answer or "")
        assert "00:00–01:00" not in (suggested.direct_answer or "")
        slots = (suggested.schedule_card or {}).get("suggested_slots") or []
        assert slots == []
        assert _insert_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_unconnected_list_skips_google_and_tool_does_not_query(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Item 13: tool source has no db.query, and an unconnected list does not call Google."""
    src = inspect.getsource(calendar_tool)
    assert "db.query" not in src
    assert "CalendarEvent" not in src
    init_db()
    user_id = _me(auth_client)["id"]
    db = SessionLocal()
    try:
        before = len(fake_google.calls)
        advice = "还有整段空闲，适合放需要专注的事。"
        monkeypatch.setattr(
            "app.agents.tools.calendar_tool.complete_text",
            lambda *_args, **_kwargs: advice,
        )
        monkeypatch.setattr(
            "app.services.calendar.complete_json",
            lambda *_args, **_kwargs: {"day": ""},
        )
        listed = execute_calendar(
            ToolContext(db=db, message="这周有哪些会", history=[], user_id=user_id)
        )
        assert listed.schedule_card is None
        assert listed.direct_answer == "接下来 7 天没有日程。\n\n" + advice
        suggested = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=user_id)
        )
        assert suggested.schedule_card is None
        assert "工作时间" not in (suggested.direct_answer or "")
        assert "00:00" not in (suggested.direct_answer or "")
        provider = calendar_provider_for(db, user_id)
        assert type(provider) is LocalCalendarProvider
        assert provider.list_events(user_id, datetime(2026, 6, 2), datetime(2026, 6, 9)) == []
        assert len(fake_google.calls) == before
        http_listed = auth_client.get(
            "/api/calendar",
            params={"start": "2026-06-02T00:00:00", "end": "2026-06-09T00:00:00"},
        )
        assert http_listed.status_code == 200
        assert http_listed.json() == []
        assert len(fake_google.calls) == before
    finally:
        _cleanup(user_id)
        db.close()


def _install_http(monkeypatch, fake: FakeGoogle, handler):
    def wrapped(method, url, *, data=None, params=None, headers=None, json=None):
        handled = handler(method, url, data=data, params=params, headers=headers, json=json)
        if handled is not None:
            return handled
        return fake(method, url, data=data, params=params, headers=headers, json=json)

    monkeypatch.setattr("app.services.calendar_secrets._http_request", wrapped)


def _expire_token(db, user_id: int) -> CalendarConnection:
    row = db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).one()
    row.token_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    db.refresh(row)
    return row


def test_transient_error_is_not_a_token_error():
    assert not issubclass(CalendarTransientError, CalendarTokenError)
    assert "重新连接" not in str(CalendarTransientError())


def test_timezone_get_retries_once_then_returns_the_zone(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    user_id = 0
    seen = {"n": 0}
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Shanghai")

        def handler(method, url, **_kwargs):
            if str(url).endswith("/users/me/settings/timezone"):
                seen["n"] += 1
                if seen["n"] == 1:
                    raise httpx.ConnectError("down")
                return httpx.Response(200, json={"id": "timezone", "value": "Asia/Singapore"})
            return None

        _install_http(monkeypatch, fake_google, handler)
        provider = calendar_provider_for(db, user_id)
        assert provider.timezone_name() == "Asia/Singapore"
        assert seen["n"] == 2
    finally:
        _cleanup(user_id)
        db.close()


def test_timezone_outage_is_transient_and_status_stays_connected(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)

        def handler(method, url, **_kwargs):
            if str(url).endswith("/users/me/settings/timezone"):
                raise httpx.ConnectError("down")
            return None

        _install_http(monkeypatch, fake_google, handler)
        provider = calendar_provider_for(db, user_id)
        with pytest.raises(CalendarTransientError) as raised:
            provider.timezone_name()
        assert "重新连接" not in str(raised.value)
        body = auth_client.get("/api/calendar/google/status").json()
        assert body["connected"] is True
        assert body["timezone"] == ""

        fake_google.timezone_status = 401
        monkeypatch.setattr("app.services.calendar_secrets._http_request", fake_google)
        with pytest.raises(CalendarTokenError):
            calendar_provider_for(db, user_id).timezone_name()
        fake_google.timezone_status = 404
        assert calendar_provider_for(db, user_id).timezone_name() == "Etc/GMT"
    finally:
        _cleanup(user_id)
        db.close()


def test_refresh_network_is_transient_and_keeps_tokens(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _expire_token(db, user_id)
        access_before = row.access_token
        refresh_before = row.refresh_token

        def handler(method, url, **_kwargs):
            if str(url).endswith("/token"):
                raise httpx.ConnectError("down")
            return None

        _install_http(monkeypatch, fake_google, handler)
        with pytest.raises(CalendarTransientError) as raised:
            calendar_provider_for(db, user_id).timezone_name()
        assert "重新连接" not in str(raised.value)
        db.refresh(row)
        assert row.access_token == access_before
        assert row.refresh_token == refresh_before
    finally:
        _cleanup(user_id)
        db.close()


def test_refresh_400_stays_token_error_and_keeps_tokens(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        row = _expire_token(db, user_id)
        access_before = row.access_token
        refresh_before = row.refresh_token
        fake_google.token_status = 400
        with pytest.raises(CalendarTokenError):
            refresh_access_token(db, row)
        db.refresh(row)
        assert row.access_token == access_before
        assert row.refresh_token == refresh_before
        assert decrypt_token(row.access_token) == ACCESS
    finally:
        _cleanup(user_id)
        db.close()


def test_chat_timezone_outage_is_transient_not_a_governance_denial(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    from app.agents.harness.gateway import invoke

    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        kept = CalendarEvent(
            user_id=user_id,
            title="本地保留",
            start_at=datetime(2026, 10, 16, 10, 0),
            end_at=datetime(2026, 10, 16, 11, 0),
            status="active",
            provider="local",
            external_id="",
        )
        db.add(kept)
        db.commit()
        fake_google.timezone_status = 500
        result = invoke(
            "calendar",
            ToolContext(db=db, message="明天下午三点开个测试会议", history=[], user_id=user_id),
            {},
        )
        assert result.denied is False
        assert result.schedule_card is None
        assert result.direct_answer == "暂时连不上 Google 日历，请稍后再试。"
        assert "工具治理拒绝" not in (result.direct_answer or "")
        assert "工具治理拒绝" not in (result.deny_reason or "")

        listed = execute_calendar(
            ToolContext(db=db, message="这周有哪些会", history=[], user_id=user_id)
        )
        assert listed.schedule_card["mode"] == "list"
        assert any(item["title"] == "本地保留" for item in listed.schedule_card["items"])
        assert "暂时读不到 Google 日历" in (listed.direct_answer or "")
        assert "本地保留" not in (listed.direct_answer or "")

        suggested = execute_calendar(
            ToolContext(db=db, message="明天有空吗", history=[], user_id=user_id)
        )
        assert suggested.schedule_card is None
        assert suggested.direct_answer == "暂时读不到 Google 日历，不能建议空闲时段。"
    finally:
        _cleanup(user_id)
        db.close()


def test_confirm_post_timezone_outage_is_502_and_writes_nothing(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.timezone_status = 500
        created = auth_client.post(
            "/api/calendar",
            json={
                "title": "会",
                "start_at": "2026-10-16T15:00:00",
                "end_at": "2026-10-16T16:00:00",
            },
        )
        assert created.status_code == 502
        assert created.json()["detail"] == "暂时连不上 Google 日历，请稍后再试。"
        assert "重新连接" not in created.json()["detail"]
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
        assert _insert_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_connected_reschedule_and_cancel_write_google_only_after_confirm(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Extraction prefills the card. Confirm patches or deletes the matching live event."""
    init_db()
    db = SessionLocal()
    user_id = 0

    def _forbid_lookup(*_args, **_kwargs):
        raise AssertionError("local candidate lookup")

    monkeypatch.setattr("app.agents.tools.calendar_tool.find_event_candidates", _forbid_lookup)
    monkeypatch.setattr("app.agents.tools.calendar_tool.apply_update_times", _forbid_lookup)

    def _extract(message, _history=None, **_kwargs):
        if "取消" in (message or ""):
            start = datetime(2026, 10, 16, 15, 0)
        else:
            start = datetime(2026, 10, 17, 16, 0)
        return EventDraft(title="测试会议", start_at=start, end_at=start + timedelta(hours=1))

    monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
    try:
        user_id = _connect_admin(auth_client, db, fake_google)
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_items = [
            _google_item(
                "evt-cancel",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="测试会议",
            )
        ]
        cancel = execute_calendar(
            ToolContext(db=db, message="取消测试会议", history=[], user_id=user_id)
        )
        assert cancel.schedule_card["mode"] == "cancel"
        assert cancel.schedule_card["title"] == "测试会议"
        assert cancel.schedule_card["start_at"].startswith("2026-10-16T15:00:00")
        assert cancel.schedule_card["end_at"].startswith("2026-10-16T16:00:00")
        assert "+" not in cancel.schedule_card["start_at"]
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        confirmed = auth_client.post(
            "/api/calendar/google/confirm",
            json={"action": "cancel", "title": "测试会议"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["id"] is None
        assert [call["url"].rstrip("/").rsplit("/", 1)[-1] for call in _delete_calls(fake_google)] == [
            "evt-cancel"
        ]
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        row = _google_row(
            db, user_id, title="测试会议", start=datetime(2026, 10, 16, 15, 0), external_id="evt-move"
        )
        old_start = row.start_at
        fake_google.calls.clear()
        fake_google.events_items = [
            _google_item(
                "evt-move",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="测试会议",
            )
        ]
        update = execute_calendar(
            ToolContext(db=db, message="把测试会议改到后天下午四点", history=[], user_id=user_id)
        )
        assert update.schedule_card["mode"] == "update"
        assert update.schedule_card["title"] == "测试会议"
        assert update.schedule_card["start_at"].startswith("2026-10-17T16:00:00")
        assert update.schedule_card["end_at"].startswith("2026-10-17T17:00:00")
        assert update.schedule_card["event_id"] is None
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []
        db.refresh(row)
        assert row.start_at == old_start

        seen = _watch_row(fake_google, row.id, {"PATCH"})
        moved = auth_client.post(
            "/api/calendar/google/confirm",
            json={
                "action": "update",
                "title": "测试会议",
                "start_at": "2026-10-17T16:00:00",
                "end_at": "2026-10-17T17:00:00",
            },
        )
        assert moved.status_code == 200, moved.text
        assert seen["start_at"] == old_start
        assert _patch_calls(fake_google)
        assert _patch_calls(fake_google)[0]["url"].rstrip("/").endswith("/evt-move")
        assert _insert_calls(fake_google) == []
        db.refresh(row)
        assert row.start_at == datetime(2026, 10, 17, 16, 0)
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before + 1

        fake_google.calls.clear()
        fake_google.on_event = None
        fake_google.events_items = [
            _google_item(
                "evt-move",
                "2026-10-17T16:00:00+08:00",
                "2026-10-17T17:00:00+08:00",
                summary="另一场",
            )
        ]
        missed = auth_client.post(
            "/api/calendar/google/confirm",
            json={"action": "cancel", "title": "测试会议"},
        )
        assert missed.status_code == 409
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []
        db.refresh(row)
        assert row.start_at == datetime(2026, 10, 17, 16, 0)
        assert row.status == "active"
    finally:
        _cleanup(user_id)
        db.close()


def _forbid_local_lookup(monkeypatch) -> None:
    def _forbid(*_args, **_kwargs):
        raise AssertionError("local candidate lookup")

    monkeypatch.setattr("app.agents.tools.calendar_tool.find_event_candidates", _forbid)
    monkeypatch.setattr("app.agents.tools.calendar_tool.apply_update_times", _forbid)


def _assert_offset_free(card: dict) -> None:
    for key in ("start_at", "end_at"):
        stamp = card[key]
        assert stamp
        assert "+" not in stamp
        assert "Z" not in stamp


def _event_gets(fake: FakeGoogle) -> list[dict]:
    return [
        call
        for call in fake.calls
        if call["method"] == "GET"
        and str(call["url"]).rstrip("/").endswith("/calendars/primary/events")
    ]


def test_connected_prefill_soft_aligns_one_live_title(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Extracted title differs, but one titles_match hit becomes the card title.

    Cancel also takes that event's wall times. Update keeps the new slot.
    Confirm still rejects any title that is not the exact summary.
    """
    init_db()
    db = SessionLocal()
    user_id = 0
    _forbid_local_lookup(monkeypatch)
    assert titles_match("Grok测试会议", "测试会议")
    assert "Grok测试会议" != "测试会议"

    def _extract(message, _history=None, **_kwargs):
        if "改" in (message or ""):
            start = datetime(2026, 10, 17, 16, 0)
        else:
            start = datetime(2026, 10, 18, 9, 0)
        return EventDraft(title="Grok测试会议", start_at=start, end_at=start + timedelta(hours=1))

    monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Singapore")
        before = db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()
        fake_google.events_items = [
            _google_item(
                "evt-real",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="测试会议",
            )
        ]
        cancel = execute_calendar(
            ToolContext(db=db, message="取消Grok测试会议", history=[], user_id=user_id)
        )
        card = cancel.schedule_card
        assert card["mode"] == "cancel"
        assert card["title"] == "测试会议"
        assert card["start_at"] == "2026-10-16T15:00:00"
        assert card["end_at"] == "2026-10-16T16:00:00"
        assert card["timezone"] == "Asia/Singapore"
        assert card["event_id"] is None
        assert card["candidates"] == []
        _assert_offset_free(card)
        listed = _event_gets(fake_google)
        assert listed
        assert str(listed[0]["params"]["timeMax"]).startswith("2100-01-01")
        assert listed[0]["params"]["timeZone"] == "Asia/Singapore"
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        update = execute_calendar(
            ToolContext(db=db, message="把Grok测试会议改到后天下午四点", history=[], user_id=user_id)
        )
        moved = update.schedule_card
        assert moved["mode"] == "update"
        assert moved["title"] == "测试会议"
        assert moved["start_at"] == "2026-10-17T16:00:00"
        assert moved["end_at"] == "2026-10-17T17:00:00"
        assert moved["timezone"] == "Asia/Singapore"
        _assert_offset_free(moved)
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google) == []

        missed = auth_client.post(
            "/api/calendar/google/confirm",
            json={"action": "cancel", "title": "Grok测试会议"},
        )
        assert missed.status_code == 409
        assert missed.json()["detail"] == "没有在 Google 日历上找到同标题的日程，未写入。"
        assert _delete_calls(fake_google) == []
        assert _patch_calls(fake_google) == []
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before

        confirmed = auth_client.post(
            "/api/calendar/google/confirm",
            json={"action": "cancel", "title": "测试会议"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert [call["url"].rstrip("/").rsplit("/", 1)[-1] for call in _delete_calls(fake_google)] == [
            "evt-real"
        ]
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count() == before
    finally:
        _cleanup(user_id)
        db.close()


def test_connected_prefill_leaves_an_exact_title(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """One exact summary match is not rewritten, including cancel times."""
    init_db()
    db = SessionLocal()
    user_id = 0
    _forbid_local_lookup(monkeypatch)

    def _extract(_message, _history=None, **_kwargs):
        start = datetime(2026, 10, 18, 9, 0)
        return EventDraft(title="测试会议", start_at=start, end_at=start + timedelta(hours=1))

    monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Singapore")
        fake_google.events_items = [
            _google_item(
                "evt-real",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="测试会议",
            ),
            _google_item(
                "evt-other",
                "2026-10-16T18:00:00+08:00",
                "2026-10-16T19:00:00+08:00",
                summary="牙医",
            ),
        ]
        cancel = execute_calendar(
            ToolContext(db=db, message="取消测试会议", history=[], user_id=user_id)
        )
        card = cancel.schedule_card
        assert card["title"] == "测试会议"
        assert card["start_at"] == "2026-10-18T09:00:00"
        assert card["end_at"] == "2026-10-18T10:00:00"
        assert card["timezone"] == "Asia/Singapore"
        _assert_offset_free(card)
        assert _delete_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_connected_prefill_leaves_zero_or_many_soft_hits(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """No unique soft hit, so the card keeps the extracted title and times."""
    init_db()
    db = SessionLocal()
    user_id = 0
    _forbid_local_lookup(monkeypatch)

    def _extract(_message, _history=None, **_kwargs):
        start = datetime(2026, 10, 18, 9, 0)
        return EventDraft(title="Grok测试会议", start_at=start, end_at=start + timedelta(hours=1))

    monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Singapore")
        fake_google.events_items = [
            _google_item(
                "evt-dentist",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="牙医",
            )
        ]
        none = execute_calendar(
            ToolContext(db=db, message="取消Grok测试会议", history=[], user_id=user_id)
        )
        assert titles_match("Grok测试会议", "牙医") is False
        card = none.schedule_card
        assert card["mode"] == "cancel"
        assert card["title"] == "Grok测试会议"
        assert card["start_at"] == "2026-10-18T09:00:00"
        assert card["end_at"] == "2026-10-18T10:00:00"
        assert card["candidates"] == []

        assert titles_match("Grok测试会议", "测试会议")
        assert titles_match("Grok测试会议", "会议")
        fake_google.events_items = [
            _google_item(
                "evt-a",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="测试会议",
            ),
            _google_item(
                "evt-b",
                "2026-10-16T18:00:00+08:00",
                "2026-10-16T19:00:00+08:00",
                summary="会议",
            ),
        ]
        many = execute_calendar(
            ToolContext(db=db, message="取消Grok测试会议", history=[], user_id=user_id)
        )
        crowded = many.schedule_card
        assert crowded["mode"] == "cancel"
        assert crowded["title"] == "Grok测试会议"
        assert crowded["start_at"] == "2026-10-18T09:00:00"
        assert crowded["end_at"] == "2026-10-18T10:00:00"
        assert crowded["candidates"] == []
        assert _delete_calls(fake_google) == []
        assert _patch_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_connected_prefill_keeps_extraction_when_list_fails(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    user_id = 0
    _forbid_local_lookup(monkeypatch)

    def _extract(_message, _history=None, **_kwargs):
        start = datetime(2026, 10, 18, 9, 0)
        return EventDraft(title="Grok测试会议", start_at=start, end_at=start + timedelta(hours=1))

    monkeypatch.setattr("app.agents.tools.calendar_tool.extract_event", _extract)
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Singapore")
        fake_google.events_items = [
            _google_item(
                "evt-real",
                "2026-10-16T15:00:00+08:00",
                "2026-10-16T16:00:00+08:00",
                summary="测试会议",
            )
        ]
        fake_google.events_status = 500
        result = execute_calendar(
            ToolContext(db=db, message="取消Grok测试会议", history=[], user_id=user_id)
        )
        card = result.schedule_card
        assert card["mode"] == "cancel"
        assert card["title"] == "Grok测试会议"
        assert card["start_at"] == "2026-10-18T09:00:00"
        assert card["timezone"] == "Asia/Singapore"
        assert _delete_calls(fake_google) == []
    finally:
        _cleanup(user_id)
        db.close()


def test_create_notes_are_written_to_google_description(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle, monkeypatch
):
    """Both create cards carry optional notes, and confirm writes Google description."""
    init_db()
    db = SessionLocal()
    user_id = 0
    payloads = [
        {
            "title": "周会",
            "start_at": "2026-10-16T10:00:00",
            "end_at": "2026-10-16T11:00:00",
            "participants": ["小李"],
            "kind": "meeting",
            "description": "带上大纲",
            "action": "create",
        },
        {
            "title": "写报告",
            "start_at": "2026-10-16T15:00:00",
            "end_at": "2026-10-16T17:00:00",
            "participants": [],
            "kind": "block",
            "description": "带上大纲",
            "action": "create",
        },
    ]

    def _json(*_args, **_kwargs):
        return payloads[_json.n]

    _json.n = 0
    monkeypatch.setattr("app.services.calendar.complete_json", _json)
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Singapore")
        for expected_kind in ("meeting", "block"):
            drafted = execute_calendar(
                ToolContext(db=db, message="带上大纲", history=[], user_id=user_id)
            )
            card = drafted.schedule_card
            assert card["kind"] == expected_kind
            assert card["description"] == "带上大纲"
            assert "+" not in card["start_at"] and "Z" not in card["start_at"]
            _json.n += 1
            fake_google.insert_id = f"evt-{expected_kind}"
            created = auth_client.post(
                "/api/calendar",
                json={
                    "title": card["title"],
                    "start_at": card["start_at"],
                    "end_at": card["end_at"],
                    "participants": card["participants"],
                    "description": card["description"],
                },
            )
            assert created.status_code == 200, created.text
            assert created.json()["description"] == "带上大纲"
            row = db.get(CalendarEvent, created.json()["id"])
            assert row is not None
            assert row.description == "带上大纲"
            payload = _insert_calls(fake_google)[-1]["json"]
            assert payload["description"] == "带上大纲"
            assert payload["start"]["dateTime"] == card["start_at"]
            assert "Z" not in payload["start"]["dateTime"]
            assert "+" not in payload["start"]["dateTime"]

        _json.n = 0
        payloads[0]["description"] = ""
        payloads[0]["title"] = "空备注会"
        payloads[0]["start_at"] = "2026-10-18T10:00:00"
        payloads[0]["end_at"] = "2026-10-18T11:00:00"
        empty = execute_calendar(
            ToolContext(db=db, message="明早开会", history=[], user_id=user_id)
        )
        assert empty.schedule_card["description"] == ""
        fake_google.insert_id = "evt-empty"
        created = auth_client.post(
            "/api/calendar",
            json={
                "title": empty.schedule_card["title"],
                "start_at": empty.schedule_card["start_at"],
                "end_at": empty.schedule_card["end_at"],
                "participants": empty.schedule_card["participants"],
                "description": "",
            },
        )
        assert created.status_code == 200, created.text
        assert _insert_calls(fake_google)[-1]["json"]["description"] == ""
    finally:
        _cleanup(user_id)
        db.close()


def test_cancel_confirm_does_not_change_description(
    auth_client: TestClient, oauth_ready, fake_google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    user_id = 0
    try:
        user_id = _connect_admin(auth_client, db, fake_google, zone="Asia/Singapore")
        row = _google_row(
            db,
            user_id,
            title="周会",
            start=datetime(2026, 10, 16, 10, 0),
            external_id="evt-note",
            description="原说明",
            timezone="Asia/Singapore",
        )
        fake_google.events_items = [
            _google_item(
                "evt-note",
                "2026-10-16T10:00:00+08:00",
                "2026-10-16T11:00:00+08:00",
                summary="周会",
            )
        ]
        confirmed = auth_client.post(
            "/api/calendar/google/confirm",
            json={
                "action": "cancel",
                "title": "周会",
                "start_at": "2026-10-16T10:00:00",
                "end_at": "2026-10-16T11:00:00",
                "description": "不要改说明",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        assert _patch_calls(fake_google) == []
        assert _delete_calls(fake_google)
        assert _delete_calls(fake_google)[0].get("json") in (None, {})
        db.refresh(row)
        assert row.description == "原说明"
        assert row.status == "cancelled"
    finally:
        _cleanup(user_id)
        db.close()
