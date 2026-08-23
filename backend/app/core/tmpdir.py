"""Host-visible temp directory for Docker bind mounts (sandbox + materialized blobs)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings


def ephemeral_dir() -> Path | None:
    """Return SANDBOX_TMP_DIR if set, creating it; otherwise None (use OS temp)."""
    raw = (settings.sandbox_tmp_dir or "").strip()
    if not raw:
        return None
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path
