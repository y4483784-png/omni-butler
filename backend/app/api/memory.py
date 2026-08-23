"""Long-term memory management API (PRD 3.4.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.core.moderation import INPUT_BLOCKED_MESSAGE, contains_sensitive
from app.core.ownership import require_owned_memory
from app.models.models import User
from app.services import memory as memory_svc

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCreate(BaseModel):
    kind: str = "preference"
    key: str = ""
    content: str = ""


class MemoryUpdate(BaseModel):
    kind: str | None = None
    key: str | None = None
    content: str | None = Field(default=None, max_length=240)


def _guard_text(*parts: str | None) -> None:
    blob = " ".join(p for p in parts if p)
    if contains_sensitive(blob):
        raise HTTPException(status_code=400, detail=INPUT_BLOCKED_MESSAGE)


@router.get("")
def list_memory(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    rows = memory_svc.list_memories(db, current_user.id, limit=100)
    return [memory_svc.serialize_memory(r) for r in rows]


@router.post("")
def create_memory(
    body: MemoryCreate,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    _guard_text(body.kind, body.key, body.content)
    try:
        row = memory_svc.create_memory(
            db,
            current_user.id,
            kind=body.kind,
            key=body.key,
            content=body.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return memory_svc.serialize_memory(row)


@router.patch("/{memory_id}")
def patch_memory(
    memory_id: int,
    body: MemoryUpdate,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    row = require_owned_memory(db, memory_id, current_user.id)
    _guard_text(body.kind, body.key, body.content)
    try:
        row = memory_svc.update_memory(
            db,
            row,
            kind=body.kind,
            key=body.key,
            content=body.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return memory_svc.serialize_memory(row)


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    row = require_owned_memory(db, memory_id, current_user.id)
    memory_svc.delete_memory(db, row)
    return {"ok": True}
