"""Durable and idempotent document-ingestion tasks."""

from __future__ import annotations

import logging
import uuid

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, maintenance_session, set_rls_context
from app.models.models import Document
from app.rag.ingestion import ingest_document, recover_stale_ingests, utc_naive
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def enqueue_document_ingest(db: Session, doc: Document) -> bool:
    """Persist task ownership before publish; mark failed if broker publish fails."""
    task_id = uuid.uuid4().hex
    doc.status = "pending"
    doc.stage = "pending"
    doc.error = ""
    doc.ingest_task_id = task_id
    doc.ingest_started_at = None
    db.commit()
    db.refresh(doc)
    try:
        ingest_document_task.apply_async(
            args=[doc.id, doc.user_id],
            task_id=task_id,
            queue="ingest",
        )
        return True
    except Exception as exc:
        logger.exception("failed to enqueue document %s", doc.id)
        db.rollback()
        current = db.get(Document, doc.id)
        if current is not None and current.ingest_task_id == task_id:
            current.status = "failed"
            current.stage = "failed"
            current.error = f"入库队列不可用：{type(exc).__name__}: {str(exc)[:300]}"
            current.ingest_task_id = ""
            current.ingest_started_at = None
            db.commit()
            db.refresh(current)
        return False


def _lookup_document_user_id(doc_id: int) -> int | None:
    """Owner-engine lookup for in-flight tasks that only carried doc_id.

    Under FORCE RLS the worker's omni_app pool cannot see the row until
    app.current_user_id is set, so tenant id must come from args or here.
    """
    db = maintenance_session()
    try:
        doc = db.get(Document, int(doc_id))
        if doc is None:
            return None
        return int(doc.user_id or 1)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="app.tasks.ingestion.ingest_document_task",
    queue="ingest",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.celery_ingest_soft_time_limit,
    time_limit=settings.celery_ingest_time_limit,
)
def ingest_document_task(self, doc_id: int, user_id: int | None = None) -> dict:
    """Claim and ingest one document.

    At-least-once redelivery carries the same Celery task id. A redelivered
    owner may safely rebuild the document; stale or duplicate task ids skip.

    Set tenant GUC *before* UPDATE/GET: otherwise FORCE RLS yields missing.
    """
    task_id = str(self.request.id or "")
    uid = int(user_id) if user_id is not None else _lookup_document_user_id(doc_id)
    if uid is None:
        return {"status": "missing", "document_id": int(doc_id)}

    db = SessionLocal()
    try:
        set_rls_context(db, uid)
        claimed = (
            db.query(Document)
            .filter(
                Document.id == int(doc_id),
                Document.status == "pending",
                Document.ingest_task_id == task_id,
            )
            .update(
                {
                    Document.status: "processing",
                    Document.stage: "parsing",
                    Document.ingest_started_at: utc_naive(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        doc = db.get(Document, int(doc_id))
        if doc is None:
            return {"status": "missing", "document_id": int(doc_id)}

        # Redelivery after worker loss: same owner id may resume by rebuilding.
        redelivered_owner = (
            doc.status == "processing"
            and bool(task_id)
            and doc.ingest_task_id == task_id
        )
        if not claimed and not redelivered_owner:
            return {
                "status": "skipped",
                "document_id": doc.id,
                "document_status": doc.status,
            }

        out = ingest_document(db, doc)
        return {
            "status": out.status,
            "document_id": out.id,
            "error": out.error or "",
        }
    except SoftTimeLimitExceeded:
        db.rollback()
        set_rls_context(db, uid)
        doc = db.get(Document, int(doc_id))
        if doc is not None and doc.ingest_task_id == task_id:
            doc.status = "failed"
            doc.stage = "failed"
            doc.error = "入库任务超时，请点击重新解析"
            doc.ingest_task_id = ""
            doc.ingest_started_at = None
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.ingestion.recover_stale_ingests_task",
    queue="maintenance",
)
def recover_stale_ingests_task() -> int:
    db = maintenance_session()
    try:
        return recover_stale_ingests(
            db,
            stale_after_seconds=settings.celery_stale_after_seconds,
        )
    finally:
        db.close()

