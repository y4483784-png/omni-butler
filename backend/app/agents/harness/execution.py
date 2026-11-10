"""Stop and allow-list checks that run at execution, not at display time."""

from __future__ import annotations

import json

from app.models.models import TaskRun
from app.services.task_run import is_card_action

from app.agents.harness.effects import EFFECTS


def effect_denial(spec_effect: str, declared: str | None) -> str | None:
    if spec_effect not in EFFECTS:
        return "工具契约无效，未执行。"
    if declared is not None and declared != spec_effect:
        return "工具契约不一致，未执行。"
    return None


def execution_denial(db, *, user_id: int, task_id: int | None, tool: str) -> str | None:
    """Deny a stopped generation, or a name absent from that generation's list.

    A missing task id keeps today's callers working. This function does not
    change a TaskRun. `completed` stays `completed`, and `stopped` stays
    `stopped`.
    """
    if task_id is None:
        return None
    if db is None:
        return "缺少任务，未执行。"
    row = db.get(TaskRun, task_id)
    if row is None or row.user_id != user_id:
        return "任务不属于当前用户，未执行。"
    generation = row.generation
    if row.status == "stopped":
        return f"这一代已经停止，未执行。generation={generation}"
    if is_card_action(row.allowed_tools):
        return f"这一代是卡片确认，不是工具名单，未执行。generation={generation}"
    try:
        allowed = json.loads(row.allowed_tools or "")
    except json.JSONDecodeError:
        return f"本回合允许列表无效，未执行。generation={generation}"
    if not isinstance(allowed, list) or tool not in {str(item) for item in allowed}:
        return f"名称不在本回合允许列表，未执行。generation={generation}"
    return None
