"""
Standard classification / multi-label metrics for agent routing eval.

References (industry practice):
- Tool selection as multi-label: precision, recall, F1, hamming loss, exact-match accuracy
  (ICLR ToolLLM / ACL Divide-Verify-Refine tool-selection tables)
- Intent routing: per-class + macro/micro P/R/F1 (LangSmith classification eval)
- Retrieval (optional layer): precision@k, recall@k, MRR — see retrieval_report()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def precision_score(tp: int, fp: int) -> float:
    return _safe_div(tp, tp + fp)


def recall_score(tp: int, fn: int) -> float:
    return _safe_div(tp, tp + fn)


def f1_score(precision: float, recall: float) -> float:
    return _safe_div(2 * precision * recall, precision + recall)


@dataclass
class BinaryReport:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def accuracy(self) -> float:
        n = self.tp + self.fp + self.tn + self.fn
        return _safe_div(self.tp + self.tn, n)

    @property
    def precision(self) -> float:
        return precision_score(self.tp, self.fp)

    @property
    def recall(self) -> float:
        return recall_score(self.tp, self.fn)

    @property
    def f1(self) -> float:
        return f1_score(self.precision, self.recall)

    def to_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


def binary_classification_report(
    y_true: Sequence[bool],
    y_pred: Sequence[bool],
) -> BinaryReport:
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred length mismatch")
    rep = BinaryReport()
    for t, p in zip(y_true, y_pred):
        if t and p:
            rep.tp += 1
        elif not t and p:
            rep.fp += 1
        elif not t and not p:
            rep.tn += 1
        else:
            rep.fn += 1
    return rep


def confusion_matrix(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
) -> dict[str, dict[str, int]]:
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred length mismatch")
    uniq = sorted(set(y_true) | set(y_pred) | set(labels or []))
    mat = {lab: {l: 0 for l in uniq} for lab in uniq}
    for t, p in zip(y_true, y_pred):
        mat[t][p] += 1
    return mat


def _per_label_counts(
    gold_sets: Sequence[set[str]],
    pred_sets: Sequence[set[str]],
    labels: Sequence[str],
) -> dict[str, dict[str, int]]:
    counts = {lab: {"tp": 0, "fp": 0, "fn": 0} for lab in labels}
    for g, p in zip(gold_sets, pred_sets):
        for lab in labels:
            in_g = lab in g
            in_p = lab in p
            if in_g and in_p:
                counts[lab]["tp"] += 1
            elif not in_g and in_p:
                counts[lab]["fp"] += 1
            elif in_g and not in_p:
                counts[lab]["fn"] += 1
    return counts


def exact_match_accuracy(gold_sets: Sequence[set[str]], pred_sets: Sequence[set[str]]) -> float:
    if len(gold_sets) != len(pred_sets):
        raise ValueError("length mismatch")
    if not gold_sets:
        return 1.0
    hits = sum(1 for g, p in zip(gold_sets, pred_sets) if g == p)
    return hits / len(gold_sets)


def hamming_loss(
    gold_sets: Sequence[set[str]],
    pred_sets: Sequence[set[str]],
    *,
    labels: Sequence[str],
) -> float:
    """Fraction of wrong labels (lower is better)."""
    if not labels or not gold_sets:
        return 0.0
    wrong = 0
    total = len(gold_sets) * len(labels)
    for g, p in zip(gold_sets, pred_sets):
        for lab in labels:
            if (lab in g) != (lab in p):
                wrong += 1
    return wrong / total


def micro_f1(
    gold_sets: Sequence[set[str]],
    pred_sets: Sequence[set[str]],
    *,
    labels: Sequence[str],
) -> dict[str, float]:
    counts = _per_label_counts(gold_sets, pred_sets, labels)
    tp = sum(c["tp"] for c in counts.values())
    fp = sum(c["fp"] for c in counts.values())
    fn = sum(c["fn"] for c in counts.values())
    p = precision_score(tp, fp)
    r = recall_score(tp, fn)
    return {"precision": p, "recall": r, "f1": f1_score(p, r)}


def macro_f1(
    gold_sets: Sequence[set[str]],
    pred_sets: Sequence[set[str]],
    *,
    labels: Sequence[str],
) -> dict[str, float]:
    counts = _per_label_counts(gold_sets, pred_sets, labels)
    ps: list[float] = []
    rs: list[float] = []
    fs: list[float] = []
    for lab in labels:
        c = counts[lab]
        p = precision_score(c["tp"], c["fp"])
        r = recall_score(c["tp"], c["fn"])
        ps.append(p)
        rs.append(r)
        fs.append(f1_score(p, r))
    n = len(labels) or 1
    return {
        "precision": sum(ps) / n,
        "recall": sum(rs) / n,
        "f1": sum(fs) / n,
    }


@dataclass
class MultilabelReport:
    labels: list[str]
    exact_match: float
    hamming: float
    micro: dict[str, float]
    macro: dict[str, float]
    per_label: dict[str, dict[str, float | int]] = field(default_factory=dict)
    n: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "exact_match_accuracy": round(self.exact_match, 4),
            "hamming_loss": round(self.hamming, 4),
            "micro": {k: round(v, 4) for k, v in self.micro.items()},
            "macro": {k: round(v, 4) for k, v in self.macro.items()},
            "per_label": self.per_label,
        }


def multilabel_report(
    gold_sets: Sequence[set[str]],
    pred_sets: Sequence[set[str]],
    *,
    labels: Sequence[str],
) -> MultilabelReport:
    lab_list = list(labels)
    counts = _per_label_counts(gold_sets, pred_sets, lab_list)
    per: dict[str, dict[str, float | int]] = {}
    for lab in lab_list:
        c = counts[lab]
        p = precision_score(c["tp"], c["fp"])
        r = recall_score(c["tp"], c["fn"])
        per[lab] = {
            "tp": c["tp"],
            "fp": c["fp"],
            "fn": c["fn"],
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1_score(p, r), 4),
        }
    return MultilabelReport(
        labels=lab_list,
        exact_match=exact_match_accuracy(gold_sets, pred_sets),
        hamming=hamming_loss(gold_sets, pred_sets, labels=lab_list),
        micro=micro_f1(gold_sets, pred_sets, labels=lab_list),
        macro=macro_f1(gold_sets, pred_sets, labels=lab_list),
        per_label=per,
        n=len(gold_sets),
    )


def recall_at_k(gold_ordered: Sequence[str], pred_ordered: Sequence[str], k: int) -> float:
    """Tool trajectory: fraction of gold tools appearing in first k predicted steps."""
    if not gold_ordered:
        return 1.0
    head = set(pred_ordered[:k])
    return sum(1 for t in gold_ordered if t in head) / len(gold_ordered)


def retrieval_report(
    gold_doc_ids: Sequence[set[str]],
    pred_ranked: Sequence[Sequence[str]],
    *,
    k: int = 3,
) -> dict[str, float]:
    """RAG layer: precision@k, recall@k, MRR (when gold doc ids are labeled)."""
    if not gold_doc_ids:
        return {"precision_at_k": 0.0, "recall_at_k": 0.0, "mrr": 0.0}
    p_sum = r_sum = mrr_sum = 0.0
    for gold, ranked in zip(gold_doc_ids, pred_ranked):
        top = ranked[:k]
        hits_in_top = len(set(top) & gold)
        p_sum += _safe_div(hits_in_top, k)
        r_sum += _safe_div(hits_in_top, len(gold))
        rr = 0.0
        for i, doc in enumerate(ranked, start=1):
            if doc in gold:
                rr = 1.0 / i
                break
        mrr_sum += rr
    n = len(gold_doc_ids)
    return {
        "precision_at_k": p_sum / n,
        "recall_at_k": r_sum / n,
        "mrr": mrr_sum / n,
    }
