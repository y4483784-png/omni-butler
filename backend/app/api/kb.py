"""Knowledge-base API: upload / list / delete / reingest (async ingest + stage)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.deps import get_db_scoped
from app.core.ownership import require_owned_document
from app.models.models import Chunk, Document, User
from app.rag.parse import SUPPORTED
from app.rag import vector_store
from app.storage import delete_document_blob, document_exists, get_blob_store, make_object_key
from app.tasks.ingestion import enqueue_document_ingest

router = APIRouter(prefix="/api/kb", tags=["kb"])


def _serialize(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "stage": getattr(doc, "stage", None) or doc.status or "",
        "error": doc.error or "",
        "chunk_count": doc.chunk_count or 0,
        "char_count": getattr(doc, "char_count", None) or 0,
        "warning": getattr(doc, "warning", None) or "",
        "parser_version": getattr(doc, "parser_version", None) or 0,
        "created_at": doc.created_at.isoformat() if doc.created_at else "",
    }


@router.get("/documents")
def list_documents(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    docs = (
        db.query(Document)
        .filter(Document.user_id == current_user.id)
        .order_by(Document.id.desc())
        .all()
    )
    return [_serialize(d) for d in docs]


@router.post("/documents")
async def upload_documents(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    if not files:
        raise HTTPException(400, "未选择文件")
    if len(files) > 5:
        raise HTTPException(400, "单次最多上传 5 个文件")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    store = get_blob_store()
    results: list[dict] = []
    uid = current_user.id

    for f in files:
        raw_name = f.filename or "unnamed"
        ext = Path(raw_name).suffix.lower()
        if ext not in SUPPORTED:
            doc = Document(
                user_id=uid,
                filename=raw_name,
                status="failed",
                stage="failed",
                error=f"格式不支持：{ext or '(无扩展名)'}，支持 {', '.join(sorted(SUPPORTED))}",
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            results.append(_serialize(doc))
            continue

        data = await f.read()
        if len(data) > max_bytes:
            doc = Document(
                user_id=uid,
                filename=raw_name,
                status="failed",
                stage="failed",
                error=f"文件超过 {settings.max_upload_mb}MB 限制",
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            results.append(_serialize(doc))
            continue

        object_key = make_object_key(user_id=uid, filename=raw_name)
        store.put(object_key, data)

        doc = Document(
            user_id=uid,
            filename=raw_name,
            stored_path=object_key,
            status="pending",
            stage="pending",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        enqueue_document_ingest(db, doc)
        db.refresh(doc)
        results.append(_serialize(doc))

    return results


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    doc = require_owned_document(db, doc_id, current_user.id)
    try:
        vector_store.delete_by_document(doc_id, user_id=current_user.id)
    except Exception:
        pass
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
    stored_key = doc.stored_path or ""
    db.delete(doc)
    db.commit()
    delete_document_blob(stored_key)
    return {"ok": True}


@router.post("/documents/{doc_id}/reingest")
def reingest_document(
    doc_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    """Re-parse and re-chunk an existing upload (new OCR / chunking policies)."""
    doc = require_owned_document(db, doc_id, current_user.id)
    if not doc.stored_path or not document_exists(doc.stored_path):
        raise HTTPException(400, "上传文件丢失，请重新上传")
    if doc.status == "processing":
        raise HTTPException(409, "文档正在解析中，请稍后再试")

    enqueue_document_ingest(db, doc)
    db.refresh(doc)
    return _serialize(doc)
