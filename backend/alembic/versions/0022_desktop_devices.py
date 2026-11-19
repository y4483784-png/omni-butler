from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_desktop_devices"
down_revision = "0021_loop"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_TABLE = "desktop_devices"


def _enable_rls(table: str) -> None:
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


def _disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_desktop_devices_token_hash"),
    )
    op.create_index("ix_desktop_devices_user_id", _TABLE, ["user_id"])
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _enable_rls(_TABLE)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _disable_rls(_TABLE)
    op.drop_index("ix_desktop_devices_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
