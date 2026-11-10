"""Inbox receipts keyed by user, source, and delivery id."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.models import EVENT_RECEIPT_ROUTES, EVENT_RECEIPT_STATES

revision = "0014_event_receipt"
down_revision = "0013_claim_remote_request"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_TABLE = "event_receipts"
_STATE_SQL = "state IN ({})".format(", ".join(repr(item) for item in EVENT_RECEIPT_STATES))
_ROUTE_SQL = "route IN ({})".format(", ".join(repr(item) for item in EVENT_RECEIPT_ROUTES))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id"), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("delivery_id", sa.String(), nullable=False),
            sa.Column("fingerprint", sa.String(), nullable=False, server_default=""),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("route", sa.String(), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=True),
            sa.Column("task_run_id", sa.Integer(), sa.ForeignKey("task_runs.id"), nullable=True),
            sa.Column("claim_token", sa.String(), nullable=False, server_default=""),
            sa.Column("external_request_id", sa.String(), nullable=False, server_default=""),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(_STATE_SQL, name="ck_event_receipts_state"),
            sa.CheckConstraint(_ROUTE_SQL, name="ck_event_receipts_route"),
            sa.CheckConstraint(
                "state <> 'external_success' OR external_request_id <> ''",
                name="ck_event_receipts_external",
            ),
            sa.UniqueConstraint(
                "user_id",
                "source",
                "delivery_id",
                name="uq_event_receipts_delivery",
            ),
            sa.Index(f"ix_{_TABLE}_user_id", "user_id"),
            sa.Index(f"ix_{_TABLE}_session_id", "session_id"),
            sa.Index(f"ix_{_TABLE}_user_generation", "user_id", "generation"),
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


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    inspector = sa.inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        op.drop_table(_TABLE)
