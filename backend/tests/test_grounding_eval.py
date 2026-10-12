"""Smoke tests for grounding faithfulness eval (mocked LLM, no live ragas)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.agents.harness.critique import (
    CritiqueResult,
    UnsupportedClaim,
    append_grounding_disclaimer,
    sandbox_number_mismatches,
)
from app.eval.grounding import (
    DEFAULT_DATASET,
    GroundingEvalCase,
    answer_body_for_scoring,
    build_evidence_for_case,
    generate_grounded_answer,
    load_cases,
    run_grounding_eval,
)
from app.eval.judges import is_refusal
from app.eval.ragas_judge import run_ragas_metrics


def test_dataset_exists_and_loads():
    assert DEFAULT_DATASET.is_file()
    cases = load_cases(DEFAULT_DATASET)
    assert cases
    assert {c.source for c in cases} == {"kb", "web", "sandbox"}
    assert any(c.expect_unanswerable for c in cases)
    assert any(c.needs_sandbox for c in cases if c.source == "sandbox")


def test_frozen_web_evidence_no_sandbox_gate():
    case = GroundingEvalCase(
        id="t_web",
        source="web",
        query="一线住宿上限？",
        contexts=["住宿上限 650 元"],
        needs_sandbox=False,
    )
    evidence = build_evidence_for_case(case, db=None, top_k=4)  # type: ignore[arg-type]
    assert evidence[0]["source_type"] == "web"
    with (
        patch("app.eval.grounding.complete_text", return_value="住宿上限 650 元。[1]"),
        patch(
            "app.eval.grounding.ground_and_repair_answer",
            return_value=(
                "住宿上限 650 元。[1]",
                CritiqueResult(grounded=True, addresses_question=True, unsupported=[]),
                False,
            ),
        ),
    ):
        final, draft, included, critique, repaired, sandbox_gate = generate_grounded_answer(
            case.query,
            evidence,
            needs_sandbox=False,
            repair_enabled=True,
        )
    assert sandbox_gate is False
    assert repaired is False
    assert critique.passed
    assert "650" in final


def test_sandbox_gate_true_when_needs_sandbox():
    case = GroundingEvalCase(
        id="t_sbx",
        source="sandbox",
        query="均值多少？",
        contexts=["===SUMMARY===\nmean=72.5\nrows=10"],
        needs_sandbox=True,
    )
    evidence = build_evidence_for_case(case, db=None, top_k=4)  # type: ignore[arg-type]
    assert evidence[0]["source_type"] == "sandbox"
    with (
        patch("app.eval.grounding.complete_text", return_value="均值为 72.5。[1]"),
        patch(
            "app.eval.grounding.ground_and_repair_answer",
            return_value=(
                "均值为 72.5。[1]",
                CritiqueResult(grounded=True, addresses_question=True, unsupported=[]),
                False,
            ),
        ) as repair,
    ):
        _, _, _, _, _, sandbox_gate = generate_grounded_answer(
            case.query,
            evidence,
            needs_sandbox=True,
            repair_enabled=True,
        )
    assert sandbox_gate is True
    assert repair.call_args.kwargs.get("sandbox_gate") is True


def test_run_grounding_eval_skip_ragas_mocked(monkeypatch):
    cases = [
        GroundingEvalCase(
            id="gf_web_mock",
            source="web",
            query="住宿上限？",
            contexts=["一线城市每晚上限 650 元"],
            tags=["web"],
        ),
        GroundingEvalCase(
            id="gf_sbx_mock",
            source="sandbox",
            query="均值？",
            contexts=["===SUMMARY===\nmean=12.5"],
            needs_sandbox=True,
            tags=["sandbox"],
        ),
    ]

    def _fake_generate(query, evidence, *, needs_sandbox=False, repair_enabled=True):
        from app.agents.workflow import build_pool_prompt

        _, included = build_pool_prompt(evidence)
        gate = bool(needs_sandbox and any(e.get("source_type") == "sandbox" for e in evidence))
        return (
            f"回答：{query}",
            f"草稿：{query}",
            included,
            CritiqueResult(grounded=True, addresses_question=True, unsupported=[]),
            False,
            gate,
        )

    monkeypatch.setattr("app.eval.grounding.generate_grounded_answer", _fake_generate)
    monkeypatch.setattr("app.eval.grounding.init_db", lambda: None)
    monkeypatch.setattr("app.eval.grounding.SessionLocal", lambda: _DummySession())

    report = run_grounding_eval(
        cases,
        skip_ragas=True,
        skip_ingest=True,
        repair_enabled=True,
    )
    assert report.n == 2
    assert report.skipped_ragas is True
    assert report.faithfulness_mean is None
    assert "web" in report.by_source
    assert "sandbox" in report.by_source
    assert report.by_source["web"]["n"] == 1
    assert report.repair_rate == 0.0
    # sandbox case should have sandbox_gate true → rule check may still be 0 hits
    sbx = next(c for c in report.cases if c["id"] == "gf_sbx_mock")
    assert sbx["sandbox_gate"] is True
    web = next(c for c in report.cases if c["id"] == "gf_web_mock")
    assert web["sandbox_gate"] is False


def test_run_ragas_metrics_optional_metrics_without_key():
    with patch("app.eval.ragas_judge.settings.llm_api_key", ""):
        scores = run_ragas_metrics(
            [{"question": "q", "answer": "a", "contexts": ["c"]}],
            metrics=["faithfulness"],
        )
    assert scores == {"faithfulness": None}


class _DummySession:
    def close(self) -> None:
        return None


def test_scoring_strips_disclaimer_but_keeps_real_refusals():
    """Numbers and refusal cues that live only in the note must not move the score."""
    evidence = [{"source_type": "sandbox", "content": "===SUMMARY===\nmean=3.14"}]
    draft = "均值为 3.14"
    final = append_grounding_disclaimer(
        draft,
        CritiqueResult(
            grounded=False,
            addresses_question=True,
            unsupported=[UnsupportedClaim(text="额外数字 99.9", reason="证据中未提及")],
        ),
    )
    assert sandbox_number_mismatches(final, evidence, sandbox_gate=True)
    body = answer_body_for_scoring(final)
    assert body == draft
    assert sandbox_number_mismatches(body, evidence, sandbox_gate=True) == []
    # A mismatch in the draft is still a mismatch after the note is removed.
    bad = append_grounding_disclaimer(draft + "，另有 9.99", CritiqueResult(critique_failed=True))
    assert sandbox_number_mismatches(answer_body_for_scoring(bad), evidence, sandbox_gate=True)
    assert is_refusal(final) is False
    declined = append_grounding_disclaimer("证据中未提及该指标。", CritiqueResult(critique_failed=True))
    assert is_refusal(declined) is True
