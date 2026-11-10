"""Side-effect contract and unknown calendar results. No live Google calls."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.harness import gateway
from app.agents.harness.effects import call_effect
from app.agents.harness.types import ToolContext
from app.core.auth import hash_password
from app.core.db import SessionLocal, init_db, set_rls_context
from app.models.models import CalendarEvent, Message, ScheduleConfirmClaim, TaskRun, ToolAuditLog, User
from app.services import task_run
from app.services.calendar_secrets import decrypt_token
from app.services.schedule_confirm import phase_for_stored_card
from tests.test_calendar_google import (
    FakeGoogle,
    _cleanup,
    _connect_admin,
    _expire_token,
    _insert_calls,
    _install_http,
)
from tests.test_schedule_confirm_claims import _admin_id, _body, _count, _delete_title, _open, _session

UNKNOWN = "结果未知，未再次写入。请先核对日历。"


def _claim(token: str) -> ScheduleConfirmClaim:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        db.expunge(row)
        return row
    finally:
        db.close()


def _post(client: TestClient, body: dict, token: str):
    return client.post("/api/calendar", json={**body, "confirm_token": token})


def _access_cipher(user_id: int) -> str:
    db = SessionLocal()
    try:
        from app.models.models import CalendarConnection

        row = db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).one()
        return row.access_token
    finally:
        db.close()


@pytest.fixture
def google(monkeypatch):
    from app.core.config import settings
    from tests.test_calendar_google import (
        CLIENT_SECRET,
        FERNET_KEY,
    )

    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(
        settings,
        "google_oauth_redirect_uri",
        "http://localhost:5173/api/calendar/google/callback",
    )
    monkeypatch.setattr(settings, "calendar_token_key", FERNET_KEY)
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173")
    fake = FakeGoogle()
    monkeypatch.setattr("app.services.calendar_secrets._http_request", fake)
    return fake


def test_fresh_token_network_failure_does_not_insert_again(
    plain_client: TestClient, google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    title = "契约新令牌未知"
    user_id = 0
    try:
        user_id = _connect_admin(plain_client, db, google)
        google.network_on = {"POST"}
        body = _body(title, start_at="2026-08-06T15:00:00", end_at="2026-08-06T16:00:00")
        token, _session_id, _turn = _open(plain_client, body)
        first = _post(plain_client, body, token)
        assert first.status_code == 409, first.text
        assert first.json()["detail"] == UNKNOWN
        assert len(_insert_calls(google)) == 1
        sent = _insert_calls(google)[0]["json"]["extendedProperties"]["private"]["requestId"]
        assert sent
        row = _claim(token)
        assert row.status == "unknown"
        assert row.remote_request_id == sent
        again = _post(plain_client, body, token)
        assert again.status_code == 409, again.text
        assert again.json()["detail"] == UNKNOWN
        assert len(_insert_calls(google)) == 1
        assert _count(title) == 0
    finally:
        _delete_title(title)
        _cleanup(user_id)
        db.close()


def test_committed_refresh_then_insert_failure_stays_unknown(
    plain_client: TestClient, google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    title = "契约刷新后未知"
    user_id = 0
    try:
        user_id = _connect_admin(plain_client, db, google)
        before = _access_cipher(user_id)
        _expire_token(db, user_id)
        google.network_on = {"POST"}
        google.access_token = "ya29.refreshed-token"
        body = _body(title, start_at="2026-08-06T15:00:00", end_at="2026-08-06T16:00:00")
        token, _session_id, _turn = _open(plain_client, body)
        failed = _post(plain_client, body, token)
        assert failed.status_code == 409, failed.text
        assert failed.json()["detail"] == UNKNOWN
        after = _access_cipher(user_id)
        assert after != before
        assert decrypt_token(after) == "ya29.refreshed-token"
        assert len(_insert_calls(google)) == 1
        assert _claim(token).status == "unknown"
        again = _post(plain_client, body, token)
        assert again.status_code == 409, again.text
        assert len(_insert_calls(google)) == 1
        assert _count(title) == 0
    finally:
        _delete_title(title)
        _cleanup(user_id)
        db.close()


def test_refresh_failure_rolls_the_claim_back_and_does_not_insert(
    plain_client: TestClient, google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    title = "契约刷新失败回滚"
    user_id = 0
    try:
        user_id = _connect_admin(plain_client, db, google)
        before = _access_cipher(user_id)
        _expire_token(db, user_id)
        google.token_status = 500
        body = _body(title, start_at="2026-08-06T15:00:00", end_at="2026-08-06T16:00:00")
        token, _session_id, _turn = _open(plain_client, body)
        failed = _post(plain_client, body, token)
        assert failed.status_code == 502, failed.text
        assert _access_cipher(user_id) == before
        assert _insert_calls(google) == []
        assert _claim(token).status == "open"
        assert _count(title) == 0
    finally:
        _delete_title(title)
        _cleanup(user_id)
        db.close()


def test_known_rejection_can_be_retried_once(plain_client: TestClient, google: FakeGoogle):
    init_db()
    db = SessionLocal()
    title = "契约明确拒绝"
    user_id = 0
    try:
        user_id = _connect_admin(plain_client, db, google)
        google.insert_status = 400
        body = _body(title, start_at="2026-08-06T15:00:00", end_at="2026-08-06T16:00:00")
        token, _session_id, _turn = _open(plain_client, body)
        rejected = _post(plain_client, body, token)
        assert rejected.status_code == 502, rejected.text
        assert _claim(token).status == "open"
        assert _claim(token).remote_request_id == ""
        google.insert_status = 200
        saved = _post(plain_client, body, token)
        assert saved.status_code == 200, saved.text
        assert len(_insert_calls(google)) == 2
        assert _count(title) == 1
        assert _claim(token).status == "written"
    finally:
        _delete_title(title)
        _cleanup(user_id)
        db.close()


def test_lookup_adopts_the_event_without_a_second_insert(
    plain_client: TestClient, google: FakeGoogle, monkeypatch
):
    init_db()
    db = SessionLocal()
    title = "契约查到已写入"
    user_id = 0

    def handler(method, url, **kwargs):
        if method == "GET" and str(url).rstrip("/").endswith("/calendars/primary/events"):
            posts = _insert_calls(google)
            if not posts:
                return None
            marker = posts[-1]["json"]["extendedProperties"]["private"]["requestId"]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "evt-found",
                            "extendedProperties": {"private": {"requestId": marker}},
                        }
                    ]
                },
            )
        return None

    try:
        user_id = _connect_admin(plain_client, db, google)
        _install_http(monkeypatch, google, handler)
        google.network_on = {"POST"}
        body = _body(title, start_at="2026-08-06T15:00:00", end_at="2026-08-06T16:00:00")
        token, _session_id, _turn = _open(plain_client, body)
        saved = _post(plain_client, body, token)
        assert saved.status_code == 200, saved.text
        assert saved.json()["external_id"] == "evt-found"
        assert len(_insert_calls(google)) == 1
        assert _count(title) == 1
        assert _claim(token).status == "written"
        again = _post(plain_client, body, token)
        assert again.status_code == 409, again.text
        assert len(_insert_calls(google)) == 1
        assert _count(title) == 1
    finally:
        _delete_title(title)
        _cleanup(user_id)
        db.close()


def test_reload_keeps_unknown_and_blocks_another_insert(
    plain_client: TestClient, google: FakeGoogle
):
    init_db()
    db = SessionLocal()
    title = "契约刷新页面"
    user_id = 0
    try:
        user_id = _connect_admin(plain_client, db, google)
        google.network_on = {"POST"}
        body = _body(title, start_at="2026-08-06T15:00:00", end_at="2026-08-06T16:00:00")
        token, session_id, _turn = _open(plain_client, body)
        db.add(
            Message(
                session_id=session_id,
                role="assistant",
                content="请确认",
                schedule_card=json.dumps(
                    {
                        "phase": "draft",
                        "confirm_token": token,
                        "title": title,
                        "actions": [{"confirm_token": token, "phase": "draft", "label": "写入"}],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
        failed = _post(plain_client, body, token)
        assert failed.status_code == 409, failed.text
        stored = (
            db.query(Message)
            .filter(Message.session_id == session_id, Message.role == "assistant")
            .one()
        )
        db.refresh(stored)
        card = json.loads(stored.schedule_card)
        assert card["phase"] == "unknown"
        assert card["actions"][0]["phase"] == "unknown"
        shown = phase_for_stored_card(
            db, {"phase": "draft", "confirm_token": token, "title": title}
        )
        assert shown is not None
        assert shown["phase"] == "unknown"
        again = _post(plain_client, body, token)
        assert again.status_code == 409, again.text
        assert len(_insert_calls(google)) == 1
    finally:
        _delete_title(title)
        _cleanup(user_id)
        db.close()


def test_other_user_token_writes_nothing(plain_client: TestClient):
    title = "契约别人的令牌"
    body = _body(title)
    token, _session_id, _turn = _open(plain_client, body)
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.external_id == "plug-other-user").one_or_none()
        if other is None:
            db.add(
                User(
                    external_id="plug-other-user",
                    name="别人",
                    password_hash=hash_password("other-pass"),
                    is_active=1,
                    is_admin=0,
                )
            )
            db.commit()
    finally:
        db.close()
    from app.main import app

    other_client = TestClient(app)
    logged = other_client.post(
        "/api/auth/login",
        json={"username": "plug-other-user", "password": "other-pass"},
    )
    assert logged.status_code == 200, logged.text
    saved = other_client.post("/api/calendar", json={**body, "confirm_token": token})
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0
    assert _claim(token).status == "open"


def _turn_row(user_id: int, generation: int) -> TaskRun:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
            .one()
        )
        db.expunge(row)
        return row
    finally:
        db.close()


def test_stopped_generation_denies_before_execute(monkeypatch):
    init_db()
    user_id = _admin_id()
    session_id = _session()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.stop(user_id=user_id, generation=turn.generation)
    row = _turn_row(user_id, turn.generation)

    def boom(*_args, **_kwargs):
        raise AssertionError("stopped tool ran")

    monkeypatch.setattr("app.agents.tools.kb.retrieve", boom)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        result = gateway.invoke(
            "kb",
            ToolContext(
                db=db,
                message="查资料",
                history=[],
                user_id=user_id,
                task_id=row.id,
                generation=row.generation,
            ),
            {},
        )
        assert result.denied is True
        assert f"generation={row.generation}" in result.deny_reason
        fresh = db.get(TaskRun, row.id)
        assert fresh is not None
        assert fresh.status == "stopped"
    finally:
        db.close()
    audit = SessionLocal()
    try:
        logged = (
            audit.query(ToolAuditLog)
            .filter(ToolAuditLog.tool == "kb", ToolAuditLog.decision == "deny")
            .order_by(ToolAuditLog.id.desc())
            .first()
        )
        assert logged is not None
        assert f"generation={row.generation}" in (logged.reason or "")
    finally:
        audit.close()


def test_card_action_marker_is_not_a_tool_allow_list(monkeypatch):
    init_db()
    user_id = _admin_id()
    session_id = _session()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, turn.id)
        assert row is not None
        row.allowed_tools = json.dumps(["card_action:9"], separators=(",", ":"))
        db.commit()

        def boom(*_args, **_kwargs):
            raise AssertionError("card action ran a tool")

        monkeypatch.setattr("app.agents.tools.kb.retrieve", boom)
        result = gateway.invoke(
            "kb",
            ToolContext(
                db=db,
                message="查资料",
                history=[],
                user_id=user_id,
                task_id=row.id,
                generation=row.generation,
            ),
            {},
        )
        assert result.denied is True
        assert "卡片确认" in result.deny_reason
        db.refresh(row)
        assert row.status == "running"
    finally:
        db.close()


def test_removed_tool_is_denied_even_when_the_catalog_lists_it(monkeypatch):
    init_db()
    user_id = _admin_id()
    session_id = _session()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, turn.id)
        assert row is not None
        row.allowed_tools = json.dumps(["kb", "web", "calendar"])
        db.commit()

        def boom(*_args, **_kwargs):
            raise AssertionError("removed tool ran")

        monkeypatch.setattr("app.agents.tools.sandbox_tool.run_analysis", boom)
        result = gateway.invoke(
            "sandbox",
            ToolContext(
                db=db,
                message="分析",
                history=[],
                user_id=user_id,
                task_id=row.id,
                generation=row.generation,
                needs_sandbox=True,
            ),
            {"network": "none", "read_only": True},
        )
        assert result.denied is True
        assert f"generation={row.generation}" in result.deny_reason
    finally:
        db.close()


def test_read_may_run_again_and_writes_no_event(monkeypatch):
    init_db()
    calls = {"n": 0}

    def hits(*_args, **_kwargs):
        calls["n"] += 1
        return []

    monkeypatch.setattr("app.agents.tools.kb.retrieve", hits)
    before = _count("契约只读")
    ctx = ToolContext(db=None, message="查资料", history=[], user_id=_admin_id())
    first = gateway.invoke("kb", ctx, {})
    second = gateway.invoke("kb", ctx, {})
    assert first.denied is False
    assert second.denied is False
    assert calls["n"] == 2
    assert _count("契约只读") == before


def test_completed_generation_is_not_rewritten(monkeypatch):
    init_db()
    user_id = _admin_id()
    session_id = _session()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    finished = task_run.finish(user_id=user_id, generation=turn.generation)
    assert finished.status == "completed"
    monkeypatch.setattr("app.agents.tools.kb.retrieve", lambda *_a, **_k: [])
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, turn.id)
        assert row is not None
        result = gateway.invoke(
            "kb",
            ToolContext(
                db=db,
                message="查资料",
                history=[],
                user_id=user_id,
                task_id=row.id,
                generation=row.generation,
            ),
            {},
        )
        assert result.denied is False
        db.refresh(row)
        assert row.status == "completed"
    finally:
        db.close()


def test_effect_drift_denies_before_execute(monkeypatch):
    real = gateway.load_constitution()

    def drifted():
        data = dict(real)
        data["tool_effect"] = {**real["tool_effect"], "kb": "idempotent_write"}
        return data

    monkeypatch.setattr(gateway, "load_constitution", drifted)

    def boom(*_args, **_kwargs):
        raise AssertionError("drifted tool ran")

    monkeypatch.setattr("app.agents.tools.kb.retrieve", boom)
    result = gateway.invoke("kb", ToolContext(db=None, message="查资料", history=[]), {})
    assert result.denied is True
    assert "契约不一致" in result.deny_reason


def test_replay_contract_branches_before_the_operation():
    seen: list[str] = []

    def operation(external_id: str) -> str:
        seen.append(external_id)
        return "sent"

    sent, value = call_effect("read", operation, attempt=1, external_id="r")
    assert sent == "send" and value == "sent"
    sent, value = call_effect(
        "idempotent_write",
        operation,
        attempt=1,
        external_id="same",
        prior_external_id="same",
    )
    assert sent == "send" and value == "sent"
    sent, value = call_effect(
        "idempotent_write",
        operation,
        attempt=1,
        external_id="other",
        prior_external_id="same",
    )
    assert sent == "refuse" and value is None
    sent, value = call_effect(
        "non_replayable_write",
        operation,
        attempt=1,
        external_id="once",
        prior_external_id="once",
    )
    assert sent == "reconcile" and value is None
    assert seen == ["r", "same"]


def test_registered_tools_are_reads():
    from app.agents.tools.registry import ensure_builtin_tools, get_tool

    ensure_builtin_tools()
    for name in ("kb", "web", "calendar", "sandbox"):
        spec = get_tool(name)
        assert spec is not None
        assert spec.effect == "read"
