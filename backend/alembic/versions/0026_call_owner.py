from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_call_owner"
down_revision = "0025_confirm_until"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("effect_ack_at", sa.DateTime(), nullable=True))
    op.add_column("tool_calls", sa.Column("owner_token", sa.String(), nullable=True))
    op.add_column("tool_calls", sa.Column("attempt", sa.Integer(), nullable=True))
    op.add_column("tool_calls", sa.Column("unresolved_at", sa.DateTime(), nullable=True))
    op.create_table(
        "task_deliveries",
        sa.Column("delivery_id", sa.String(), primary_key=True),
        sa.Column("worker_incarnation", sa.String(), nullable=False),
        sa.Column("celery_request_id", sa.String(), nullable=False),
        sa.Column("child_pid", sa.Integer(), nullable=False),
        sa.Column("task_run_id", sa.Integer(), sa.ForeignKey("task_runs.id"), nullable=True),
        sa.Column("exited_at", sa.DateTime(), nullable=True),
        sa.Column("exitcode", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_task_deliveries_task_run_id", "task_deliveries", ["task_run_id"])
    op.create_table(
        "tool_call_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tool_call_pk", sa.Integer(), sa.ForeignKey("tool_calls.id"), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("delivery_id", sa.String(), sa.ForeignKey("task_deliveries.delivery_id"), nullable=False),
        sa.Column("handed_off_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tool_call_pk", "owner_token", "attempt", name="uq_tool_call_attempts"),
    )
    op.create_index("ix_tool_call_attempts_tool_call_pk", "tool_call_attempts", ["tool_call_pk"])
    op.create_index("ix_tool_call_attempts_delivery_id", "tool_call_attempts", ["delivery_id"])
    op.create_table(
        "tool_call_effects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tool_call_pk", sa.Integer(), sa.ForeignKey("tool_calls.id"), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "uq_tool_call_effects_attributed",
        "tool_call_effects",
        ["tool_call_pk", "owner_token", "attempt"],
        unique=True,
        postgresql_where=sa.text("owner_token IS NOT NULL"),
        sqlite_where=sa.text("owner_token IS NOT NULL"),
    )
    op.create_index(
        "uq_tool_call_effects_unattributed",
        "tool_call_effects",
        ["tool_call_pk", "body"],
        unique=True,
        postgresql_where=sa.text("owner_token IS NULL"),
        sqlite_where=sa.text("owner_token IS NULL"),
    )
    op.create_index("ix_tool_call_effects_tool_call_pk", "tool_call_effects", ["tool_call_pk"])
    op.create_table(
        "tool_call_effect_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tool_call_pk", sa.Integer(), sa.ForeignKey("tool_calls.id"), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_tool_call_effect_conflicts_tool_call_pk",
        "tool_call_effect_conflicts",
        ["tool_call_pk"],
    )
    from sqlalchemy.orm import Session

    from app.services.call_owner import mark_legacy_unresolved

    session = Session(bind=op.get_bind())
    mark_legacy_unresolved(session, dry_run=False)


def downgrade() -> None:
    op.drop_table("tool_call_effect_conflicts")
    op.drop_index("uq_tool_call_effects_unattributed", table_name="tool_call_effects")
    op.drop_index("uq_tool_call_effects_attributed", table_name="tool_call_effects")
    op.drop_index("ix_tool_call_effects_tool_call_pk", table_name="tool_call_effects")
    op.drop_table("tool_call_effects")
    op.drop_index("ix_tool_call_attempts_delivery_id", table_name="tool_call_attempts")
    op.drop_index("ix_tool_call_attempts_tool_call_pk", table_name="tool_call_attempts")
    op.drop_table("tool_call_attempts")
    op.drop_index("ix_task_deliveries_task_run_id", table_name="task_deliveries")
    op.drop_table("task_deliveries")
    op.drop_column("tool_calls", "unresolved_at")
    op.drop_column("tool_calls", "attempt")
    op.drop_column("tool_calls", "owner_token")
    op.drop_column("task_runs", "effect_ack_at")
