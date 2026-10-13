"""Fixed-id eval users + RLS tenant GUC for Postgres-backed suites."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.db import set_rls_context
from app.models.models import User


def ensure_eval_user(
    db: Session,
    user_id: int,
    external_id: str,
    name: str,
) -> None:
    """Create a fixed-id eval user so documents/sessions FK and RLS can succeed.

    Do not bump users_id_seq: runtime role omni_app has USAGE/SELECT only
    (setval needs UPDATE), and MAX(id)=9200x would skip ordinary user ids.
    Explicit PK insert does not consume the serial.
    """
    existing = db.query(User).filter(User.id == user_id).first()
    if existing is not None:
        return
    taken = db.query(User).filter(User.external_id == external_id).first()
    if taken is not None:
        raise RuntimeError(
            f"external_id {external_id!r} exists as users.id={taken.id}, "
            f"expected id={user_id}"
        )
    db.add(
        User(
            id=user_id,
            external_id=external_id,
            name=name,
            password_hash="",
            is_active=1,
            is_admin=0,
        )
    )
    db.flush()


def activate_eval_tenant(
    db: Session,
    user_id: int,
    *,
    external_id: str,
    name: str,
) -> None:
    """Ensure the fixture user exists, then SET LOCAL app.current_user_id (no-op on SQLite)."""
    ensure_eval_user(db, user_id, external_id, name)
    set_rls_context(db, user_id)
