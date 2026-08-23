"""Bootstrap admin user and seed passwords on startup."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.core.config import settings
from app.models.models import User

logger = logging.getLogger(__name__)


def _reassign_user_rows(db: Session, *, src_id: int, dst_id: int) -> None:
    """Move leftover rows from a duplicate bootstrap admin onto the legacy user."""
    if src_id == dst_id:
        return
    for table in ("sessions", "documents", "calendar_events", "memories"):
        db.execute(
            text(f"UPDATE {table} SET user_id = :dst WHERE user_id = :src"),
            {"dst": dst_id, "src": src_id},
        )


def ensure_bootstrap_users(db: Session) -> None:
    """Ensure default admin exists and legacy user id=1 can log in.

    Historical data lives on users.id=1. Login as ADMIN_USERNAME must map to
    that row so sessions/KB/memory are not stranded on a second empty account.
    """
    admin_name = (settings.admin_username or "admin").strip()
    admin_pass = settings.admin_password

    default = db.query(User).filter(User.id == 1).first()
    if default is None:
        default = User(
            id=1,
            external_id=admin_name,
            name="管理员",
            is_active=1,
            is_admin=1,
        )
        db.add(default)
        db.flush()

    extra = (
        db.query(User)
        .filter(User.external_id == admin_name, User.id != default.id)
        .first()
    )
    if extra is not None:
        if extra.password_hash and not default.password_hash:
            default.password_hash = extra.password_hash
        extra.external_id = f"retired-admin-{extra.id}"
        extra.is_active = 0
        extra.is_admin = 0
        db.flush()
        _reassign_user_rows(db, src_id=extra.id, dst_id=default.id)

    default.external_id = admin_name
    default.is_admin = 1
    default.is_active = 1
    if not default.name or default.name == "默认用户":
        default.name = "管理员"

    if admin_pass and not default.password_hash:
        default.password_hash = hash_password(admin_pass)
    elif not default.password_hash:
        logger.warning(
            "ADMIN_PASSWORD is empty; set ADMIN_PASSWORD in .env for bootstrap admin."
        )

    db.commit()
