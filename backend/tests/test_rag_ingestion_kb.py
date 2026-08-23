from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, init_db
from app.models.models import Chunk, Document
from app.rag.ingestion import ingest_document


@pytest.fixture
def object_store():
    store = MagicMock()
    store.put.side_effect = lambda key, _data: key
    store.exists.return_value = True
    from app.storage import factory as blob_factory

    blob_factory.reset_blob_store_for_tests()
    blob_factory._store = store
    try:
        yield store
    finally:
        blob_factory.reset_blob_store_for_tests()


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


def test_ingest_document_writes_chunks_and_ready_warning():
    doc_id: int | None = None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "guide.md"
        p.write_text("# 总览\n\n第一段说明。\n\n第二段继续。", encoding="utf-8")

        init_db()
        db = SessionLocal()
        try:
            doc = Document(
                user_id=1,
                filename="guide.md",
                stored_path=str(p),
                status="pending",
                stage="pending",
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            doc_id = doc.id

            with (
                patch("app.rag.ingestion.settings.llm_api_key", "fake-key"),
                patch("app.rag.ingestion.vector_store.is_available", return_value=False),
                patch("app.rag.ingestion.vector_store.delete_by_document"),
            ):
                out = ingest_document(db, doc)

            chunks = (
                db.query(Chunk)
                .filter(Chunk.document_id == doc_id)
                .order_by(Chunk.chunk_index.asc())
                .all()
            )
            assert out.status == "ready"
            assert out.stage == "ready"
            assert out.chunk_count == len(chunks) > 0
            assert out.char_count > 0
            assert out.parser_version > 0
            assert "Qdrant 不可达" in out.warning
            assert any(c.heading == "总览" for c in chunks)
        finally:
            db.close()
            if doc_id is not None:
                _delete_document_tree(doc_id)


def test_ingest_document_marks_failed_when_file_missing():
    doc_id: int | None = None
    init_db()
    db = SessionLocal()
    try:
        doc = Document(
            user_id=1,
            filename="missing.md",
            stored_path=str(Path(tempfile.gettempdir()) / "definitely_missing.md"),
            status="pending",
            stage="pending",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id

        out = ingest_document(db, doc)

        assert out.status == "failed"
        assert out.stage == "failed"
        assert "上传文件丢失" in out.error
    finally:
        db.close()
        if doc_id is not None:
            _delete_document_tree(doc_id)


def test_ingest_real_xlsx_fixture_preserves_sheet_metadata():
    init_db()
    fixture_dir = Path(__file__).parent / "fixtures" / "xlsx"
    candidates = sorted(fixture_dir.glob("*汇总表.xlsx"))
    assert candidates, f"未在 {fixture_dir} 找到 xlsx 测试夹具"

    doc_id: int | None = None
    db = SessionLocal()
    try:
        doc = Document(
            user_id=1,
            filename=candidates[0].name,
            stored_path=str(candidates[0]),
            status="pending",
            stage="pending",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id

        with (
            patch("app.rag.ingestion.settings.llm_api_key", "fake-key"),
            patch("app.rag.ingestion.vector_store.is_available", return_value=False),
            patch("app.rag.ingestion.vector_store.delete_by_document"),
        ):
            out = ingest_document(db, doc)

        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index.asc())
            .all()
        )
        assert out.status == "ready"
        assert out.chunk_count == len(chunks) > 1
        assert any("sheet1" in (c.heading or "") for c in chunks)
        assert any("数据列数：4" in (c.content or "") for c in chunks)
    finally:
        db.close()
        if doc_id is not None:
            _delete_document_tree(doc_id)


def test_upload_documents_rejects_unsupported_extension(object_store, auth_client: TestClient):
    init_db()
    resp = auth_client.post(
            "/api/kb/documents",
            files=[("files", ("demo.exe", io.BytesIO(b"abc"), "application/octet-stream"))],
        )

    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["status"] == "failed"
    assert "格式不支持" in rows[0]["error"]
    _delete_document_tree(rows[0]["id"])


def test_upload_documents_stores_file_and_queues_ingest(object_store, auth_client: TestClient):
    init_db()
    with patch("app.api.kb.enqueue_document_ingest", return_value=True) as enqueue_mock:
        resp = auth_client.post(
            "/api/kb/documents",
            files=[("files", ("notes.md", io.BytesIO(b"# Title\n\nbody"), "text/markdown"))],
        )

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["status"] == "pending"
    assert row["stage"] == "pending"
    enqueue_mock.assert_called_once()
    assert enqueue_mock.call_args.args[1].id == row["id"]

    db = SessionLocal()
    try:
        doc = db.get(Document, row["id"])
        assert doc is not None
        assert doc.stored_path
        assert doc.stored_path.startswith("u1/")
        object_store.put.assert_called_once_with(doc.stored_path, b"# Title\n\nbody")
    finally:
        db.close()
        _delete_document_tree(row["id"])


def test_reingest_document_rejects_in_progress(object_store, auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        doc = Document(
            user_id=1,
            filename="busy.md",
            stored_path="u1/busy.md",
            status="processing",
            stage="parsing",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    resp = auth_client.post(f"/api/kb/documents/{doc_id}/reingest")

    assert resp.status_code == 409
    assert "正在解析" in resp.json()["detail"]
    _delete_document_tree(doc_id)


def test_reingest_document_rejects_missing_stored_file(object_store, auth_client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        doc = Document(
            user_id=1,
            filename="gone.md",
            stored_path=str(Path(tempfile.gettempdir()) / "gone.md"),
            status="ready",
            stage="ready",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    resp = auth_client.post(f"/api/kb/documents/{doc_id}/reingest")

    assert resp.status_code == 400
    assert "上传文件丢失" in resp.json()["detail"]
    _delete_document_tree(doc_id)
