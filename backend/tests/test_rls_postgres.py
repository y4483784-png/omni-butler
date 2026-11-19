"""Postgres RLS cross-user isolation.

Skipped on SQLite, because row-level security is a Postgres feature: on the default
test database this test would prove nothing. When it does run it needs the schema
that carries the policies (``alembic upgrade head``), not just ``create_all``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

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


class _GateStore:
    """Block inside exists so another transaction can commit before the sweep writes."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.objects = {key: b"png-bytes"}
        self.started = threading.Event()
        self.release = threading.Event()
        self.opened = False

    def put(self, key, data):
        raw = data if isinstance(data, (bytes, bytearray)) else data.read()
        self.objects[key] = bytes(raw)
        return key

    def exists(self, key) -> bool:
        if key == self.key and not self.opened:
            self.opened = True
            self.started.set()
            assert self.release.wait(5), "the terminal transaction did not commit"
        return key in self.objects

    def delete(self, key) -> None:
        self.objects.pop(key, None)

    def materialize(self, key):
        raise FileNotFoundError(key)

    def release_materialized(self, path, key) -> None:
        return None


@_needs_postgres
def test_reconcile_keeps_a_delete_committed_during_the_blob_check():
    """SQLite serializes the sweep's open transaction, so this interleaving runs on Postgres."""
    from app.core.db import maintenance_session
    from app.models.models import TaskImage
    from app.services import task_images, task_run

    user_id = 91021
    db = SessionLocal()
    gate = None
    try:
        _ensure_user(db, user_id, "picture-fence")
        set_rls_context(db, user_id)
        session = ChatSession(user_id=user_id, title="fence")
        db.add(session)
        db.commit()
        set_rls_context(db, user_id)
        other = ChatSession(user_id=user_id, title="fence-kept")
        db.add(other)
        db.commit()
        set_rls_context(db, user_id)
        run = TaskRun(
            user_id=user_id,
            session_id=session.id,
            generation=1,
            status="running",
            allowed_tools="[]",
        )
        kept = TaskRun(
            user_id=user_id,
            session_id=other.id,
            generation=2,
            status="running",
            allowed_tools="[]",
        )
        db.add(run)
        db.add(kept)
        db.flush()
        staged = TaskImage(
            user_id=user_id,
            task_run_id=run.id,
            media_type="image/png",
            byte_length=9,
            sha256="fence",
            storage_key=f"task-image/{user_id}/{run.id}/fence",
            purge_state="staged",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        safe = TaskImage(
            user_id=user_id,
            task_run_id=kept.id,
            media_type="image/png",
            byte_length=9,
            sha256="kept",
            storage_key=f"task-image/{user_id}/{kept.id}/kept",
            purge_state="ready",
        )
        db.add(staged)
        db.add(safe)
        db.commit()
        gate = _GateStore(staged.storage_key)
        gate.objects[safe.storage_key] = b"kept-bytes"
        holder: dict = {}

        def sweep() -> None:
            try:
                holder["count"] = task_images.reconcile_task_images(store=gate)
            except Exception as exc:
                holder["error"] = exc

        worker = threading.Thread(target=sweep)
        worker.start()
        assert gate.started.wait(5), "reconcile did not reach the blob check"
        task_run.stop(user_id=user_id, generation=1)
        gate.release.set()
        worker.join(5)
        assert not worker.is_alive()
        if "error" in holder:
            raise holder["error"]
        check = maintenance_session()
        try:
            left = (
                check.query(TaskImage)
                .filter(TaskImage.storage_key == staged.storage_key)
                .one_or_none()
            )
            assert left is not None
            assert left.purge_state == "tombstone"
            survivor = check.get(TaskImage, safe.id)
            assert survivor is not None
            assert survivor.purge_state == "ready"
        finally:
            check.close()
        assert staged.storage_key not in gate.objects
        assert gate.objects[safe.storage_key] == b"kept-bytes"
    finally:
        if gate is not None:
            gate.release.set()
        db.rollback()
        set_rls_context(db, user_id)
        db.execute(text("DELETE FROM task_images WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM task_runs WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM sessions WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        db.commit()
        db.close()


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
