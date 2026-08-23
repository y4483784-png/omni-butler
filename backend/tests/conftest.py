from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests must stay hermetic: force SQLite regardless of what .env points at
os.environ.setdefault("DATABASE_URL", "sqlite:///./omni_butler.db")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("SANDBOX_RUNNER_URL", "")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

TEST_ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


@pytest.fixture(scope="session", autouse=True)
def _db_schema_ready():
    """Create tables and bootstrap admin for the test session."""
    from app.core.auth import hash_password
    from app.core.bootstrap import ensure_bootstrap_users
    from app.core.db import SessionLocal, init_db
    from app.models.models import User

    init_db()
    db = SessionLocal()
    try:
        ensure_bootstrap_users(db)
        # Unify admin with legacy user_id=1 fixtures used across tests.
        admin_row = db.query(User).filter(User.external_id == "admin").first()
        u = db.query(User).filter(User.id == 1).first()
        if admin_row is not None and u is not None and admin_row.id != u.id:
            db.delete(admin_row)
            db.flush()
        if u is not None:
            u.external_id = "admin"
            u.is_admin = 1
            u.is_active = 1
            u.password_hash = hash_password(TEST_ADMIN_PASSWORD)
            db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _stub_blob_store():
    """Keep tests off real MinIO. Startup / get_blob_store() would otherwise hang on localhost:9000."""
    from unittest.mock import MagicMock

    from app.storage import factory as blob_factory

    if blob_factory._store is None:
        store = MagicMock()
        store.put.side_effect = lambda key, _data: key
        store.exists.return_value = True
        blob_factory._store = store
    yield


@pytest.fixture
def auth_client() -> TestClient:
    from app.main import app

    client = TestClient(app)
    r = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return client


def login_as(client: TestClient, username: str, password: str) -> dict:
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()
