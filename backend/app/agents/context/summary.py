"""Incremental session-summary compaction for ContextManager."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.context.window import recent_turns, to_logical_turns
from app.agents.context.working_state import (
    dump_working_state,
    load_working_state,
    merge_working_state,
)
from app.core.config import settings
from app.core.llm import complete_json, resolved_planner_model
from app.models.models import Message
from app.models.models import Session as ChatSession

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = """你是办公助手的会话摘要器。只输出 JSON：{"summary":"一段中文摘要"}。
硬性要求：
1. 保留用户目标与约束、已确认事实与关键数字（可带来源文件名）、未完成任务、工具失败原因、涉及文档。
2. 禁止写成散文日记或逐轮复述；信息密度优先。
3. 在已有摘要基础上合并新溢出轮次；矛盾时以更新内容为准。
4. 摘要不超过指定字数；没有可保留事实则输出较短摘要，不要编造。"""


def invalidate_summary_if_needed(session: ChatSession, max_message_id: int) -> bool:
    """Clear summary when regenerate deleted messages the summary still covered.

    Returns True if the summary was cleared.
    """
    upto = int(getattr(session, "context_summary_upto_message_id", 0) or 0)
    mid = max(0, int(max_message_id or 0))
    if upto > mid:
        session.context_summary = ""
        session.context_summary_upto_message_id = 0
        return True
    return False


def summarize_incremental(
    prev_summary: str,
    overflow_msgs: list[dict],
    *,
    max_chars: int | None = None,
) -> str | None:
    """LLM-compress overflow turns into an updated summary. None = failure."""
    if not settings.context_summary_enabled:
        return None
    if not overflow_msgs:
        return (prev_summary or "").strip() or None
    budget = int(max_chars if max_chars is not None else settings.context_summary_max_chars)
    lines = []
    for m in overflow_msgs:
        role = "用户" if m.get("role") == "user" else "助手"
        content = str(m.get("content") or "")[:600]
        lines.append(f"{role}：{content}")
    transcript = "\n".join(lines)
    parsed = complete_json(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"摘要上限约 {budget} 字。\n"
                    f"已有摘要：\n{(prev_summary or '').strip() or '（无）'}\n\n"
                    f"新溢出对话：\n{transcript}"
                ),
            },
        ],
        model=resolved_planner_model(),
    )
    if not isinstance(parsed, dict):
        return None
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        return None
    if len(summary) > budget:
        summary = summary[:budget]
    return summary


def refresh_session_context(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    citations: list[dict] | None = None,
    document_ids: list[int] | None = None,
    analysis_summary: dict | None = None,
    artifact: dict | None = None,
) -> None:
    """Async-safe: refresh summary for overflow turns and merge working_state."""
    try:
        row = db.get(ChatSession, session_id)
        if row is None:
            return
        msgs = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.id)
            .all()
        )
        history = [{"role": m.role, "content": m.content or "", "id": m.id} for m in msgs]
        turns = to_logical_turns(history)
        kept, overflow = recent_turns(turns, int(settings.max_context_turns))
        upto = int(getattr(row, "context_summary_upto_message_id", 0) or 0)

        # Only summarize overflow turns that include messages newer than upto.
        new_overflow: list[dict] = []
        max_id_in_overflow = upto
        for turn in overflow:
            for m in turn:
                mid = int(m.get("id") or 0)
                if mid > upto:
                    new_overflow.append({"role": m.get("role"), "content": m.get("content")})
                    if mid > max_id_in_overflow:
                        max_id_in_overflow = mid

        min_turns = max(1, int(settings.context_summary_min_overflow_turns))
        overflow_turn_count = len(overflow)
        if (
            settings.context_summary_enabled
            and settings.context_manager_enabled
            and overflow_turn_count >= min_turns
            and new_overflow
        ):
            updated = summarize_incremental(row.context_summary or "", new_overflow)
            if updated is not None:
                row.context_summary = updated
                # Mark coverage through the newest message still outside the window,
                # or the newest overflow id we actually fed in.
                covered = max_id_in_overflow
                if kept:
                    # Also advance past any messages that are still in overflow list
                    for turn in overflow:
                        for m in turn:
                            mid = int(m.get("id") or 0)
                            if mid > covered:
                                covered = mid
                row.context_summary_upto_message_id = covered

        ws = merge_working_state(
            load_working_state(row),
            document_ids=document_ids,
            citations=citations,
            analysis_summary=analysis_summary,
            artifact=artifact,
            compacted_turns=overflow_turn_count,
        )
        row.working_state = dump_working_state(ws)
        db.commit()
    except Exception:
        logger.exception("refresh_session_context failed session_id=%s", session_id)
        try:
            db.rollback()
        except Exception:
            pass
