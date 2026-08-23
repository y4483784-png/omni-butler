"""Add auth columns to users."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_user_auth"
down_revision = "0002_ingest_task_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column("users", sa.Column("is_active", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("is_admin", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "is_admin")
    op.drop_column("users", "is_active")
    op.drop_column("users", "password_hash")
