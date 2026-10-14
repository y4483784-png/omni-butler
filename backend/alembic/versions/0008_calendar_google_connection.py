"""Google calendar connection tokens and external event id uniqueness.

Revision ID: 0008_calendar_google_connection
Revises: 0007_calendar_provider_fields
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_calendar_google_connection"
down_revision = "0007_calendar_provider_fields"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_EXTERNAL_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_events_external "
    "ON calendar_events (user_id, provider, external_id) "
    "WHERE external_id <> ''"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "calendar_connections" not in tables:
        op.create_table(
            "calendar_connections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("provider", sa.String(), nullable=False, server_default="google"),
            sa.Column("google_sub", sa.String(), nullable=False, server_default=""),
            sa.Column("account_email", sa.String(), nullable=False, server_default=""),
            sa.Column("access_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("refresh_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("token_expiry", sa.DateTime(), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", name="uq_calendar_connections_user_id"),
            sa.CheckConstraint("provider = 'google'", name="ck_calendar_connections_provider"),
        )
    if "calendar_events" in tables or "calendar_events" in set(sa.inspect(bind).get_table_names()):
        op.execute(_EXTERNAL_INDEX)

    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE calendar_connections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE calendar_connections FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS calendar_connections_tenant ON calendar_connections")
    op.execute(
        f"""
        CREATE POLICY calendar_connections_tenant ON calendar_connections
        USING (user_id = {_TENANT})
        WITH CHECK (user_id = {_TENANT})
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS calendar_connections_tenant ON calendar_connections")
        op.execute("ALTER TABLE calendar_connections NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE calendar_connections DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS uq_calendar_events_external")
    inspector = sa.inspect(bind)
    if "calendar_connections" in set(inspector.get_table_names()):
        op.drop_table("calendar_connections")
