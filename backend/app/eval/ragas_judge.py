from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.core.config import ZhipuService, settings, zhipu_endpoint

logger = logging.getLogger(__name__)

DEFAULT_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


@dataclass
class RagasOutcome:
    means: dict[str, float | None] = field(default_factory=dict)
    per_row: list[dict[str, float | None]] = field(default_factory=list)
    infra_error: bool = False


def build_ragas_llm_embeddings():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_openai import OpenAIEmbeddings

    from app.core.llm import Role, complete_text

    class _JudgeChat(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "deepseek-judge"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            payload: list[dict[str, str]] = []
            for message in messages:
                role = getattr(message, "type", None) or "user"
                if role == "human":
                    role = "user"
                elif role == "ai":
                    role = "assistant"
                elif role not in ("system", "user", "assistant"):
                    role = "user"
                content = getattr(message, "content", "")
                if not isinstance(content, str):
                    content = "" if content is None else str(content)
                payload.append({"role": role, "content": content})
            spoken = complete_text(payload, role=Role.JUDGE, raise_on_error=True)
            text = spoken.content if hasattr(spoken, "content") else str(spoken or "")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    llm = _JudgeChat()
    embedding = zhipu_endpoint(ZhipuService.EMBEDDING)
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=embedding.api_key,
        base_url=embedding.base_url,
        timeout=settings.llm_timeout,
        max_retries=1,
    )
    return llm, embeddings


def _resolve_metrics(metric_names: Sequence[str]):
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    mapping = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    out = []
    for name in metric_names:
        key = str(name).strip()
        if key not in mapping:
            raise ValueError(f"unknown ragas metric: {name}")
        out.append(mapping[key])
    return out


def _failure_is_infra(exc: BaseException) -> bool:
    from app.agents.router import router_failure_is_infra

    return router_failure_is_infra(exc)


def run_ragas_metrics(
    rows: list[dict[str, Any]],
    *,
    metrics: Sequence[str] | None = None,
) -> dict[str, float | None]:
    """Run selected ragas metrics; default is the full YZ quartet (aggregate only)."""
    return run_ragas_rows(rows, metrics=metrics).means


def run_ragas_rows(
    rows: list[dict[str, Any]],
    *,
    metrics: Sequence[str] | None = None,
) -> RagasOutcome:
    """One ``evaluate()`` call. Infra leaves means empty instead of a wrong score."""
    names = tuple(metrics) if metrics is not None else DEFAULT_METRICS
    empty = {n: None for n in names}
    empty_rows = [dict(empty) for _ in rows]
    if not rows:
        return RagasOutcome(means={}, per_row=[])
    if not settings.llm_api_key:
        logger.warning("LLM_API_KEY missing — skipping ragas")
        return RagasOutcome(means=empty, per_row=empty_rows)

    try:
        from datasets import Dataset
        from ragas import evaluate
    except ImportError as exc:
        logger.warning("ragas not installed: %s", exc)
        return RagasOutcome(means=empty, per_row=empty_rows)

    try:
        metric_objs = _resolve_metrics(names)
    except ValueError as exc:
        logger.warning("%s", exc)
        return RagasOutcome(means=empty, per_row=empty_rows)

    llm, embeddings = build_ragas_llm_embeddings()
    payload: dict[str, list[Any]] = {
        "question": [r["question"] for r in rows],
        "answer": [r["answer"] for r in rows],
        "contexts": [r["contexts"] for r in rows],
    }
    # context_precision / context_recall need a reference answer column
    if any(n in ("context_precision", "context_recall") for n in names):
        payload["ground_truth"] = [r.get("ground_truth") or "" for r in rows]

    dataset = Dataset.from_dict(payload)
    try:
        result = evaluate(
            dataset,
            metrics=metric_objs,
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=True,
        )
        df = result.to_pandas()
    except Exception as exc:
        if _failure_is_infra(exc):
            logger.warning("ragas infra: %s", exc)
            return RagasOutcome(means={}, per_row=[], infra_error=True)
        logger.warning("ragas failed: %s", exc)
        return RagasOutcome(means=empty, per_row=empty_rows)
    out: dict[str, float | None] = {}
    per_row: list[dict[str, float | None]] = []
    for i in range(len(rows)):
        row: dict[str, float | None] = {}
        for col in names:
            if col in df.columns and i < len(df):
                val = df[col].iloc[i]
                row[col] = float(val) if val == val else None
            else:
                row[col] = None
        per_row.append(row)
    for col in names:
        if col in df.columns:
            val = df[col].mean(skipna=True)
            out[col] = float(val) if val == val else None
        else:
            out[col] = None
    return RagasOutcome(means=out, per_row=per_row)
