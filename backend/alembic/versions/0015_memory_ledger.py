"""Append-only memory change ledger."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.models import (
    LEDGER_DECISIONS,
    LEDGER_SOURCE_ACTOR_SQL,
    LEDGER_SOURCES,
    LEDGER_ACTORS,
)

revision = "0015_memory_ledger"
down_revision = "0014_event_receipt"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_TABLE = "memory_ledger"
_SOURCE_SQL = "source IN ({})".format(", ".join(repr(item) for item in LEDGER_SOURCES))
_ACTOR_SQL = "actor IN ({})".format(", ".join(repr(item) for item in LEDGER_ACTORS))
_DECISION_SQL = "decision IS NULL OR decision IN ({})".format(
    ", ".join(repr(item) for item in LEDGER_DECISIONS)
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False, server_default=""),
            sa.Column("key", sa.String(), nullable=False, server_default=""),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("before", sa.Text(), nullable=False, server_default=""),
            sa.Column("after", sa.Text(), nullable=False, server_default=""),
            sa.Column("pending", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("decision", sa.String(), nullable=True),
            sa.Column("source_ref", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(_SOURCE_SQL, name="ck_memory_ledger_source"),
            sa.CheckConstraint(_ACTOR_SQL, name="ck_memory_ledger_actor"),
            sa.CheckConstraint(LEDGER_SOURCE_ACTOR_SQL, name="ck_memory_ledger_source_actor"),
            sa.CheckConstraint(_DECISION_SQL, name="ck_memory_ledger_decision"),
            sa.CheckConstraint("pending IN (0, 1)", name="ck_memory_ledger_pending"),
            sa.Index(f"ix_{_TABLE}_user_id", "user_id"),
        )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_ledger_turn "
        f"ON {_TABLE} (user_id, source_ref, kind, key) "
        "WHERE source = 'turn'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_ledger_settle "
        f"ON {_TABLE} (user_id, source_ref) "
        "WHERE source = 'settle'"
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
