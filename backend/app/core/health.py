"""Liveness vs readiness.

``/health`` is for Docker healthchecks (must stay cheap and dependency-free).
``/health/ready`` checks Postgres and Redis; do not wire it to the api container
healthcheck or a Redis blip will restart uvicorn.
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.core.redis import get_redis


def readiness_checks() -> tuple[dict[str, str], bool]:
    checks: dict[str, str] = {}
    ok = True

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"fail:{type(exc).__name__}"
        ok = False

    if not settings.redis_enabled:
        checks["redis"] = "disabled"
    else:
        client = get_redis()
        if client is None:
            checks["redis"] = "fail"
            ok = False
        else:
            try:
                client.ping()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"fail:{type(exc).__name__}"
                ok = False

    return checks, ok
