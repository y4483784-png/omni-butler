from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_claim_armed_at"
down_revision = "0023_resume"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedule_confirm_claims", sa.Column("armed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedule_confirm_claims", "armed_at")
