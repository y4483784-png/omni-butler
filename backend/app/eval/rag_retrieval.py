"""RAG retrieval eval: seed mini corpus + Precision@k / Recall@k / MRR."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.eval.metrics import retrieval_report
from app.eval.tenant import activate_eval_tenant
from app.models.models import Chunk, Document
from app.rag.retrieval import retrieve

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "rag_retrieval.jsonl"
EVAL_USER_ID = 92001
EVAL_EXTERNAL_ID = "rag_eval_92001"


def _activate(db: Session) -> None:
    activate_eval_tenant(
        db,
        EVAL_USER_ID,
        external_id=EVAL_EXTERNAL_ID,
        name="RAG Eval Fixture",
    )

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "data" / "eval" / "corpus" / "rag"


def load_eval_corpus(path: Path = CORPUS_ROOT) -> dict[str, dict[str, Any]]:
    """Load one stable document per Markdown file.

    Blank lines delimit chunks.  The file stem is the durable gold key, so
    changing chunking does not silently invalidate document-level labels.
    """
    corpus: dict[str, dict[str, Any]] = {}
    for source in sorted(path.glob("*.md")):
        if source.name.casefold() == "readme.md":
            continue
        chunks = [
            block.strip()
            for block in source.read_text(encoding="utf-8").split("\n\n")
            if block.strip()
        ]
        if chunks:
            corpus[source.stem] = {"filename": source.name, "chunks": chunks}
    if not corpus:
        raise RuntimeError(f"RAG eval corpus is empty: {path}")
    return corpus


_CORPUS = load_eval_corpus()


@dataclass
class RagEvalCase:
    id: str
    query: str
    gold_doc_keys: list[str]
    tags: list[str] = field(default_factory=list)
    difficulty: str = "medium"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RagEvalCase":
        return cls(
            id=str(raw["id"]),
            query=str(raw.get("query") or ""),
            gold_doc_keys=list(raw.get("gold_doc_keys") or []),
            tags=list(raw.get("tags") or []),
            difficulty=str(raw.get("difficulty") or "medium"),
        )


def load_cases(path: str | Path | None = None) -> list[RagEvalCase]:
    p = Path(path) if path else DEFAULT_DATASET
    out: list[RagEvalCase] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(RagEvalCase.from_dict(json.loads(line)))
    return out


def clear_eval_corpus(db: Session) -> None:
    _activate(db)
    docs = db.query(Document).filter(Document.user_id == EVAL_USER_ID).all()
    for d in docs:
        db.query(Chunk).filter(Chunk.document_id == d.id).delete()
        db.delete(d)
    db.commit()


def seed_eval_corpus(db: Session) -> dict[str, int]:
    """Insert mini corpus; return map doc_key -> document_id."""
    _activate(db)
    clear_eval_corpus(db)
    key_to_id: dict[str, int] = {}
    for key, spec in _CORPUS.items():
        doc = Document(
            user_id=EVAL_USER_ID,
            filename=spec["filename"],
            status="ready",
            stored_path="",
            char_count=sum(len(c) for c in spec["chunks"]),
        )
        db.add(doc)
        db.flush()
        for i, text in enumerate(spec["chunks"]):
            db.add(
                Chunk(
                    document_id=doc.id,
                    chunk_index=i,
                    content=text,
                    kind="text",
                    heading=key,
                )
            )
        key_to_id[key] = doc.id
    db.commit()
    return key_to_id


_VECTOR_INDEX_FAILURES = ("跳过向量化", "向量化失败", "向量写入失败")


def vector_index_failed(warning: str) -> bool:
    text = warning or ""
    return any(marker in text for marker in _VECTOR_INDEX_FAILURES)


def classify_rag_run(
    *,
    production: bool,
    zhipu_rerank: bool,
    ingest_warnings: list[str],
    call_diags: list[dict],
) -> dict[str, Any]:
    """Decide whether a run may be labeled ``production_hybrid``.

    Keyword smoke is intentionally degraded. A production run that lost embeddings,
    vector search, or the requested reranker mid-flight is also degraded, and must
    not keep ``mode=production_hybrid`` with ``degraded=false``.
    """
    reasons: list[str] = []
    if production:
        for warning in ingest_warnings:
            if vector_index_failed(warning):
                reasons.append(f"ingest:{(warning or '')[:180]}")
        for diag in call_diags:
            err = str((diag or {}).get("vector_error") or "")
            if err:
                reasons.append(f"vector:{err[:180]}")
            if zhipu_rerank and (diag or {}).get("rerank_fallback"):
                reasons.append("rerank:heuristic_fallback")
    seen: set[str] = set()
    unique: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    if not production:
        mode, degraded = "keyword_paragraph", True
    elif unique:
        mode, degraded = "production_degraded", True
    else:
        mode, degraded = "production_hybrid", False
    return {"mode": mode, "degraded": degraded, "degraded_reasons": unique[:12]}


def seed_eval_corpus_production(db: Session) -> tuple[dict[str, int], list[str]]:
    """Ingest the corpus through the production path (blob store -> parse -> chunk -> embed -> Qdrant).

    Replaces the paragraph-split shortcut so chunking (size/overlap), headings and
    vectors are exactly what users get. Requires MinIO + Qdrant (Compose ``services``).
    """
    from app.rag import vector_store
    from app.rag.ingestion import ingest_document
    from app.storage import get_blob_store, make_object_key

    _activate(db)
    for d in db.query(Document).filter(Document.user_id == EVAL_USER_ID).all():
        try:
            vector_store.delete_by_document(d.id, user_id=EVAL_USER_ID)
        except Exception:
            pass
        db.query(Chunk).filter(Chunk.document_id == d.id).delete()
        db.delete(d)
    db.commit()

    store = get_blob_store()
    key_to_id: dict[str, int] = {}
    ingest_warnings: list[str] = []
    for key, spec in _CORPUS.items():
        path = CORPUS_ROOT / spec["filename"]
        data = path.read_bytes()
        object_key = make_object_key(user_id=EVAL_USER_ID, filename=spec["filename"])
        store.put(object_key, data)
        doc = Document(
            user_id=EVAL_USER_ID,
            filename=spec["filename"],
            status="pending",
            stage="pending",
            stored_path=object_key,
            char_count=len(data),
        )
        db.add(doc)
        db.flush()
        ingest_document(db, doc)
        db.refresh(doc)
        if doc.status != "ready":
            raise RuntimeError(f"rag eval ingest failed for {spec['filename']}: {doc.error or doc.status}")
        if vector_index_failed(doc.warning or ""):
            ingest_warnings.append(f"{spec['filename']}: {doc.warning}")
        key_to_id[key] = doc.id
    db.commit()
    return key_to_id, ingest_warnings


@dataclass
class RagEvalReport:
    n: int
    metrics: dict[str, float | int]
    failures: list[dict[str, Any]] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [
            f"=== Omni-Butler RAG Retrieval Eval (n={self.n}) ===",
            f"  precision@k  {self.metrics.get('precision_at_k', 0):.2%}",
            f"  recall@k     {self.metrics.get('recall_at_k', 0):.2%}",
            f"  hit@k        {self.metrics.get('hit_at_k', 0):.2%}",
            f"  MRR          {self.metrics.get('mrr', 0):.4f}",
            f"  scored rows  {self.metrics.get('n_scored', 0)}",
            f"  mode         {self.metrics.get('mode', '')}  degraded={self.metrics.get('degraded')}",
        ]
        for name, sl in (self.metrics.get("slices") or {}).items():
            lines.append(
                f"  slice {name:<14} n={sl['n']:<4} hit={sl['hit_at_k']:.3f} "
                f"recall={sl['recall_at_k']:.3f} mrr={sl['mrr']:.3f}"
            )
        if self.failures:
            lines.append(f"Failures ({len(self.failures)}):")
            for f in self.failures[:10]:
                lines.append(
                    f"  - {f['id']}: gold={f['gold']} pred={f['pred']}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "metrics": self.metrics, "failures": self.failures}


def run_rag_retrieval_eval(
    cases: list[RagEvalCase] | None = None,
    *,
    dataset_path: str | Path | None = None,
    k: int | None = None,
    use_zhipu_rerank: bool = False,
    production: bool = False,
) -> RagEvalReport:
    """Retrieval eval on the seeded corpus.

    ``production=False``: deterministic keyword + expand + heuristic rerank on a
    paragraph-split corpus (CI smoke). ``production=True``: real ``ingest_document``
    chunking + embeddings + hybrid (keyword + Qdrant, RRF) retrieval.
    """
    items = cases if cases is not None else load_cases(dataset_path)
    top_k = int(k if k is not None else settings.rag_top_k)

    prev_hybrid = settings.rag_hybrid_enabled
    prev_provider = settings.rag_rerank_provider
    prev_rerank = settings.rag_rerank_enabled
    settings.rag_hybrid_enabled = bool(production)
    settings.rag_rerank_enabled = True
    settings.rag_rerank_provider = "zhipu" if use_zhipu_rerank else "none"

    init_db()
    db = SessionLocal()
    try:
        _activate(db)
        ingest_warnings: list[str] = []
        if production:
            key_to_id, ingest_warnings = seed_eval_corpus_production(db)
        else:
            key_to_id = seed_eval_corpus(db)
        gold_sets: list[set[str]] = []
        pred_ranked: list[list[str]] = []
        failures: list[dict[str, Any]] = []
        call_diags: list[dict[str, Any]] = []

        for case in items:
            gold_ids = {str(key_to_id[k]) for k in case.gold_doc_keys if k in key_to_id}
            diag: dict[str, Any] = {}
            hits = retrieve(db, case.query, user_id=EVAL_USER_ID, top_k=top_k, diag=diag)
            call_diags.append(diag)
            ranked_docs: list[str] = []
            seen: set[str] = set()
            for h in hits:
                did = str(h.document_id)
                if did not in seen:
                    seen.add(did)
                    ranked_docs.append(did)
            gold_sets.append(gold_ids)
            pred_ranked.append(ranked_docs)
            if not gold_ids.intersection(ranked_docs[:top_k]):
                failures.append(
                    {
                        "id": case.id,
                        "query": case.query,
                        "gold": sorted(gold_ids),
                        "pred": ranked_docs[:top_k],
                        "difficulty": case.difficulty,
                        "tags": case.tags,
                    }
                )

        metrics = retrieval_report(gold_sets, pred_ranked, k=top_k)
        # Slices that matter more than the saturated overall mean.
        slices: dict[str, dict[str, float | int]] = {}
        for tag in ("hard_negative", "multi_doc", "single_doc"):
            idx = [i for i, c in enumerate(items) if tag in (c.tags or [])]
            if idx:
                rep = retrieval_report(
                    [gold_sets[i] for i in idx], [pred_ranked[i] for i in idx], k=top_k
                )
                slices[tag] = {
                    "n": len(idx),
                    "hit_at_k": round(float(rep.get("hit_at_k", 0)), 4),
                    "recall_at_k": round(float(rep.get("recall_at_k", 0)), 4),
                    "mrr": round(float(rep.get("mrr", 0)), 4),
                }
        # Precision@k is bounded by 1/k for single-doc gold; keep it out of gates/key metrics.
        path = classify_rag_run(
            production=production,
            zhipu_rerank=use_zhipu_rerank,
            ingest_warnings=ingest_warnings,
            call_diags=call_diags,
        )
        metrics = {
            **metrics,
            **path,
            "hybrid_enabled": bool(settings.rag_hybrid_enabled),
            "slices": slices,
            "precision_at_k_note": "bounded by |gold|/k; informational",
        }
        return RagEvalReport(n=len(items), metrics=metrics, failures=failures)
    except Exception:
        db.rollback()
        raise
    finally:
        try:
            clear_eval_corpus(db)
        except Exception:
            db.rollback()
        db.close()
        settings.rag_hybrid_enabled = prev_hybrid
        settings.rag_rerank_provider = prev_provider
        settings.rag_rerank_enabled = prev_rerank
