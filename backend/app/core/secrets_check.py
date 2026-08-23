"""Refuse or warn on well-known default credentials (production hygiene)."""

from __future__ import annotations

import logging
import os
import sys

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_SECRET = "change-me-in-production"
_DEFAULT_MINIO = "minioadmin"


def _in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def insecure_secret_findings() -> list[str]:
    hits: list[str] = []
    if (settings.secret_key or "").strip() in ("", _DEFAULT_SECRET):
        hits.append("SECRET_KEY")
    if (settings.s3_access_key, settings.s3_secret_key) == (_DEFAULT_MINIO, _DEFAULT_MINIO):
        hits.append("MinIO minioadmin")
    url = (settings.database_url or "").lower()
    if "://omni:omni@" in url or "://omni_app:omni@" in url:
        hits.append("Postgres default password")
    return hits


def check_insecure_secrets() -> None:
    if _in_pytest():
        return
    hits = insecure_secret_findings()
    if not hits:
        return
    msg = (
        "Insecure default credentials still in use: "
        + ", ".join(hits)
        + ". Change them in backend/.env / Compose and set ENFORCE_SECURE_SECRETS=true in production."
    )
    if settings.enforce_secure_secrets:
        raise RuntimeError(msg)
    logger.warning(msg)
