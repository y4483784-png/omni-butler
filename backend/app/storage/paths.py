"""Object keys and legacy-path detection for uploaded files."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_SAFE_NAME = re.compile(r"[^\w.\-\u4e00-\u9fff]+", re.UNICODE)
_LEGACY_WIN = re.compile(r"^[A-Za-z]:\\")
_LEGACY_ABS = re.compile(r"^/[^/]")


def is_legacy_absolute_path(stored_path: str) -> bool:
    """True when DB still holds a host filesystem path from pre-migration uploads."""
    if not stored_path:
        return False
    p = stored_path.strip()
    if _LEGACY_WIN.match(p) or _LEGACY_ABS.match(p):
        return True
    try:
        path = Path(p)
        return path.is_absolute() and (path.drive != "" or p.startswith("/"))
    except OSError:
        return False


def make_object_key(*, user_id: int, filename: str) -> str:
    """Stable relative key stored in Document.stored_path."""
    raw = filename or "unnamed"
    ext = Path(raw).suffix.lower()
    safe = _SAFE_NAME.sub("_", Path(raw).stem)[:80] or "file"
    token = uuid.uuid4().hex[:12]
    return f"u{int(user_id)}/{token}_{safe}{ext}"
