"""Unit tests for structured task-completion judges."""

from __future__ import annotations

import pytest

from app.agents.harness.critique import CritiqueResult, UnsupportedClaim, append_grounding_disclaimer
from app.eval.judges import (
    extract_numbers,
    is_refusal,
    judge_completion,
    number_matches,
    strip_grounding_disclaimer,
)


def _with_grounding_note(
    draft: str,
    *,
    unavailable: bool = False,
    claim: str = "预算 1200 万元",
    reason: str = "证据没有该金额",
) -> str:
    """Build a final answer the way production appends the grounding note."""
    if unavailable:
        critique = CritiqueResult(critique_failed=True, critique_error="timeout")
    else:
        critique = CritiqueResult(
            grounded=False,
            addresses_question=True,
            unsupported=[UnsupportedClaim(text=claim, reason=reason)],
        )
    return append_grounding_disclaimer(draft, critique)


@pytest.mark.unit
def test_number_boundary_does_not_match_substring():
    assert number_matches("一共 12 人", 2) is False
    assert number_matches("一共 12 人", 12) is True
    assert number_matches("取消率 8.2%", 0.082) is True


@pytest.mark.unit
def test_extract_numbers_keeps_percent_and_fraction():
    nums = extract_numbers("增长 12.5%")
    assert 12.5 in nums
    assert 0.125 in nums


@pytest.mark.unit
def test_refuse_requires_refusal_language():
    v = judge_completion("refuse", "证据中未提及年假天数。")
    assert v.ok
    v2 = judge_completion("refuse", "年假一共 15 天。")
    assert not v2.ok


@pytest.mark.unit
def test_fact_qa_uses_answer_spec():
    v = judge_completion(
        "fact_qa",
        "正常工作时间是 09:00 到 18:00。",
        answer_spec={"type": "time_range", "start": "09:00", "end": "18:00"},
    )
    assert v.ok
    v2 = judge_completion("fact_qa", "不知道", answer_spec={"type": "number", "value": 5})
    assert not v2.ok


@pytest.mark.unit
def test_is_refusal_patterns():
    assert is_refusal("证据中未提及该数字")
    assert not is_refusal("迟到超过 5 次取消全勤奖")


@pytest.mark.unit
def test_no_tool_fails_when_a_tool_ran():
    """Availability clamp: executing anything means the clamp did not hold."""
    v = judge_completion(
        "no_tool",
        "没有可用的表格数据，无法统计合计。",
        answer_spec={"type": "enum", "any_of": ["无法"]},
        sandbox_executed=True,
    )
    assert not v.ok
    assert v.reason == "tool_used_despite_unavailable"


@pytest.mark.unit
def test_no_tool_accepts_spec_or_unavailable_wording():
    spec = {"type": "enum", "any_of": ["无法", "不能"]}
    assert judge_completion("no_tool", "无法完成统计。", answer_spec=spec, sandbox_executed=False).ok
    # Wording is not fixed: explaining the missing table is equally correct.
    ok = judge_completion(
        "no_tool",
        "当前没有可用的表格文件，所以给不出合计。",
        answer_spec=spec,
        sandbox_executed=False,
    )
    assert ok.ok
    # A confident fabricated number is not accepted.
    bad = judge_completion(
        "no_tool", "合计是 1234 元。", answer_spec=spec, sandbox_executed=False
    )
    assert not bad.ok


@pytest.mark.unit
def test_fabricated_answer_plus_disclaimer_is_not_a_refusal():
    """A made-up fact with the production disclaimer is still not a refusal."""
    draft = "年假一共 15 天。"
    final = _with_grounding_note(draft, claim="年假 15 天", reason="证据中未提及")
    assert "依据核验说明" in final
    assert strip_grounding_disclaimer(final) == draft
    assert not is_refusal(final)
    verdict = judge_completion("refuse", final)
    assert not verdict.ok
    assert verdict.reason == "did_not_refuse"


@pytest.mark.unit
def test_fact_qa_correct_answer_plus_disclaimer_is_not_false_refusal():
    draft = "正常工作时间是 09:00 到 18:00。"
    final = _with_grounding_note(draft, claim="09:00 到 18:00", reason="证据中未提及")
    spec = {"type": "time_range", "start": "09:00", "end": "18:00"}
    assert not is_refusal(final)
    verdict = judge_completion("fact_qa", final, answer_spec=spec)
    assert verdict.ok
    assert verdict.reason != "refused_answerable"


@pytest.mark.unit
def test_real_refusal_detected_with_and_without_disclaimer():
    plain = "证据中未提及年假天数。"
    # The note quotes a large number. That must not turn a real refusal into a fabrication.
    noted = _with_grounding_note(plain, claim="预算 1200 万元", reason="证据没有该金额")
    assert is_refusal(plain)
    assert is_refusal(noted)
    assert judge_completion("refuse", plain).ok
    noted_verdict = judge_completion("refuse", noted)
    assert noted_verdict.ok
    assert noted_verdict.reason != "refusal_with_fabricated_number"
    spec = {"type": "number", "value": 15}
    bare = judge_completion("fact_qa", plain, answer_spec=spec)
    with_note = judge_completion("fact_qa", noted, answer_spec=spec)
    assert not bare.ok and bare.reason == "refused_answerable"
    assert not with_note.ok and with_note.reason == "refused_answerable"


@pytest.mark.unit
def test_grounding_unavailable_note_is_not_a_refusal():
    draft = "迟到超过 5 次取消全勤奖。"
    final = _with_grounding_note(draft, unavailable=True)
    assert "依据核验暂时不可用" in final
    assert "依据核验说明" not in final
    assert strip_grounding_disclaimer(final) == draft
    assert not is_refusal(final)
    assert not is_refusal(_with_grounding_note("", unavailable=True))
    verdict = judge_completion(
        "fact_qa",
        final,
        answer_spec={"type": "number", "value": 5},
    )
    assert verdict.ok
    assert verdict.reason != "refused_answerable"


@pytest.mark.unit
def test_grounding_final_refusal_ignores_disclaimer():
    """``grounding.py`` scores ``refused=is_refusal(final)`` on the emitted answer."""
    fabricated = _with_grounding_note("年假一共 15 天。", claim="年假 15 天", reason="证据中未提及")
    declined = _with_grounding_note("证据中未提及年假天数。", claim="预算 1200 万元")
    assert is_refusal(fabricated) is False
    assert is_refusal(declined) is True


@pytest.mark.unit
def test_completion_branches_score_body_not_disclaimer():
    """Gold values and refusal cues that live only in the note must not count."""
    leaked = _with_grounding_note(
        "合计暂记为 1。",
        claim="没有可用的表格，合计 12，半天事假",
        reason="缺少依据",
    )
    compute = judge_completion(
        "compute",
        leaked,
        answer_spec={"type": "number", "value": 12},
        sandbox_executed=True,
    )
    assert not compute.ok
    multi = judge_completion(
        "multi_tool",
        leaked,
        answer_spec={"type": "enum", "any_of": ["半天事假"]},
    )
    assert not multi.ok
    # Body already satisfies the spec; the header must not flip it to a false refusal.
    no_tool = judge_completion(
        "no_tool",
        _with_grounding_note("无法完成统计。"),
        answer_spec={"type": "enum", "any_of": ["无法", "不能"]},
        sandbox_executed=False,
    )
    assert no_tool.ok
    assert no_tool.reason != "refused_answerable"
    # A correct number in the body still matches after the note is removed.
    compute_ok = judge_completion(
        "compute",
        _with_grounding_note("合计 12 人。"),
        answer_spec={"type": "number", "value": 12},
        sandbox_executed=True,
    )
    assert compute_ok.ok
    only_note = _with_grounding_note("")
    assert judge_completion("chat", only_note, routing_ok=True).reason == "empty_answer"
    assert judge_completion("calendar", only_note, routing_ok=True).reason == "empty_answer"
    assert judge_completion("refuse", only_note).reason == "empty_answer"
    assert (
        judge_completion(
            "fact_qa",
            only_note,
            answer_spec={"type": "number", "value": 1},
        ).reason
        == "empty_answer"
    )


@pytest.mark.unit
def test_strip_ignores_unrelated_horizontal_rule_and_drifted_copy():
    section = "分节如下。\n\n---\n证据中未提及加班费。"
    assert strip_grounding_disclaimer(section) == section
    assert is_refusal(section)
    # Same separator + marker, parenthetical no longer equal to the constant.
    drifted = (
        "年假一共 15 天。\n\n---\n"
        "**依据核验说明**（文案已调整）：\n"
        "- 证据中未提及\n"
    )
    assert strip_grounding_disclaimer(drifted) == "年假一共 15 天。"
    assert not is_refusal(drifted)
    unavailable = (
        "迟到超过 5 次取消全勤奖。\n---\n"
        "**说明**：依据核验暂时不可用，请谨慎引用。\n"
        "证据中未提及\n"
    )
    assert strip_grounding_disclaimer(unavailable) == "迟到超过 5 次取消全勤奖。"
    assert not is_refusal(unavailable)
