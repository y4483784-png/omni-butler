"""Stop and allow-list checks that run at execution, not at display time."""

from __future__ import annotations

from app.agents.harness.effects import EFFECTS
from app.services import task_run


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
    `stopped`. Status and the allow list come from a new session. The workflow
    session can still be holding the row as running after stop commits.
    """
    if task_id is None:
        return None
    if db is None:
        return "缺少任务，未执行。"
    try:
        turn = task_run.load_turn(user_id=user_id, task_id=task_id)
    except task_run.BadAllowList as exc:
        return f"本回合允许列表无效，未执行。generation={exc.generation}"
    if turn is None:
        return "任务不属于当前用户，未执行。"
    generation = turn.generation
    if turn.status == "stopped":
        return f"这一代已经停止，未执行。generation={generation}"
    if task_run.is_card_action(turn.allowed_tools):
        return f"这一代是卡片确认，不是工具名单，未执行。generation={generation}"
    if tool not in {str(item) for item in turn.allowed_tools}:
        return f"名称不在本回合允许列表，未执行。generation={generation}"
    return None
