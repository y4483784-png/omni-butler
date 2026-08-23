import importlib.util

import pytest
from fastapi.testclient import TestClient

from app.core import health as health_mod
from app.main import app


@pytest.mark.skipif(
    importlib.util.find_spec("boto3") is None,
    reason="requires boto3 for app startup blob store",
)
def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.skipif(
    importlib.util.find_spec("boto3") is None,
    reason="requires boto3 for app startup blob store",
)
def test_health_ready_sqlite_without_redis():
    with TestClient(app) as client:
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["checks"]["postgres"] == "ok"
        assert body["checks"]["redis"] == "disabled"


def test_health_ready_redis_fail(monkeypatch):
    monkeypatch.setattr(health_mod.settings, "redis_enabled", True)
    monkeypatch.setattr(health_mod, "get_redis", lambda: None)
    checks, ok = health_mod.readiness_checks()
    assert not ok
    assert checks["redis"] == "fail"


def test_health_ready_ok_when_redis_disabled(monkeypatch):
    monkeypatch.setattr(health_mod.settings, "redis_enabled", False)
    checks, ok = health_mod.readiness_checks()
    assert ok
    assert checks["postgres"] == "ok"
    assert checks["redis"] == "disabled"
