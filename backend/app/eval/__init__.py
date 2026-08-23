"""Agent evaluation: routing metrics, datasets, and reports."""

from app.eval.metrics import (
    binary_classification_report,
    confusion_matrix,
    exact_match_accuracy,
    f1_score,
    hamming_loss,
    macro_f1,
    micro_f1,
    multilabel_report,
    precision_score,
    recall_score,
)
from app.eval.tool_routing import EvalReport, load_cases, run_tool_routing_eval

__all__ = [
    "EvalReport",
    "binary_classification_report",
    "confusion_matrix",
    "exact_match_accuracy",
    "f1_score",
    "hamming_loss",
    "load_cases",
    "macro_f1",
    "micro_f1",
    "multilabel_report",
    "precision_score",
    "recall_score",
    "run_tool_routing_eval",
]
