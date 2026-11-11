"""Rebuild the receipt route check and record one accepted send."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.models import EVENT_RECEIPT_ROUTES

revision = "0016_mail_entry"
down_revision = "0015_memory_ledger"
branch_labels = None
depends_on = None

_ROUTE_SQL = "route IN ({})".format(", ".join(repr(item) for item in EVENT_RECEIPT_ROUTES))
_PREVIOUS_ROUTES = (
    "noise",
    "held",
    "passthrough",
    "opened",
    "stopped",
    "queued",
    "kept",
    "reissued",
)
_PREVIOUS_SQL = "route IN ({})".format(", ".join(repr(item) for item in _PREVIOUS_ROUTES))
_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_TABLE = "mail_acceptances"


def _rebuild_route(sql: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE event_receipts DROP CONSTRAINT IF EXISTS ck_event_receipts_route")
        op.create_check_constraint("ck_event_receipts_route", "event_receipts", sql)
        return
    with op.batch_alter_table("event_receipts") as batch:
        batch.drop_constraint("ck_event_receipts_route", type_="check")
        batch.create_check_constraint("ck_event_receipts_route", sql)


def _create_acceptances() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("remote_request_id", sa.String(), nullable=False),
            sa.Column("delivery_id", sa.String(), nullable=False),
            sa.Column("recipients", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("subject", sa.Text(), nullable=False, server_default=""),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", "remote_request_id", name="uq_mail_acceptances_remote"),
            sa.UniqueConstraint("user_id", "delivery_id", name="uq_mail_acceptances_delivery"),
            sa.Index(f"ix_{_TABLE}_user_id", "user_id"),
        )
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant ON {_TABLE}")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant ON {_TABLE}
        USING (user_id = {_TENANT})
        WITH CHECK (user_id = {_TENANT})
        """
    )


def upgrade() -> None:
    _rebuild_route(_ROUTE_SQL)
    _create_acceptances()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    inspector = sa.inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        op.drop_table(_TABLE)
    _rebuild_route(_PREVIOUS_SQL)
