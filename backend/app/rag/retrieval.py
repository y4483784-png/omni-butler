"""Hybrid retrieval: SQLite keyword + Qdrant vectors with RRF, expand, rerank."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.embeddings import EmbeddingError, embed_query
from app.models.models import Chunk, Document
from app.rag import vector_store
from app.rag.expand import ExpandSeed, expand_hits
from app.rag.rerank import rerank_hits

logger = logging.getLogger(__name__)

_RRF_K = 60
_SEARCH_QUERY_MAX = 500


@dataclass
class RetrievedChunk:
    index: int  # 1-based citation index
    chunk_id: int
    document_id: int
    filename: str
    content: str
    score: float
    heading: str = ""
    page: int | None = None
    kind: str = "text"

    @property
    def snippet(self) -> str:
        prefix_parts: list[str] = []
        if self.heading:
            prefix_parts.append(self.heading)
        if self.page is not None and f"第 {self.page}" not in (self.heading or ""):
            prefix_parts.append(f"p.{self.page}")
        prefix = (" · ".join(prefix_parts) + "｜") if prefix_parts else ""
        # Prefer seed-only text for hover snippet when expand markers present
        raw = self.content or ""
        if raw.startswith("[命中]"):
            seed = raw.split("\n---\n", 1)[0].replace("[命中]", "", 1).strip()
            s = seed.replace("\n", " ")
        else:
            s = raw.strip().replace("\n", " ")
        body = s if len(s) <= 180 else s[:177] + "…"
        full = prefix + body
        return full if len(full) <= 220 else full[:217] + "…"


_LATIN_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def build_search_query(
    current: str,
    history: list[dict],
    *,
    max_prior_user: int = 2,
    max_len: int = _SEARCH_QUERY_MAX,
) -> str:
    """Current user message + up to N prior user turns (deduped), for retrieval only."""
    cur = (current or "").strip()
    parts: list[str] = []
    prior = [m.get("content", "").strip() for m in history if m.get("role") == "user"]
    for text in prior[-max_prior_user:]:
        if not text or text == cur or text in parts:
            continue
        parts.append(text)
    parts.append(cur)
    joined = "\n".join(p for p in parts if p)
    if len(joined) > max_len:
        joined = joined[-max_len:]
    return joined or cur


def _tokens(q: str) -> list[str]:
    """Keyword tokens for retrieval.

    Latin/digits: whole words. CJK: overlapping 2-/3-grams so phrases like
    「分析神经网络基础」still match chunks containing「神经网络」.
    """
    stop = {
        "的", "了", "是", "在", "和", "与", "或", "我", "你", "他", "她", "它", "吗", "呢", "啊",
        "什么", "哪些", "一下", "这个", "那个", "一个", "没有", "可以", "如何", "怎么",
    }
    raw: list[str] = []
    text = q or ""

    for m in _LATIN_RE.finditer(text):
        t = m.group(0).lower()
        if t not in stop and len(t) >= 1:
            raw.append(t)

    for m in _CJK_RE.finditer(text):
        s = m.group(0)
        if len(s) <= 2:
            if s not in stop:
                raw.append(s)
            continue
        for n in (2, 3):
            if len(s) < n:
                continue
            for i in range(len(s) - n + 1):
                gram = s[i : i + n]
                if gram not in stop:
                    raw.append(gram)

    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:48]


def rrf_fuse(
    ranked_lists: list[list[int]],
    *,
    k: int = _RRF_K,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion over lists of chunk_ids (best-first)."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, cid in enumerate(ranked, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _keyword_ranked(
    db: Session,
    query: str,
    user_id: int,
    limit: int,
    document_ids: list[int] | None = None,
) -> list[tuple[float, Chunk, Document]]:
    toks = _tokens(query)
    if not toks:
        return []

    q = (
        db.query(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .filter(Document.user_id == user_id, Document.status == "ready")
    )
    if document_ids:
        q = q.filter(Document.id.in_(document_ids))
    rows = q.all()
    if not rows:
        return []

    scored: list[tuple[float, Chunk, Document]] = []
    for chunk, doc in rows:
        text = (chunk.content or "").lower()
        fn = (doc.filename or "").lower()
        score = 0.0
        fn_hits = 0
        for t in toks:
            if t in fn:
                fn_hits += 1
                score += 12.0
            if t in text:
                score += min(text.count(t), 2) * (1.0 + (0.5 if len(t) >= 2 else 0.0))
        if fn_hits >= 2:
            score += 20.0
        if fn.endswith((".csv", ".xlsx")) and re.search(r"[a-z_]{3,}", text) and "," in text:
            score += 5.0
        if score > 0:
            scored.append((score, chunk, doc))

    scored.sort(key=lambda x: x[0], reverse=True)

    top = scored[:limit]
    top_ids = {c.id for _, c, _ in top}
    promoted: list[tuple[float, Chunk, Document]] = []
    for score, chunk, doc in scored:
        if chunk.id in top_ids:
            continue
        fn = (doc.filename or "").lower()
        if sum(1 for t in toks if t in fn) >= 2 and not any(d.id == doc.id for _, _, d in top):
            promoted.append((score + 50.0, chunk, doc))
            break
    if promoted:
        scored = promoted + [x for x in scored if x[1].id != promoted[0][1].id]
        scored.sort(key=lambda x: x[0], reverse=True)

    return scored[:limit]


def _vector_ranked(
    query: str,
    user_id: int,
    limit: int,
    document_ids: list[int] | None = None,
) -> list[int]:
    if not settings.rag_hybrid_enabled or not settings.llm_api_key:
        return []
    if not vector_store.is_available():
        return []
    try:
        qv = embed_query(query)
        hits = vector_store.search(
            qv, user_id=user_id, top_k=limit, document_ids=document_ids
        )
        return [h.chunk_id for h in hits]
    except EmbeddingError as e:
        logger.info("vector retrieve skipped: %s", e)
        return []
    except Exception as e:
        logger.warning("vector retrieve failed: %s", e)
        return []


def retrieve(
    db: Session,
    query: str,
    user_id: int = 1,
    top_k: int | None = None,
    document_ids: list[int] | None = None,
) -> list[RetrievedChunk]:
    k = top_k if top_k is not None else settings.rag_top_k
    candidate_k = max(k, int(settings.rag_candidate_k or k))
    vec_n = max(settings.rag_vector_top_k, candidate_k)
    ids = list(document_ids) if document_ids else None

    kw = _keyword_ranked(db, query, user_id, limit=candidate_k, document_ids=ids)
    kw_ids = [c.id for _, c, _ in kw]
    vec_ids = _vector_ranked(query, user_id, limit=vec_n, document_ids=ids)

    if not kw_ids and not vec_ids:
        return []

    if kw_ids and vec_ids:
        fused = rrf_fuse([kw_ids, vec_ids])
        ordered_ids = [cid for cid, _ in fused[:candidate_k]]
        score_map = {cid: sc for cid, sc in fused}
    elif vec_ids:
        ordered_ids = vec_ids[:candidate_k]
        score_map = {cid: float(len(ordered_ids) - i) for i, cid in enumerate(ordered_ids)}
    else:
        ordered_ids = kw_ids[:candidate_k]
        score_map = {c.id: sc for sc, c, _ in kw if c.id in set(ordered_ids)}

    rows = (
        db.query(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .filter(
            Chunk.id.in_(ordered_ids),
            Document.status == "ready",
            Document.user_id == user_id,
        )
        .all()
    )
    by_id = {c.id: (c, d) for c, d in rows}

    seeds = []
    for cid in ordered_ids:
        pair = by_id.get(cid)
        if not pair:
            continue
        chunk, doc = pair
        seeds.append(
            ExpandSeed(
                chunk_id=chunk.id,
                document_id=doc.id,
                filename=doc.filename or "",
                content=chunk.content or "",
                score=float(score_map.get(cid, 0.0)),
                chunk_index=int(getattr(chunk, "chunk_index", 0) or 0),
                heading=getattr(chunk, "heading", None) or "",
                page=getattr(chunk, "page", None),
                kind=getattr(chunk, "kind", None) or "text",
            )
        )

    window = int(settings.rag_expand_window or 0)
    max_chars = int(settings.rag_expand_max_chars or 2400)
    if window > 0:
        expanded = expand_hits(db, seeds, window=window, max_chars=max_chars)
    else:
        from app.rag.expand import ExpandedHit

        expanded = [
            ExpandedHit(
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                filename=s.filename,
                content=s.content,
                score=s.score,
                heading=s.heading,
                page=s.page,
                kind=s.kind,
                seed_content=s.content,
            )
            for s in seeds
        ]

    ranked = rerank_hits(query, expanded, top_n=k)

    out: list[RetrievedChunk] = []
    for i, item in enumerate(ranked, start=1):
        h = item.hit
        out.append(
            RetrievedChunk(
                index=i,
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                filename=h.filename,
                content=h.content,
                score=float(item.score),
                heading=h.heading,
                page=h.page,
                kind=h.kind,
            )
        )
    return out
