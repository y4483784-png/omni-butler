"""Agent evaluation package.

Keep this module light: do not import tool_routing / db here — eval CLI must
bootstrap DATABASE_URL before those modules bind SQLAlchemy engines.
"""

from __future__ import annotations

__all__ = [
    "EvalReport",
    "load_cases",
    "run_tool_routing_eval",
    "binary_classification_report",
    "confusion_matrix",
    "exact_match_accuracy",
    "f1_score",
    "hamming_loss",
    "macro_f1",
    "micro_f1",
    "multilabel_report",
    "precision_score",
    "recall_score",
]


def __getattr__(name: str):
    if name in {
        "EvalReport",
        "load_cases",
        "run_tool_routing_eval",
    }:
        from app.eval import tool_routing as tr

        return getattr(tr, name)
    if name in {
        "binary_classification_report",
        "confusion_matrix",
        "exact_match_accuracy",
        "f1_score",
        "hamming_loss",
        "macro_f1",
        "micro_f1",
        "multilabel_report",
        "precision_score",
        "recall_score",
    }:
        from app.eval import metrics as m

        return getattr(m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
