from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.models import TASK_RUN_STATUSES

revision = "0011_task_run"
down_revision = "0010_schedule_confirm_claims"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_TABLE = "task_runs"
_STATUS_SQL = "status IN ({})".format(
    ", ".join(repr(status) for status in TASK_RUN_STATUSES)
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id"), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("allowed_tools", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(_STATUS_SQL, name="ck_task_runs_status"),
            sa.UniqueConstraint("user_id", "generation", name="uq_task_runs_user_generation"),
            sa.Index(f"ix_{_TABLE}_user_id", "user_id"),
            sa.Index(f"ix_{_TABLE}_session_id", "session_id"),
            sa.Index(f"ix_{_TABLE}_session_status", "user_id", "session_id", "status"),
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
