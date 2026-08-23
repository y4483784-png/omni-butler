"""Unit tests for adjacency expand + heuristic / Zhipu rerank soft-fail."""

from __future__ import annotations

from app.core.db import SessionLocal, init_db
from app.models.models import Chunk, Document
from app.rag.expand import ExpandSeed, expand_hits
from app.rag.rerank import HeuristicReranker, rerank_hits
from app.rag.expand import ExpandedHit


def _seed_three_chunks() -> tuple[int, list[int]]:
    init_db()
    db = SessionLocal()
    try:
        doc = Document(
            user_id=9101,
            filename="policy.md",
            status="ready",
            stored_path="",
        )
        db.add(doc)
        db.flush()
        ids: list[int] = []
        texts = ["前文：差旅总则", "命中：住宿标准每天500元", "后文：餐补标准"]
        for i, text in enumerate(texts):
            c = Chunk(
                document_id=doc.id,
                chunk_index=i,
                content=text,
                kind="text",
                heading=f"h{i}",
            )
            db.add(c)
            db.flush()
            ids.append(c.id)
        db.commit()
        return doc.id, ids
    finally:
        db.close()


def test_expand_includes_neighbors():
    doc_id, ids = _seed_three_chunks()
    db = SessionLocal()
    try:
        mid = (
            db.query(Chunk).filter(Chunk.id == ids[1]).one()
        )
        seeds = [
            ExpandSeed(
                chunk_id=mid.id,
                document_id=doc_id,
                filename="policy.md",
                content=mid.content or "",
                score=1.0,
                chunk_index=mid.chunk_index,
                heading=mid.heading or "",
                kind="text",
            )
        ]
        expanded = expand_hits(db, seeds, window=1, max_chars=2400)
        assert len(expanded) == 1
        text = expanded[0].content
        assert "住宿标准" in text
        assert "差旅总则" in text
        assert "餐补标准" in text
        assert expanded[0].chunk_id == mid.id  # citation stays on seed
    finally:
        db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
        db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
        db.close()


def test_heuristic_rerank_orders_by_coverage():
    hits = [
        ExpandedHit(
            chunk_id=1,
            document_id=1,
            filename="a.md",
            content="无关内容天气很好",
            score=0.9,
            seed_content="无关内容天气很好",
        ),
        ExpandedHit(
            chunk_id=2,
            document_id=1,
            filename="差旅制度.md",
            content="差旅住宿标准每天限额",
            score=0.1,
            seed_content="差旅住宿标准每天限额",
        ),
    ]
    ranked = HeuristicReranker().rerank("差旅住宿标准", hits, top_n=2)
    assert ranked[0].hit.chunk_id == 2


def test_rerank_hits_soft_fails_to_heuristic(monkeypatch):
    hits = [
        ExpandedHit(
            chunk_id=1,
            document_id=1,
            filename="x.md",
            content="alpha",
            score=0.05,
            seed_content="alpha",
        ),
        ExpandedHit(
            chunk_id=2,
            document_id=1,
            filename="差旅.md",
            content="差旅报销流程",
            score=0.05,
            seed_content="差旅报销流程",
        ),
    ]

    class Boom:
        def rerank(self, query, hits, *, top_n):  # noqa: ANN001
            raise RuntimeError("api down")

    out = rerank_hits("差旅报销", hits, top_n=1, reranker=Boom())
    assert len(out) == 1
    assert out[0].hit.chunk_id == 2


def test_expand_skips_schema_dump():
    init_db()
    db = SessionLocal()
    doc_id = None
    try:
        doc = Document(user_id=9102, filename="t.xlsx", status="ready", stored_path="")
        db.add(doc)
        db.flush()
        doc_id = doc.id
        a = Chunk(document_id=doc.id, chunk_index=0, content="schema-big-" + ("x" * 2000), kind="schema")
        b = Chunk(document_id=doc.id, chunk_index=1, content="命中行数据", kind="table")
        db.add_all([a, b])
        db.commit()
        db.refresh(b)
        seeds = [
            ExpandSeed(
                chunk_id=b.id,
                document_id=doc.id,
                filename="t.xlsx",
                content=b.content or "",
                score=1.0,
                chunk_index=1,
                kind="table",
            )
        ]
        expanded = expand_hits(db, seeds, window=1, max_chars=2400)
        assert "省略" in expanded[0].content or "schema" in expanded[0].content.lower()
        assert "xxxxx" not in expanded[0].content  # big dump skipped
    finally:
        if doc_id is not None:
            db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
            db.query(Document).filter(Document.id == doc_id).delete()
            db.commit()
        db.close()
