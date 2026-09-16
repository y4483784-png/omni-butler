"""Session context summary and working_state for ContextManager (H5).

Revision ID: 0006_session_context
Revises: 0005_tool_audit
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_session_context"
down_revision = "0005_tool_audit"
branch_labels = None
depends_on = None


def _session_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {c["name"] for c in inspector.get_columns("sessions")}


def upgrade() -> None:
    # init_db() / create_all may have added these before Alembic caught up.
    existing = _session_columns()
    if "context_summary" not in existing:
        op.add_column("sessions", sa.Column("context_summary", sa.Text(), nullable=True))
    if "context_summary_upto_message_id" not in existing:
        op.add_column(
            "sessions",
            sa.Column("context_summary_upto_message_id", sa.Integer(), nullable=True),
        )
    if "working_state" not in existing:
        op.add_column("sessions", sa.Column("working_state", sa.Text(), nullable=True))


def downgrade() -> None:
    existing = _session_columns()
    if "working_state" in existing:
        op.drop_column("sessions", "working_state")
    if "context_summary_upto_message_id" in existing:
        op.drop_column("sessions", "context_summary_upto_message_id")
    if "context_summary" in existing:
        op.drop_column("sessions", "context_summary")
