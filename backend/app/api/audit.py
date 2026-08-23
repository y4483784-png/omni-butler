"""Admin-only tool audit log (durable copy of harness events)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_admin
from app.core.db import get_db
from app.models.models import ToolAuditLog, User

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/tools")
def list_tool_audits(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = (
        db.query(ToolAuditLog)
        .order_by(ToolAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "request_id": row.request_id,
            "user_id": row.user_id,
            "tool": row.tool,
            "decision": row.decision,
            "risk": row.risk,
            "reason": row.reason,
            "engine": row.engine,
            "ok": None if row.ok is None else bool(row.ok),
            "evidence_count": row.evidence_count,
            "elapsed_ms": row.elapsed_ms,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    ]
