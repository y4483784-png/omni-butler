"""Reserved calendar_events columns for a later external provider.

Revision ID: 0007_calendar_provider_fields
Revises: 0006_session_context
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_calendar_provider_fields"
down_revision = "0006_session_context"
branch_labels = None
depends_on = None


def _event_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "calendar_events" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("calendar_events")}


def upgrade() -> None:
    # init_db() / create_all may have added these before Alembic caught up.
    existing = _event_columns()
    if not existing:
        return
    columns = (
        ("timezone", sa.String(), ""),
        ("location", sa.String(), ""),
        ("description", sa.Text(), ""),
        ("external_id", sa.String(), ""),
        ("provider", sa.String(), "local"),
    )
    for name, type_, default in columns:
        if name in existing:
            continue
        op.add_column(
            "calendar_events",
            sa.Column(name, type_, nullable=True, server_default=default),
        )


def downgrade() -> None:
    existing = _event_columns()
    for name in ("provider", "external_id", "description", "location", "timezone"):
        if name in existing:
            op.drop_column("calendar_events", name)
