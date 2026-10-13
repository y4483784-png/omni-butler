"""RAG retrieval eval smoke + threshold."""

from __future__ import annotations

from app.core.embeddings import EmbeddingError
from app.eval.gates import GATES
from app.eval.rag_retrieval import classify_rag_run, load_cases, run_rag_retrieval_eval
from app.rag.rerank import rerank_hits
from app.rag.expand import ExpandedHit
from app.rag.retrieval import _vector_ranked


def _rag_floor(metric: str) -> float:
    for gate in GATES[("rag", "smoke")]:
        if gate.metric == metric and gate.min is not None:
            return gate.min
    raise AssertionError(metric)


def test_rag_retrieval_eval_meets_floor():
    cases = load_cases()
    assert cases
    report = run_rag_retrieval_eval(cases, use_zhipu_rerank=False)
    assert report.n == len(cases)
    # 2026-09-17 keyword baseline: recall=.9888, MRR=.9322.
    # hard_negative slice on that path: recall=1.0, MRR=.875 (n=40).
    assert report.metrics["recall_at_k"] >= _rag_floor("metrics.recall_at_k"), report.format_text()
    assert report.metrics["mrr"] >= _rag_floor("metrics.mrr"), report.format_text()
    hard = report.metrics["slices"]["hard_negative"]
    assert hard["n"] == 40
    assert hard["recall_at_k"] >= _rag_floor("metrics.slices.hard_negative.recall_at_k"), report.format_text()
    assert hard["mrr"] >= _rag_floor("metrics.slices.hard_negative.mrr"), report.format_text()
    # Keyword smoke is not the production path.
    assert report.metrics["mode"] == "keyword_paragraph"
    assert report.metrics["degraded"] is True


def test_production_hybrid_requires_vector_and_rerank_success():
    healthy = classify_rag_run(
        production=True,
        zhipu_rerank=True,
        ingest_warnings=[],
        call_diags=[{"vector_error": "", "rerank_fallback": False}],
    )
    assert healthy == {"mode": "production_hybrid", "degraded": False, "degraded_reasons": []}

    vector_down = classify_rag_run(
        production=True,
        zhipu_rerank=True,
        ingest_warnings=[],
        call_diags=[{"vector_error": "embedding:timeout", "rerank_fallback": False}],
    )
    assert vector_down["degraded"] is True
    assert vector_down["mode"] != "production_hybrid"
    assert any(r.startswith("vector:") for r in vector_down["degraded_reasons"])

    ingest_skip = classify_rag_run(
        production=True,
        zhipu_rerank=False,
        ingest_warnings=["policy.md: 向量化失败：boom"],
        call_diags=[],
    )
    assert ingest_skip["degraded"] is True
    assert ingest_skip["mode"] == "production_degraded"

    rerank_fallback = classify_rag_run(
        production=True,
        zhipu_rerank=True,
        ingest_warnings=[],
        call_diags=[{"vector_error": "", "rerank_fallback": True}],
    )
    assert rerank_fallback["degraded"] is True
    assert "rerank:heuristic_fallback" in rerank_fallback["degraded_reasons"]
    # Heuristic rerank that was requested as the arm is not a fallback.
    chosen = classify_rag_run(
        production=True,
        zhipu_rerank=False,
        ingest_warnings=[],
        call_diags=[{"vector_error": "", "rerank_fallback": True}],
    )
    assert chosen["degraded"] is False
    assert chosen["mode"] == "production_hybrid"


def test_vector_ranked_reports_embedding_failure(monkeypatch):
    monkeypatch.setattr("app.rag.retrieval.settings.rag_hybrid_enabled", True)
    monkeypatch.setattr("app.rag.retrieval.settings.llm_api_key", "test-key")
    monkeypatch.setattr("app.rag.retrieval.vector_store.is_available", lambda: True)

    def boom(_query: str):
        raise EmbeddingError("429 rate limit")

    monkeypatch.setattr("app.rag.retrieval.embed_query", boom)
    ids, err = _vector_ranked("年假", user_id=1, limit=3)
    assert ids == []
    assert err.startswith("embedding:")


def test_rerank_fallback_is_visible_on_diag():
    hits = [
        ExpandedHit(
            chunk_id=1,
            document_id=1,
            filename="差旅.md",
            content="差旅报销流程",
            score=0.2,
            seed_content="差旅报销流程",
        )
    ]

    class Boom:
        def rerank(self, query, hits, *, top_n):  # noqa: ANN001
            raise RuntimeError("rerank timeout")

    diag: dict = {}
    out = rerank_hits("差旅报销", hits, top_n=1, reranker=Boom(), diag=diag)
    assert out
    assert diag["rerank_fallback"] is True
    assert "timeout" in diag["rerank_error"]
