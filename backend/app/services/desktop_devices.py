from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import DesktopDevice


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enroll(db: Session, *, user_id: int, label: str) -> tuple[int, str, str]:
    token = secrets.token_urlsafe(32)
    row = DesktopDevice(user_id=user_id, token_hash=_digest(token), label=label)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    return row.id, token, row.label


def list_devices(db: Session, *, user_id: int) -> list[dict]:
    rows = (
        db.query(DesktopDevice)
        .filter(DesktopDevice.user_id == user_id, DesktopDevice.revoked_at.is_(None))
        .order_by(DesktopDevice.id.asc())
        .all()
    )
    return [{"id": row.id, "label": row.label, "created_at": row.created_at} for row in rows]


def revoke(db: Session, *, user_id: int, device_id: int) -> int | None:
    row = db.get(DesktopDevice, device_id)
    if row is None or row.user_id != user_id:
        return None
    if row.revoked_at is None:
        row.revoked_at = _now()
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
    return row.id
