"""Google Calendar connection, tokens, factory, and account timezone. No live Google calls."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
from app.agents.tools.calendar_tool import execute_calendar
from app.services.calendar import calendar_now, create_event, extract_event, find_event_candidates
from app.services.calendar_factory import calendar_provider_for
from app.services.calendar_google import GoogleCalendarProvider
from app.services.calendar_local import LocalCalendarProvider
from app.services.calendar_secrets import (
    GOOGLE_CALENDAR_SCOPES,
    CalendarTokenError,
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
        self.include_refresh = True
        self.timezone_status = 404
        self.timezone_value = ""

    def __call__(self, method, url, *, data=None, params=None, headers=None):
        self.calls.append(
            {"method": method, "url": url, "data": data, "params": params, "headers": headers}
        )
        if url.endswith("/token"):
            body = {
                "access_token": self.access_token,
                "expires_in": self.expires_in,
                "scope": self.scope,
                "token_type": "Bearer",
            }
            if self.include_refresh:
                body["refresh_token"] = self.refresh_token
            grant = (data or {}).get("grant_type")
            if grant == "refresh_token" and self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(self.token_status, json=body)
        if url.endswith("/tokeninfo"):
            return httpx.Response(
                self.tokeninfo_status,
                json={"sub": self.sub, "email": self.email, "scope": self.scope},
            )
        if url.endswith("/calendars/primary"):
            return httpx.Response(self.primary_status, json={"id": self.email})
        if url.endswith("/users/me/settings/timezone"):
            if self.timezone_status == 404:
                return httpx.Response(404, json={"error": {"message": "not found"}})
            return httpx.Response(
                self.timezone_status,
                json={"id": "timezone", "value": self.timezone_value},
            )
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
    assert "/events" not in google
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
    assert set(query["scope"][0].split()) == set(GOOGLE_CALENDAR_SCOPES)
    assert CLIENT_SECRET not in location
    assert "calendar.events.freebusy" not in location
    assert "https://www.googleapis.com/auth/calendar" not in set(query["scope"][0].split())
    assert "https://www.googleapis.com/auth/calendar.readonly" not in set(query["scope"][0].split())


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
                "title": "连接后仍本地",
                "start_at": "2026-11-03T09:00:00",
                "end_at": "2026-11-03T10:00:00",
                "participants": [],
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["provider"] == "local"
        assert body["external_id"] == ""
        assert len(fake_google.calls) == 3
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
            assert event.provider == "local"
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
            title="连接后仍写本地",
            start_at=datetime(2026, 6, 4, 9, 0),
            end_at=datetime(2026, 6, 4, 10, 0),
            participants=[],
        )
        assert saved.provider == "local"
        assert saved.external_id == ""
        assert saved.timezone == ""
        assert len(fake_google.calls) == calls_before_write

        status = auth_client.get("/api/calendar/google/status")
        assert status.status_code == 200, status.text
        assert status.json()["connected"] is True
        assert status.json()["timezone"] == "Asia/Shanghai"
        zone_calls = [call for call in fake_google.calls if call["url"].endswith("/settings/timezone")]
        assert zone_calls
        assert all(call["method"] == "GET" for call in zone_calls)
        assert all("/events" not in call["url"] for call in fake_google.calls)
        assert all(ACCESS not in call["url"] for call in zone_calls)
        assert all(not (call.get("params") or {}) for call in zone_calls)
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
        with pytest.raises(CalendarTokenError):
            calendar_now(db, user_id)
        failed = auth_client.get("/api/calendar/google/status")
        assert failed.status_code == 200
        assert failed.json()["connected"] is True
        assert failed.json()["timezone"] == ""
        assert all("/events" not in call["url"] for call in fake_google.calls)
    finally:
        _cleanup(user_id)
        db.close()
