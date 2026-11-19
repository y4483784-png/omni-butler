from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_watch"
down_revision = "0019_talk"
branch_labels = None
depends_on = None

_RUNS = "task_runs"
_COLUMNS = ("observed_progress_at", "notice_progress_at")


def _names(inspector, table: str) -> set[str]:
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if column.name in _names(inspector, table):
        return
    op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _RUNS not in set(inspector.get_table_names()):
        return
    for name in _COLUMNS:
        _add(_RUNS, sa.Column(name, sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _RUNS not in set(inspector.get_table_names()):
        return
    present = _names(inspector, _RUNS)
    dropping = [name for name in _COLUMNS if name in present]
    if not dropping:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_RUNS) as batch:
            for name in dropping:
                batch.drop_column(name)
        return
    for name in dropping:
        op.drop_column(_RUNS, name)
