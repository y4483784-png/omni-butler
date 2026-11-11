"""Rebuild the receipt route check so a filed mail can be stored."""

from __future__ import annotations

from alembic import op

from app.models.models import EVENT_RECEIPT_ROUTES

revision = "0016_mail_entry"
down_revision = "0015_memory_ledger"
branch_labels = None
depends_on = None

_ROUTE_SQL = "route IN ({})".format(", ".join(repr(item) for item in EVENT_RECEIPT_ROUTES))
_PREVIOUS_ROUTES = (
    "noise",
    "held",
    "passthrough",
    "opened",
    "stopped",
    "queued",
    "kept",
    "reissued",
)
_PREVIOUS_SQL = "route IN ({})".format(", ".join(repr(item) for item in _PREVIOUS_ROUTES))


def _rebuild_route(sql: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE event_receipts DROP CONSTRAINT IF EXISTS ck_event_receipts_route")
        op.create_check_constraint("ck_event_receipts_route", "event_receipts", sql)
        return
    with op.batch_alter_table("event_receipts") as batch:
        batch.drop_constraint("ck_event_receipts_route", type_="check")
        batch.create_check_constraint("ck_event_receipts_route", sql)


def upgrade() -> None:
    _rebuild_route(_ROUTE_SQL)


def downgrade() -> None:
    _rebuild_route(_PREVIOUS_SQL)
