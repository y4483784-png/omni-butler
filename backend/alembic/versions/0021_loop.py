from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.models import TASK_STEP_PHASES, TOOL_CALL_STATES

revision = "0021_loop"
down_revision = "0020_watch"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_STEPS = "task_steps"
_CALLS = "tool_calls"
_PHASE_SQL = "phase IN ({})".format(", ".join(repr(item) for item in TASK_STEP_PHASES))
_STATE_SQL = "state IN ({})".format(", ".join(repr(item) for item in TOOL_CALL_STATES))


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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())
    if _STEPS not in present:
        op.create_table(
            _STEPS,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("task_run_id", sa.Integer(), sa.ForeignKey("task_runs.id"), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("phase", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("reasoning_content", sa.Text(), nullable=False, server_default=""),
            sa.CheckConstraint(_PHASE_SQL, name="ck_task_steps_phase"),
            sa.UniqueConstraint("task_run_id", "seq", name="uq_task_steps_run_seq"),
            sa.Index(f"ix_{_STEPS}_user_id", "user_id"),
            sa.Index(f"ix_{_STEPS}_task_run_id", "task_run_id"),
        )
    if _CALLS not in present:
        op.create_table(
            _CALLS,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("task_step_id", sa.Integer(), sa.ForeignKey("task_steps.id"), nullable=False),
            sa.Column("tool_call_id", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("arguments", sa.Text(), nullable=False, server_default=""),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("claim_token", sa.String(), nullable=True),
            sa.Column("intent_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("result", sa.Text(), nullable=True),
            sa.CheckConstraint(_STATE_SQL, name="ck_tool_calls_state"),
            sa.UniqueConstraint("task_step_id", "tool_call_id", name="uq_tool_calls_step_call"),
            sa.Index(f"ix_{_CALLS}_user_id", "user_id"),
            sa.Index(f"ix_{_CALLS}_task_step_id", "task_step_id"),
        )
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())
    if _STEPS in present:
        _enable_rls(_STEPS)
    if _CALLS in present:
        _enable_rls(_CALLS)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        inspector = sa.inspect(bind)
        present = set(inspector.get_table_names())
        if _CALLS in present:
            _disable_rls(_CALLS)
        if _STEPS in present:
            _disable_rls(_STEPS)
    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())
    if _CALLS in present:
        op.drop_table(_CALLS)
    if _STEPS in present:
        op.drop_table(_STEPS)
