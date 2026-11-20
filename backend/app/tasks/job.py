from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy import update

from app.core.config import settings
from app.core.db import SessionLocal, maintenance_session, set_rls_context
from app.core.generation_stop import bind, is_stopped, reset
from app.models.models import Message, TaskRun, TaskStep, ToolCall
from app.services import task_run
from app.tasks.celery_app import celery_app

_executor: Callable | None = None
_CALENDAR_READS = frozenset(
    {
        "list",
        "suggest",
        "prep",
        "followup",
        "calendar.list",
        "calendar.suggest",
        "calendar.prep",
        "calendar.followup",
    }
)


def set_executor(executor: Callable | None) -> None:
    global _executor
    _executor = executor


def commit_job_output(
    db,
    *,
    user_id: int,
    session_id: int,
    generation: int,
    message_id: int | None,
    content: str,
    now: datetime,
    previous_commit_at: datetime | None,
    newer: bool,
    call_ended: bool,
    exec_token: str,
    exec_epoch: int,
    intent_version: int,
) -> tuple[int | None, datetime | None] | None:
    due = task_run.output_commit_due(
        have_output=bool(content),
        newer=newer,
        previous_commit_at=previous_commit_at,
        now=now,
        call_ended=call_ended,
    )
    if not due:
        return message_id, previous_commit_at
    held = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == int(user_id))
        .where(TaskRun.generation == int(generation))
        .where(TaskRun.kind == "job")
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == exec_token)
        .where(TaskRun.exec_epoch == int(exec_epoch))
        .where(TaskRun.intent_version == int(intent_version))
        .values(exec_token=exec_token)
    )
    if int(held.rowcount or 0) != 1:
        db.rollback()
        return None
    if message_id is None:
        row = Message(
            session_id=int(session_id),
            role="assistant",
            content=content,
            reasoning=None,
            citations="",
            schedule_card="",
            artifact="",
        )
        db.add(row)
        db.flush()
        message_id = int(row.id)
    else:
        row = db.get(Message, message_id)
        row.content = content
    task_run.touch_progress(
        db,
        user_id=int(user_id),
        generation=int(generation),
        now=now,
    )
    from app.services.resume import publish_event

    publish_event(
        db,
        user_id=int(user_id),
        session_id=int(session_id),
        kind="message",
        body=content or "",
    )
    db.commit()
    return message_id, now


def _loop_enabled() -> bool:
    return _executor is not None or bool(settings.llm_api_key)


def _start(
    db,
    *,
    user_id: int,
    session_id: int,
    generation: int,
    goal: str,
    exec_token: str,
    exec_epoch: int,
    intent_version: int,
) -> tuple[int | None, datetime | None] | None:
    text = (goal or "").strip()
    return commit_job_output(
        db,
        user_id=int(user_id),
        session_id=int(session_id),
        generation=int(generation),
        message_id=None,
        content=f"已开始：{text}" if text else "已开始。",
        now=datetime.now(timezone.utc),
        previous_commit_at=None,
        newer=True,
        call_ended=True,
        exec_token=exec_token,
        exec_epoch=exec_epoch,
        intent_version=intent_version,
    )


@celery_app.task(name="app.tasks.job.run_job", queue="default")
def run_job(
    task_run_id: int,
    dispatch_token: str,
    dispatch_epoch: int,
    intent_version: int,
) -> None:
    user_id = _task_user_id(int(task_run_id))
    if user_id is None:
        return
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        from app.services.resume import adopt

        pre = db.get(TaskRun, int(task_run_id))
        if pre is None or pre.kind != "job":
            db.rollback()
            return
        worker = uuid4().hex
        taken = adopt(
            db,
            task_run_id=int(task_run_id),
            token=str(dispatch_token),
            epoch=int(dispatch_epoch),
            intent_version=int(intent_version),
            worker=worker,
        )
        if taken != 1:
            db.rollback()
            return
        prior_epoch = int(pre.exec_epoch or 0)
        db.refresh(pre)
        held_token = str(pre.exec_token or "")
        held_epoch = int(pre.exec_epoch or 0)
        held_intent = int(pre.intent_version or 1)
        if held_token != worker or held_epoch <= prior_epoch:
            db.rollback()
            return
        goal = pre.goal or ""
        session_id = int(pre.session_id)
        generation = int(pre.generation)
        task_id = int(pre.id)
        last_progress_at = pre.last_progress_at
        db.commit()
        token = bind(user_id=int(user_id), generation=generation)
        try:
            if _latest_step(db, task_id) is None and last_progress_at is None:
                started = _start(
                    db,
                    user_id=int(user_id),
                    session_id=session_id,
                    generation=generation,
                    goal=goal,
                    exec_token=held_token,
                    exec_epoch=held_epoch,
                    intent_version=held_intent,
                )
                if started is None:
                    return
            if not _loop_enabled():
                return
            _drive(
                db,
                user_id=int(user_id),
                task_id=task_id,
                worker=held_token,
                exec_epoch=held_epoch,
                intent_version=held_intent,
            )
        finally:
            reset(token)
    finally:
        db.close()


def _task_user_id(task_run_id: int) -> int | None:
    db = maintenance_session()
    try:
        row = db.get(TaskRun, int(task_run_id))
        if row is None or row.user_id is None:
            return None
        return int(row.user_id)
    finally:
        db.close()


def _latest_step(db, task_id: int):
    return (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == int(task_id))
        .order_by(TaskStep.seq.desc())
        .first()
    )


class _HoldPulse:
    def __init__(self, hold) -> None:
        self._hold = hold
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hold-pulse", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def lost(self) -> bool:
        return self._lost.is_set()

    def _run(self) -> None:
        interval = float(settings.llm_timeout) / 2
        while not self._stop.wait(interval):
            db = SessionLocal()
            try:
                set_rls_context(db, int(self._hold.user_id))
                taken = task_run.renew_hold(
                    db,
                    user_id=int(self._hold.user_id),
                    generation=int(self._hold.generation),
                    exec_token=str(self._hold.exec_token),
                    exec_epoch=int(self._hold.exec_epoch),
                    intent_version=int(self._hold.intent_version),
                )
            except Exception:
                db.rollback()
                continue
            finally:
                db.close()
            if taken != 1:
                self._lost.set()
                return


def _drive(
    db,
    *,
    user_id: int,
    task_id: int,
    worker: str,
    exec_epoch: int,
    intent_version: int,
) -> None:
    from app.services.loop_round import Hold

    job = db.get(TaskRun, task_id)
    db.refresh(job)
    if str(job.exec_token or "") != worker or int(job.exec_epoch or 0) != int(exec_epoch):
        return
    if int(job.intent_version or 1) != int(intent_version):
        return
    if _leave_before_model(job):
        return
    hold = Hold(
        user_id=int(user_id),
        generation=int(job.generation),
        exec_token=worker,
        exec_epoch=int(exec_epoch),
        intent_version=int(intent_version),
    )
    pulse = _HoldPulse(hold)
    pulse.start()
    try:
        _drive_held(db, job, hold, pulse)
    finally:
        pulse.stop()


def _drive_held(db, job: TaskRun, hold, pulse: "_HoldPulse") -> None:
    from app.services.loop_round import Phase, save_turn

    while True:
        if is_stopped() or pulse.lost():
            return
        if _renew(db, hold) != 1:
            return
        db.refresh(job)
        if _leave_before_model(job):
            return
        step = _latest_step(db, job.id)
        if step is not None and step.phase == Phase.FINAL_ANSWER.value:
            _complete_saved_answer(db, hold)
            return
        if step is None or step.phase == Phase.RESULTS_READY.value:
            asked = None if step is None else int(step.seq)
            produced, message_id = _request(db, job, hold, pulse)
            if produced is None or is_stopped():
                return
            saved = save_turn(db, hold=hold, turn=produced, message_id=message_id)
            if saved is None or saved.phase is Phase.FINAL_ANSWER:
                return
            if asked is not None and saved.seq == asked:
                return
            continue
        drafts: dict[str, dict] = {}
        while True:
            action = _one_pass(db, hold, step.seq, job, drafts, pulse)
            if action == "again":
                continue
            if action == "model":
                break
            return


def _leave_before_model(job: TaskRun) -> bool:
    if job.status in {"stopped", "completed"}:
        return True
    return job.status == "waiting_person" and not bool(job.continuation_due)


def _complete_saved_answer(db, hold) -> None:
    from sqlalchemy import update

    from app.services.loop_round import _leave

    matched = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == hold.user_id)
        .where(TaskRun.generation == hold.generation)
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == hold.exec_token)
        .where(TaskRun.exec_epoch == hold.exec_epoch)
        .where(TaskRun.intent_version == hold.intent_version)
        .values(continuation_due=False)
    )
    if int(matched.rowcount or 0) != 1 or _leave(db, hold, status="completed") != 1:
        db.rollback()
        return
    db.commit()


def _renew(db, hold) -> int:
    return task_run.renew_hold(
        db,
        user_id=hold.user_id,
        generation=hold.generation,
        exec_token=hold.exec_token,
        exec_epoch=hold.exec_epoch,
        intent_version=hold.intent_version,
    )


def _request(db, job: TaskRun, hold, pulse: "_HoldPulse"):
    from app.agents.harness.constitution import load_constitution
    from app.core.llm import _build_job_request, assert_body_within_limit, resolve_executor
    from app.services.loop_round import job_tools
    from app.services.task_images import load_image_blocks
    from app.storage.factory import get_blob_store

    db.refresh(job)
    loaded = load_image_blocks(
        db,
        user_id=int(job.user_id),
        task_run_id=int(job.id),
        store=get_blob_store(),
    )
    messages = _transcript(db, job, loaded.blocks if loaded.kind != "hidden" else ())
    tools = job_tools(constitution=load_constitution(), web_constraint=job.web_constraint or "allow")
    assert_body_within_limit(_build_job_request(resolve_executor(), messages, stream=False, tools=tools))
    db.commit()
    if pulse.lost():
        return None, None
    if _executor is not None:
        return _executor(messages, tools), None
    streamed = asyncio.run(_stream_turn(db, job, messages, tools, hold, pulse))
    if streamed is None:
        return None, None
    return streamed


def _transcript(db, job: TaskRun, blocks) -> list[dict]:
    from app.services.loop_round import Phase, stored_reasoning

    goal = job.goal or ""
    if blocks:
        content: str | list = [{"type": "text", "text": goal}, *list(blocks)]
    else:
        content = goal
    messages: list[dict] = [{"role": "user", "content": content}]
    steps = (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == job.id)
        .order_by(TaskStep.seq.asc())
        .all()
    )
    for step in steps:
        if step.phase != Phase.RESULTS_READY.value:
            continue
        calls = (
            db.query(ToolCall)
            .filter(ToolCall.task_step_id == step.id)
            .order_by(ToolCall.position.asc())
            .all()
        )
        from app.core.llm import assistant_tool_message

        messages.append(
            assistant_tool_message(
                content=step.content or "",
                reasoning=stored_reasoning(step),
                calls=[
                    {
                        "id": call.tool_call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments or "{}"},
                    }
                    for call in calls
                ],
            )
        )
        for call in calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.tool_call_id,
                    "content": call.result or "",
                }
            )
    return messages


async def _stream_turn(db, job: TaskRun, messages: list[dict], tools: list[dict], hold, pulse: "_HoldPulse"):
    from app.core.llm import (
        AnswerDelta,
        ModelTurn,
        ReasoningDelta,
        Role,
        ToolCallDelta,
        ToolCallSpec,
        ToolRoundEnd,
        stream_chat,
    )

    content: list[str] = []
    reasoning: list[str] = []
    fragments: dict[int, dict[str, str]] = {}
    finish = "stop"
    message_id = None
    previous = None

    async for item in stream_chat(messages, role=Role.CHAT, tools=tools, job=True):
        if pulse.lost():
            return None
        if isinstance(item, ReasoningDelta):
            reasoning.append(item.text)
            continue
        if isinstance(item, AnswerDelta):
            content.append(item.text)
            stored = commit_job_output(
                db,
                user_id=int(job.user_id),
                session_id=int(job.session_id),
                generation=int(job.generation),
                message_id=message_id,
                content="".join(content),
                now=datetime.now(timezone.utc),
                previous_commit_at=previous,
                newer=True,
                call_ended=False,
                exec_token=hold.exec_token,
                exec_epoch=hold.exec_epoch,
                intent_version=hold.intent_version,
            )
            if stored is None:
                return None
            message_id, previous = stored
            continue
        if isinstance(item, ToolCallDelta):
            slot = fragments.setdefault(item.index, {"id": "", "name": "", "arguments": ""})
            if item.id:
                slot["id"] = item.id
            if item.name:
                slot["name"] = item.name
            slot["arguments"] += item.arguments
            continue
        if isinstance(item, ToolRoundEnd):
            finish = item.finish
    if pulse.lost():
        return None
    if content:
        stored = commit_job_output(
            db,
            user_id=int(job.user_id),
            session_id=int(job.session_id),
            generation=int(job.generation),
            message_id=message_id,
            content="".join(content),
            now=datetime.now(timezone.utc),
            previous_commit_at=previous,
            newer=True,
            call_ended=True,
            exec_token=hold.exec_token,
            exec_epoch=hold.exec_epoch,
            intent_version=hold.intent_version,
        )
        if stored is None:
            return None
        message_id, _previous = stored
    specs = tuple(
        ToolCallSpec(slot["id"], slot["name"], slot["arguments"])
        for _index, slot in sorted(fragments.items())
    )
    return ModelTurn("".join(content).strip(), "".join(reasoning), specs, finish), message_id


def _remote_marker(db, row: ToolCall) -> str:
    token = (row.claim_token or "").strip()
    if not token:
        return ""
    from app.models.models import ScheduleConfirmClaim

    claim = db.get(ScheduleConfirmClaim, token)
    if claim is None:
        return ""
    return (claim.remote_request_id or "").strip()


def _lookup_armed(db, hold, row: ToolCall) -> str:
    before = row.state
    from app.services.loop_round import note_unknown

    note_unknown(db, hold=hold, tool_call_id=row.tool_call_id)
    fresh = db.get(ToolCall, row.id)
    if fresh is None or fresh.state == before:
        return "leave"
    step = db.get(TaskStep, fresh.task_step_id)
    if step is not None and step.phase == "results_ready":
        return "model"
    return "again"


def _one_pass(db, hold, seq: int, job: TaskRun, drafts: dict[str, dict], pulse: "_HoldPulse") -> str:
    from app.services.loop_round import TERMINAL, CallState, Phase, begin_read, claim_call, observe_ready, open_cards, save_read_result

    step = (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == job.id, TaskStep.seq == seq)
        .one()
    )
    rows = (
        db.query(ToolCall)
        .filter(ToolCall.task_step_id == step.id)
        .order_by(ToolCall.position.asc())
        .all()
    )
    for row in rows:
        if CallState(row.state) in TERMINAL:
            continue
        if _remote_marker(db, row):
            if row.name in {"desktop.open", "desktop.run"}:
                from app.services.loop_round import lookup_desktop_write

                return lookup_desktop_write(db, hold=hold, tool_call_id=row.tool_call_id)
            return _lookup_armed(db, hold, row)
        rejection = _reject_uninvoked(row)
        if rejection is not None:
            return _stored(db, hold, row.tool_call_id, rejection, rejected=True)
        arguments = _load(row.arguments)
        if claim_call(row.name, arguments):
            continue
        if row.state == CallState.RUNNING.value and row.tool_call_id in drafts:
            continue
        if row.state not in {CallState.PENDING.value, CallState.RUNNING.value}:
            continue
        if is_stopped() or pulse.lost():
            return "leave"
        if _renew(db, hold) != 1:
            return "leave"
        outcome = begin_read(db, hold=hold, tool_call_id=row.tool_call_id)
        if outcome == "stale":
            return "leave"
        if outcome == "denied":
            fresh = db.get(TaskStep, step.id)
            db.refresh(fresh)
            if fresh.phase == Phase.RESULTS_READY.value:
                return "model"
            return "again"
        db.commit()
        try:
            result = _invoke(db, job, row)
        except Exception as exc:
            if pulse.lost():
                return "leave"
            return _stored(db, hold, row.tool_call_id, f"{type(exc).__name__}: {exc}", rejected=True)
        if pulse.lost():
            return "leave"
        card = result.schedule_card if isinstance(result.schedule_card, dict) else None
        if card is not None:
            from app.services.schedule_confirm import schedule_card_needs_claim

            if schedule_card_needs_claim(card):
                drafts[row.tool_call_id] = card
                return "again"
        if result.denied:
            return _stored(db, hold, row.tool_call_id, result.deny_reason or "未执行", rejected=True)
        return _stored(db, hold, row.tool_call_id, _result_text(result), rejected=False)
    pending_claims = [
        row
        for row in rows
        if row.state == CallState.PENDING.value and claim_call(row.name, _load(row.arguments))
    ]
    if pending_claims or drafts:
        if is_stopped():
            return "leave"
        open_cards(db, hold=hold, step_id=step.id, drafts=drafts or None)
        return "leave"
    if any(CallState(row.state) not in TERMINAL for row in rows):
        return "leave"
    if observe_ready(db, hold=hold, step_id=step.id):
        return "model"
    return "leave"


def _stored(db, hold, tool_call_id: str, result: str, *, rejected: bool) -> str:
    from app.services.loop_round import save_read_result

    _step, entered = save_read_result(
        db,
        hold=hold,
        tool_call_id=tool_call_id,
        result=result,
        rejected=rejected,
    )
    return "model" if entered else "again"


def _reject_uninvoked(row: ToolCall) -> str | None:
    from app.services.loop_round import claim_call

    if row.name in {"desktop.open", "desktop.run"}:
        if (row.claim_token or "").strip():
            return None
        if type(_load(row.arguments).get("device_id")) is not int:
            return "缺少设备，未打开。"
        return None
    if row.name == "errand.place" and (row.arguments or "").strip() in {"", "{}"}:
        return "订单无效，未执行。"
    if row.name != "calendar":
        return None
    arguments = _load(row.arguments)
    if claim_call(row.name, arguments):
        return None
    action = str(arguments.get("action") or "").strip()
    if action in _CALENDAR_READS:
        return None
    return "未知的日历动作，未执行。"


def _invoke(db, job: TaskRun, row: ToolCall):
    from app.agents.harness.gateway import invoke
    from app.agents.harness.types import ToolContext

    return invoke(
        row.name,
        ToolContext(
            db=db,
            message=job.goal or "",
            history=[],
            user_id=int(job.user_id),
            task_id=int(job.id),
            generation=int(job.generation),
            require_task=True,
        ),
        _load(row.arguments),
    )


def _result_text(result) -> str:
    answer = getattr(result, "direct_answer", None)
    if isinstance(answer, str) and answer.strip():
        return answer
    blocks: list[str] = []
    for item in getattr(result, "evidence", None) or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url:
            title = item.get("title")
            label = title if isinstance(title, str) and title else url
            lines = [f"[{label}]({url})"]
            for key in ("snippet", "summary", "content"):
                text = item.get(key)
                if isinstance(text, str) and text:
                    lines.append(text)
            blocks.append("\n".join(lines))
            continue
        text = item.get("snippet") or item.get("content") or item.get("title") or ""
        if text:
            blocks.append(str(text))
    if blocks:
        return "\n\n".join(blocks)
    card = getattr(result, "schedule_card", None)
    if isinstance(card, dict):
        title = card.get("title") or card.get("mode") or ""
        if title:
            return str(title)
    return ""


def _load(raw: str | None) -> dict:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
