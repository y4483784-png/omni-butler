"""Celery ingest queue: enqueue, idempotency, stale recovery (eager mode)."""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.db import SessionLocal, init_db
from app.models.models import Chunk, Document
from app.rag.ingestion import recover_stale_ingests, utc_naive
from app.tasks.celery_app import celery_app
from app.tasks.ingestion import enqueue_document_ingest, ingest_document_task


def _delete_document_tree(doc_id: int) -> None:
    db = SessionLocal()
    try:
        db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
        doc = db.get(Document, doc_id)
        if doc:
            db.delete(doc)
        db.commit()
    finally:
        db.close()


def _make_pending_md(db, path: Path) -> Document:
    doc = Document(
        user_id=1,
        filename=path.name,
        stored_path=str(path),
        status="pending",
        stage="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_eager_ingest_task_writes_ready_document():
    celery_app.conf.task_always_eager = True
    init_db()
    doc_id: int | None = None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "notes.md"
        p.write_text("# 标题\n\n正文一段。", encoding="utf-8")
        db = SessionLocal()
        try:
            doc = _make_pending_md(db, p)
            doc_id = doc.id
            with (
                patch("app.rag.ingestion.settings.llm_api_key", "fake-key"),
                patch("app.rag.ingestion.vector_store.is_available", return_value=False),
                patch("app.rag.ingestion.vector_store.delete_by_document"),
            ):
                assert enqueue_document_ingest(db, doc) is True
            db.refresh(doc)
            assert doc.status == "ready"
            assert doc.ingest_task_id == ""
            chunks = db.query(Chunk).filter(Chunk.document_id == doc_id).all()
            assert len(chunks) > 0
        finally:
            db.close()
            if doc_id is not None:
                _delete_document_tree(doc_id)


def test_ingest_task_skips_when_task_id_does_not_match():
    celery_app.conf.task_always_eager = True
    init_db()
    db = SessionLocal()
    doc_id: int | None = None
    try:
        doc = Document(
            user_id=1,
            filename="x.md",
            stored_path="u1/x.md",
            status="pending",
            stage="pending",
            ingest_task_id="owner-a",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
        result = ingest_document_task.apply(args=[doc.id, doc.user_id], task_id="owner-b").get()
        assert result["status"] == "skipped"
        db.refresh(doc)
        assert doc.status == "pending"
        assert doc.ingest_task_id == "owner-a"
    finally:
        db.close()
        if doc_id is not None:
            _delete_document_tree(doc_id)


def test_enqueue_marks_failed_when_broker_publish_fails():
    init_db()
    db = SessionLocal()
    doc_id: int | None = None
    try:
        doc = Document(
            user_id=1,
            filename="x.md",
            stored_path="u1/x.md",
            status="ready",
            stage="ready",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
        with patch(
            "app.tasks.ingestion.ingest_document_task.apply_async",
            side_effect=RuntimeError("broker down"),
        ):
            ok = enqueue_document_ingest(db, doc)
        assert ok is False
        db.refresh(doc)
        assert doc.status == "failed"
        assert "入库队列不可用" in doc.error
        assert doc.ingest_task_id == ""
    finally:
        db.close()
        if doc_id is not None:
            _delete_document_tree(doc_id)


def test_recover_stale_ingests_fails_old_jobs_only():
    init_db()
    db = SessionLocal()
    ids: list[int] = []
    try:
        old = utc_naive() - timedelta(hours=2)
        stale = Document(
            user_id=1,
            filename="stale.md",
            stored_path="u1/stale.md",
            status="processing",
            stage="embedding",
            ingest_task_id="t1",
            ingest_started_at=old,
            created_at=old,
        )
        pending_old = Document(
            user_id=1,
            filename="pending.md",
            stored_path="u1/pending.md",
            status="pending",
            stage="pending",
            ingest_task_id="t3",
            created_at=old,
        )
        fresh = Document(
            user_id=1,
            filename="fresh.md",
            stored_path="u1/fresh.md",
            status="processing",
            stage="parsing",
            ingest_task_id="t2",
            ingest_started_at=utc_naive(),
            created_at=utc_naive(),
        )
        db.add_all([stale, pending_old, fresh])
        db.commit()
        db.refresh(stale)
        db.refresh(pending_old)
        db.refresh(fresh)
        ids = [stale.id, pending_old.id, fresh.id]
        n = recover_stale_ingests(db, stale_after_seconds=3600)
        assert n == 2
        db.refresh(stale)
        db.refresh(pending_old)
        db.refresh(fresh)
        assert stale.status == "failed"
        assert pending_old.status == "failed"
        assert "超时或中断" in stale.error
        assert fresh.status == "processing"
    finally:
        db.close()
        for i in ids:
            _delete_document_tree(i)


def test_enqueue_passes_user_id_to_celery():
    init_db()
    db = SessionLocal()
    doc_id: int | None = None
    try:
        doc = Document(
            user_id=1,
            filename="x.md",
            stored_path="u1/x.md",
            status="ready",
            stage="ready",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
        with patch("app.tasks.ingestion.ingest_document_task.apply_async") as mock:
            assert enqueue_document_ingest(db, doc) is True
        mock.assert_called_once()
        args = mock.call_args.kwargs["args"]
        assert args[0] == doc.id
        assert args[1] == doc.user_id
    finally:
        db.close()
        if doc_id is not None:
            _delete_document_tree(doc_id)


def test_ingest_sets_rls_before_claim_and_get():
    from pathlib import Path

    import app.tasks.ingestion as ing

    src = Path(ing.__file__).read_text(encoding="utf-8")
    start = src.index("def ingest_document_task")
    end = src.index("def recover_stale_ingests_task")
    body = src[start:end]
    assert body.index("set_rls_context") < body.index(".update(")
    assert body.index("set_rls_context") < body.index("db.get(Document")
    rec = src[end:]
    assert "maintenance_session" in rec


def test_legacy_ingest_task_without_user_id_still_skips():
    """In-flight broker messages may only carry doc_id; lookup then set RLS."""
    celery_app.conf.task_always_eager = True
    init_db()
    db = SessionLocal()
    doc_id: int | None = None
    try:
        doc = Document(
            user_id=1,
            filename="legacy.md",
            stored_path="u1/legacy.md",
            status="pending",
            stage="pending",
            ingest_task_id="owner-a",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
        result = ingest_document_task.apply(args=[doc.id], task_id="owner-b").get()
        assert result["status"] == "skipped"
        db.refresh(doc)
        assert doc.status == "pending"
    finally:
        db.close()
        if doc_id is not None:
            _delete_document_tree(doc_id)
