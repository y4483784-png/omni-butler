#!/bin/sh
set -e
cd /app

if [ -n "${SANDBOX_TMP_DIR:-}" ]; then
  mkdir -p "$SANDBOX_TMP_DIR"
fi

# Compose `run --rm api alembic upgrade head` (and other one-shots) pass argv through.
if [ "$#" -gt 0 ]; then
  if [ "$1" = "alembic" ] && [ -n "${DATABASE_MIGRATE_URL:-}" ]; then
    export DATABASE_URL="$DATABASE_MIGRATE_URL"
  fi
  exec "$@"
fi

MODE="${MODE:-api}"
if [ "$MODE" = "worker" ]; then
  exec celery -A app.tasks.celery_app:celery_app worker -Q ingest,maintenance -l info
fi
if [ "$MODE" = "beat" ]; then
  exec celery -A app.tasks.celery_app:celery_app beat -l info
fi
if [ "$MODE" = "sandbox" ]; then
  exec uvicorn app.sandbox.server:app --host 0.0.0.0 --port 8002
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8001
