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
os.environ.setdefault("RAG_HYBRID_ENABLED", "false")
os.environ.setdefault("VISION_OCR_ENABLED", "false")
os.environ.setdefault("RAG_RERANK_ENABLED", "false")

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


@pytest.fixture(autouse=True)
def _calendar_action_double(monkeypatch):
    """Tests pass an action or get this fixed stand-in.

    Production classification is ``classify_calendar_action``. This double
    only keeps older tests on the branch they already named in the sentence.
    """
    import re

    def _fake(message, history=None, *, now=None, timezone_name=None):
        text = message or ""
        if re.search(r"(有空|空闲|什么时候方便)", text):
            return "suggest"
        if re.search(r"(有哪些|有什么安排|有什么会|查一下日程|查日程|我的日程|哪些日程|怎么安排)", text):
            return "list"
        if re.search(r"(取消|撤销|删掉|删除)", text):
            return "cancel"
        if re.search(r"(改到|改成|改为|推迟|提前|换到|挪到|延后|改期|修改)", text):
            return "update"
        return "create"

    monkeypatch.setattr("app.agents.tools.calendar_tool.classify_calendar_action", _fake)


@pytest.fixture
def fake_llm(monkeypatch):
    """Deterministic LLM stubs used by workflow / chat tests (no network)."""

    async def _stream(*_a, **_k):
        for ch in "你好":
            yield ch

    def _complete_text(*_a, **_k):
        return "演示回答"

    def _complete_json(*_a, **_k):
        return {
            "reasoning": "mock chat",
            "needs_kb": False,
            "needs_web": False,
            "needs_calendar": False,
            "needs_sandbox": False,
            "needs_freshness": False,
            "confidence": "high",
        }

    def _complete_json_schema(*_a, **_k):
        return _complete_json()

    def _critique(*_a, **_k):
        """Grounding critique: accept the draft as-is, no repair, no network call."""
        return {
            "verdict": "ok",
            "unsupported": [],
            "reason": "stub",
            "needs_repair": False,
            "repaired_answer": "",
        }

    def _summary(*_a, **_k):
        return {"summary": "（测试摘要）"}

    monkeypatch.setattr("app.core.llm.stream_chat", _stream)
    monkeypatch.setattr("app.core.llm.complete_text", _complete_text)
    monkeypatch.setattr("app.core.llm.complete_json", _complete_json)
    monkeypatch.setattr("app.core.llm.complete_json_schema", _complete_json_schema)
    monkeypatch.setattr("app.agents.router.complete_json_schema", _complete_json_schema)
    monkeypatch.setattr("app.agents.workflow.complete_text", _complete_text)
    monkeypatch.setattr("app.api.chat.stream_chat", _stream)
    # Seams bound at import time: patching app.core.llm alone leaves these pointing at
    # the real client, so with a live key in .env the test would call the network.
    monkeypatch.setattr("app.agents.harness.critique.complete_text", _critique, raising=False)
    monkeypatch.setattr(
        "app.agents.harness.critique.complete_json_schema", _complete_json_schema, raising=False
    )
    monkeypatch.setattr("app.agents.context.summary.complete_json", _summary, raising=False)
    monkeypatch.setattr("app.services.memory.complete_json", _complete_json, raising=False)
    monkeypatch.setattr("app.agents.tools.web.complete_json", _complete_json, raising=False)
    monkeypatch.setattr(
        "app.agents.tools.web.complete_json_schema", _complete_json_schema, raising=False
    )
    yield


def login_as(client: TestClient, username: str, password: str) -> dict:
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()
