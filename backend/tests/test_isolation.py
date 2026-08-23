"""Multi-user isolation: auth required, no cross-user IDOR."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.db import SessionLocal, init_db
from app.models.models import CalendarEvent, Document, Session as ChatSession, User
from app.main import app


def _ensure_user(db, *, username: str, password: str, user_id: int | None = None) -> User:
    u = db.query(User).filter(User.external_id == username).first()
    if u is None:
        u = User(
            id=user_id,
            external_id=username,
            name=username,
            password_hash=hash_password(password),
            is_active=1,
            is_admin=0,
        )
        db.add(u)
        db.flush()
    else:
        u.password_hash = hash_password(password)
    return u


def test_unauthenticated_sessions_returns_401():
    with patch("app.main.get_blob_store", return_value=MagicMock()):
        with TestClient(app) as client:
            r = client.get("/api/sessions")
    assert r.status_code == 401


def test_users_cannot_see_each_others_sessions(auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        alice = _ensure_user(db, username="alice", password="alice-pass", user_id=9101)
        bob = _ensure_user(db, username="bob", password="bob-pass", user_id=9102)
        db.add(ChatSession(user_id=alice.id, title="Alice only"))
        db.add(ChatSession(user_id=bob.id, title="Bob only"))
        db.commit()
    finally:
        db.close()

    r_alice = auth_client.get("/api/sessions")
    assert r_alice.status_code == 200
    titles_a = {s["title"] for s in r_alice.json()}
    assert "Alice only" not in titles_a  # admin is not alice

    from tests.conftest import login_as

    with patch("app.main.get_blob_store", return_value=MagicMock()):
        client_b = TestClient(app)
        login_as(client_b, "bob", "bob-pass")
        titles_b = {s["title"] for s in client_b.get("/api/sessions").json()}
    assert "Bob only" in titles_b
    assert "Alice only" not in titles_b


def test_cross_user_session_messages_404(auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        alice = _ensure_user(db, username="alice2", password="alice-pass", user_id=9201)
        sess = ChatSession(user_id=alice.id, title="secret")
        db.add(sess)
        db.commit()
        db.refresh(sess)
        sid = sess.id
    finally:
        db.close()

    r = auth_client.get(f"/api/sessions/{sid}/messages")
    assert r.status_code == 404


def test_cross_user_document_delete_404(auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        alice = _ensure_user(db, username="alice3", password="alice-pass", user_id=9301)
        doc = Document(user_id=alice.id, filename="secret.pdf", status="ready", stage="ready")
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    r = auth_client.delete(f"/api/kb/documents/{doc_id}")
    assert r.status_code == 404


def test_cross_user_calendar_patch_404(auth_client: TestClient):
    from datetime import datetime, timedelta

    init_db()
    db = SessionLocal()
    try:
        alice = _ensure_user(db, username="alice4", password="alice-pass", user_id=9401)
        start = datetime.utcnow()
        ev = CalendarEvent(
            user_id=alice.id,
            title="meet",
            start_at=start,
            end_at=start + timedelta(hours=1),
            status="active",
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        eid = ev.id
    finally:
        db.close()

    r = auth_client.patch(f"/api/calendar/{eid}", json={"title": "hacked"})
    assert r.status_code == 404


def test_kb_list_scoped_to_current_user(auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        bob = _ensure_user(db, username="bob2", password="bob-pass")
        db.add(
            Document(
                user_id=bob.id,
                filename="bob-doc.txt",
                status="ready",
                stage="ready",
            )
        )
        db.add(
            Document(
                user_id=1,
                filename="admin-doc.txt",
                status="ready",
                stage="ready",
            )
        )
        db.commit()
    finally:
        db.close()

    r = auth_client.get("/api/kb/documents")
    assert r.status_code == 200
    names = {d["filename"] for d in r.json()}
    assert "admin-doc.txt" in names
    assert "bob-doc.txt" not in names
