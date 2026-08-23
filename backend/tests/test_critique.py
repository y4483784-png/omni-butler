"""Tests for grounding critique and one-shot repair."""

from __future__ import annotations

from unittest.mock import patch

from app.agents.harness.critique import (
    CritiqueResult,
    UnsupportedClaim,
    append_grounding_disclaimer,
    append_repair_to_messages,
    critique_draft,
    finalize_grounded_answer,
    format_repair_block,
    ground_and_repair_answer,
    grounding_thinking_steps,
    sandbox_number_mismatches,
    should_apply_sandbox_number_gate,
    should_ground_answer,
)
from app.core.llm import LLMStructuredError


def test_should_ground_answer_only_for_kb_web_sandbox():
    assert should_ground_answer(evidence=[{"source_type": "kb"}], direct_answer=None)
    assert not should_ground_answer(evidence=[{"source_type": "calendar"}], direct_answer=None)
    assert not should_ground_answer(
        evidence=[{"source_type": "kb"}],
        direct_answer="已有回复",
    )
    assert not should_ground_answer(
        evidence=[{"source_type": "kb"}],
        direct_answer=None,
        grounding_enabled=False,
    )


def test_sandbox_gate_requires_needs_sandbox():
    sandbox = [{"source_type": "sandbox", "content": "===SUMMARY===\nmean=3.14"}]
    web = [{"source_type": "web", "content": "news 4.8"}]
    assert not should_apply_sandbox_number_gate(evidence=web, needs_sandbox=False)
    assert not should_apply_sandbox_number_gate(evidence=sandbox + web, needs_sandbox=False)
    assert not should_apply_sandbox_number_gate(evidence=sandbox, needs_sandbox=False)
    assert should_apply_sandbox_number_gate(evidence=sandbox, needs_sandbox=True)
    assert should_apply_sandbox_number_gate(evidence=sandbox + web, needs_sandbox=True)


def test_sandbox_numbers_not_flagged_without_gate():
    draft = "版本 4.8 于 2026 年发布，得分 91.3。"
    leftover = [{"source_type": "sandbox", "content": "===SUMMARY===\nmean=12.5"}]
    assert sandbox_number_mismatches(draft, leftover) == []
    assert sandbox_number_mismatches(draft, leftover, sandbox_gate=False) == []
    assert sandbox_number_mismatches(draft, [{"source_type": "web", "content": "x"}], sandbox_gate=True) == []


def test_sandbox_number_mismatch_flags_unknown_value():
    evidence = [
        {
            "source_type": "sandbox",
            "content": "===SUMMARY===\nmean=12.5\nrows=100",
        }
    ]
    hits = sandbox_number_mismatches("平均值为 13.0 元", evidence, sandbox_gate=True)
    assert hits
    assert any("13" in h.text for h in hits)


def test_sandbox_gate_allows_summary_number_and_ignores_year():
    evidence = [{"source_type": "sandbox", "content": "===SUMMARY===\nmean=3.14"}]
    ok = sandbox_number_mismatches("均值为 3.14", evidence, sandbox_gate=True)
    assert ok == []
    hits = sandbox_number_mismatches("均值为 9.99，统计于 2026 年", evidence, sandbox_gate=True)
    texts = [h.text for h in hits]
    assert any("9.99" in t for t in texts)
    assert not any("2026" in t for t in texts)


def test_sandbox_rate_equivalence_and_metric_gate():
    evidence = [
        {
            "source_type": "sandbox",
            "content": "===SUMMARY===\n===SUMMARY_JSON===\n"
            '{"metrics":[{"id":"rate","value":0.082}],"missing":[]}',
            "metrics": [{"id": "rate", "value": 0.082}],
        }
    ]
    assert sandbox_number_mismatches("取消率为 8.2%", evidence, sandbox_gate=True) == []
    assert sandbox_number_mismatches("取消率为 0.082", evidence, sandbox_gate=True) == []
    hits = sandbox_number_mismatches("取消率为 9.9%", evidence, sandbox_gate=True)
    assert hits


def test_sandbox_short_int_count_is_gated():
    evidence = [
        {
            "source_type": "sandbox",
            "metrics": [{"id": "row_count", "value": 7}],
            "content": "===SUMMARY===\n===SUMMARY_JSON===\n"
            '{"metrics":[{"id":"row_count","value":7}]}',
        }
    ]
    assert sandbox_number_mismatches("共有 7 人", evidence, sandbox_gate=True) == []
    hits = sandbox_number_mismatches("共有 9 人", evidence, sandbox_gate=True)
    assert hits


def test_sandbox_without_summary_mark_does_not_enable_rule():
    evidence = [{"source_type": "sandbox", "content": "stdout rows=100 mean=12.5"}]
    hits = sandbox_number_mismatches("结果是 99.9", evidence, sandbox_gate=True)
    assert hits == []


def test_critique_merges_llm_unsupported_and_rule_hits():
    evidence = [{"source_type": "kb", "index": 1, "content": "住宿上限 500 元"}]
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            return_value={
                "grounded": False,
                "addresses_question": True,
                "unsupported": [{"text": "上限 800 元", "reason": "证据为 500 元"}],
            },
        ):
            result = critique_draft(
                question="差旅住宿标准？",
                draft="住宿上限 800 元。",
                evidence=evidence,
                included=evidence,
            )
    assert not result.passed
    assert any("800" in u.text for u in result.unsupported)


def test_merge_unsupported_accepts_string_items():
    from app.agents.harness.critique import _merge_unsupported

    out = _merge_unsupported(["上限 800 元", {"text": "日期错误", "reason": "无依据"}], [])
    assert len(out) == 2
    assert out[0].text == "上限 800 元"
    assert out[1].text == "日期错误"


def test_finalize_appends_disclaimer_when_not_passed():
    draft = "住宿上限 800 元。"
    cr = CritiqueResult(
        grounded=False,
        addresses_question=True,
        unsupported=[UnsupportedClaim(text="上限 800 元", reason="证据为 500 元")],
    )
    out = append_grounding_disclaimer(draft, cr)
    assert "依据核验说明" in out
    assert "800" in out
    assert out.startswith("住宿上限 800 元")


def test_finalize_passed_no_disclaimer():
    draft = "住宿上限 500 元。[1]"
    cr = CritiqueResult(grounded=True, addresses_question=True, unsupported=[])
    assert append_grounding_disclaimer(draft, cr) == draft


def test_disclaimer_idempotent_does_not_append_second_block():
    draft = (
        "正文结论。\n\n---\n"
        "**依据核验说明**（以下表述未能在上文证据中找到充分支撑）：\n"
        "- 已有一条\n"
    )
    cr = CritiqueResult(
        grounded=False,
        addresses_question=True,
        unsupported=[UnsupportedClaim(text="新条目", reason="无依据")],
    )
    out = append_grounding_disclaimer(draft, cr)
    assert out.count("依据核验说明") == 1
    assert "新条目" not in out


def test_critique_failed_appends_unavailable_note():
    draft = "结论如下。"
    cr = CritiqueResult(critique_failed=True, critique_error="boom")
    out = append_grounding_disclaimer(draft, cr)
    assert "依据核验暂时不可用" in out


def test_finalize_grounded_answer_end_to_end_mock():
    evidence = [{"source_type": "kb", "index": 1, "filename": "a.pdf", "content": "500 元"}]
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            return_value={
                "grounded": True,
                "addresses_question": True,
                "unsupported": [],
            },
        ):
            final, cr = finalize_grounded_answer(
                question="标准？",
                draft="上限 500 元。[1]",
                evidence=evidence,
                included=evidence,
            )
    assert cr.passed
    assert final == "上限 500 元。[1]"
    assert "依据核验说明" not in final


def test_critique_schema_failure_marks_critique_failed():
    evidence = [{"source_type": "kb", "index": 1, "content": "x"}]
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            side_effect=LLMStructuredError("schema fail"),
        ):
            result = critique_draft(
                question="q",
                draft="答案",
                evidence=evidence,
                included=evidence,
            )
    assert result.critique_failed
    final = append_grounding_disclaimer("答案", result)
    assert "依据核验暂时不可用" in final


def test_repair_block_contains_unsupported_feedback():
    cr = CritiqueResult(
        grounded=False,
        addresses_question=False,
        unsupported=[UnsupportedClaim(text="上限 800 元", reason="证据为 500 元")],
    )
    block = format_repair_block(cr)
    assert "上限 800 元" in block
    assert "证据为 500 元" in block
    assert "未解决用户问题" in block
    messages = append_repair_to_messages(
        [{"role": "system", "content": "【证据池】\n上限 500 元"}, {"role": "user", "content": "标准？"}],
        cr,
    )
    assert "上限 800 元" in messages[0]["content"]
    assert messages[0]["content"].startswith("【证据池】")


def test_repair_regenerates_when_first_critique_fails():
    evidence = [{"source_type": "kb", "index": 1, "content": "住宿上限 500 元"}]
    messages = [
        {"role": "system", "content": "【证据池】\n住宿上限 500 元"},
        {"role": "user", "content": "标准？"},
    ]
    critiques = [
        {
            "grounded": False,
            "addresses_question": True,
            "unsupported": [{"text": "800 元", "reason": "证据为 500 元"}],
        },
        {"grounded": True, "addresses_question": True, "unsupported": []},
    ]
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            side_effect=critiques,
        ):
            with patch(
                "app.agents.harness.critique.complete_text",
                return_value="住宿上限 500 元。[1]",
            ) as gen:
                final, cr, repaired = ground_and_repair_answer(
                    question="标准？",
                    draft="住宿上限 800 元。",
                    evidence=evidence,
                    included=evidence,
                    messages=messages,
                )
    assert repaired
    assert cr.passed
    assert final == "住宿上限 500 元。[1]"
    assert "依据核验说明" not in final
    gen.assert_called_once()
    sent = gen.call_args[0][0]
    assert any("800 元" in (m.get("content") or "") for m in sent)


def test_repair_second_fail_appends_one_disclaimer():
    evidence = [{"source_type": "kb", "index": 1, "content": "住宿上限 500 元"}]
    messages = [
        {"role": "system", "content": "【证据池】\n住宿上限 500 元"},
        {"role": "user", "content": "标准？"},
    ]
    fail = {
        "grounded": False,
        "addresses_question": True,
        "unsupported": [{"text": "800 元", "reason": "证据为 500 元"}],
    }
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            return_value=fail,
        ):
            with patch(
                "app.agents.harness.critique.complete_text",
                return_value="住宿仍写 800 元。",
            ):
                final, cr, repaired = ground_and_repair_answer(
                    question="标准？",
                    draft="住宿上限 800 元。",
                    evidence=evidence,
                    included=evidence,
                    messages=messages,
                )
    assert repaired
    assert not cr.passed
    assert final.count("依据核验说明") == 1
    assert final.startswith("住宿仍写 800 元")


def test_repair_disabled_appends_disclaimer_without_regenerate():
    evidence = [{"source_type": "kb", "index": 1, "content": "住宿上限 500 元"}]
    messages = [
        {"role": "system", "content": "【证据池】"},
        {"role": "user", "content": "标准？"},
    ]
    fail = {
        "grounded": False,
        "addresses_question": True,
        "unsupported": [{"text": "800 元", "reason": "证据为 500 元"}],
    }
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            return_value=fail,
        ):
            with patch("app.agents.harness.critique.complete_text") as gen:
                final, cr, repaired = ground_and_repair_answer(
                    question="标准？",
                    draft="住宿上限 800 元。",
                    evidence=evidence,
                    included=evidence,
                    messages=messages,
                    repair_enabled=False,
                )
    gen.assert_not_called()
    assert not repaired
    assert not cr.passed
    assert "依据核验说明" in final


def test_critique_failed_skips_repair():
    evidence = [{"source_type": "kb", "index": 1, "content": "x"}]
    messages = [{"role": "user", "content": "q"}]
    with patch("app.agents.harness.critique.settings.llm_api_key", "fake"):
        with patch(
            "app.agents.harness.critique.complete_json_schema",
            side_effect=LLMStructuredError("schema fail"),
        ):
            with patch("app.agents.harness.critique.complete_text") as gen:
                final, cr, repaired = ground_and_repair_answer(
                    question="q",
                    draft="答案",
                    evidence=evidence,
                    included=evidence,
                    messages=messages,
                )
    gen.assert_not_called()
    assert not repaired
    assert cr.critique_failed
    assert "依据核验暂时不可用" in final


def test_grounding_thinking_steps():
    passed = CritiqueResult(grounded=True, addresses_question=True)
    assert grounding_thinking_steps(passed, repaired=False) == ["依据核验通过"]
    failed = CritiqueResult(
        grounded=False,
        addresses_question=True,
        unsupported=[UnsupportedClaim(text="x", reason="y")],
    )
    assert "正在按反馈重写" in grounding_thinking_steps(failed, repaired=True)[0]
    assert grounding_thinking_steps(failed, repaired=True)[1] == "重写后仍有未支撑项，已附加说明"
    assert grounding_thinking_steps(passed, repaired=True)[1] == "重写后通过"
