from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_claim_fingerprint"
down_revision = "0011_task_run"
branch_labels = None
depends_on = None

_TABLE = "schedule_confirm_claims"
_INDEX = "uq_schedule_confirm_open_write"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "action" not in columns:
        op.add_column(_TABLE, sa.Column("action", sa.String(), nullable=False, server_default=""))
    if "fingerprint" not in columns:
        op.add_column(_TABLE, sa.Column("fingerprint", sa.String(), nullable=False, server_default=""))
    if "scope" not in columns:
        op.add_column(_TABLE, sa.Column("scope", sa.String(), nullable=False, server_default=""))
    if "generation_ids" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("generation_ids", sa.Text(), nullable=False, server_default="[]"),
        )
    op.execute(
        f"UPDATE {_TABLE} SET action = COALESCE(action, '')"
    )
    op.execute(
        f"UPDATE {_TABLE} SET fingerprint = token "
        f"WHERE fingerprint IS NULL OR fingerprint = ''"
    )
    op.execute(f"UPDATE {_TABLE} SET scope = COALESCE(scope, '')")
    op.execute(
        f"UPDATE {_TABLE} SET generation_ids = '[]' "
        f"WHERE generation_ids IS NULL OR generation_ids = ''"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
        f"ON {_TABLE} (user_id, action, fingerprint) "
        f"WHERE status = 'open'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    for name in ("generation_ids", "scope", "fingerprint", "action"):
        if name in columns:
            op.drop_column(_TABLE, name)
