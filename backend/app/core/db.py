from __future__ import annotations

import re

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from app.core.config import settings

_IS_SQLITE = settings.database_url.startswith("sqlite")
_MIGRATE_URL = (settings.database_migrate_url or "").strip() or settings.database_url
_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


engine = _make_engine(settings.database_url)
ddl_engine = (
    engine if _MIGRATE_URL == settings.database_url else _make_engine(_MIGRATE_URL)
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
# Superuser / migrate engine: Beat and other cross-tenant jobs. FORCE RLS does not
# apply to the table owner; the runtime `omni_app` pool cannot see other tenants.
MaintenanceSessionLocal = sessionmaker(bind=ddl_engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _clear_rls_on_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    """Drop tenant GUC when a pooled connection is reused (prevents cross-request leak)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SELECT set_config('app.current_user_id', '', false)")
    finally:
        cursor.close()


def _restore_rls_after_begin(session, transaction, connection) -> None:
    """Re-apply tenant GUC for each new transaction.

    SQLAlchemy 2 releases the pooled connection on COMMIT. The next statement
    checks out a connection (checkout listener clears GUC) and begins a new
    transaction, so SET/SET LOCAL from the previous transaction is gone.
    """
    uid = session.info.get("rls_user_id")
    if not uid:
        return
    connection.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(int(uid))},
    )


if not _IS_SQLITE:
    event.listen(engine, "checkout", _clear_rls_on_checkout)
    event.listen(Session, "after_begin", _restore_rls_after_begin)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def maintenance_session() -> Session:
    """Session on the migrate/owner engine for cross-tenant maintenance.

    Celery Beat stale-ingest recovery and privileged `documents.user_id` lookup
    must not use `SessionLocal()` (omni_app + FORCE RLS → empty result set).
    """
    return MaintenanceSessionLocal()


def set_rls_context(db: Session, user_id: int) -> None:
    """Postgres RLS tenant context; no-op on SQLite.

    Stores user_id on the Session so after_begin can SET LOCAL again after every
    COMMIT (SQLAlchemy starts a new transaction/connection). Checkout still
    clears leftover session-level GUC so pooled connections cannot leak tenants.
    """
    if _IS_SQLITE:
        return
    db.info["rls_user_id"] = int(user_id)
    db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(int(user_id))},
    )


def _sqlite_add_column_if_missing(table: str, column: str, coltype: str) -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        names = {r[1] for r in rows}
        if column not in names:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
            conn.commit()


def _postgres_add_column_if_missing(table: str, column: str, coltype: str) -> None:
    """Lightweight safety net when alembic was skipped or ran against another DB."""
    if _IS_SQLITE:
        return
    with ddl_engine.connect() as conn:
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}")
        )
        conn.commit()


def ensure_app_role() -> None:
    """Create a non-superuser login so FORCE RLS actually applies (Postgres docs).

    Superusers always bypass RLS. Compose runtime uses omni_app; migrations use omni.
    """
    if _IS_SQLITE:
        return
    app_url = make_url(settings.database_url)
    mig_url = make_url(_MIGRATE_URL)
    if not app_url.username or app_url.username == mig_url.username:
        return
    role = app_url.username
    password = app_url.password or ""
    ident = role.replace('"', "")
    if not _ROLE_NAME.fullmatch(ident):
        raise RuntimeError(f"unsafe database role name: {ident!r}")
    dbname = (mig_url.database or "").replace('"', "")
    if not _ROLE_NAME.fullmatch(dbname):
        raise RuntimeError(f"unsafe database name: {dbname!r}")
    _ensure_app_role_body(ident, password, dbname)


def _ensure_app_role_body(ident: str, password: str, dbname: str) -> None:
    # Transaction-scoped lock: session-level pg_advisory_lock survived rollback and
    # leaked into SQLAlchemy's pool, blocking api/api2 (LockNotAvailable).
    with ddl_engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('omni_butler.ensure_app_role'))")
        )
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
            {"r": ident},
        ).scalar()
        if not exists:
            quoted_pw = conn.execute(
                text("SELECT quote_literal(CAST(:pw AS text))"),
                {"pw": password},
            ).scalar()
            conn.exec_driver_sql(
                f'CREATE ROLE "{ident}" LOGIN PASSWORD {quoted_pw} NOSUPERUSER NOBYPASSRLS'
            )
        elif password:
            quoted_pw = conn.execute(
                text("SELECT quote_literal(CAST(:pw AS text))"),
                {"pw": password},
            ).scalar()
            conn.exec_driver_sql(
                f'ALTER ROLE "{ident}" WITH LOGIN PASSWORD {quoted_pw}'
            )
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{dbname}" TO "{ident}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{ident}"'))
        conn.execute(
            text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{ident}"')
        )
        conn.execute(
            text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{ident}"')
        )
        conn.execute(
            text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{ident}"'
            )
        )
        conn.execute(
            text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f'GRANT USAGE, SELECT ON SEQUENCES TO "{ident}"'
            )
        )


def init_db():
    import app.models.models  # noqa: F401  (register mappers)

    if not _IS_SQLITE:
        ensure_app_role()
    Base.metadata.create_all(bind=ddl_engine)
    if not _IS_SQLITE:
        ensure_app_role()
    # Lightweight migrate for existing SQLite skeleton DBs
    _sqlite_add_column_if_missing("sessions", "pending_calendar", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("messages", "citations", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("messages", "schedule_card", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("messages", "artifact", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("messages", "feedback", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("documents", "char_count", "INTEGER DEFAULT 0")
    _sqlite_add_column_if_missing("documents", "warning", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("documents", "parser_version", "INTEGER DEFAULT 0")
    _sqlite_add_column_if_missing("documents", "stage", "TEXT DEFAULT 'pending'")
    _sqlite_add_column_if_missing("documents", "ingest_task_id", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("documents", "ingest_started_at", "DATETIME")
    _sqlite_add_column_if_missing("chunks", "kind", "TEXT DEFAULT 'text'")
    _sqlite_add_column_if_missing("chunks", "heading", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("chunks", "page", "INTEGER")
    _sqlite_add_column_if_missing("users", "password_hash", "TEXT DEFAULT ''")
    _sqlite_add_column_if_missing("users", "is_active", "INTEGER DEFAULT 1")
    _sqlite_add_column_if_missing("users", "is_admin", "INTEGER DEFAULT 0")
    # Postgres: ensure auth columns exist (belt if alembic 0003 not applied to this DB)
    _postgres_add_column_if_missing("users", "password_hash", "VARCHAR")
    _postgres_add_column_if_missing("users", "is_active", "INTEGER")
    _postgres_add_column_if_missing("users", "is_admin", "INTEGER")
