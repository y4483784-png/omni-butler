from __future__ import annotations

import json
import os
from typing import Any

CACHE_SCHEMA = 3


def load_or_drop_entries(path: str | os.PathLike[str]) -> dict[str, Any]:
    location = os.fspath(path)
    if not os.path.isfile(location):
        return {}
    try:
        with open(location, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        _unlink(location)
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
        _unlink(location)
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_entries(path: str | os.PathLike[str], entries: dict[str, Any]) -> None:
    location = os.fspath(path)
    parent = os.path.dirname(location)
    if parent:
        os.makedirs(parent, exist_ok=True)
    body = json.dumps({"schema": CACHE_SCHEMA, "entries": entries}, ensure_ascii=False)
    temporary = location + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.replace(temporary, location)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
