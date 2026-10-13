"""Postgres RLS cross-user isolation.

Skipped on SQLite, because row-level security is a Postgres feature: on the default
test database this test would prove nothing. When it does run it needs the schema
that carries the policies (``alembic upgrade head``), not just ``create_all``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal, set_rls_context
from app.models.models import Document, User

pytestmark = pytest.mark.integration

_ALICE = 91001
_BOB = 91002

_needs_postgres = pytest.mark.skipif(
    not (settings.database_url or "").startswith("postgresql"),
    reason="Postgres RLS is not exercised on SQLite (run the rls CI job)",
)


def _ensure_user(db, user_id: int, external_id: str) -> None:
    """Documents carry an FK to users; RLS test rows need real owners."""
    if db.query(User).filter(User.id == user_id).first() is None:
        db.add(User(id=user_id, external_id=external_id, name=external_id, is_active=1))
        db.commit()


def _cleanup(db) -> None:
    """Committed rows survive a rollback, so delete them explicitly.

    The app role is subject to FORCE RLS, so a document delete only sees the
    tenant in the current GUC. Delete each user's rows under that user's
    context; otherwise the users FK delete fails on the rows RLS hid.
    """
    db.rollback()
    for user_id in (_ALICE, _BOB):
        set_rls_context(db, user_id)
        db.execute(text("DELETE FROM documents WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    db.commit()


@_needs_postgres
def test_rls_policies_are_installed():
    """Guards the other tests: without policies they would pass for free."""
    db = SessionLocal()
    try:
        forced = db.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'documents'")
        ).scalar()
        policies = db.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = 'documents'")
        ).scalar()
        bypass = db.execute(
            text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).scalar()
        assert forced is True, "documents must have FORCE ROW LEVEL SECURITY (alembic 0004)"
        assert int(policies or 0) >= 1, "documents must carry an RLS policy"
        assert bypass is False, (
            "SessionLocal must be a non-superuser role without BYPASSRLS; "
            "superusers ignore FORCE RLS"
        )
    finally:
        db.close()


@_needs_postgres
def test_rls_hides_other_users_documents():
    db = SessionLocal()
    try:
        _ensure_user(db, _ALICE, "rls-alice")
        _ensure_user(db, _BOB, "rls-bob")

        set_rls_context(db, _ALICE)
        db.add(Document(user_id=_ALICE, filename="alice.md", status="ready"))
        db.commit()
        set_rls_context(db, _BOB)
        db.add(Document(user_id=_BOB, filename="bob.md", status="ready"))
        db.commit()

        # No application-level WHERE clause: only the policy can filter these.
        set_rls_context(db, _ALICE)
        names_a = {d.filename for d in db.query(Document).all()}
        set_rls_context(db, _BOB)
        names_b = {d.filename for d in db.query(Document).all()}

        assert "alice.md" in names_a
        assert "bob.md" not in names_a, "RLS leaked another user's document"
        assert "bob.md" in names_b
        assert "alice.md" not in names_b, "RLS leaked another user's document"
    finally:
        try:
            _cleanup(db)
        finally:
            db.close()


@_needs_postgres
def test_rls_context_switches_between_requests():
    """A leaked GUC would let the next request read the previous user's rows."""
    db = SessionLocal()
    try:
        _ensure_user(db, _ALICE, "rls-alice")
        set_rls_context(db, _ALICE)
        db.add(Document(user_id=_ALICE, filename="alice-scoped.md", status="ready"))
        db.commit()

        set_rls_context(db, _BOB)
        visible = {d.filename for d in db.query(Document).all()}
        assert "alice-scoped.md" not in visible

        set_rls_context(db, _ALICE)
        visible = {d.filename for d in db.query(Document).all()}
        assert "alice-scoped.md" in visible
    finally:
        try:
            _cleanup(db)
        finally:
            db.close()
