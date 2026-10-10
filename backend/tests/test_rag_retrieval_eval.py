"""RAG retrieval eval smoke + threshold."""

from __future__ import annotations

from app.eval.rag_retrieval import load_cases, run_rag_retrieval_eval

_MIN_RECALL = 0.93
_MIN_MRR = 0.88


def test_rag_retrieval_eval_meets_floor():
    cases = load_cases()
    assert cases
    report = run_rag_retrieval_eval(cases, use_zhipu_rerank=False)
    assert report.n == len(cases)
    # 2026-09-17 reviewed-set baseline: recall=.9888, MRR=.9322.
    # Five-point regression budget keeps the gate meaningful but stable.
    assert report.metrics["recall_at_k"] >= _MIN_RECALL, report.format_text()
    assert report.metrics["mrr"] >= _MIN_MRR, report.format_text()
