"""Calendar scheduling tool."""

from __future__ import annotations

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.services.calendar import (
    EventDraft,
    check_conflict,
    create_event,
    extract_event,
    fill_pending_calendar,
    format_missing_fields,
    is_calendar_cancel,
    serialize_event_card,
    suggest_next_slot,
)


def execute_calendar(ctx: ToolContext, _args: dict) -> ToolResult:
    steps = [f"第 {ctx.iteration} 轮：解析并安排日程"]
    pending = ctx.pending_calendar or None
    if pending and is_calendar_cancel(ctx.message):
        steps.append("用户取消了当前日程安排")
        return ToolResult(
            ok=True,
            thinking_steps=steps,
            direct_answer="已取消本次日程安排。",
            pending_calendar=None,
            update_pending_calendar=True,
            need_more=False,
            risk="medium",
        )
    draft = EventDraft.from_dict(pending) if pending else extract_event(ctx.message, ctx.history)
    if pending:
        draft = fill_pending_calendar(draft, ctx.message, ctx.history)
    if draft.missing_fields:
        reply = format_missing_fields(draft)
        steps.append("日程信息不完整，等待用户一次性补充剩余字段")
        return ToolResult(
            ok=True,
            thinking_steps=steps,
            direct_answer=reply,
            pending_calendar=draft.to_dict(),
            update_pending_calendar=True,
            need_more=False,
            risk="medium",
        )
    conflicts = check_conflict(ctx.db, ctx.user_id, draft.start_at, draft.end_at)
    if conflicts:
        first = conflicts[0]
        alt_start, alt_end = suggest_next_slot(first.start_at, first.end_at)
        reply = (
            f"该时段与「{first.title}」冲突，时间为 "
            f"{first.start_at:%Y-%m-%d %H:%M} - {first.end_at:%H:%M}。"
            f" 可考虑改到 {alt_start:%Y-%m-%d %H:%M} - {alt_end:%H:%M}。"
        )
        steps.append("检测到日程冲突，等待用户调整时间")
        return ToolResult(
            ok=True,
            thinking_steps=steps,
            direct_answer=reply,
            pending_calendar=draft.to_dict(),
            update_pending_calendar=True,
            need_more=False,
            risk="medium",
        )

    event = create_event(
        ctx.db,
        user_id=ctx.user_id,
        title=draft.title,
        start_at=draft.start_at,
        end_at=draft.end_at,
        participants=draft.participants,
    )
    card = serialize_event_card(event)
    steps.append("日程创建成功")
    evidence = [
        {
            "index": 0,
            "source_type": "calendar",
            "tool": "calendar",
            "filename": "本地日历",
            "title": event.title,
            "snippet": f"{event.start_at:%Y-%m-%d %H:%M} - {event.end_at:%H:%M}",
            "content": (
                f"已创建日程：{event.title}，时间 {event.start_at:%Y-%m-%d %H:%M}"
                f" - {event.end_at:%H:%M}。"
            ),
            "url": "",
        }
    ]
    return ToolResult(
        ok=True,
        evidence=evidence,
        thinking_steps=steps,
        schedule_card=card,
        direct_answer=(
            f"已为你安排「{event.title}」，时间 {event.start_at:%Y-%m-%d %H:%M} - {event.end_at:%H:%M}。"
        ),
        pending_calendar=None,
        update_pending_calendar=True,
        risk="medium",
    )


register(
    ToolSpec(
        name="calendar",
        risk="medium",
        execute=execute_calendar,
        description="Create or continue a local calendar event",
    )
)
