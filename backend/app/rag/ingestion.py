"""RAG ingestion: parse → structure-aware chunk → SQLite → optional Qdrant vectors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.messages import ingest_user_error
from app.core.embeddings import EmbeddingError, embed_texts
from app.models.models import Chunk, Document
from app.rag.chunking import PARSER_VERSION, chunk_text
from app.rag.parse import parse_file
from app.rag import vector_store
from app.storage import document_local_path


def utc_naive() -> datetime:
    """Naive UTC for DateTime columns (no timezone=True on models)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _embedding_input_for_chunk(chunk: Chunk) -> str:
    text = (chunk.content or "").strip() or " "
    if (getattr(chunk, "kind", "") or "") == "table":
        return text[:3200]
    return text[:8000]


def _set_stage(db: Session, doc: Document, stage: str, *, status: str | None = None) -> None:
    doc.stage = stage
    if status is not None:
        doc.status = status
    db.commit()


def recover_stale_ingests(db: Session, *, stale_after_seconds: int | None = None) -> int:
    """Mark only genuinely stale queued/running jobs failed.

    Celery jobs survive API restarts, so startup must not fail every pending row.
    This function is invoked periodically by Celery Beat instead.
    """
    age = max(
        60,
        int(
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.celery_stale_after_seconds
        ),
    )
    cutoff = utc_naive() - timedelta(seconds=age)
    stale = (
        db.query(Document)
        .filter(
            or_(
                (
                    (Document.status == "processing")
                    & (Document.ingest_started_at.is_not(None))
                    & (Document.ingest_started_at < cutoff)
                ),
                (
                    (Document.status == "pending")
                    & (Document.created_at < cutoff)
                ),
            )
        )
        .all()
    )
    n = 0
    for doc in stale:
        doc.status = "failed"
        doc.stage = "failed"
        doc.error = "入库任务超时或中断，请点击重新解析"
        doc.ingest_task_id = ""
        doc.ingest_started_at = None
        n += 1
    if n:
        db.commit()
    return n


def ingest_document(db: Session, doc: Document) -> Document:
    """Parse a stored file, write chunks, update document status/stage."""
    doc.status = "processing"
    doc.stage = "parsing"
    doc.error = ""
    doc.warning = ""
    if doc.ingest_started_at is None:
        doc.ingest_started_at = utc_naive()
    db.commit()

    try:
        with document_local_path(doc.stored_path) as path:
            if not path.is_file():
                raise ValueError("上传文件丢失")

            _set_stage(db, doc, "parsing", status="processing")
            result = parse_file(path)

            _set_stage(db, doc, "chunking")
            pieces = chunk_text(result)
            if not pieces:
                raise ValueError("分块结果为空")

            # Drop prior vectors (old chunk ids) before replacing SQLite rows
            try:
                vector_store.delete_by_document(doc.id, user_id=doc.user_id or 1)
            except Exception:
                pass

            db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
            for i, piece in enumerate(pieces):
                db.add(
                    Chunk(
                        document_id=doc.id,
                        chunk_index=i,
                        content=piece.content,
                        kind=piece.kind or "text",
                        heading=piece.heading or "",
                        page=piece.page,
                    )
                )
            doc.chunk_count = len(pieces)
            doc.char_count = result.char_count
            warnings: list[str] = []
            if result.warning:
                warnings.append(result.warning)
            doc.parser_version = PARSER_VERSION
            doc.error = ""
            db.commit()

            # Refresh to obtain chunk primary keys for Qdrant point ids
            chunks = (
                db.query(Chunk)
                .filter(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index.asc())
                .all()
            )

            _set_stage(db, doc, "embedding")
            vec_warn = _index_vectors(doc, chunks)
            if vec_warn:
                warnings.append(vec_warn)

            doc.warning = "；".join(warnings)[:500]
            doc.status = "ready"
            doc.stage = "ready"
            doc.ingest_task_id = ""
            doc.ingest_started_at = None
            db.commit()

            db.refresh(doc)
            return doc
    except Exception as e:
        doc.status = "failed"
        doc.stage = "failed"
        doc.error = ingest_user_error(e)
        doc.chunk_count = 0
        doc.char_count = 0
        doc.warning = ""
        doc.parser_version = 0
        doc.ingest_task_id = ""
        doc.ingest_started_at = None
        db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
        try:
            vector_store.delete_by_document(doc.id, user_id=doc.user_id or 1)
        except Exception:
            pass
        db.commit()
        db.refresh(doc)
        return doc


def _index_vectors(doc: Document, chunks: list[Chunk]) -> str:
    """Embed + upsert; return warning text on soft failure, else empty."""
    if not settings.rag_hybrid_enabled:
        return ""
    if not chunks:
        return ""
    if not settings.llm_api_key:
        return "未配置 API Key，已跳过向量化（仅关键词检索）"
    if not vector_store.is_available():
        return "Qdrant 不可达，已跳过向量化（仅关键词检索）"

    try:
        vectors = embed_texts([_embedding_input_for_chunk(c) for c in chunks])
        vector_store.upsert_chunks(
            user_id=doc.user_id or 1,
            document_id=doc.id,
            filename=doc.filename or "",
            chunk_rows=chunks,
            vectors=vectors,
        )
        return ""
    except EmbeddingError as e:
        return f"向量化失败：{e}"
    except Exception as e:
        return f"向量写入失败：{str(e)[:200]}"
