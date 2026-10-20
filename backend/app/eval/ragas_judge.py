from __future__ import annotations

import json
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


_SCHEMA_KEYS = {"$defs", "$schema", "properties", "required", "title", "type", "description"}


def json_mode_output_instruction(kind: str) -> str:
    if kind == "statements":
        return (
            "Return one JSON object and no other text. "
            "The object has one key, statements, whose value is an array of strings. "
            "Do not echo a schema."
        )
    if kind == "nli":
        return (
            "Return one JSON object and no other text. "
            "The object has one key, statements. "
            "Each item is an object with keys statement, reason, and verdict. "
            "verdict is the integer 0 or 1. "
            "Do not echo a schema."
        )
    raise ValueError(f"unknown judge instruction: {kind}")


def _outer_json_objects(text: str) -> list[str]:
    blobs: list[str] = []
    i = 0
    limit = len(text)
    while i < limit:
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_string = False
        escaped = False
        for j in range(i, limit):
            char = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blobs.append(text[start : j + 1])
                    i = j + 1
                    break
        else:
            break
    return blobs


def _usable_statements(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if not value:
        return True
    if all(isinstance(item, str) and item.strip() for item in value):
        return True
    return all(
        isinstance(item, dict) and isinstance(item.get("statement"), str) and "verdict" in item
        for item in value
    )


def _payloads(obj: Any) -> list[dict]:
    found: list[dict] = []
    if isinstance(obj, dict):
        if _usable_statements(obj.get("statements")):
            found.append(obj)
        for key, value in obj.items():
            if key in _SCHEMA_KEYS:
                continue
            found.extend(_payloads(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_payloads(item))
    return found


def _coerce_verdict(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value in (0.0, 1.0):
        return int(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes"}:
            return 1
        if token in {"0", "false", "no"}:
            return 0
    return None


def _prepare_payload(payload: dict) -> dict:
    statements = payload.get("statements")
    if not isinstance(statements, list) or not statements:
        return payload
    if all(isinstance(item, str) for item in statements):
        return {"statements": list(statements)}
    prepared: list[dict[str, Any]] = []
    for item in statements:
        if not isinstance(item, dict):
            return payload
        verdict = _coerce_verdict(item.get("verdict"))
        statement = item.get("statement")
        if verdict is None or not isinstance(statement, str):
            return payload
        reason = item.get("reason")
        prepared.append(
            {
                "statement": statement,
                "reason": reason if isinstance(reason, str) else "",
                "verdict": verdict,
            }
        )
    return {"statements": prepared}


def last_usable_statements_json(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return raw
    found: list[dict] = []
    for blob in _outer_json_objects(raw):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        found.extend(_payloads(obj))
    if not found:
        return raw
    prepared = _prepare_payload(found[-1])
    if not isinstance(prepared.get("statements"), list):
        return raw
    return json.dumps(prepared, ensure_ascii=False)


def faithfulness_without_schema():
    from ragas.metrics import Faithfulness
    from ragas.metrics._faithfulness import NLIStatementPrompt, StatementGeneratorPrompt

    class _Statements(StatementGeneratorPrompt):
        def _generate_output_signature(self, indent: int = 4) -> str:
            return json_mode_output_instruction("statements")

    class _NLI(NLIStatementPrompt):
        def _generate_output_signature(self, indent: int = 4) -> str:
            return json_mode_output_instruction("nli")

    return Faithfulness(
        statement_generator_prompt=_Statements(),
        nli_statements_prompt=_NLI(),
    )


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
            spoken = complete_text(payload, role=Role.JUDGE, raise_on_error=True, kind="json")
            raw = spoken.content if hasattr(spoken, "content") else str(spoken or "")
            text = last_usable_statements_json(raw)
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
    )

    mapping = {
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    out = []
    for name in metric_names:
        key = str(name).strip()
        if key == "faithfulness":
            out.append(faithfulness_without_schema())
            continue
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
