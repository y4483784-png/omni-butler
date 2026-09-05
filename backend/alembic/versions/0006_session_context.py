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


def upgrade() -> None:
    op.add_column("sessions", sa.Column("context_summary", sa.Text(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("context_summary_upto_message_id", sa.Integer(), nullable=True),
    )
    op.add_column("sessions", sa.Column("working_state", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "working_state")
    op.drop_column("sessions", "context_summary_upto_message_id")
    op.drop_column("sessions", "context_summary")
