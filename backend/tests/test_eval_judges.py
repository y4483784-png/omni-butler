"""Unit tests for structured task-completion judges."""

from __future__ import annotations

import pytest

from app.eval.judges import extract_numbers, is_refusal, judge_completion, number_matches


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
