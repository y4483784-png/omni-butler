"""Postgres row-level security for multi-user isolation.

SQLite dev/test skips this migration (no-op).

Revision ID: 0004_rls
Revises: 0003_user_auth
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "0004_rls"
down_revision = "0003_user_auth"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in ("sessions", "documents", "calendar_events", "memories"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (user_id = {_TENANT})
            WITH CHECK (user_id = {_TENANT})
            """
        )

    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS messages_tenant ON messages")
    op.execute(
        f"""
        CREATE POLICY messages_tenant ON messages
        USING (
            session_id IN (
                SELECT id FROM sessions WHERE user_id = {_TENANT}
            )
        )
        WITH CHECK (
            session_id IN (
                SELECT id FROM sessions WHERE user_id = {_TENANT}
            )
        )
        """
    )

    op.execute("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chunks FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS chunks_tenant ON chunks")
    op.execute(
        f"""
        CREATE POLICY chunks_tenant ON chunks
        USING (
            document_id IN (
                SELECT id FROM documents WHERE user_id = {_TENANT}
            )
        )
        WITH CHECK (
            document_id IN (
                SELECT id FROM documents WHERE user_id = {_TENANT}
            )
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, policy in (
        ("chunks", "chunks_tenant"),
        ("messages", "messages_tenant"),
        ("memories", "memories_tenant"),
        ("calendar_events", "calendar_events_tenant"),
        ("documents", "documents_tenant"),
        ("sessions", "sessions_tenant"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
