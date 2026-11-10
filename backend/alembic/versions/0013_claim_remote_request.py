"""Remember the external key for one confirm claim."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_claim_remote_request"
down_revision = "0012_claim_fingerprint"
branch_labels = None
depends_on = None

_TABLE = "schedule_confirm_claims"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "remote_request_id" in columns:
        return
    op.add_column(
        _TABLE,
        sa.Column("remote_request_id", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "remote_request_id" not in columns:
        return
    op.drop_column(_TABLE, "remote_request_id")
