"""Faithfulness critique + one Reflexion-style repair before SSE emit.

Industry mapping:
- LangGraph Self-RAG hallucination / answer graders (grounded, addresses question)
- RAGAS Faithfulness (atomic statements, NLI against retrieved context only)
- Reflexion (verbal critique → revise once, then stop)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.llm import LLMStructuredError, complete_json_schema, complete_text, resolved_router_model

logger = logging.getLogger(__name__)

_SUMMARY_MARK = "===SUMMARY==="
_NUMBER_RE = re.compile(r"(?<![\[\d])(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\]\d])")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grounded": {
            "type": "boolean",
            "description": "True when factual claims in the draft are supported by the evidence pool.",
        },
        "addresses_question": {
            "type": "boolean",
            "description": "True when the draft answers the user question.",
        },
        "unsupported": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["text", "reason"],
                "additionalProperties": False,
            },
            "description": "Statements that cannot be inferred from evidence (max ~8).",
        },
    },
    "required": ["grounded", "addresses_question", "unsupported"],
    "additionalProperties": False,
}

_DISCLAIMER_HEADER = "\n\n---\n**依据核验说明**（以下表述未能在上文证据中找到充分支撑）：\n"
_UNAVAILABLE_NOTE = "\n\n---\n**说明**：依据核验暂时不可用，请谨慎引用上文中的具体数字与制度条款。\n"
_DISCLAIMER_MARKERS = ("**依据核验说明**", "**说明**：依据核验暂时不可用")

# RAGAS NLI + LangGraph hallucination grader; few-shot is pedagogical NLI only.
_CRITIQUE_SYSTEM = """你是忠实度判定器。只根据【证据】判断【回答】中的事实断言能否被蕴含。禁止使用【证据】以外的任何知识。

工作方式（在内部完成，不要把中间步骤写进 JSON）：
1. 将【回答】拆成若干条独立、可判定的事实断言（一条断言一个事实；不要用代词指代未说明的对象）。
2. 对每条断言做自然语言推断：若证据中至少有一处可以直接推出该断言，则支撑成立（同义转述、压缩、合并多条证据均可）。证据其余部分是否无关，不影响判定。
3. 下列内容不是需要判定的事实断言：过渡句、结构小标题、对用户问题的复述、明确的不确定表述（如「证据中未提及」「可能已过期」）、引用角标本身。

判定字段：
- grounded：所有事实断言均被证据蕴含则为 true，否则 false。
- addresses_question：回答是否实质回应了【问题】。
- unsupported：未能被证据蕴含的事实断言。text 尽量摘自原文；reason 说明为何推不出（指出证据缺什么，不要用外部常识补理由）。最多 8 条。没有则 []。

不要因为「措辞与证据不完全相同」判定未支撑。不要因为「证据很长、相关句不在开头」判定未支撑。

例：证据写「甲主修计算机，本学期选修数据结构」。
- 「甲主修计算机」→ 蕴含
- 「甲主修生物」→ 未蕴含（证据写的是计算机）
- 「甲勤奋」且证据写「经常在图书馆学习到很晚」→ 蕴含（可直接推出）
- 「甲有兼职」→ 未蕴含（证据未提及）
"""

_REPAIR_HEADER = """【修订说明】
请根据【证据池】重写完整回答，不要续写旧稿。

使用上一轮核验：
- 删掉或改写下面这些未能由证据蕴含的断言；不要用记忆编造新事实来填补。
- 证据里能推出的内容应保留，并继续使用证据编号作为角标。
- 证据推不出的，写「证据中未提及」，不要保留原断言中的具体事实。
- 直接输出给用户的正文；不要输出核验清单或修订过程。
"""


@dataclass
class UnsupportedClaim:
    text: str
    reason: str


@dataclass
class CritiqueResult:
    grounded: bool = True
    addresses_question: bool = True
    unsupported: list[UnsupportedClaim] = field(default_factory=list)
    critique_failed: bool = False
    critique_error: str = ""

    @property
    def passed(self) -> bool:
        if self.critique_failed:
            return False
        return self.grounded and self.addresses_question and not self.unsupported


def should_ground_answer(
    *,
    evidence: list[dict],
    direct_answer: str | None,
    grounding_enabled: bool | None = None,
) -> bool:
    """True when kb/web/sandbox evidence exists and we should draft+critique before emit."""
    if grounding_enabled is None:
        grounding_enabled = bool(settings.grounding_enabled)
    if not grounding_enabled:
        return False
    if direct_answer:
        return False
    return any(e.get("source_type") in ("kb", "web", "sandbox") for e in evidence)


def should_apply_sandbox_number_gate(
    *,
    evidence: list[dict],
    needs_sandbox: bool = False,
) -> bool:
    """SUMMARY number check only for this-turn data analysis, not leftover sandbox rows."""
    has_sandbox = any(e.get("source_type") == "sandbox" for e in evidence)
    return has_sandbox and bool(needs_sandbox)


def _format_evidence_for_critique(included: list[dict]) -> str:
    lines: list[str] = []
    for e in included:
        idx = e.get("index")
        st = e.get("source_type") or "unknown"
        header = f"[{idx}] {st}"
        if e.get("filename"):
            header += f" · {e.get('filename')}"
        body = str(e.get("content") or e.get("snippet") or "").strip()
        if len(body) > 6000:
            body = body[:6000] + "…"
        lines.append(f"{header}\n{body}")
    return "\n\n".join(lines) if lines else "（无证据）"


def _summary_numbers(evidence: list[dict]) -> set[str]:
    nums: set[str] = set()
    for e in evidence:
        if e.get("source_type") != "sandbox":
            continue
        # Prefer structured metrics on evidence
        metrics = e.get("metrics")
        if isinstance(metrics, list) and metrics:
            from app.services.analysis_ir import expand_allowed_number_tokens

            vals: list[float] = []
            for m in metrics:
                if isinstance(m, dict):
                    try:
                        vals.append(float(m.get("value")))
                    except (TypeError, ValueError):
                        continue
            nums |= expand_allowed_number_tokens(vals)
            continue
        text = str(e.get("content") or e.get("snippet") or "")
        if _SUMMARY_MARK not in text:
            continue
        from app.services.analysis_ir import expand_allowed_number_tokens, parse_summary_payload

        payload = parse_summary_payload(text)
        vals = []
        for m in payload.get("metrics") or []:
            if isinstance(m, dict):
                try:
                    vals.append(float(m.get("value")))
                except (TypeError, ValueError):
                    continue
        if vals:
            nums |= expand_allowed_number_tokens(vals)
            continue
        text = text.split(_SUMMARY_MARK, 1)[1]
        for m in _NUMBER_RE.findall(text):
            nums.add(_normalize_number(m))
    return nums


def _normalize_number(raw: str) -> str:
    return raw.replace(",", "").strip().rstrip("%")


def _number_allowed(norm: str, allowed: set[str]) -> bool:
    if norm in allowed:
        return True
    # percent token
    if norm.endswith("%") and norm[:-1] in allowed:
        return True
    if (norm + "%") in allowed:
        return True
    try:
        val = float(norm)
    except ValueError:
        return False
    from app.services.analysis_ir import expand_allowed_number_tokens, numbers_equivalent

    # Compare against parsed floats from allowed tokens
    for tok in allowed:
        t = tok.rstrip("%")
        try:
            other = float(t)
        except ValueError:
            continue
        if numbers_equivalent(val, other):
            return True
    return False


def sandbox_number_mismatches(
    draft: str,
    evidence: list[dict],
    *,
    sandbox_gate: bool = False,
) -> list[UnsupportedClaim]:
    """Flag draft numbers absent from sandbox metric values (rule-based, no LLM)."""
    if not sandbox_gate:
        return []
    allowed = _summary_numbers(evidence)
    if not allowed:
        return []
    hits: list[UnsupportedClaim] = []
    seen: set[str] = set()
    for m in _NUMBER_RE.findall(draft or ""):
        norm = _normalize_number(m)
        if norm in seen:
            continue
        seen.add(norm)
        if _number_allowed(norm, allowed):
            continue
        if _YEAR_RE.match(norm):
            continue
        # Do NOT ignore short integers — counts/headcounts are often 1–2 digits.
        # Still skip lone citation-like very small ints only when not in metrics? Keep all.
        hits.append(
            UnsupportedClaim(
                text=m,
                reason="该数值未出现在沙箱指标汇总中",
            )
        )
        if len(hits) >= 8:
            break
    return hits


def _merge_unsupported(
    llm_items: list[Any],
    rule_items: list[UnsupportedClaim],
    *,
    max_items: int = 8,
) -> list[UnsupportedClaim]:
    out: list[UnsupportedClaim] = []
    seen: set[str] = set()
    for item in rule_items:
        key = (item.text or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    for raw in llm_items:
        # Models may return unsupported as strings instead of {text, reason} objects.
        if isinstance(raw, str):
            text = raw.strip()
            reason = "证据中未找到支撑"
        elif isinstance(raw, dict):
            text = str(raw.get("text") or "").strip()
            reason = str(raw.get("reason") or "").strip() or "证据中未找到支撑"
        else:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(UnsupportedClaim(text=text, reason=reason))
        if len(out) >= max_items:
            break
    return out[:max_items]


def critique_draft(
    *,
    question: str,
    draft: str,
    evidence: list[dict],
    included: list[dict] | None = None,
    sandbox_gate: bool = False,
) -> CritiqueResult:
    """Single structured LLM call: grounded + addresses_question + unsupported list."""
    pool = included if included is not None else evidence
    rule_hits = sandbox_number_mismatches(draft, evidence, sandbox_gate=sandbox_gate)

    if not settings.llm_api_key:
        return CritiqueResult(
            grounded=len(rule_hits) == 0,
            addresses_question=True,
            unsupported=rule_hits,
            critique_failed=False,
        )

    evidence_text = _format_evidence_for_critique(pool)
    user = (
        f"【问题】\n{question.strip()}\n\n"
        f"【证据】\n{evidence_text}\n\n"
        f"【回答】\n{draft.strip()}\n\n"
        "输出 JSON：grounded、addresses_question、unsupported。"
    )
    messages = [
        {"role": "system", "content": _CRITIQUE_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        raw = complete_json_schema(
            messages,
            schema=_CRITIQUE_SCHEMA,
            name="grounding_critique",
            model=resolved_router_model(),
            max_attempts=2,
        )
    except LLMStructuredError as exc:
        logger.warning("grounding critique schema failed: %s", exc)
        merged = rule_hits
        return CritiqueResult(
            grounded=False,
            addresses_question=False,
            unsupported=merged,
            critique_failed=True,
            critique_error=str(exc),
        )
    except Exception as exc:
        logger.warning("grounding critique failed: %s", exc)
        return CritiqueResult(
            grounded=False,
            addresses_question=False,
            unsupported=rule_hits,
            critique_failed=True,
            critique_error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )

    unsupported_raw = raw.get("unsupported") if isinstance(raw, dict) else None
    if isinstance(unsupported_raw, str) and unsupported_raw.strip():
        unsupported_list: list[Any] = [unsupported_raw.strip()]
    elif isinstance(unsupported_raw, list):
        unsupported_list = unsupported_raw
    else:
        unsupported_list = []
    unsupported = _merge_unsupported(
        unsupported_list,
        rule_hits,
    )
    grounded = bool(raw.get("grounded")) and not unsupported
    addresses = bool(raw.get("addresses_question"))
    if unsupported:
        grounded = False
    return CritiqueResult(
        grounded=grounded,
        addresses_question=addresses,
        unsupported=unsupported,
        critique_failed=False,
    )


def _already_has_grounding_note(body: str) -> bool:
    return any(marker in (body or "") for marker in _DISCLAIMER_MARKERS)


def append_grounding_disclaimer(draft: str, critique: CritiqueResult) -> str:
    """Append fixed disclaimer block; never rewrite the draft body."""
    body = (draft or "").rstrip()
    if critique.critique_failed:
        if _already_has_grounding_note(body):
            return body
        return body + _UNAVAILABLE_NOTE
    if critique.passed:
        return body
    if _already_has_grounding_note(body):
        return body
    if not critique.unsupported and not critique.addresses_question:
        note = _DISCLAIMER_HEADER + "- 回答可能未完全覆盖用户问题。\n"
        return body + note
    lines = [_DISCLAIMER_HEADER]
    for item in critique.unsupported:
        lines.append(f"- {item.text}（{item.reason}）")
    if not critique.addresses_question:
        lines.append("- 回答可能未完全覆盖用户问题。")
    return body + "\n".join(lines)


def format_repair_block(critique: CritiqueResult) -> str:
    """Verbal feedback for one Reflexion-style rewrite (no extra retrieval)."""
    lines = [_REPAIR_HEADER.rstrip(), "", "未能蕴含的断言："]
    if critique.unsupported:
        for item in critique.unsupported:
            lines.append(f"- {item.text}：{item.reason}")
    else:
        lines.append("- （核验未通过，但未列出具体断言；请严格依据证据重写，证据没有的写「证据中未提及」。）")
    if not critique.addresses_question:
        lines.append("")
        lines.append("上一稿未解决用户问题。请先直接回答【问题】，再给依据。")
    return "\n".join(lines)


def append_repair_to_messages(messages: list[dict], critique: CritiqueResult) -> list[dict]:
    block = format_repair_block(critique)
    out = [{**m} for m in messages]
    for m in out:
        if m.get("role") == "system":
            m["content"] = (m.get("content") or "").rstrip() + "\n\n" + block
            return out
    out.insert(0, {"role": "system", "content": block})
    return out


def grounding_thinking_steps(critique: CritiqueResult, *, repaired: bool) -> list[str]:
    if critique.critique_failed:
        return ["依据核验暂时不可用，已附加说明"]
    if repaired:
        if critique.passed:
            return ["依据核验未通过，正在按反馈重写", "重写后通过"]
        return ["依据核验未通过，正在按反馈重写", "重写后仍有未支撑项，已附加说明"]
    if critique.passed:
        return ["依据核验通过"]
    return ["依据核验未完全通过，已在文末附加说明"]


def finalize_grounded_answer(
    *,
    question: str,
    draft: str,
    evidence: list[dict],
    included: list[dict],
    sandbox_gate: bool = False,
) -> tuple[str, CritiqueResult]:
    """Run rule checks + one LLM critique; disclaimer only (no rewrite)."""
    critique = critique_draft(
        question=question,
        draft=draft,
        evidence=evidence,
        included=included,
        sandbox_gate=sandbox_gate,
    )
    final = append_grounding_disclaimer(draft, critique)
    return final, critique


def ground_and_repair_answer(
    *,
    question: str,
    draft: str,
    evidence: list[dict],
    included: list[dict],
    messages: list[dict],
    sandbox_gate: bool = False,
    repair_enabled: bool | None = None,
) -> tuple[str, CritiqueResult, bool]:
    """Critique draft; on failure, regenerate once with verbal feedback, then re-critique."""
    if repair_enabled is None:
        repair_enabled = bool(settings.grounding_repair_enabled)

    critique = critique_draft(
        question=question,
        draft=draft,
        evidence=evidence,
        included=included,
        sandbox_gate=sandbox_gate,
    )
    if critique.passed:
        return (draft or "").rstrip(), critique, False
    if critique.critique_failed or not repair_enabled:
        return append_grounding_disclaimer(draft, critique), critique, False

    repair_messages = append_repair_to_messages(messages, critique)
    try:
        draft2 = complete_text(repair_messages)
    except Exception as exc:
        logger.warning("grounding repair generate failed: %s", exc)
        return append_grounding_disclaimer(draft, critique), critique, False

    if not (draft2 or "").strip():
        return append_grounding_disclaimer(draft, critique), critique, False

    critique2 = critique_draft(
        question=question,
        draft=draft2,
        evidence=evidence,
        included=included,
        sandbox_gate=sandbox_gate,
    )
    if critique2.passed:
        return draft2.rstrip(), critique2, True
    return append_grounding_disclaimer(draft2, critique2), critique2, True
