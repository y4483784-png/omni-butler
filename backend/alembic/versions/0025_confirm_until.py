from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_confirm_until"
down_revision = "0024_claim_armed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedule_confirm_claims", sa.Column("confirm_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedule_confirm_claims", "confirm_until")
