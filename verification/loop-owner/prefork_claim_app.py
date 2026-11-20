"""Celery app for the production mailbox claim. Not imported by the product."""

from __future__ import annotations

import os
from pathlib import Path

from celery import Celery
from celery.signals import worker_init, worker_process_init

BROKER = os.environ.get("PROBE_BROKER", "redis://127.0.0.1:6379/14")
APP = Celery("prefork_claim", broker=BROKER)
APP.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_default_queue="claimprobe",
    broker_connection_retry_on_startup=True,
)


@worker_init.connect
def _publish(**_kwargs) -> None:
    from app.services.delivery_bind import incarnation, install_parent_hooks

    install_parent_hooks()
    root = Path(os.environ["OMNI_DELIVERY_DIR"])
    (root / "ready").write_text(incarnation(), encoding="utf-8")


@worker_process_init.connect
def _fresh_child_connection(**_kwargs) -> None:
    from app.core.db import engine

    engine.dispose()


@APP.task(name="prefork_claim.touch")
def touch() -> None:
    from app.core.db import SessionLocal
    from app.services.delivery_bind import claim_mailbox, incarnation

    db = SessionLocal()
    try:
        claimed = claim_mailbox(db, str(touch.request.id), os.getpid())
    finally:
        db.close()
    root = Path(os.environ["OMNI_DELIVERY_DIR"])
    (root / "child-incarnation").write_text(incarnation(), encoding="utf-8")
    (root / "child-pid").write_text(str(os.getpid()), encoding="utf-8")
    (root / "claimed").write_text(claimed or "", encoding="utf-8")
    if not claimed:
        from celery.exceptions import Reject

        raise Reject("delivery mailbox was not claimed", requeue=False)
