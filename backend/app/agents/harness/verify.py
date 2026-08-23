"""Deterministic verification for reflect_node (H2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any

from app.services.analysis_ir import asked_ids_covered, parse_summary_payload
from app.services.data_analysis import sandbox_hint

_DATE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_CN = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?")
_STALE_TITLE = re.compile(r"(年度盘点|回顾|周报|日报|热搜榜单|历史盘点|盘点)", re.I)
_SUMMARY_MARK = "===SUMMARY==="


@dataclass
class VerifyDecision:
    ok: bool
    reason: str = ""
    next_tool: str | None = None
    mark_kb_retried: bool = False
    mark_web_retried: bool = False
    mark_sandbox_replanned: bool = False
    sandbox_feedback: str = ""


def _parse_publish_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    m = _DATE_ISO.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_CN.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def web_is_stale(web: list[dict]) -> bool:
    if not web:
        return True
    parsed: list[date] = []
    for e in web:
        d = _parse_publish_date(str(e.get("publish_date") or ""))
        if d is not None:
            parsed.append(d)
    if parsed:
        stale_count = sum(1 for d in parsed if (date.today() - d).days > 7)
        return stale_count >= len(parsed) * 0.8
    title_stale = sum(1 for e in web if _STALE_TITLE.search(str(e.get("title") or "")))
    return title_stale == len(web)


def _sandbox_payload(state: dict[str, Any], sandbox: list[dict]) -> dict[str, Any]:
    summary = state.get("analysis_summary")
    if isinstance(summary, dict) and (summary.get("metrics") is not None or summary.get("missing") is not None):
        return summary
    for e in sandbox:
        text = str(e.get("content") or e.get("snippet") or "")
        if _SUMMARY_MARK in text:
            return parse_summary_payload(text)
    return {}


def _metric_coverage_decision(
    state: dict[str, Any],
    *,
    sandbox: list[dict],
    it: int,
    max_iter: int,
) -> VerifyDecision | None:
    """Return a VerifyDecision when sandbox metric coverage needs handling; else None."""
    payload = _sandbox_payload(state, sandbox)
    asked = list(state.get("analysis_asked_ids") or payload.get("asked_ids") or [])
    ir = state.get("analysis_ir") if isinstance(state.get("analysis_ir"), dict) else {}
    if not asked and isinstance(ir, dict):
        asked = list(ir.get("asked_ids") or [])

    covered, still = asked_ids_covered(payload, asked)
    missing = list(payload.get("missing") or [])
    if state.get("analysis_uncomputable") or (missing and not payload.get("metrics")):
        return VerifyDecision(ok=True, reason="sandbox_uncomputable_honest")

    if covered or not asked:
        return VerifyDecision(ok=True, reason="sandbox_metrics_ready")

    # Gap: asked metric missing. If missing_column looks real and we have not replanned, retry once.
    replanned = bool(state.get("sandbox_replanned"))
    recoverable = []
    for item in missing:
        if not isinstance(item, dict):
            continue
        col = str(item.get("missing_column") or "")
        # Heuristic: non-empty column name that isn't a placeholder
        if col and col not in ("measure", "利润") and len(col) >= 1:
            recoverable.append(col)
        elif col:
            recoverable.append(col)

    # Also: asked id missing entirely with empty missing list — planner computed wrong op
    if not replanned and it < max_iter and still:
        feedback = (
            f"上次结果未覆盖指标 {still}。"
            "请用正确的 operation/filters/metrics 重算；"
            "若列不存在请写入 uncomputable/missing，不要用相邻汇总顶替。"
        )
        if recoverable:
            feedback += f" 相关列线索：{recoverable[:5]}"
        return VerifyDecision(
            ok=False,
            reason="沙箱指标未覆盖用户问题，补算 sandbox",
            next_tool="sandbox",
            mark_sandbox_replanned=True,
            sandbox_feedback=feedback,
        )

    # After replan still incomplete — answer with honesty flag
    return VerifyDecision(ok=True, reason="sandbox_metrics_partial")


def verify_state(state: dict[str, Any]) -> VerifyDecision:
    """Machine-checkable success criteria before answering."""
    message = state.get("message") or ""
    evidence = list(state.get("evidence") or [])
    last = state.get("next_tool")
    kb = [e for e in evidence if e.get("source_type") == "kb"]
    web = [e for e in evidence if e.get("source_type") == "web"]
    sandbox = [e for e in evidence if e.get("source_type") == "sandbox"]
    it = int(state.get("iteration") or 1)
    max_iter = int(state.get("max_iterations") or 4)
    kb_retried = bool(state.get("kb_retried"))
    web_retried = bool(state.get("web_retried"))
    pending = list(state.get("pending_tools") or [])

    if state.get("direct_answer"):
        return VerifyDecision(ok=True, reason="direct_answer_ready")

    # Sandbox round: require SUMMARY; then check metric coverage.
    if last == "sandbox":
        has_summary = sandbox and any(
            _SUMMARY_MARK in str(e.get("content") or "") or _SUMMARY_MARK in str(e.get("snippet") or "")
            for e in sandbox
        )
        if has_summary:
            cov = _metric_coverage_decision(state, sandbox=sandbox, it=it, max_iter=max_iter)
            if cov is not None:
                return cov
            return VerifyDecision(ok=True, reason="sandbox_summary_ready")
        if sandbox:
            # Attached without SUMMARY (legacy / error) — still allow answer path
            return VerifyDecision(ok=True, reason="sandbox_evidence_attached")
        if needs_sandbox := bool(
            state.get("needs_sandbox")
            or (sandbox_hint(message) and bool(state.get("has_tabular_docs")))
        ):
            if it < max_iter and "sandbox" not in pending:
                return VerifyDecision(
                    ok=False,
                    reason="表格分析缺少沙箱证据，补调 sandbox",
                    next_tool="sandbox",
                )
            return VerifyDecision(ok=False, reason="表格分析缺少沙箱证据")
        return VerifyDecision(ok=True, reason="sandbox_skipped")

    has_tabular = bool(state.get("has_tabular_docs"))
    needs_sandbox = bool(
        state.get("needs_sandbox") or (sandbox_hint(message) and has_tabular)
    )
    if needs_sandbox and not sandbox:
        if it < max_iter and "sandbox" not in pending:
            return VerifyDecision(
                ok=False,
                reason="表格分析缺少沙箱证据，补调 sandbox",
                next_tool="sandbox",
            )
        return VerifyDecision(ok=False, reason="表格分析缺少沙箱证据")

    if pending and it < max_iter:
        return VerifyDecision(ok=False, reason="queue_remaining", next_tool=pending[0])

    if last == "web" and it < max_iter and not web_retried:
        if not web or (state.get("needs_freshness") and web_is_stale(web)):
            return VerifyDecision(
                ok=False,
                reason="联网结果不足或过旧，重试 web",
                next_tool="web",
                mark_web_retried=True,
            )

    if last == "kb" and it < max_iter and not web and not kb_retried and not kb:
        return VerifyDecision(
            ok=False,
            reason="知识库未命中，重试 kb",
            next_tool="kb",
            mark_kb_retried=True,
        )

    if state.get("forced") and not kb:
        return VerifyDecision(ok=True, reason="forced_kb_empty")

    return VerifyDecision(ok=True, reason="evidence_sufficient")
