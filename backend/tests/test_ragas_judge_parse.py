"""DeepSeek JSON-mode judge text must still yield a faithfulness score."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.llm import Spoken


class _Verdict(BaseModel):
    statement: str
    reason: str
    verdict: int


class _NLI(BaseModel):
    statements: list[_Verdict]


_SCHEMA = {
    "$defs": {
        "StatementFaithfulnessAnswer": {
            "properties": {"verdict": {"type": "integer"}},
            "type": "object",
        }
    },
    "properties": {"statements": {"type": "array"}},
    "required": ["statements"],
    "title": "NLIStatementOutput",
    "type": "object",
}


def _answer(*, verdict: object, reason: str | None = "证据写明上限") -> dict:
    item = {"statement": "住宿上限为650元", "verdict": verdict}
    if reason is not None:
        item["reason"] = reason
    return {"statements": [item]}


def _score(payload: dict) -> float:
    parsed = _NLI.model_validate(payload)
    return sum(1 if row.verdict else 0 for row in parsed.statements) / len(parsed.statements)


def test_schema_object_is_not_a_faithfulness_score():
    """ragas extract_json keeps the first object. A JSON Schema is not a score."""
    from ragas.prompt.utils import extract_json

    raw = json.dumps(_SCHEMA, ensure_ascii=False) + "\n" + json.dumps(_answer(verdict=1))
    with pytest.raises(ValidationError):
        _NLI.model_validate_json(extract_json(raw))


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(_SCHEMA, ensure_ascii=False) + "\n" + json.dumps(_answer(verdict=True)),
        "```json\n" + json.dumps(_answer(verdict=True)) + "\n```",
        json.dumps(_answer(verdict=1, reason=None)),
    ],
)
def test_judge_returns_a_numeric_score_for_usable_json_mode_text(monkeypatch, raw):
    monkeypatch.setattr(settings, "embedding_base_url", "http://embedding.test/v4/")
    monkeypatch.setattr(settings, "embedding_api_key", "embedding-key")
    monkeypatch.setattr(settings, "embedding_model", "embedding-3")
    seen: dict = {}

    def fake_complete(messages, **kwargs):
        seen["kwargs"] = kwargs
        seen["messages"] = messages
        return Spoken(raw, "", 3)

    monkeypatch.setattr("app.core.llm.complete_text", fake_complete)
    from langchain_core.messages import HumanMessage

    from app.eval.ragas_judge import build_ragas_llm_embeddings

    llm, _embeddings = build_ragas_llm_embeddings()
    result = llm._generate([HumanMessage(content="judge")])
    text = result.generations[0].message.content
    assert seen["kwargs"].get("kind") == "json"
    assert _score(json.loads(text)) == 1.0


def test_schema_only_output_does_not_invent_a_score(monkeypatch):
    monkeypatch.setattr(settings, "embedding_base_url", "http://embedding.test/v4/")
    monkeypatch.setattr(settings, "embedding_api_key", "embedding-key")
    monkeypatch.setattr(settings, "embedding_model", "embedding-3")

    def fake_complete(messages, **kwargs):
        return Spoken(json.dumps(_SCHEMA), "", 1)

    monkeypatch.setattr("app.core.llm.complete_text", fake_complete)
    from langchain_core.messages import HumanMessage

    from app.eval.ragas_judge import build_ragas_llm_embeddings

    llm, _embeddings = build_ragas_llm_embeddings()
    text = llm._generate([HumanMessage(content="judge")]).generations[0].message.content
    with pytest.raises(ValidationError):
        _NLI.model_validate_json(text)


def test_complete_text_json_kind_requests_json_object_not_schema(monkeypatch):
    from app.core import llm as llm_mod
    from app.core.llm import Role, complete_text

    calls: list[dict] = []

    class _Msg:
        content = '{"statements": ["住宿上限为650元"]}'
        reasoning_content = None

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]
        usage = None

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _Resp()

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {})()
            self.chat.completions = Completions()

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_sync_client", lambda: Client())
    spoken = complete_text(
        [{"role": "user", "content": "hi"}],
        role=Role.JUDGE,
        kind="json",
    )
    assert spoken.content == '{"statements": ["住宿上限为650元"]}'
    blob = json.dumps(calls[0])
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "json_schema" not in blob
    assert calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}


def test_faithfulness_prompt_does_not_embed_json_schema():
    pytest.importorskip("ragas")
    from ragas.metrics._faithfulness import NLIStatementInput, StatementGeneratorInput

    from app.eval.ragas_judge import faithfulness_metric

    metric = faithfulness_metric()
    statement_prompt = metric.statement_generator_prompt.to_string(
        StatementGeneratorInput(question="上限是多少", answer="住宿上限为650元")
    )
    nli_prompt = metric.nli_statements_prompt.to_string(
        NLIStatementInput(context="一线住宿上限 650 元", statements=["住宿上限为650元"])
    )
    for prompt in (statement_prompt, nli_prompt):
        assert "$defs" not in prompt
        assert "JSON Schema" not in prompt
    assert "0" in nli_prompt and "1" in nli_prompt
