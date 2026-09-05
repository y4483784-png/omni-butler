"""Session working-state JSON: evidence pointers + last analysis (offload metadata)."""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings


def load_working_state(session: Any) -> dict[str, Any]:
    raw = getattr(session, "working_state", None) or ""
    if not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def dump_working_state(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    return json.dumps(data, ensure_ascii=False)


def merge_working_state(
    existing: dict[str, Any] | None,
    *,
    document_ids: list[int] | None = None,
    citations: list[dict] | None = None,
    analysis_summary: dict | None = None,
    artifact: dict | None = None,
    compacted_turns: int | None = None,
) -> dict[str, Any]:
    """Update offload pointers; never store full evidence bodies."""
    ws = dict(existing or {})
    if document_ids:
        ids = []
        for x in document_ids:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        if ids:
            ws["last_document_ids"] = ids[:12]

    refs: list[dict[str, Any]] = []
    for c in citations or []:
        if not isinstance(c, dict):
            continue
        st = str(c.get("source_type") or "")
        if st in ("calendar", "sandbox"):
            continue
        refs.append(
            {
                "source_type": st or "kb",
                "filename": str(c.get("filename") or "")[:120],
                "title": str(c.get("title") or "")[:120],
                "heading": str(c.get("heading") or "")[:120],
                "page": c.get("page"),
                "url": str(c.get("url") or "")[:240],
            }
        )
        if len(refs) >= 6:
            break
    if refs:
        ws["last_evidence_refs"] = refs

    analysis: dict[str, Any] = {}
    if isinstance(analysis_summary, dict) and analysis_summary:
        metrics = analysis_summary.get("metrics")
        asked = analysis_summary.get("asked_ids") or analysis_summary.get("asked")
        if isinstance(metrics, list):
            analysis["metrics"] = metrics[:6]
        elif metrics is not None:
            analysis["metrics"] = [metrics]
        if isinstance(asked, list):
            analysis["asked_ids"] = [str(x) for x in asked[:12]]
        fn = analysis_summary.get("filename") or analysis_summary.get("title")
        if fn:
            analysis["filename"] = str(fn)[:120]
    if isinstance(artifact, dict) and artifact.get("title"):
        analysis.setdefault("filename", str(artifact.get("title") or "")[:120])
        analysis["artifact_kind"] = str(artifact.get("kind") or "")[:40]
    if analysis:
        ws["last_analysis"] = analysis

    if compacted_turns is not None:
        ws["compacted_turns"] = max(0, int(compacted_turns))
    return ws


def format_working_block(
    pending_calendar: dict | None,
    ws: dict[str, Any] | None,
    *,
    max_chars: int | None = None,
) -> str:
    budget = int(
        max_chars
        if max_chars is not None
        else settings.context_working_state_max_chars
    )
    if budget <= 0:
        return ""
    lines: list[str] = []
    if isinstance(pending_calendar, dict) and pending_calendar:
        title = str(pending_calendar.get("title") or "未命名日程").strip()
        missing = pending_calendar.get("missing_fields") or []
        miss_s = "、".join(str(x) for x in missing[:5]) if missing else "无"
        lines.append(f"- 未完成日程草稿：{title}（待补：{miss_s}）")

    data = ws or {}
    docs = data.get("last_document_ids") or []
    if docs:
        lines.append(f"- 最近选用文档 id：{', '.join(str(x) for x in docs[:8])}")

    refs = data.get("last_evidence_refs") or []
    if refs:
        bits = []
        for r in refs[:4]:
            if not isinstance(r, dict):
                continue
            name = r.get("filename") or r.get("title") or r.get("source_type") or "证据"
            bits.append(str(name)[:40])
        if bits:
            lines.append(f"- 近期证据指针：{'；'.join(bits)}")

    analysis = data.get("last_analysis") if isinstance(data.get("last_analysis"), dict) else {}
    if analysis:
        fn = analysis.get("filename") or "表格分析"
        asked = analysis.get("asked_ids") or []
        asked_s = "、".join(str(x) for x in asked[:4]) if asked else ""
        extra = f"（指标：{asked_s}）" if asked_s else ""
        lines.append(f"- 最近数据分析：{fn}{extra}")

    if not lines:
        return ""
    header = "【当前工作状态】以下为未完成任务与证据指针，回答时参考，勿逐条复述："
    out = [header]
    used = len(header)
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        out.append(line)
        used += len(line) + 1
    if len(out) == 1:
        return ""
    return "\n".join(out)


def format_context_line(
    pending_calendar: dict | None,
    ws: dict[str, Any] | None,
    *,
    max_chars: int = 200,
) -> str:
    """One-line working hint for the router prompt."""
    parts: list[str] = []
    if isinstance(pending_calendar, dict) and pending_calendar:
        title = str(pending_calendar.get("title") or "日程").strip() or "日程"
        parts.append(f"待补日程「{title}」")
    data = ws or {}
    analysis = data.get("last_analysis") if isinstance(data.get("last_analysis"), dict) else {}
    if analysis.get("filename"):
        parts.append(f"刚分析过「{analysis['filename']}」")
    docs = data.get("last_document_ids") or []
    if docs and not analysis:
        parts.append(f"选用文档{docs[0]}")
    if not parts:
        return ""
    text = "；".join(parts)
    if max_chars and len(text) > max_chars:
        return text[:max_chars]
    return text
