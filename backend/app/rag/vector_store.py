"""Qdrant vector store for chunk embeddings (Phase 2+ hybrid RAG)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_avail_cache: tuple[float, bool] | None = None
_AVAIL_TTL_OK = 30.0
_AVAIL_TTL_FAIL = 10.0


@dataclass
class VectorHit:
    chunk_id: int
    document_id: int
    score: float


def _client():
    from qdrant_client import QdrantClient

    return QdrantClient(url=settings.qdrant_url, timeout=2.0)


def is_available() -> bool:
    global _avail_cache
    if not settings.rag_hybrid_enabled:
        return False
    now = time.monotonic()
    if _avail_cache is not None:
        ts, ok = _avail_cache
        ttl = _AVAIL_TTL_OK if ok else _AVAIL_TTL_FAIL
        if now - ts < ttl:
            return ok
    try:
        c = _client()
        c.get_collections()
        _avail_cache = (now, True)
        return True
    except Exception as e:
        logger.debug("Qdrant unavailable: %s", e)
        _avail_cache = (now, False)
        return False


def _collection_vector_size(client, name: str) -> int | None:
    """Return configured vector size for collection, or None if missing/unknown."""
    try:
        info = client.get_collection(name)
        params = getattr(info.config, "params", None)
        vectors = getattr(params, "vectors", None) if params else None
        # Named vectors vs single VectorParams
        size = getattr(vectors, "size", None)
        if size is not None:
            return int(size)
        if isinstance(vectors, dict) and vectors:
            first = next(iter(vectors.values()))
            return int(getattr(first, "size", 0) or 0) or None
    except Exception as e:
        logger.debug("Could not read collection size for %s: %s", name, e)
    return None


def ensure_collection(dim: int) -> None:
    """Create collection if missing; recreate when embedding dim changes."""
    from qdrant_client.http import models as qm

    c = _client()
    name = settings.qdrant_collection
    names = {col.name for col in c.get_collections().collections}
    if name in names:
        existing = _collection_vector_size(c, name)
        if existing is None or existing == dim:
            _ensure_payload_indexes(c, name)
            return
        logger.warning(
            "Qdrant collection %s dim %s != embedding dim %s — recreating "
            "(re-ingest all documents to restore vectors)",
            name,
            existing,
            dim,
        )
        c.delete_collection(collection_name=name)

    c.create_collection(
        collection_name=name,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )
    logger.info("Created Qdrant collection %s (dim=%s)", name, dim)
    _ensure_payload_indexes(c, name)


def _ensure_payload_indexes(c, collection_name: str) -> None:
    """Index tenant/document fields for filtered search and delete."""
    from qdrant_client.http import models as qm

    for field in ("user_id", "document_id"):
        try:
            c.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=qm.PayloadSchemaType.INTEGER,
            )
        except Exception as e:
            logger.debug("payload index %s may already exist: %s", field, e)


def _touch_payload_indexes() -> None:
    try:
        c = _client()
        names = {col.name for col in c.get_collections().collections}
        if settings.qdrant_collection in names:
            _ensure_payload_indexes(c, settings.qdrant_collection)
    except Exception as e:
        logger.debug("payload index ensure skipped: %s", e)


def upsert_chunks(
    *,
    user_id: int,
    document_id: int,
    filename: str,
    chunk_rows: list[Any],
    vectors: list[list[float]],
) -> None:
    """Upsert points keyed by chunk.id. Replaces prior vectors for this document first."""
    from qdrant_client.http import models as qm

    if len(chunk_rows) != len(vectors):
        raise ValueError("chunk_rows and vectors length mismatch")
    if not chunk_rows:
        return

    ensure_collection(len(vectors[0]))
    _touch_payload_indexes()
    delete_by_document(document_id, user_id=user_id)

    c = _client()
    points = []
    for chunk, vec in zip(chunk_rows, vectors):
        points.append(
            qm.PointStruct(
                id=int(chunk.id),
                vector=vec,
                payload={
                    "chunk_id": int(chunk.id),
                    "document_id": int(document_id),
                    "user_id": int(user_id),
                    "filename": filename or "",
                    "heading": getattr(chunk, "heading", None) or "",
                    "page": getattr(chunk, "page", None),
                    "kind": getattr(chunk, "kind", None) or "text",
                },
            )
        )
    # Batched upsert for large docs
    batch = 64
    for i in range(0, len(points), batch):
        c.upsert(collection_name=settings.qdrant_collection, points=points[i : i + batch])


def delete_by_document(document_id: int, *, user_id: int) -> None:
    from qdrant_client.http import models as qm

    try:
        c = _client()
        names = {col.name for col in c.get_collections().collections}
        if settings.qdrant_collection not in names:
            return
        c.delete(
            collection_name=settings.qdrant_collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="document_id",
                            match=qm.MatchValue(value=int(document_id)),
                        ),
                        qm.FieldCondition(
                            key="user_id",
                            match=qm.MatchValue(value=int(user_id)),
                        ),
                    ]
                )
            ),
        )
    except Exception as e:
        logger.warning(
            "Qdrant delete_by_document(%s, user_id=%s) failed: %s",
            document_id,
            user_id,
            e,
        )


def search(
    query_vector: list[float],
    *,
    user_id: int,
    top_k: int | None = None,
    document_ids: list[int] | None = None,
) -> list[VectorHit]:
    from qdrant_client.http import models as qm

    k = top_k if top_k is not None else settings.rag_vector_top_k
    c = _client()
    names = {col.name for col in c.get_collections().collections}
    if settings.qdrant_collection not in names:
        return []

    existing = _collection_vector_size(c, settings.qdrant_collection)
    qdim = len(query_vector)
    if existing is not None and existing != qdim:
        logger.warning(
            "vector dim mismatch (collection=%s query=%s); skip vector search "
            "(re-ingest after embedding model change)",
            existing,
            qdim,
        )
        return []

    must: list[qm.FieldCondition] = [
        qm.FieldCondition(
            key="user_id",
            match=qm.MatchValue(value=int(user_id)),
        )
    ]
    if document_ids:
        must.append(
            qm.FieldCondition(
                key="document_id",
                match=qm.MatchAny(any=[int(i) for i in document_ids]),
            )
        )

    res = c.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        limit=k,
        query_filter=qm.Filter(must=must),
    )
    hits: list[VectorHit] = []
    for p in res.points:
        payload = p.payload or {}
        cid = payload.get("chunk_id")
        did = payload.get("document_id")
        if cid is None:
            continue
        hits.append(
            VectorHit(
                chunk_id=int(cid),
                document_id=int(did) if did is not None else 0,
                score=float(p.score or 0.0),
            )
        )
    return hits
