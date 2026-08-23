"""Alembic environment — reuses app settings and model metadata.

Run from backend/:
    alembic upgrade head          # create/upgrade schema
    alembic stamp head            # adopt an existing create_all() database
    alembic revision --autogenerate -m "..."   # future schema changes
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models.models  # noqa: E402,F401  (register mappers on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_db_url = (settings.database_migrate_url or "").strip() or settings.database_url
config.set_main_option("sqlalchemy.url", _db_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Prefer DATABASE_MIGRATE_URL (table owner) so runtime omni_app is not used for DDL.
    connectable = engine_from_config(
        {"sqlalchemy.url": _db_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
