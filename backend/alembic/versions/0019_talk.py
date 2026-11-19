"""Talk columns on task runs and confirm claims."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_talk"
down_revision = "0018_picture"
branch_labels = None
depends_on = None

_RUNS = "task_runs"
_CLAIMS = "schedule_confirm_claims"


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


def _check(name: str, table: str, sql: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    existing = {item["name"] for item in inspector.get_check_constraints(table)}
    if name in existing:
        return
    op.create_check_constraint(name, table, sql)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _RUNS in set(inspector.get_table_names()):
        _add(_RUNS, sa.Column("kind", sa.String(), nullable=False, server_default="turn"))
        _add(_RUNS, sa.Column("goal", sa.Text(), nullable=False, server_default=""))
        _add(
            _RUNS,
            sa.Column("web_constraint", sa.String(), nullable=False, server_default="allow"),
        )
        _add(_RUNS, sa.Column("web_constraint_message_id", sa.Integer(), nullable=True))
        _add(_RUNS, sa.Column("intent_version", sa.Integer(), nullable=False, server_default="1"))
        _add(_RUNS, sa.Column("last_progress_at", sa.DateTime(), nullable=True))
        _add(_RUNS, sa.Column("dispatch_token", sa.String(), nullable=True))
        _add(_RUNS, sa.Column("dispatch_epoch", sa.Integer(), nullable=False, server_default="0"))
        _add(_RUNS, sa.Column("dispatch_until", sa.DateTime(), nullable=True))
        _add(_RUNS, sa.Column("exec_token", sa.String(), nullable=True))
        _add(_RUNS, sa.Column("exec_epoch", sa.Integer(), nullable=False, server_default="0"))
        _add(_RUNS, sa.Column("hold_until", sa.DateTime(), nullable=True))
        due_default = sa.false() if bind.dialect.name == "postgresql" else sa.text("0")
        _add(
            _RUNS,
            sa.Column("continuation_due", sa.Boolean(), nullable=False, server_default=due_default),
        )
        if bind.dialect.name != "postgresql":
            _sqlite_checks()
        _check("ck_task_runs_kind", _RUNS, "kind IN ('turn', 'job')")
        _check("ck_task_runs_web", _RUNS, "web_constraint IN ('allow', 'forbid')")
    _add(_CLAIMS, sa.Column("intent_version", sa.Integer(), nullable=False, server_default="1"))


def _sqlite_checks() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    ddl = str(
        bind.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'task_runs'")
        ).scalar()
        or ""
    )
    if "ck_task_runs_kind" in ddl and "ck_task_runs_web" in ddl:
        return
    columns = _names(inspector, _RUNS)
    needed = {
        "kind",
        "goal",
        "web_constraint",
        "web_constraint_message_id",
        "intent_version",
        "last_progress_at",
        "dispatch_token",
        "dispatch_epoch",
        "dispatch_until",
        "exec_token",
        "exec_epoch",
        "hold_until",
        "continuation_due",
    }
    if not needed <= columns:
        return
    with op.batch_alter_table(_RUNS) as batch:
        if "ck_task_runs_kind" not in ddl:
            batch.create_check_constraint("ck_task_runs_kind", "kind IN ('turn', 'job')")
        if "ck_task_runs_web" not in ddl:
            batch.create_check_constraint(
                "ck_task_runs_web", "web_constraint IN ('allow', 'forbid')"
            )


_RUN_COLUMNS = (
    "continuation_due",
    "hold_until",
    "exec_epoch",
    "exec_token",
    "dispatch_until",
    "dispatch_epoch",
    "dispatch_token",
    "last_progress_at",
    "intent_version",
    "web_constraint_message_id",
    "web_constraint",
    "goal",
    "kind",
)


def _drop(table: str, column: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if column not in _names(inspector, table):
        return
    op.drop_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _RUNS in set(inspector.get_table_names()):
        if bind.dialect.name == "postgresql":
            op.execute("ALTER TABLE task_runs DROP CONSTRAINT IF EXISTS ck_task_runs_kind")
            op.execute("ALTER TABLE task_runs DROP CONSTRAINT IF EXISTS ck_task_runs_web")
            for column in _RUN_COLUMNS:
                _drop(_RUNS, column)
        else:
            present = _names(inspector, _RUNS)
            with op.batch_alter_table(_RUNS) as batch:
                batch.drop_constraint("ck_task_runs_kind", type_="check")
                batch.drop_constraint("ck_task_runs_web", type_="check")
                for column in _RUN_COLUMNS:
                    if column in present:
                        batch.drop_column(column)
    _drop(_CLAIMS, "intent_version")
