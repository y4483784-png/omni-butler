"""Unit tests for tenant GUC helpers (SQLite cannot exercise FORCE RLS)."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy.sql.elements import TextClause

from app.core import db as dbmod


def test_set_rls_context_stores_info_and_set_config(monkeypatch):
    monkeypatch.setattr(dbmod, "_IS_SQLITE", False)
    session = MagicMock()
    session.info = {}
    dbmod.set_rls_context(session, 42)
    assert session.info["rls_user_id"] == 42
    session.execute.assert_called_once()
    stmt, params = session.execute.call_args.args[0], session.execute.call_args.args[1]
    assert isinstance(stmt, TextClause)
    assert "set_config" in str(stmt)
    assert params == {"uid": "42"}
    assert "true" in str(stmt)


def test_restore_rls_after_begin_sets_local_on_connection():
    executed: list[tuple[object, dict]] = []

    class FakeConn:
        def execute(self, stmt, params=None):
            executed.append((stmt, params))

    class FakeSession:
        info = {"rls_user_id": 7}

    dbmod._restore_rls_after_begin(FakeSession(), None, FakeConn())
    assert len(executed) == 1
    stmt, params = executed[0]
    assert isinstance(stmt, TextClause)
    assert "set_config" in str(stmt)
    assert "true" in str(stmt)
    assert params == {"uid": "7"}


def test_restore_rls_after_begin_skips_without_tenant():
    class FakeConn:
        def execute(self, *args, **kwargs):
            raise AssertionError("must not SET GUC without rls_user_id")

    class FakeSession:
        info = {}

    dbmod._restore_rls_after_begin(FakeSession(), None, FakeConn())


def test_set_rls_context_sqlite_is_noop(monkeypatch):
    monkeypatch.setattr(dbmod, "_IS_SQLITE", True)
    session = MagicMock()
    session.info = {}
    dbmod.set_rls_context(session, 1)
    session.execute.assert_not_called()
    assert "rls_user_id" not in session.info


def test_maintenance_session_binds_ddl_engine():
    s = dbmod.maintenance_session()
    try:
        assert s.get_bind() is dbmod.ddl_engine
    finally:
        s.close()
