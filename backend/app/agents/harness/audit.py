"""JSONL + database audit trail for governed tool calls."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.agents.harness.constitution import load_constitution
from app.core.request_id import get_request_id

_AUDIT_DIR = Path(__file__).resolve().parents[3] / "data" / "harness"
logger = logging.getLogger(__name__)


def _audit_path() -> Path:
    cfg = load_constitution().get("audit") or {}
    name = str(cfg.get("filename") or "harness-audit.jsonl")
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIT_DIR / name


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(args or {})
    for k in list(cleaned.keys()):
        lk = str(k).lower()
        if any(x in lk for x in ("key", "secret", "password", "token")):
            cleaned[k] = "***"
    return cleaned


def write_audit(event: dict[str, Any]) -> None:
    cfg = load_constitution().get("audit") or {}
    if not cfg.get("enabled", True):
        return
    args = _redact_args(dict(event.get("args") or {}))
    payload = {
        "timestamp": int(time.time() * 1000),
        "request_id": get_request_id() or event.get("request_id") or "",
        **event,
        "args": args,
    }
    try:
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    _persist_db(payload)


def _persist_db(payload: dict[str, Any]) -> None:
    try:
        from app.core.db import SessionLocal
        from app.models.models import ToolAuditLog

        db = SessionLocal()
        try:
            ok = payload.get("ok")
            db.add(
                ToolAuditLog(
                    request_id=str(payload.get("request_id") or "")[:128],
                    user_id=payload.get("user_id"),
                    tool=str(payload.get("tool") or "")[:64],
                    decision=str(payload.get("decision") or "")[:32],
                    risk=str(payload.get("risk") or "")[:32],
                    reason=str(payload.get("reason") or "")[:500],
                    engine=str(payload.get("engine") or "")[:64],
                    ok=None if ok is None else int(bool(ok)),
                    evidence_count=int(payload.get("evidence_count") or 0),
                    elapsed_ms=int(payload.get("elapsed_ms") or 0),
                    args=json.dumps(payload.get("args") or {}, ensure_ascii=False)[:4000],
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.debug("tool audit db persist skipped", exc_info=True)
