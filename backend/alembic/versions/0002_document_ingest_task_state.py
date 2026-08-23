"""Add durable document-ingest task ownership fields.

Revision ID: 0002_ingest_task_state
Revises: 0001_baseline
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_ingest_task_state"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("ingest_task_id", sa.String(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("ingest_started_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_documents_ingest_task_id",
        "documents",
        ["ingest_task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_ingest_task_id", table_name="documents")
    op.drop_column("documents", "ingest_started_at")
    op.drop_column("documents", "ingest_task_id")
