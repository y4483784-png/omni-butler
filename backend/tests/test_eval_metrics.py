"""Unit tests for eval metrics primitives."""

from __future__ import annotations

from app.eval.metrics import (
    binary_classification_report,
    exact_match_accuracy,
    hamming_loss,
    multilabel_report,
    recall_at_k,
    retrieval_report,
)


def test_binary_classification_perfect():
    rep = binary_classification_report([True, False, True], [True, False, True])
    assert rep.accuracy == 1.0
    assert rep.f1 == 1.0


def test_multilabel_micro_macro():
    gold = [{"kb"}, {"web"}, {"kb", "web"}]
    pred = [{"kb"}, {"web"}, {"kb"}]
    rep = multilabel_report(gold, pred, labels=["kb", "web", "calendar", "sandbox"])
    assert rep.exact_match == 2 / 3
    assert rep.micro["f1"] > 0.8
    assert rep.per_label["kb"]["tp"] == 2


def test_hamming_loss_zero_when_equal():
    gold = [{"kb"}, set()]
    pred = [{"kb"}, set()]
    assert hamming_loss(gold, pred, labels=["kb", "web"]) == 0.0


def test_recall_at_k():
    assert recall_at_k(["kb", "web"], ["kb", "calendar", "web"], 1) == 0.5
    assert recall_at_k(["kb", "web"], ["kb", "web"], 2) == 1.0


def test_retrieval_mrr():
    out = retrieval_report(
        [{"d1", "d2"}],
        [["d3", "d1", "d2"]],
        k=3,
    )
    assert out["mrr"] == 0.5
    assert out["recall_at_k"] == 1.0


def test_exact_match_accuracy():
    assert exact_match_accuracy([{"a"}, {"b"}], [{"a"}, {"b"}]) == 1.0
    assert exact_match_accuracy([{"a"}], [{"a", "b"}]) == 0.0
