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
from app.models.models import Document, LlmCall, TaskImage, TaskRun, User
from app.models.models import Session as ChatSession

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
        db.execute(text("DELETE FROM task_images WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM llm_calls WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM task_runs WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM sessions WHERE user_id = :u"), {"u": user_id})
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
        for table in ("llm_calls", "task_images"):
            forced_row = db.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :name"),
                {"name": table},
            ).scalar()
            policy_count = db.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :name"),
                {"name": table},
            ).scalar()
            assert forced_row is True, f"{table} must have FORCE ROW LEVEL SECURITY"
            assert int(policy_count or 0) >= 1, f"{table} must carry an RLS policy"
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


@_needs_postgres
def test_rls_hides_llm_calls_and_task_images():
    db = SessionLocal()
    try:
        _ensure_user(db, _ALICE, "rls-alice")
        _ensure_user(db, _BOB, "rls-bob")
        set_rls_context(db, _ALICE)
        session = ChatSession(user_id=_ALICE, title="picture-rls")
        db.add(session)
        db.flush()
        run = TaskRun(
            user_id=_ALICE,
            session_id=session.id,
            generation=1,
            status="running",
            allowed_tools="",
        )
        db.add(run)
        db.flush()
        db.add(
            LlmCall(
                user_id=_ALICE,
                generation=1,
                role="chat",
                model="deepseek-flash",
                prompt_tokens=None,
                completion_tokens=0,
            )
        )
        db.add(
            TaskImage(
                user_id=_ALICE,
                task_run_id=run.id,
                media_type="image/png",
                byte_length=4,
                sha256="ab",
                storage_key="task-image/alice/1/ab-0",
                purge_state="ready",
            )
        )
        db.commit()

        set_rls_context(db, _ALICE)
        call = db.query(LlmCall).one()
        assert call.prompt_tokens is None
        assert call.completion_tokens == 0
        assert db.query(TaskImage).count() == 1

        set_rls_context(db, _BOB)
        assert db.query(LlmCall).count() == 0
        assert db.query(TaskImage).count() == 0

        set_rls_context(db, _ALICE)
        db.add(LlmCall(user_id=None, role="chat", model="deepseek-flash"))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
        set_rls_context(db, _ALICE)
        assert db.query(LlmCall).count() == 1
    finally:
        try:
            _cleanup(db)
        finally:
            db.close()
