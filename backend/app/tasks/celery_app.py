"""Celery application shared by API publishers, workers and Beat."""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

broker_url = (settings.celery_broker_url or "").strip() or settings.redis_url

# Do not pass include= here: Celery() would import tasks before this name is bound.
celery_app = Celery("omni_butler", broker=broker_url)
celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "app.tasks.ingestion.ingest_document_task": {"queue": "ingest"},
        "app.tasks.ingestion.recover_stale_ingests_task": {"queue": "maintenance"},
    },
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        # Must exceed the hard task limit so a healthy long ingest is not redelivered.
        "visibility_timeout": max(3600, settings.celery_ingest_time_limit + 300),
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 1,
    },
    task_publish_retry=False,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "recover-stale-document-ingests": {
            "task": "app.tasks.ingestion.recover_stale_ingests_task",
            "schedule": max(60, settings.celery_stale_check_seconds),
            "options": {"queue": "maintenance"},
        }
    },
)

# Register tasks after celery_app exists (avoids circular import on Celery()).
import app.tasks.ingestion as _ingestion  # noqa: E402,F401

from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _ensure_app_role_on_worker(**_kwargs) -> None:
    """Create omni_app after fork. Do not do this at import — it blocked uvicorn."""
    try:
        from app.core.db import ensure_app_role

        ensure_app_role()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("ensure_app_role on worker init failed", exc_info=True)

