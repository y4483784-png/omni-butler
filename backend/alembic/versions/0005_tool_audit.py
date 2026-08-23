"""Tool audit log table. App role omni_app is created at runtime (ensure_app_role).

Revision ID: 0005_tool_audit
Revises: 0004_rls
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_tool_audit"
down_revision = "0004_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tool_audit_logs" in inspector.get_table_names():
        # init_db() create_all may have created the table before this revision ran.
        return
    op.create_table(
        "tool_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("tool", sa.String(), nullable=True),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("risk", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("engine", sa.String(), nullable=True),
        sa.Column("ok", sa.Integer(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("args", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tool_audit_logs_request_id", "tool_audit_logs", ["request_id"])
    op.create_index("ix_tool_audit_logs_user_id", "tool_audit_logs", ["user_id"])
    op.create_index("ix_tool_audit_logs_created_at", "tool_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_audit_logs_created_at", table_name="tool_audit_logs")
    op.drop_index("ix_tool_audit_logs_user_id", table_name="tool_audit_logs")
    op.drop_index("ix_tool_audit_logs_request_id", table_name="tool_audit_logs")
    op.drop_table("tool_audit_logs")
