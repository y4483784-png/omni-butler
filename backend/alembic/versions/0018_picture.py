"""Chat image rows and per-call usage."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.models import LLM_CALL_ROLES, TASK_IMAGE_MEDIA, TASK_IMAGE_PURGE

revision = "0018_picture"
down_revision = "0017_errand"
branch_labels = None
depends_on = None

_TENANT = "nullif(current_setting('app.current_user_id', true), '')::int"
_RLS_TABLES = ("llm_calls", "task_images")
_ROLE_SQL = "role IN ({})".format(", ".join(repr(role) for role in LLM_CALL_ROLES))
_MEDIA_SQL = "media_type IN ({})".format(", ".join(repr(item) for item in TASK_IMAGE_MEDIA))
_PURGE_SQL = "purge_state IN ({})".format(", ".join(repr(item) for item in TASK_IMAGE_PURGE))


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant ON {table}
        USING (user_id = {_TENANT})
        WITH CHECK (user_id = {_TENANT})
        """
    )


def _disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "llm_calls" not in tables:
        op.create_table(
            "llm_calls",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("generation", sa.Integer(), nullable=True),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(_ROLE_SQL, name="ck_llm_calls_role"),
        )
        op.create_index("ix_llm_calls_user_id", "llm_calls", ["user_id"])
    if "task_images" not in tables:
        op.create_table(
            "task_images",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("task_run_id", sa.Integer(), sa.ForeignKey("task_runs.id"), nullable=False),
            sa.Column("media_type", sa.String(), nullable=False),
            sa.Column("byte_length", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(), nullable=False),
            sa.Column("storage_key", sa.String(), nullable=False),
            sa.Column("purge_state", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(_MEDIA_SQL, name="ck_task_images_media"),
            sa.CheckConstraint(_PURGE_SQL, name="ck_task_images_purge"),
        )
        op.create_index("ix_task_images_user_id", "task_images", ["user_id"])
        op.create_index("ix_task_images_task_run_id", "task_images", ["task_run_id"])
    if bind.dialect.name != "postgresql":
        return
    for table in _RLS_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _RLS_TABLES:
            _disable_rls(table)
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "task_images" in tables:
        op.drop_table("task_images")
    if "llm_calls" in tables:
        op.drop_table("llm_calls")
