"""Task-completion judges keyed by ``task_type`` (replaces bare substring matching).

Task types
----------
fact_qa     answer must contain the structured ``answer_spec`` value(s)
compute     numeric answer within tolerance of a pandas-computed gold
refuse      must decline (no fabricated numbers); faithfulness/citations not scored
chat        no KB citation / no tool needed
calendar    judged on routing (calendar tool) only
multi_tool  every ``answer_spec`` item must be present
no_tool     availability clamp: answer must match ``answer_spec`` without any tool

``answer_spec`` forms::

    {"type": "number", "value": 9, "unit": "人", "tol": 0}
    {"type": "time_range", "start": "09:00", "end": "18:00"}
    {"type": "enum", "any_of": ["半天事假", "半日事假"]}
    {"type": "all_of", "items": [<spec>, ...]}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# Same strings ``append_grounding_disclaimer`` writes. Importing them keeps a
# copy edit from desyncing the judge (the note reuses refusal wording).
from app.agents.harness.critique import (
    _DISCLAIMER_HEADER,
    _DISCLAIMER_MARKERS,
    _UNAVAILABLE_NOTE,
)

TASK_TYPES = ("fact_qa", "compute", "refuse", "chat", "calendar", "multi_tool", "no_tool")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100}

# A refusal must *say* the evidence is missing. Order matters little; any hit counts.
_REFUSAL_PATTERNS = [
    r"证据中?未提及",
    r"未(能)?(在.{0,12})?(找到|检索到|提及|查到|涉及|说明|给出|提供)",
    r"没有(找到|检索到|查到|提及|相关|明确|写)",
    r"未写",
    r"无法(回答|确定|得出|提供|判断|找到)",
    r"不(能|可)(回答|确定|得出)",
    r"知识库中未",
    r"未检索到",
    r"暂无(相关)?(信息|内容|记录)",
    r"(缺少|缺失).{0,6}(列|字段|数据|信息)",
    r"无法.{0,8}(join|关联|合并)",
]
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS), re.I)

# "The table / column / document I would need is not here" — accepted for
# availability-clamp tasks, whose wording is not fixed.
_UNAVAILABLE_PATTERNS = [
    r"没有(可用的)?(表格|数据|文件|文档|相关信息|明细)",
    r"(缺少|缺失|未提供|没有提供).{0,6}(表格|数据|文件|文档|列|字段)",
    r"无法(完成|计算|统计|汇总|查询|获取)",
    r"(当前|目前).{0,4}(没有|无).{0,6}(表格|数据|文件|文档)",
]
_UNAVAILABLE_RE = re.compile("|".join(_UNAVAILABLE_PATTERNS), re.I)

_NUM_RE = re.compile(r"(?<![\d.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_TIME_RE = re.compile(r"(\d{1,2})[:：点时](\d{2})?")


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, **({"detail": self.detail} if self.detail else {})}


def strip_grounding_disclaimer(answer: str) -> str:
    """Drop the grounding note production appends after the draft.

    ``append_grounding_disclaimer`` suffixes either ``_DISCLAIMER_HEADER``
    (plus bullet lines) or ``_UNAVAILABLE_NOTE``. Both open with a ``---``
    separator and a marker in ``_DISCLAIMER_MARKERS``. Judges score the draft
    only; the note is not a refusal.
    """
    text = answer or ""
    if not text:
        return ""
    cut = _grounding_disclaimer_start(text)
    if cut is None:
        return text
    return text[:cut].rstrip()


def _grounding_disclaimer_start(text: str) -> int | None:
    """Index of a production grounding note, or None when the draft has none."""
    hits = [
        idx
        for idx in (text.find(_DISCLAIMER_HEADER), text.find(_UNAVAILABLE_NOTE))
        if idx != -1
    ]
    if hits:
        return min(hits)
    # Parenthetical copy can drift; still cut from the separator that
    # introduces a known marker through the end of the answer.
    found: list[int] = []
    for marker in _DISCLAIMER_MARKERS:
        start = 0
        while start < len(text):
            idx = text.find(marker, start)
            if idx == -1:
                break
            sep = text.rfind("---", 0, idx)
            if sep != -1 and text[sep + 3 : idx].strip() == "":
                found.append(sep)
                break
            start = idx + len(marker)
    return min(found) if found else None


def is_refusal(answer: str) -> bool:
    return bool(_REFUSAL_RE.search(strip_grounding_disclaimer(answer)))


def cn_numeral_to_int(text: str) -> int | None:
    """Convert short Chinese numerals (十二, 两, 三十五) to int; None when not numeric."""
    text = (text or "").strip()
    if not text:
        return None
    if all(ch in _CN_DIGITS for ch in text):
        return int("".join(str(_CN_DIGITS[c]) for c in text))
    total, cur, seen = 0, 0, False
    for ch in text:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            total += (cur or 1) * _CN_UNITS[ch]
            cur = 0
            seen = True
        else:
            return None
    return total + cur if seen else None


def extract_numbers(text: str) -> list[float]:
    """All numeric values in ``text`` with boundary checks (``12`` is never ``2``).

    ``%`` values are returned twice (``8.2`` and ``0.082``) so ratio/percent forms match.
    """
    out: list[float] = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0)
        pct = raw.endswith("%")
        try:
            val = float(raw.rstrip("%").replace(",", ""))
        except ValueError:
            continue
        out.append(val)
        if pct:
            out.append(val / 100.0)
    for m in re.finditer(r"[零〇一二两三四五六七八九十百]+", text or ""):
        n = cn_numeral_to_int(m.group(0))
        if n is not None:
            out.append(float(n))
    return out


def number_matches(answer: str, value: float, *, tol: float = 0.0, rel_tol: float = 0.0) -> bool:
    for n in extract_numbers(answer):
        if abs(n - value) <= max(tol, abs(value) * rel_tol) + 1e-9:
            return True
    return False


def _norm_time(h: str, m: str | None) -> str:
    return f"{int(h):02d}:{int(m or 0):02d}"


def times_in(text: str) -> list[str]:
    return [_norm_time(h, m) for h, m in _TIME_RE.findall(text or "")]


def spec_matches(answer: str, spec: dict[str, Any]) -> Verdict:
    kind = spec.get("type")
    if kind == "number":
        value = float(spec["value"])
        ok = number_matches(answer, value, tol=float(spec.get("tol") or 0), rel_tol=float(spec.get("rel_tol") or 0))
        return Verdict(ok, "" if ok else f"number {value} not found", {"found": extract_numbers(answer)[:8]})
    if kind == "time_range":
        found = times_in(answer)
        need = [_norm_time(*str(spec["start"]).split(":")), _norm_time(*str(spec["end"]).split(":"))]
        ok = all(t in found for t in need)
        return Verdict(ok, "" if ok else f"times {need} not in {found}")
    if kind == "enum":
        opts = [str(x) for x in spec.get("any_of") or []]
        ok = any(o and o in (answer or "") for o in opts)
        return Verdict(ok, "" if ok else f"none of {opts}")
    if kind == "all_of":
        misses = []
        for sub in spec.get("items") or []:
            v = spec_matches(answer, sub)
            if not v.ok:
                misses.append(v.reason)
        return Verdict(not misses, "; ".join(misses))
    if kind == "text":
        needle = str(spec.get("value") or "")
        ok = bool(needle) and needle in (answer or "")
        return Verdict(ok, "" if ok else f"text {needle!r} missing")
    return Verdict(False, f"unknown spec type {kind!r}")


def judge_completion(
    task_type: str,
    answer: str,
    *,
    answer_spec: dict[str, Any] | None = None,
    gold_numbers: Iterable[float] | None = None,
    routing_ok: bool | None = None,
    cited_any: bool | None = None,
    sandbox_executed: bool | None = None,
    abstained_numbers_allowed: Iterable[float] = (),
) -> Verdict:
    """Return whether the task counts as *completed* under its type's rule."""
    # Every task type scores the draft. The appended grounding note is not part of it.
    answer = strip_grounding_disclaimer(answer or "")
    if not answer.strip():
        return Verdict(False, "empty_answer")
    if task_type == "refuse":
        if not is_refusal(answer):
            return Verdict(False, "did_not_refuse")
        allowed = {round(float(x), 6) for x in abstained_numbers_allowed}
        invented = [n for n in extract_numbers(answer) if round(n, 6) not in allowed and n not in (1.0, 2.0)]
        # numbers in a refusal are fine only if they echo the question; be strict on 3+ digit values.
        invented = [n for n in invented if abs(n) >= 100]
        if invented:
            return Verdict(False, "refusal_with_fabricated_number", {"numbers": invented[:5]})
        return Verdict(True)
    if task_type == "chat":
        if cited_any:
            return Verdict(False, "chat_should_not_cite")
        return Verdict(True if routing_ok is not False else False, "" if routing_ok is not False else "chat_misrouted")
    if task_type == "calendar":
        return Verdict(bool(routing_ok), "" if routing_ok else "calendar_misrouted")
    if task_type == "compute":
        if sandbox_executed is False:
            return Verdict(False, "sandbox_not_executed")
        nums = list(gold_numbers or [])
        if answer_spec:
            return spec_matches(answer, answer_spec)
        ok = bool(nums) and all(number_matches(answer, n, rel_tol=1e-3, tol=0.01) for n in nums)
        return Verdict(ok, "" if ok else "number_mismatch")
    if task_type == "no_tool":
        # Availability clamp: the workspace cannot offer the implied tool, so the
        # only correct behaviour is to answer from what is available and to avoid
        # executing anything. Unlike "chat" a citation is allowed (context may
        # still carry documents), but any executed tool fails the task.
        if sandbox_executed:
            return Verdict(False, "tool_used_despite_unavailable")
        if not answer_spec:
            return Verdict(False, "missing_answer_spec")
        if is_refusal(answer):
            return Verdict(False, "refused_answerable")
        verdict = spec_matches(answer, answer_spec)
        if verdict.ok:
            return verdict
        # Wording is not fixed: explaining that the needed data/table is absent
        # is equally correct, as long as nothing was executed.
        if _UNAVAILABLE_RE.search(answer):
            return Verdict(True)
        return verdict
    if task_type in ("fact_qa", "multi_tool"):
        if not answer_spec:
            return Verdict(False, "missing_answer_spec")
        if is_refusal(answer) and task_type == "fact_qa":
            return Verdict(False, "refused_answerable")
        return spec_matches(answer, answer_spec)
    return Verdict(False, f"unknown_task_type:{task_type}")
