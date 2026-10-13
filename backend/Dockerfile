# Backend image: api, worker, beat, and sandbox-runner.
#
# Do not COPY named files one-by-one: BuildKit checksums against HGFS/FUSE
# (VMware shared folders) often fail with "not found" even when ls sees the file.
# Do not download Docker CLI from download.docker.com (often reset in CN).
# sandbox-runner bind-mounts the host docker binary + docker.sock.
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

COPY . /app

# `EVAL_DEV_DEPS=1` also installs requirements-dev.txt so the eval container can run
# the pytest suite (`eval.py run pytest`). Default stays off: the api image must not
# carry test-only dependencies.
ARG EVAL_DEV_DEPS=0

RUN pip install --no-cache-dir -r /app/requirements.txt \
    && if [ "$EVAL_DEV_DEPS" = "1" ]; then pip install --no-cache-dir -r /app/requirements-dev.txt; fi \
    && printf '%s\n' \
        '#!/bin/sh' \
        'set -e' \
        'cd /app' \
        'if [ -n "${SANDBOX_TMP_DIR:-}" ]; then mkdir -p "$SANDBOX_TMP_DIR"; fi' \
        'if [ "$#" -gt 0 ]; then exec "$@"; fi' \
        'MODE="${MODE:-api}"' \
        'if [ "$MODE" = "worker" ]; then exec celery -A app.tasks.celery_app:celery_app worker -Q ingest,maintenance -l info; fi' \
        'if [ "$MODE" = "beat" ]; then exec celery -A app.tasks.celery_app:celery_app beat -l info; fi' \
        'if [ "$MODE" = "sandbox" ]; then exec uvicorn app.sandbox.server:app --host 0.0.0.0 --port 8002; fi' \
        'exec uvicorn app.main:app --host 0.0.0.0 --port 8001' \
        > /entrypoint.sh \
    && chmod +x /entrypoint.sh

EXPOSE 8001 8002

# sandbox-runner talks to /var/run/docker.sock as root.
ENTRYPOINT ["/entrypoint.sh"]
