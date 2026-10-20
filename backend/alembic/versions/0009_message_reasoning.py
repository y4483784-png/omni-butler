from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_message_reasoning"
down_revision = "0008_calendar_google_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "messages" not in set(inspector.get_table_names()):
        return
    columns = {col["name"] for col in inspector.get_columns("messages")}
    if "reasoning" in columns:
        return
    op.add_column("messages", sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "messages" not in set(inspector.get_table_names()):
        return
    columns = {col["name"] for col in inspector.get_columns("messages")}
    if "reasoning" not in columns:
        return
    op.drop_column("messages", "reasoning")
