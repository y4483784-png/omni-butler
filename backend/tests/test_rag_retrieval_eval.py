"""RAG retrieval eval smoke + threshold."""

from __future__ import annotations

from app.eval.rag_retrieval import load_cases, run_rag_retrieval_eval


def test_rag_retrieval_eval_meets_floor():
    cases = load_cases()
    assert len(cases) >= 500
    report = run_rag_retrieval_eval(cases, use_zhipu_rerank=False)
    assert report.n == len(cases)
    # Keyword path on seeded corpus should recall most gold docs
    assert report.metrics["recall_at_k"] >= 0.75, report.format_text()
    assert report.metrics["mrr"] >= 0.6, report.format_text()
