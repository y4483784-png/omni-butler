from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_resume"
down_revision = "0022_desktop_devices"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_TABLES = ("session_cursors", "session_events")
_KINDS = ("message", "step", "status", "confirm", "notice")


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
        "session_cursors",
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_session_cursors_user_id", "session_cursors", ["user_id"])
    op.create_table(
        "session_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.CheckConstraint(
            "kind IN ({})".format(", ".join(repr(kind) for kind in _KINDS)),
            name="ck_session_events_kind",
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_events_session_seq"),
    )
    op.create_index("ix_session_events_user_id", "session_events", ["user_id"])
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _TABLES:
        _enable_rls(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in reversed(_TABLES):
            _disable_rls(table)
    op.drop_index("ix_session_events_user_id", table_name="session_events")
    op.drop_table("session_events")
    op.drop_index("ix_session_cursors_user_id", table_name="session_cursors")
    op.drop_table("session_cursors")
