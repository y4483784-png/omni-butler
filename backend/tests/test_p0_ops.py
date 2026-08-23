from __future__ import annotations

from fastapi.testclient import TestClient

from app.agents.harness import gateway
from app.agents.harness.constitution import reload_constitution
from app.agents.harness.types import ToolContext
from app.core.messages import EMPTY_KB_MESSAGE
from app.core.request_id import bind_request_id
from app.core.secrets_check import insecure_secret_findings


def test_tool_audit_persists_and_admin_can_list(auth_client: TestClient):
    reload_constitution()
    bind_request_id("auditreq1deadbeef")
    gateway.invoke("shell", ToolContext(db=None, message="hi", history=[], user_id=1), {})
    r = auth_client.get("/api/audit/tools?limit=20")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert any(row.get("tool") == "shell" and row.get("decision") == "deny" for row in rows)


def test_empty_kb_copy_constant():
    assert EMPTY_KB_MESSAGE == "知识库中未找到相关内容。"


def test_insecure_secret_findings_detects_defaults(monkeypatch):
    from app.core import secrets_check

    monkeypatch.setattr(secrets_check.settings, "secret_key", "change-me-in-production")
    monkeypatch.setattr(secrets_check.settings, "s3_access_key", "minioadmin")
    monkeypatch.setattr(secrets_check.settings, "s3_secret_key", "minioadmin")
    monkeypatch.setattr(
        secrets_check.settings,
        "database_url",
        "postgresql+psycopg://omni:omni@postgres:5432/omni_butler",
    )
    hits = insecure_secret_findings()
    assert "SECRET_KEY" in hits
    assert any("MinIO" in h for h in hits)
    assert any("Postgres" in h for h in hits)


def test_openapi_lists_auth_and_audit(auth_client: TestClient):
    spec = auth_client.get("/openapi.json").json()
    assert "auth" in {t["name"] for t in spec.get("tags") or []}
    assert "/api/auth/password" in spec["paths"]
    assert "/api/audit/tools" in spec["paths"]


def test_audit_requires_admin(auth_client: TestClient):
    from app.core.auth import hash_password
    from app.core.db import SessionLocal
    from app.models.models import User
    from tests.conftest import login_as

    created = auth_client.post(
        "/api/auth/users",
        json={"username": "p0audituser", "password": "pass123", "name": "p0"},
    )
    assert created.status_code in (200, 409)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.external_id == "p0audituser").first()
        assert u is not None
        u.password_hash = hash_password("pass123")
        u.is_admin = 0
        u.is_active = 1
        db.commit()
    finally:
        db.close()
    login_as(auth_client, "p0audituser", "pass123")
    r = auth_client.get("/api/audit/tools")
    assert r.status_code == 403


def test_enforce_secure_secrets_raises(monkeypatch):
    from app.core import secrets_check

    monkeypatch.setattr(secrets_check.settings, "secret_key", "change-me-in-production")
    monkeypatch.setattr(secrets_check.settings, "enforce_secure_secrets", True)
    monkeypatch.setattr(secrets_check, "_in_pytest", lambda: False)
    try:
        secrets_check.check_insecure_secrets()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "SECRET_KEY" in str(exc)
