from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from sqlalchemy import and_, exists, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.llm import ModelTurn, ToolCallSpec
from app.models.models import (
    Message,
    ScheduleConfirmClaim,
    TaskRun,
    TaskStep,
    ToolCall,
)
from app.services import task_run

WEB_DENIAL = "这一代禁止检索，未执行。"
_WEB = frozenset({"web", "web_fetch"})
_READ_NAMES = frozenset({"kb", "web", "web_fetch", "sandbox"})
_SECRET_KEYS = frozenset(
    {
        "password",
        "pan",
        "cvv",
        "cookie",
        "secret",
        "token",
        "pay_secret",
        "access_token",
        "refresh_token",
    }
)
CONFIRM_ONLY = ("mail.send", "errand.place", "desktop.open", "desktop.run")
_LOOKUP_WRITES = frozenset({*CONFIRM_ONLY, "calendar"})
_CALENDAR_WRITES = frozenset(
    {
        "create",
        "update",
        "cancel",
        "delete",
        "rsvp",
        "choose",
        "calendar.create",
        "calendar.update",
        "calendar.cancel",
        "calendar.delete",
        "calendar.rsvp",
        "calendar.choose",
    }
)
_OUTCOME_TEXT = {"confirm": "已确认", "reject": "已拒绝", "expire": "已过期"}


class Phase(str, Enum):
    CALLS_SAVED = "calls_saved"
    AWAITING_RESULTS = "awaiting_results"
    RESULTS_READY = "results_ready"
    FINAL_ANSWER = "final_answer"


class CallState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRM = "awaiting_confirm"
    RESULT = "result"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL = frozenset(
    {
        CallState.RESULT,
        CallState.REJECTED,
        CallState.EXPIRED,
        CallState.CANCELLED,
        CallState.UNKNOWN,
    }
)


class LoopEvent(str, Enum):
    SAVE_CALLS = "save_calls"
    SAVE_ANSWER = "save_answer"
    START_READ = "start_read"
    SAVE_RESULT = "save_result"
    DENY = "deny"
    OPEN_CARDS = "open_cards"
    CONFIRM = "confirm"
    REJECT = "reject"
    EXPIRE = "expire"
    CANCEL_CALL = "cancel_call"
    NOTE_UNKNOWN = "note_unknown"


class IllegalPhase(Exception):
    pass


_EDGES: dict[tuple[CallState, LoopEvent], CallState] = {
    (CallState.PENDING, LoopEvent.START_READ): CallState.RUNNING,
    (CallState.PENDING, LoopEvent.DENY): CallState.REJECTED,
    (CallState.PENDING, LoopEvent.OPEN_CARDS): CallState.AWAITING_CONFIRM,
    (CallState.RUNNING, LoopEvent.SAVE_RESULT): CallState.RESULT,
    (CallState.RUNNING, LoopEvent.DENY): CallState.REJECTED,
    (CallState.RUNNING, LoopEvent.OPEN_CARDS): CallState.AWAITING_CONFIRM,
    (CallState.AWAITING_CONFIRM, LoopEvent.CONFIRM): CallState.RESULT,
    (CallState.AWAITING_CONFIRM, LoopEvent.REJECT): CallState.REJECTED,
    (CallState.AWAITING_CONFIRM, LoopEvent.EXPIRE): CallState.EXPIRED,
    (CallState.AWAITING_CONFIRM, LoopEvent.NOTE_UNKNOWN): CallState.UNKNOWN,
    (CallState.PENDING, LoopEvent.CANCEL_CALL): CallState.CANCELLED,
    (CallState.RUNNING, LoopEvent.CANCEL_CALL): CallState.CANCELLED,
    (CallState.AWAITING_CONFIRM, LoopEvent.CANCEL_CALL): CallState.CANCELLED,
}


@dataclass(frozen=True)
class Call:
    tool_call_id: str
    name: str
    state: CallState
    claim_token: str | None
    intent_version: int


@dataclass(frozen=True)
class Step:
    seq: int
    phase: Phase
    calls: tuple[Call, ...]


@dataclass(frozen=True)
class Hold:
    user_id: int
    generation: int
    exec_token: str
    exec_epoch: int
    intent_version: int


@dataclass(frozen=True)
class JobEffect:
    status: str | None
    clear_hold: bool
    continuation_due: bool | None


def phase_after(calls: tuple[Call, ...]) -> Phase:
    if not calls:
        return Phase.FINAL_ANSWER
    states = tuple(call.state for call in calls)
    if all(state is CallState.PENDING for state in states):
        return Phase.CALLS_SAVED
    if all(state in TERMINAL for state in states):
        return Phase.RESULTS_READY
    return Phase.AWAITING_RESULTS


def apply(
    step: Step | None,
    event: LoopEvent,
    *,
    call_id: str | None = None,
    calls: tuple[Call, ...] | None = None,
    seq: int | None = None,
) -> Step:
    if event is LoopEvent.SAVE_CALLS:
        if step is not None or not calls:
            raise IllegalPhase()
        if any(call.state is not CallState.PENDING for call in calls):
            raise IllegalPhase()
        return Step(seq=int(seq or 1), phase=phase_after(calls), calls=calls)
    if event is LoopEvent.SAVE_ANSWER:
        if step is not None or calls:
            raise IllegalPhase()
        return Step(seq=int(seq or 1), phase=Phase.FINAL_ANSWER, calls=())
    if step is None or call_id is None:
        raise IllegalPhase()
    if event is LoopEvent.OPEN_CARDS and _read_still_open(step, call_id):
        raise IllegalPhase()
    updated: list[Call] = []
    matched = False
    for call in step.calls:
        if call.tool_call_id != call_id:
            updated.append(call)
            continue
        if matched:
            raise IllegalPhase()
        matched = True
        target = _EDGES.get((call.state, event))
        if target is None:
            raise IllegalPhase()
        updated.append(replace(call, state=target))
    if not matched:
        raise IllegalPhase()
    new_calls = tuple(updated)
    return Step(seq=step.seq, phase=phase_after(new_calls), calls=new_calls)


def job_effect(step: Step, *, status: str, continuation_due: bool) -> JobEffect:
    if step.phase is Phase.FINAL_ANSWER:
        return JobEffect(status="completed", clear_hold=True, continuation_due=False)
    if step.phase is Phase.RESULTS_READY and status == "running":
        return JobEffect(
            status=None,
            clear_hold=False,
            continuation_due=False if continuation_due else None,
        )
    if step.phase is Phase.RESULTS_READY and status == "waiting_person":
        return JobEffect(status=None, clear_hold=False, continuation_due=True)
    if step.phase is Phase.AWAITING_RESULTS and status == "waiting_person":
        return JobEffect(status=None, clear_hold=False, continuation_due=False)
    return JobEffect(status=None, clear_hold=False, continuation_due=None)


def stored_reasoning(step: TaskStep) -> str:
    return step.reasoning_content or ""


def job_tools(*, constitution: dict, web_constraint: str) -> list[dict]:
    from app.agents.tools.registry import ensure_builtin_tools, get_tool

    ensure_builtin_tools()
    names = [str(item) for item in (constitution.get("allowed_tools") or [])]
    names.extend(CONFIRM_ONLY)
    if web_constraint == "forbid":
        names = [name for name in names if name not in _WEB]
    descriptions = {
        "mail.send": "Prepare a mail message. It is not sent until the person confirms.",
        "errand.place": "Prepare an errand order. It is not placed until the person confirms.",
        "desktop.open": "Prepare a desktop open. It does not open until the person confirms.",
        "desktop.run": "Prepare a desktop run. It does not run until the person confirms.",
    }
    listed = []
    for name in names:
        spec = get_tool(name)
        description = spec.description if spec is not None and spec.description else descriptions.get(name, name)
        parameters = spec.parameters if spec is not None and spec.parameters else {"type": "object"}
        listed.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
    return listed


def claim_call(name: str, arguments: dict | None) -> bool:
    if name in CONFIRM_ONLY:
        return True
    if name != "calendar":
        return False
    action = str((arguments or {}).get("action") or "").strip()
    return action in _CALENDAR_WRITES


def take_created_hold(db: Session, *, user_id: int, task_id: int, exec_token: str) -> int:
    from app.services.task_run import _hold_until

    result = db.execute(
        update(TaskRun)
        .where(TaskRun.id == int(task_id))
        .where(TaskRun.user_id == int(user_id))
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token.is_(None))
        .where(TaskRun.dispatch_token.is_(None))
        .where(TaskRun.continuation_due.is_(False))
        .values(
            exec_token=exec_token,
            exec_epoch=TaskRun.exec_epoch + 1,
            hold_until=_hold_until(),
        )
    )
    return int(result.rowcount or 0)


def save_turn(
    db: Session,
    *,
    hold: Hold,
    turn: ModelTurn,
    message_id: int | None = None,
) -> Step | None:
    job = _lock_holder(db, hold)
    if job is None:
        return _completed_step(db, hold)
    existing = _matching_step(db, job.id, turn)
    if existing is not None:
        db.rollback()
        return existing
    if not turn.tool_calls:
        return _save_final(db, job=job, hold=hold, turn=turn, message_id=message_id)
    try:
        stored = _stored_calls(turn, int(hold.intent_version))
        seq = _next_seq(db, job.id)
        step = apply(None, LoopEvent.SAVE_CALLS, calls=stored, seq=seq)
    except IllegalPhase:
        db.rollback()
        return None
    row = TaskStep(
        user_id=job.user_id,
        task_run_id=job.id,
        seq=step.seq,
        phase=step.phase.value,
        content=turn.content or "",
        reasoning_content=turn.reasoning if isinstance(turn.reasoning, str) else "",
    )
    db.add(row)
    db.flush()
    for index, spec in enumerate(turn.tool_calls):
        db.add(
            ToolCall(
                user_id=job.user_id,
                task_step_id=row.id,
                tool_call_id=spec.id,
                position=index,
                name=spec.name,
                arguments=_stored_arguments(spec.name, spec.arguments),
                state=CallState.PENDING.value,
                claim_token=None,
                intent_version=int(hold.intent_version),
                result=None,
            )
        )
    if (turn.content or "").strip():
        _write_answer(db, job.session_id, message_id, turn.content)
        _note(db, job, "message", turn.content)
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    if not _commit_job(db, job, kind="step", body=step.phase.value):
        return None
    return step


def begin_read(db: Session, *, hold: Hold, tool_call_id: str) -> Literal["invoke", "denied", "stale"]:
    job = _lock_holder(db, hold)
    if job is None:
        db.rollback()
        return "stale"
    found = _find_call(db, job.id, tool_call_id)
    if found is None:
        db.rollback()
        return "stale"
    step_row, call_row, memory = found
    if claim_call(call_row.name, _load_json(call_row.arguments)):
        db.rollback()
        return "stale"
    if call_row.name in _WEB and job.web_constraint != "allow":
        return _deny_web(db, job=job, hold=hold, step_row=step_row, call_row=call_row, memory=memory)
    if call_row.state == CallState.RUNNING.value:
        if not _commit_job(db, job):
            return "stale"
        return "invoke"
    if call_row.name in _WEB:
        changed = _cas_running(db, call_row, hold, require_allow=True)
    else:
        changed = _cas_running(db, call_row, hold, require_allow=False)
    if changed != 1:
        db.rollback()
        return "stale"
    try:
        step = apply(memory, LoopEvent.START_READ, call_id=tool_call_id)
    except IllegalPhase:
        db.rollback()
        return "stale"
    _store_phase(db, step_row, step.phase)
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    if not _commit_job(db, job, kind="step", body=step.phase.value):
        return "stale"
    return "invoke"


def save_read_result(
    db: Session,
    *,
    hold: Hold,
    tool_call_id: str,
    result: str,
    rejected: bool = False,
) -> tuple[Step | None, bool]:
    job = _lock_holder(db, hold)
    if job is None:
        db.rollback()
        return None, False
    found = _find_call(db, job.id, tool_call_id)
    if found is None:
        db.rollback()
        return None, False
    step_row, call_row, memory = found
    event = LoopEvent.DENY if rejected else LoopEvent.SAVE_RESULT
    try:
        step = apply(memory, event, call_id=tool_call_id)
    except IllegalPhase:
        db.rollback()
        return None, False
    target = _call_by_id(step, tool_call_id)
    call_row.state = target.state.value
    call_row.result = redact_result(result)
    entered = _store_phase(db, step_row, step.phase)
    _assistant_message(db, job.session_id, call_row.result or "")
    _note(db, job, "message", call_row.result or "")
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    if entered:
        _clear_due_if_set(db, hold)
    if not _commit_job(db, job, kind="step", body=step.phase.value):
        return None, False
    return step, entered


def open_cards(
    db: Session,
    *,
    hold: Hold,
    step_id: int,
    drafts: dict[str, dict] | None = None,
) -> tuple[str, ...]:
    preview = _job_for(db, hold)
    if preview is None:
        return _cards_already_open(db, hold, step_id)
    step_row = db.get(TaskStep, step_id)
    if step_row is None or step_row.task_run_id != preview.id:
        db.rollback()
        return ()
    rows = _call_rows(db, step_row.id)
    drafts = drafts or {}
    opening = [row for row in rows if _should_open(row, drafts)]
    if not opening:
        db.rollback()
        return ()
    opening_ids = {row.tool_call_id for row in opening}
    if any(row.tool_call_id not in opening_ids and row.state not in {state.value for state in TERMINAL} for row in rows):
        db.rollback()
        return ()
    from app.services.calendar import draft_expires_at
    from app.services.schedule_confirm import (
        ScheduleConfirmClosed,
        bind_claim,
        lock_open_claims,
        parse_card_expiry,
        write_fingerprint,
        write_parts,
    )

    try:
        keys = []
        for row in opening:
            raw = drafts.get(row.tool_call_id) or _load_json(row.arguments)
            action = _claim_action(row.name, raw)
            keys.append((action, write_fingerprint(write_parts(action, raw))))
        lock_open_claims(db, preview.user_id, keys)
    except (ScheduleConfirmClosed, ValueError):
        db.rollback()
        return ()
    job = _lock_holder(db, hold)
    if job is None:
        return _cards_already_open(db, hold, step_id)
    step_row = db.get(TaskStep, step_id)
    rows = _call_rows(db, step_row.id)
    opening = [row for row in rows if _should_open(row, drafts)]
    if {row.tool_call_id for row in opening} != opening_ids:
        db.rollback()
        return ()
    memory = _as_step(step_row, rows)
    tokens: list[str] = []
    try:
        expires_at = parse_card_expiry(draft_expires_at())
        for row in opening:
            raw = drafts.get(row.tool_call_id) or _load_json(row.arguments)
            action = _claim_action(row.name, raw)
            token, expiry = bind_claim(
                db,
                user_id=job.user_id,
                session_id=job.session_id,
                action=action,
                raw=raw,
                task_run_ids=[job.id],
                expires_at=expires_at,
            )
            card = _card_body(action, raw, token, expiry)
            _assistant_message(db, job.session_id, _visible(card), json.dumps(card, ensure_ascii=False))
            memory = apply(memory, LoopEvent.OPEN_CARDS, call_id=row.tool_call_id)
            row.state = CallState.AWAITING_CONFIRM.value
            row.claim_token = token
            row.intent_version = int(hold.intent_version)
            tokens.append(token)
        _store_phase(db, step_row, memory.phase)
    except (IllegalPhase, ScheduleConfirmClosed, ValueError):
        db.rollback()
        return ()
    db.execute(
        update(TaskRun)
        .where(TaskRun.id == job.id)
        .where(TaskRun.user_id == hold.user_id)
        .where(TaskRun.generation == hold.generation)
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == hold.exec_token)
        .where(TaskRun.exec_epoch == hold.exec_epoch)
        .where(TaskRun.intent_version == hold.intent_version)
        .values(continuation_due=False)
    )
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    if _leave(db, hold, status="waiting_person") != 1:
        db.rollback()
        return ()
    for token in tokens:
        _note(db, job, "confirm", token)
    if not _commit_job(db, job):
        return ()
    return tuple(tokens)


def observe_ready(db: Session, *, hold: Hold, step_id: int) -> bool:
    job = _lock_holder(db, hold)
    if job is None:
        db.rollback()
        return False
    step_row = db.get(TaskStep, step_id)
    if step_row is None or step_row.task_run_id != job.id:
        db.rollback()
        return False
    rows = _call_rows(db, step_row.id)
    if not rows or any(CallState(row.state) not in TERMINAL for row in rows):
        db.rollback()
        return False
    entered = _store_phase(db, step_row, Phase.RESULTS_READY)
    cleared = _clear_due_if_set(db, hold)
    if not entered and cleared != 1:
        db.rollback()
        return False
    return _commit_job(db, job, kind="step", body=Phase.RESULTS_READY.value)


def confirm_call(
    db: Session,
    *,
    user_id: int,
    token: str,
    outcome: Literal["confirm", "reject", "expire"],
    raw: dict,
) -> Step:
    from app.services.schedule_confirm import (
        ScheduleConfirmClosed,
        write_fingerprint,
        write_parts,
        write_scope,
    )

    key = str(token or "").strip()
    now = _now()
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id:
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    if claim.status == "unknown":
        db.rollback()
        raise ScheduleConfirmClosed("unknown")
    if claim.status != "open" or _expired(claim.expires_at, now):
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    try:
        parts = write_parts(claim.action, raw)
    except ScheduleConfirmClosed:
        db.rollback()
        raise
    if claim.fingerprint != write_fingerprint(parts) or claim.scope != write_scope(user_id, parts):
        db.rollback()
        raise ScheduleConfirmClosed("mismatch")
    call_row = (
        db.query(ToolCall)
        .filter(ToolCall.user_id == user_id, ToolCall.claim_token == key)
        .one_or_none()
    )
    if call_row is None:
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    step_row = db.get(TaskStep, call_row.task_step_id)
    job = (
        db.query(TaskRun)
        .filter(TaskRun.id == step_row.task_run_id, TaskRun.user_id == user_id)
        .with_for_update()
        .one_or_none()
    )
    if job is None or job.status not in {"running", "waiting_person", "waiting_resource"}:
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    if int(job.intent_version or 1) > int(claim.intent_version or 1):
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    try:
        step = _record_outcome(
            db,
            job=job,
            step_row=step_row,
            call_row=call_row,
            outcome=outcome,
        )
    except IllegalPhase:
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    if outcome == "confirm":
        claim.status = "unknown"
    else:
        claim.status = "cancelled"
    if not _commit_job(db, job, kind="confirm", body=key):
        raise ScheduleConfirmClosed("closed")
    return step


def note_confirmed(db: Session, *, user_id: int, token: str) -> None:
    key = str(token or "").strip()
    if not key:
        return
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id:
        return
    call_row = (
        db.query(ToolCall)
        .filter(ToolCall.user_id == user_id, ToolCall.claim_token == key)
        .one_or_none()
    )
    if call_row is None or call_row.state != CallState.AWAITING_CONFIRM.value:
        return
    step_row = db.get(TaskStep, call_row.task_step_id)
    if step_row is None:
        return
    job = (
        db.query(TaskRun)
        .filter(TaskRun.id == step_row.task_run_id, TaskRun.user_id == user_id)
        .with_for_update()
        .one_or_none()
    )
    if job is None or job.status not in {"running", "waiting_person", "waiting_resource"}:
        return
    db.refresh(call_row)
    if call_row.state != CallState.AWAITING_CONFIRM.value:
        return
    try:
        _record_outcome(db, job=job, step_row=step_row, call_row=call_row, outcome="confirm")
    except IllegalPhase:
        return


def note_unknown(db: Session, *, hold: Hold, tool_call_id: str) -> Step | None:
    preview = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == hold.user_id, TaskRun.generation == hold.generation)
        .one_or_none()
    )
    if preview is None:
        db.rollback()
        return None
    peeked = _find_call(db, preview.id, tool_call_id)
    if peeked is not None and peeked[1].claim_token:
        (
            db.query(ScheduleConfirmClaim)
            .filter(ScheduleConfirmClaim.token == peeked[1].claim_token)
            .with_for_update()
            .one_or_none()
        )
    job = _lock_holder(db, hold)
    if job is None:
        db.rollback()
        return None
    found = _find_call(db, job.id, tool_call_id)
    if found is None:
        db.rollback()
        return None
    step_row, call_row, memory = found
    if call_row.state == CallState.UNKNOWN.value:
        db.rollback()
        return memory
    if call_row.name not in _LOOKUP_WRITES:
        db.rollback()
        return memory
    claim = None
    if call_row.claim_token:
        claim = (
            db.query(ScheduleConfirmClaim)
            .filter(ScheduleConfirmClaim.token == call_row.claim_token)
            .with_for_update()
            .one_or_none()
        )
    marker = "" if claim is None else (claim.remote_request_id or "").strip()
    if claim is None or not marker:
        db.rollback()
        return memory
    written = claim.status == "written"
    try:
        step = apply(
            memory,
            LoopEvent.CONFIRM if written else LoopEvent.NOTE_UNKNOWN,
            call_id=tool_call_id,
        )
    except IllegalPhase:
        db.rollback()
        return None
    target = _call_by_id(step, tool_call_id)
    call_row.state = target.state.value
    call_row.result = f"已写入：{marker}" if written else "结果未知"
    entered = _store_phase(db, step_row, step.phase)
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    if entered:
        _clear_due_if_set(db, hold)
    if not _commit_job(db, job, kind="step", body=step.phase.value):
        return None
    return step


def _owner_gone(claim: ScheduleConfirmClaim) -> bool:
    """A live provider renews confirm_until. No lease is not proof the caller died."""
    until = claim.confirm_until
    if until is None:
        return False
    if until.tzinfo is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) >= until


def reconcile_parked_writes(db: Session, task_run_id: int) -> int:
    """Repair a parked card whose write already committed and whose tool result did not.

    An untouched waiting card, a waiting_resource row, an armed claim with no
    live-confirm lease, and a lease that is still in the future are left alone.
    This does not reserve, adopt, or send.
    """
    job = db.get(TaskRun, int(task_run_id))
    if (
        job is None
        or job.kind != "job"
        or job.status != "waiting_person"
        or job.continuation_due
        or job.exec_token
    ):
        db.rollback()
        return 0
    candidates: list[tuple[TaskStep, ToolCall, str]] = []
    steps = (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == job.id)
        .order_by(TaskStep.seq.asc())
        .all()
    )
    for step_row in steps:
        for call_row in _call_rows(db, step_row.id):
            if call_row.state != CallState.AWAITING_CONFIRM.value or call_row.name not in _LOOKUP_WRITES:
                continue
            token = str(call_row.claim_token or "").strip()
            if not token:
                continue
            candidates.append((step_row, call_row, token))
    locked: list[tuple[TaskStep, ToolCall, ScheduleConfirmClaim]] = []
    for step_row, call_row, token in sorted(candidates, key=lambda item: str(item[2])):
        claim = (
            db.query(ScheduleConfirmClaim)
            .filter(ScheduleConfirmClaim.token == token)
            .with_for_update()
            .one_or_none()
        )
        marker = "" if claim is None else (claim.remote_request_id or "").strip()
        if claim is None or not marker or claim.status not in {"unknown", "written"}:
            continue
        if claim.status == "unknown" and not _owner_gone(claim):
            continue
        locked.append((step_row, call_row, claim))
    if not locked:
        db.rollback()
        return 0
    job = (
        db.query(TaskRun)
        .filter(TaskRun.id == int(task_run_id), TaskRun.user_id == job.user_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        job is None
        or job.status != "waiting_person"
        or job.continuation_due
        or job.exec_token
    ):
        db.rollback()
        return 0
    changed = 0
    for step_row, call_row, claim in locked:
        db.refresh(call_row)
        if call_row.state != CallState.AWAITING_CONFIRM.value:
            continue
        memory = _as_step(step_row, _call_rows(db, step_row.id))
        written = claim.status == "written"
        try:
            step = apply(
                memory,
                LoopEvent.CONFIRM if written else LoopEvent.NOTE_UNKNOWN,
                call_id=call_row.tool_call_id,
            )
        except IllegalPhase:
            db.rollback()
            return 0
        target = _call_by_id(step, call_row.tool_call_id)
        call_row.state = target.state.value
        marker = (claim.remote_request_id or "").strip()
        call_row.result = f"已写入：{marker}" if written else "结果未知"
        _store_phase(db, step_row, step.phase)
        changed += 1
    if changed == 0:
        db.rollback()
        return 0
    calls = []
    for step_row in db.query(TaskStep).filter(TaskStep.task_run_id == job.id).all():
        calls.extend(_call_rows(db, step_row.id))
    ready = bool(calls) and all(CallState(row.state) in TERMINAL for row in calls)
    if ready:
        result = db.execute(
            update(TaskRun)
            .where(TaskRun.id == job.id)
            .where(TaskRun.status == "waiting_person")
            .where(TaskRun.exec_token.is_(None))
            .where(TaskRun.continuation_due == False)  # noqa: E712
            .values(continuation_due=True)
        )
        if int(result.rowcount or 0) != 1:
            db.rollback()
            return 0
    if not _commit_job(db, job, kind="step", body=Phase.RESULTS_READY.value if ready else Phase.AWAITING_RESULTS.value):
        return 0
    return changed


def cancel_open_calls(db: Session, *, user_id: int, generation: int) -> int:
    job = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id, TaskRun.generation == generation, TaskRun.kind == "job")
        .with_for_update()
        .one_or_none()
    )
    if job is None or job.status in {"completed", "stopped"}:
        return 0
    from_status = job.status
    for step_row in db.query(TaskStep).filter(TaskStep.task_run_id == job.id).all():
        rows = _call_rows(db, step_row.id)
        memory = _as_step(step_row, rows)
        for row in rows:
            if CallState(row.state) in TERMINAL:
                continue
            try:
                memory = apply(memory, LoopEvent.CANCEL_CALL, call_id=row.tool_call_id)
            except IllegalPhase:
                db.rollback()
                return 0
            row.state = CallState.CANCELLED.value
        step_row.phase = memory.phase.value
    result = db.execute(
        update(TaskRun)
        .where(TaskRun.id == job.id)
        .where(TaskRun.user_id == user_id)
        .where(TaskRun.status == from_status)
        .values(
            status="stopped",
            continuation_due=False,
            exec_token=None,
            hold_until=None,
            dispatch_token=None,
            dispatch_until=None,
            exec_epoch=TaskRun.exec_epoch + 1,
        )
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        return 0
    from app.services.task_images import mark_pending_delete

    mark_pending_delete(db, user_id=user_id, task_run_id=job.id)
    db.expire(job)
    _note(db, job, "status", "stopped")
    return 1


def redact_result(text: str) -> str:
    if not isinstance(text, str):
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, (dict, list)):
        return _dump(_strip(parsed))
    return text


def _read_still_open(step: Step, call_id: str) -> bool:
    for call in step.calls:
        if call.tool_call_id == call_id:
            continue
        if call.state in {CallState.PENDING, CallState.RUNNING} and call.name in _READ_NAMES:
            return True
    return False


def _lock_holder(db: Session, hold: Hold) -> TaskRun | None:
    return (
        db.query(TaskRun)
        .filter(
            TaskRun.user_id == hold.user_id,
            TaskRun.generation == hold.generation,
            TaskRun.status == "running",
            TaskRun.exec_token == hold.exec_token,
            TaskRun.exec_epoch == hold.exec_epoch,
            TaskRun.intent_version == hold.intent_version,
        )
        .with_for_update()
        .one_or_none()
    )


def _job_for(db: Session, hold: Hold) -> TaskRun | None:
    return (
        db.query(TaskRun)
        .filter(TaskRun.user_id == hold.user_id, TaskRun.generation == hold.generation)
        .one_or_none()
    )


def _completed_step(db: Session, hold: Hold) -> Step | None:
    job = _job_for(db, hold)
    if job is None or job.status != "completed":
        db.rollback()
        return None
    row = (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == job.id, TaskStep.phase == Phase.FINAL_ANSWER.value)
        .order_by(TaskStep.seq.desc())
        .first()
    )
    step = None if row is None else _as_step(row, _call_rows(db, row.id))
    db.rollback()
    return step


def _matching_step(db: Session, task_run_id: int, turn: ModelTurn) -> Step | None:
    row = _latest_step(db, task_run_id)
    if row is None or row.phase not in {Phase.CALLS_SAVED.value, Phase.FINAL_ANSWER.value}:
        return None
    calls = _call_rows(db, row.id)
    ids = tuple(call.tool_call_id for call in calls)
    new_ids = tuple(spec.id for spec in turn.tool_calls)
    if row.content != (turn.content or "") or ids != new_ids:
        return None
    return _as_step(row, calls)


def _latest_step(db: Session, task_run_id: int) -> TaskStep | None:
    return (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == task_run_id)
        .order_by(TaskStep.seq.desc())
        .first()
    )


def _save_final(
    db: Session,
    *,
    job: TaskRun,
    hold: Hold,
    turn: ModelTurn,
    message_id: int | None,
) -> Step | None:
    try:
        seq = _next_seq(db, job.id)
        step = apply(None, LoopEvent.SAVE_ANSWER, seq=seq)
    except IllegalPhase:
        db.rollback()
        return None
    row = TaskStep(
        user_id=job.user_id,
        task_run_id=job.id,
        seq=step.seq,
        phase=step.phase.value,
        content=turn.content or "",
        reasoning_content=turn.reasoning if isinstance(turn.reasoning, str) else "",
    )
    db.add(row)
    _write_answer(db, job.session_id, message_id, turn.content or "")
    _note(db, job, "message", turn.content or "")
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    from app.services.task_images import mark_pending_delete

    mark_pending_delete(db, user_id=job.user_id, task_run_id=job.id)
    effect = job_effect(step, status=job.status, continuation_due=bool(job.continuation_due))
    db.execute(
        update(TaskRun)
        .where(TaskRun.id == job.id)
        .where(TaskRun.user_id == hold.user_id)
        .values(continuation_due=False)
    )
    if effect.clear_hold and _leave(db, hold, status=effect.status or "completed") != 1:
        db.rollback()
        return None
    if not _commit_job(db, job, kind="step", body=step.phase.value):
        return None
    return step


def _deny_web(db, *, job, hold, step_row, call_row, memory) -> Literal["denied", "stale"]:
    changed = db.execute(
        update(ToolCall)
        .where(ToolCall.id == call_row.id)
        .where(ToolCall.state.in_((CallState.PENDING.value, CallState.RUNNING.value)))
        .where(ToolCall.intent_version == int(hold.intent_version))
        .where(exists().where(_holder_match(hold, web="forbid")))
        .values(state=CallState.REJECTED.value, result=WEB_DENIAL)
    )
    if int(changed.rowcount or 0) != 1:
        db.rollback()
        return "stale"
    try:
        step = apply(memory, LoopEvent.DENY, call_id=call_row.tool_call_id)
    except IllegalPhase:
        db.rollback()
        return "stale"
    db.expire(call_row)
    _store_phase(db, step_row, step.phase)
    task_run.touch_progress(db, user_id=job.user_id, generation=job.generation)
    if step.phase is Phase.RESULTS_READY:
        _clear_due_if_set(db, hold)
    if not _commit_job(db, job, kind="step", body=step.phase.value):
        return "stale"
    return "denied"


def _holder_match(hold: Hold, *, web: str | None = None):
    clauses = [
        TaskRun.user_id == hold.user_id,
        TaskRun.generation == hold.generation,
        TaskRun.status == "running",
        TaskRun.exec_token == hold.exec_token,
        TaskRun.exec_epoch == hold.exec_epoch,
        TaskRun.intent_version == hold.intent_version,
    ]
    if web is not None:
        clauses.append(TaskRun.web_constraint == web)
    return and_(*clauses)


def _cas_running(db, call_row, hold: Hold, *, require_allow: bool) -> int:
    web = "allow" if require_allow else None
    result = db.execute(
        update(ToolCall)
        .where(ToolCall.id == call_row.id)
        .where(ToolCall.state == CallState.PENDING.value)
        .where(ToolCall.intent_version == int(hold.intent_version))
        .where(exists().where(_holder_match(hold, web=web)))
        .values(state=CallState.RUNNING.value)
    )
    db.expire(call_row)
    return int(result.rowcount or 0)


def _record_outcome(db, *, job: TaskRun, step_row: TaskStep, call_row: ToolCall, outcome: str) -> Step:
    event = {
        "confirm": LoopEvent.CONFIRM,
        "reject": LoopEvent.REJECT,
        "expire": LoopEvent.EXPIRE,
    }[outcome]
    memory = _as_step(step_row, _call_rows(db, step_row.id))
    step = apply(memory, event, call_id=call_row.tool_call_id)
    target = _call_by_id(step, call_row.tool_call_id)
    call_row.state = target.state.value
    call_row.result = _OUTCOME_TEXT[outcome]
    _store_phase(db, step_row, step.phase)
    task_run.touch_progress(db, user_id=job.user_id, generation=int(job.generation))
    _apply_confirm_due(db, job, step)
    return step


def _apply_confirm_due(db, job: TaskRun, step: Step) -> None:
    if job.status == "running" and job.exec_token:
        _set_due(db, job, step.phase is Phase.RESULTS_READY)
        return
    if job.status == "waiting_person" and not job.exec_token:
        effect = job_effect(step, status=job.status, continuation_due=bool(job.continuation_due))
        if effect.continuation_due is not None:
            _set_due(db, job, bool(effect.continuation_due))


def _set_due(db, job: TaskRun, value: bool) -> None:
    db.execute(
        update(TaskRun)
        .where(TaskRun.id == job.id)
        .where(TaskRun.user_id == job.user_id)
        .values(continuation_due=value)
    )
    db.expire(job)


def _clear_due_if_set(db, hold: Hold) -> int:
    result = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == hold.user_id)
        .where(TaskRun.generation == hold.generation)
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == hold.exec_token)
        .where(TaskRun.exec_epoch == hold.exec_epoch)
        .where(TaskRun.intent_version == hold.intent_version)
        .where(TaskRun.continuation_due.is_(True))
        .values(continuation_due=False)
    )
    return int(result.rowcount or 0)


def _leave(db, hold: Hold, *, status: str) -> int:
    result = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == hold.user_id)
        .where(TaskRun.generation == hold.generation)
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == hold.exec_token)
        .where(TaskRun.exec_epoch == hold.exec_epoch)
        .where(TaskRun.intent_version == hold.intent_version)
        .values(
            status=status,
            exec_token=None,
            hold_until=None,
            dispatch_token=None,
            dispatch_until=None,
            exec_epoch=TaskRun.exec_epoch + 1,
        )
    )
    count = int(result.rowcount or 0)
    if count == 1:
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == hold.user_id, TaskRun.generation == hold.generation)
            .one_or_none()
        )
        if row is not None:
            _note(db, row, "status", status)
    return count


def _store_phase(db, step_row: TaskStep, phase: Phase) -> bool:
    if phase is Phase.RESULTS_READY:
        result = db.execute(
            update(TaskStep)
            .where(TaskStep.id == step_row.id)
            .where(TaskStep.phase != Phase.RESULTS_READY.value)
            .values(phase=phase.value)
        )
        db.expire(step_row)
        return int(result.rowcount or 0) == 1
    step_row.phase = phase.value
    return False


def _note(db, job: TaskRun, kind: str, body: str) -> None:
    from app.services.resume import publish_event

    publish_event(
        db,
        user_id=int(job.user_id),
        session_id=int(job.session_id),
        kind=kind,
        body=body,
    )


def _commit_job(db, job: TaskRun, *, kind: str | None = None, body: str = "") -> bool:
    if kind:
        _note(db, job, kind, body)
    db.expire(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def _cards_already_open(db, hold: Hold, step_id: int) -> tuple[str, ...]:
    job = _job_for(db, hold)
    if job is None or job.status != "waiting_person":
        db.rollback()
        return ()
    rows = _call_rows(db, step_id)
    if not rows or any(row.state == CallState.PENDING.value for row in rows):
        db.rollback()
        return ()
    tokens = tuple(row.claim_token for row in rows if row.claim_token)
    db.rollback()
    return tokens


def _should_open(row: ToolCall, drafts: dict[str, dict]) -> bool:
    if row.state == CallState.PENDING.value and claim_call(row.name, _load_json(row.arguments)):
        return True
    return row.state == CallState.RUNNING.value and row.tool_call_id in drafts


def _claim_action(name: str, raw: dict) -> str:
    from app.services.schedule_confirm import canonical_action

    if name in CONFIRM_ONLY:
        return canonical_action(name)
    return canonical_action(raw.get("action"))


def _card_body(action: str, raw: dict, token: str, expires_at: datetime | None) -> dict:
    from app.services.schedule_confirm import write_parts

    parts = write_parts(action, raw)
    if action.startswith("calendar."):
        mode = {
            "calendar.create": "create",
            "calendar.update": "update",
            "calendar.delete": "cancel",
            "calendar.choose": "choose",
            "calendar.rsvp": "rsvp",
        }.get(action, "create")
        card = {
            "mode": mode,
            "phase": "draft",
            "action": action,
            "title": parts.get("title", ""),
            "start_at": parts.get("start_at", ""),
            "end_at": parts.get("end_at", ""),
            "participants": parts.get("participants", []),
            "location": parts.get("location", ""),
            "description": parts.get("description", ""),
            "meet": bool(parts.get("meet", False)),
            "confirm_token": token,
        }
        for key in ("external_id", "event_id", "response"):
            if key in parts:
                card[key] = parts[key]
    else:
        card = {"phase": "draft", "action": action, "confirm_token": token, **parts}
    if expires_at is not None:
        card["expires_at"] = expires_at.replace(microsecond=0).isoformat(timespec="seconds")
    return card


def _visible(card: dict) -> str:
    for key in ("title", "subject", "local_target"):
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _stored_calls(turn: ModelTurn, intent_version: int) -> tuple[Call, ...]:
    calls = []
    for spec in turn.tool_calls:
        if not str(spec.id or "").strip():
            raise IllegalPhase()
        calls.append(
            Call(
                tool_call_id=spec.id,
                name=spec.name,
                state=CallState.PENDING,
                claim_token=None,
                intent_version=intent_version,
            )
        )
    return tuple(calls)


def _stored_arguments(name: str, raw: str) -> str:
    arguments = _load_json(raw)
    if name == "errand.place":
        from app.services.errand_order import ErrandOrder, ErrandOrderError

        try:
            return _dump(ErrandOrder.parse(arguments).claim_parts())
        except ErrandOrderError:
            return "{}"
    if name in CONFIRM_ONLY or claim_call(name, arguments):
        from app.services.schedule_confirm import ScheduleConfirmClosed, canonical_action, write_parts

        try:
            action = canonical_action(name if name in CONFIRM_ONLY else arguments.get("action"))
            parts = write_parts(action, arguments)
        except (ScheduleConfirmClosed, ValueError):
            return _dump(_strip(arguments))
        if name == "calendar":
            parts = {**parts, "action": action}
        return _dump(parts)
    return _dump(_strip(arguments))


def _find_call(db, task_run_id: int, tool_call_id: str):
    step_rows = (
        db.query(TaskStep)
        .filter(TaskStep.task_run_id == task_run_id)
        .order_by(TaskStep.seq.desc())
        .all()
    )
    for step_row in step_rows:
        rows = _call_rows(db, step_row.id)
        for row in rows:
            if row.tool_call_id == tool_call_id:
                return step_row, row, _as_step(step_row, rows)
    return None


def _call_rows(db, step_id: int) -> list[ToolCall]:
    return (
        db.query(ToolCall)
        .filter(ToolCall.task_step_id == step_id)
        .order_by(ToolCall.position.asc(), ToolCall.id.asc())
        .all()
    )


def _as_step(step_row: TaskStep, rows: list[ToolCall]) -> Step:
    calls = tuple(
        Call(
            tool_call_id=row.tool_call_id,
            name=row.name,
            state=CallState(row.state),
            claim_token=row.claim_token,
            intent_version=int(row.intent_version or 1),
        )
        for row in rows
    )
    return Step(seq=int(step_row.seq), phase=Phase(step_row.phase), calls=calls)


def _call_by_id(step: Step, tool_call_id: str) -> Call:
    for call in step.calls:
        if call.tool_call_id == tool_call_id:
            return call
    raise IllegalPhase()


def _next_seq(db, task_run_id: int) -> int:
    current = db.query(func.max(TaskStep.seq)).filter(TaskStep.task_run_id == task_run_id).scalar()
    return int(current or 0) + 1


def _assistant_message(db, session_id: int, content: str, schedule_card: str = "") -> Message:
    row = Message(
        session_id=int(session_id),
        role="assistant",
        content=content,
        reasoning=None,
        citations="",
        schedule_card=schedule_card,
        artifact="",
    )
    db.add(row)
    db.flush()
    return row


def _write_answer(db, session_id: int, message_id: int | None, content: str) -> None:
    if message_id is not None:
        row = db.get(Message, message_id)
        if row is not None and row.session_id == session_id:
            row.content = content
            return
    _assistant_message(db, session_id, content)


def _load_json(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip(value):
    if isinstance(value, dict):
        return {
            str(key): _strip(item)
            for key, item in value.items()
            if str(key).strip().casefold() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now
