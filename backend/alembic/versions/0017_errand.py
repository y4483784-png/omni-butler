"""Record one accepted errand and its vault."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_errand"
down_revision = "0016_mail_entry"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_RLS_TABLES = ("errand_records", "errand_vault")


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
        "errand_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("merchant_key", sa.String(), nullable=False, server_default=""),
        sa.Column("order_id", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_errand_records_idempotency"),
    )
    op.create_index("ix_errand_records_user_id", "errand_records", ["user_id"])
    op.create_table(
        "errand_vault",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("merchant_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "merchant_key", "kind", name="uq_errand_vault_kind"),
        sa.CheckConstraint("kind IN ('login', 'pay')", name="ck_errand_vault_kind"),
    )
    op.create_index("ix_errand_vault_user_id", "errand_vault", ["user_id"])
    op.create_table(
        "merchant_connectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_key", sa.String(), nullable=False),
        sa.Column("place_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("lookup_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("needs_login", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("merchant_key", name="uq_merchant_connectors_key"),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _RLS_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _RLS_TABLES:
            _disable_rls(table)
    op.drop_table("merchant_connectors")
    op.drop_index("ix_errand_vault_user_id", table_name="errand_vault")
    op.drop_table("errand_vault")
    op.drop_index("ix_errand_records_user_id", table_name="errand_records")
    op.drop_table("errand_records")
