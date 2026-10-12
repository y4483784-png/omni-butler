"""Eval environment bootstrap for venv vs Compose service targets.

Must be called before importing app.core.db / Settings consumers that bind
engines at import time. Uses setdefault so an explicit CLI env still wins.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

EvalTarget = Literal["venv", "services"]

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def resolve_target(
    explicit: str | None = None,
    *,
    default: EvalTarget = "venv",
) -> EvalTarget:
    """Pick target: CLI ``--target`` > ``EVAL_TARGET`` env > suite/default.

    Compose ``eval`` sets ``EVAL_TARGET=services``. Do not pass the suite
    default as ``explicit`` or that env var is ignored.
    """
    raw = (explicit or os.environ.get("EVAL_TARGET") or default or "venv").strip().lower()
    if raw in ("services", "compose", "docker"):
        return "services"
    return "venv"


def bootstrap(
    target: EvalTarget = "venv",
    *,
    sqlite_path: Path | None = None,
    force: bool = False,
    isolate_app_db: bool = False,
) -> EvalTarget:
    """Apply hermetic env for offline eval, or leave Compose injection alone.

    Parameters
    ----------
    target:
        ``venv`` forces SQLite + Redis off + eager Celery.
        ``services`` leaves DATABASE_URL / REDIS_URL as Compose injected them.
    sqlite_path:
        File-backed SQLite for venv (not ``:memory:``) so multi-connection
        sessions share one schema.
    force:
        If True, overwrite existing env vars (setdefault → set).
        For ``venv`` DATABASE_URL, always overwrite non-sqlite URLs so a
        leftover Postgres ``.env`` cannot win after package imports.
    isolate_app_db:
        When ``target=services``, still bind a scratch SQLite app DB so
        rag/context/tabular do not write through FORCE RLS on the live
        Postgres. Leaves SANDBOX_RUNNER_URL / MinIO / Redis as injected.
    """
    os.environ["EVAL_TARGET"] = target
    os.environ["EVAL_BOOTSTRAPPED"] = "1"
    # Router Redis is shared across processes and keyed by a frozen EVAL_TODAY.
    # A full refresh must call the model and write disk cache itself; it must
    # not copy a stale Redis decision under the new prompt fingerprint.
    # Embeddings and rate limits still use Redis on the services target.
    os.environ["EVAL_SKIP_ROUTER_REDIS"] = "1"
    # Frozen "today": answer/router prompts embed the date; keep web-staleness cases stable.
    os.environ.setdefault("EVAL_TODAY", "2026-09-19")

    def _set(key: str, value: str, *, overwrite: bool = False) -> None:
        if force or overwrite or key not in os.environ:
            os.environ[key] = value

    use_sqlite = target == "venv" or isolate_app_db
    if use_sqlite:
        db_path = sqlite_path or (BACKEND_ROOT / "reports" / "_eval_scratch.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path.resolve().as_posix()}"
        # Always replace postgres/mysql URLs inherited from process/.env loaders.
        cur = os.environ.get("DATABASE_URL", "")
        overwrite_db = force or (not cur.startswith("sqlite"))
        _set("DATABASE_URL", url, overwrite=overwrite_db)
        _set("DATABASE_MIGRATE_URL", "", overwrite=True)

    if target == "venv":
        # Force hermetic knobs: leftover Compose/.env values must not leak.
        _set("REDIS_ENABLED", "false", overwrite=True)
        _set("CELERY_TASK_ALWAYS_EAGER", "true", overwrite=True)
        _set("SANDBOX_RUNNER_URL", "", overwrite=True)
        _set("SANDBOX_TMP_DIR", "", overwrite=True)
        _set("ENFORCE_SECURE_SECRETS", "false", overwrite=True)
    return target


def ensure_bootstrap(*, isolate_app_db: bool = False) -> EvalTarget:
    """Bootstrap once for standalone script runs; no-op when ``eval.py`` already did."""
    if os.environ.get("EVAL_BOOTSTRAPPED") == "1":
        return resolve_target()
    return bootstrap(resolve_target(), isolate_app_db=isolate_app_db)


def stub_blob_store_if_needed() -> None:
    """Install a no-op MinIO stand-in when offline suites do not need object storage."""
    try:
        from unittest.mock import MagicMock

        from app.storage import factory as blob_factory
    except Exception:
        return
    if blob_factory._store is None:
        store = MagicMock()
        store.put.side_effect = lambda key, _data: key
        store.exists.return_value = True
        store.delete.return_value = None
        store.materialize.side_effect = FileNotFoundError("eval stub blob store")
        blob_factory._store = store
