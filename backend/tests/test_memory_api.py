"""Memory management API (PRD 3.4.2) + isolation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.db import SessionLocal, init_db
from app.models.models import MemoryItem
from app.main import app
from tests.conftest import login_as
from tests.test_isolation import _ensure_user


def test_memory_crud_scoped_to_user(auth_client: TestClient):
    created = auth_client.post(
        "/api/memory",
        json={"kind": "preference", "key": "style", "content": "用户偏好用表格看数据"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["key"] == "style"
    mid = body["id"]

    listed = auth_client.get("/api/memory")
    assert listed.status_code == 200
    ids = {row["id"] for row in listed.json()}
    assert mid in ids

    patched = auth_client.patch(f"/api/memory/{mid}", json={"content": "用户偏好简洁回答"})
    assert patched.status_code == 200
    assert "简洁" in patched.json()["content"]

    deleted = auth_client.delete(f"/api/memory/{mid}")
    assert deleted.status_code == 200
    ids_after = {row["id"] for row in auth_client.get("/api/memory").json()}
    assert mid not in ids_after


def test_memory_rejects_invalid_kind(auth_client: TestClient):
    r = auth_client.post(
        "/api/memory",
        json={"kind": "secret", "key": "x", "content": "something"},
    )
    assert r.status_code == 400


def test_memory_rejects_sensitive_content(auth_client: TestClient, monkeypatch):
    from app.core import moderation as mod

    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", True)
    monkeypatch.setattr(mod.settings, "sensitive_use_builtin", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "forbiddenxyz")
    r = auth_client.post(
        "/api/memory",
        json={"kind": "preference", "key": "note", "content": "remember forbiddenxyz"},
    )
    assert r.status_code == 400


def test_cross_user_memory_404(auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        alice = _ensure_user(db, username="alice-mem", password="alice-pass", user_id=9501)
        row = MemoryItem(
            user_id=alice.id,
            kind="identity",
            key="name",
            content="用户希望被称为「Alice」",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        mid = row.id
    finally:
        db.close()

    r = auth_client.get("/api/memory")
    assert r.status_code == 200
    assert all(item["id"] != mid for item in r.json())

    assert auth_client.patch(f"/api/memory/{mid}", json={"content": "hacked"}).status_code == 404
    assert auth_client.delete(f"/api/memory/{mid}").status_code == 404

    with patch("app.main.get_blob_store", return_value=MagicMock()):
        client_a = TestClient(app)
        login_as(client_a, "alice-mem", "alice-pass")
        listed = client_a.get("/api/memory").json()
        assert any(item["id"] == mid for item in listed)
