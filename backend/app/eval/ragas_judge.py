"""Shared ragas judge helpers (Zhipu-compatible ChatOpenAI + evaluate)."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


def _clamp_temperature(kwargs: dict[str, Any]) -> dict[str, Any]:
    """智谱 API: temperature 最多 2 位小数；ragas 常传入 1e-8 等非法值。"""
    if "temperature" not in kwargs or kwargs["temperature"] is None:
        return kwargs
    out = dict(kwargs)
    out["temperature"] = round(float(out["temperature"]), 2)
    return out


def build_ragas_llm_embeddings():
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    class _ZhipuSafeChatOpenAI(ChatOpenAI):
        """Round temperature to 2 decimals for Zhipu-compatible endpoints."""

        @property
        def _default_params(self) -> dict[str, Any]:
            params = dict(super()._default_params)
            if params.get("temperature") is not None:
                params["temperature"] = round(float(params["temperature"]), 2)
            return params

        def bind(self, **kwargs):  # type: ignore[override]
            return super().bind(**_clamp_temperature(kwargs))

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return super()._generate(
                messages, stop=stop, run_manager=run_manager, **_clamp_temperature(kwargs)
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **_clamp_temperature(kwargs)
            )

        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            if isinstance(payload, dict) and payload.get("temperature") is not None:
                payload["temperature"] = round(float(payload["temperature"]), 2)
            return payload

    llm = _ZhipuSafeChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key or "dummy",
        base_url=settings.llm_base_url,
        temperature=0.0,
        timeout=settings.llm_timeout,
        max_retries=1,
    )
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.llm_api_key or "dummy",
        base_url=settings.llm_base_url,
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


def run_ragas_metrics(
    rows: list[dict[str, Any]],
    *,
    metrics: Sequence[str] | None = None,
) -> dict[str, float | None]:
    """Run selected ragas metrics; default is the full YZ quartet."""
    names = tuple(metrics) if metrics is not None else DEFAULT_METRICS
    empty = {n: None for n in names}
    if not rows:
        return {}
    if not settings.llm_api_key:
        logger.warning("LLM_API_KEY missing — skipping ragas")
        return empty

    try:
        from datasets import Dataset
        from ragas import evaluate
    except ImportError as exc:
        logger.warning("ragas not installed: %s", exc)
        return empty

    try:
        metric_objs = _resolve_metrics(names)
    except ValueError as exc:
        logger.warning("%s", exc)
        return empty

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
    result = evaluate(
        dataset,
        metrics=metric_objs,
        llm=llm,
        embeddings=embeddings,
    )
    out: dict[str, float | None] = {}
    df = result.to_pandas()
    for col in names:
        if col in df.columns:
            val = df[col].mean(skipna=True)
            out[col] = float(val) if val == val else None  # NaN check
        else:
            out[col] = None
    return out
