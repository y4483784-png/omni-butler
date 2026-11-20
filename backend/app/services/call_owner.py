"""Call ownership, handoff, park, and late-result audit."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import (
    Message,
    TaskDelivery,
    TaskRun,
    TaskStep,
    ToolCall,
    ToolCallAttempt,
    ToolCallEffect,
    ToolCallEffectConflict,
)
from app.services.call_policy import (
    ACK_CONTROL,
    UNKNOWN_SENTENCE,
    acknowledgement_authorizes,
    recovery_decision,
    unresolved_sentence,
)
from app.services.delivery_bind import current_delivery


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mark_handoff(db: Session, *, tool_call_pk: int) -> bool:
    delivery_id = current_delivery()
    if not delivery_id:
        db.rollback()
        return False
    result = db.execute(
        update(ToolCallAttempt)
        .where(ToolCallAttempt.tool_call_pk == int(tool_call_pk))
        .where(ToolCallAttempt.delivery_id == delivery_id)
        .where(ToolCallAttempt.handed_off_at.is_(None))
        .values(handed_off_at=_now())
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        return False
    db.commit()
    return True


def insert_effect(
    db: Session,
    *,
    tool_call_pk: int,
    owner_token: str | None,
    attempt: int | None,
    body: str,
) -> str:
    nested = db.begin_nested()
    try:
        db.add(
            ToolCallEffect(
                tool_call_pk=int(tool_call_pk),
                owner_token=owner_token,
                attempt=attempt,
                body=body,
            )
        )
        db.flush()
        nested.commit()
        return "stored"
    except IntegrityError:
        nested.rollback()
    if owner_token is None:
        return "duplicate"
    existing = (
        db.query(ToolCallEffect)
        .filter(
            ToolCallEffect.tool_call_pk == int(tool_call_pk),
            ToolCallEffect.owner_token == owner_token,
            ToolCallEffect.attempt == attempt,
        )
        .one()
    )
    if existing.body == body:
        return "same"
    db.add(
        ToolCallEffectConflict(
            tool_call_pk=int(tool_call_pk),
            owner_token=owner_token,
            attempt=int(attempt or 0),
            body=body,
        )
    )
    db.flush()
    return "conflict"


def audit_unattributed(db: Session, *, tool_call_pk: int, body: str) -> str:
    outcome = insert_effect(
        db,
        tool_call_pk=tool_call_pk,
        owner_token=None,
        attempt=None,
        body=body,
    )
    db.commit()
    return outcome


def _attempts(db: Session, tool_call_pk: int) -> list[ToolCallAttempt]:
    return (
        db.query(ToolCallAttempt)
        .filter(ToolCallAttempt.tool_call_pk == int(tool_call_pk))
        .order_by(ToolCallAttempt.attempt.asc())
        .all()
    )


def _running_calls(db: Session, task_run_id: int) -> list[ToolCall]:
    return (
        db.query(ToolCall)
        .join(TaskStep, ToolCall.task_step_id == TaskStep.id)
        .filter(TaskStep.task_run_id == int(task_run_id), ToolCall.state == "running")
        .all()
    )


def _open_calls(db: Session, task_run_id: int) -> list[ToolCall]:
    return (
        db.query(ToolCall)
        .join(TaskStep, ToolCall.task_step_id == TaskStep.id)
        .filter(
            TaskStep.task_run_id == int(task_run_id),
            ToolCall.state.in_(("pending", "running")),
        )
        .all()
    )


def _publish_ack_card(db: Session, job: TaskRun, call: ToolCall) -> None:
    sentence = unresolved_sentence(call.tool_call_id)
    card = {
        "mode": "acknowledge",
        "control": ACK_CONTROL,
        "call_id": call.tool_call_id,
        "generation": int(job.generation),
        "sentence": sentence,
    }
    db.add(
        Message(
            session_id=int(job.session_id),
            role="assistant",
            content=sentence,
            reasoning=None,
            citations="",
            schedule_card=json.dumps(card, ensure_ascii=False),
            artifact="",
        )
    )


def _step_memory(db: Session, step_id: int):
    from app.services.loop_round import Call, CallState, Phase, Step

    step_row = db.get(TaskStep, int(step_id))
    rows = (
        db.query(ToolCall)
        .filter(ToolCall.task_step_id == int(step_id))
        .order_by(ToolCall.position.asc(), ToolCall.id.asc())
        .all()
    )
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
    return step_row, Step(seq=int(step_row.seq), phase=Phase(step_row.phase), calls=calls)


def _align_phase(db: Session, step_id: int) -> None:
    from app.services.loop_round import _store_phase, phase_after

    step_row, memory = _step_memory(db, step_id)
    _store_phase(db, step_row, phase_after(memory.calls))


def _apply_unknown(db: Session, call: ToolCall) -> None:
    from app.services.loop_round import LoopEvent, apply

    _step_row, memory = _step_memory(db, call.task_step_id)
    nxt = apply(memory, LoopEvent.NOTE_UNKNOWN, call_id=call.tool_call_id)
    target = next(item for item in nxt.calls if item.tool_call_id == call.tool_call_id)
    call.state = target.state.value
    call.result = UNKNOWN_SENTENCE
    call.unresolved_at = None


def _write_unknown_message(db: Session, job: TaskRun) -> None:
    db.add(
        Message(
            session_id=int(job.session_id),
            role="assistant",
            content=UNKNOWN_SENTENCE,
            reasoning=None,
            citations="",
            schedule_card="",
            artifact="",
        )
    )


def _publish_visible(db: Session, job: TaskRun, sentences: list[str]) -> None:
    from app.services.resume import publish_event

    db.flush()
    publish_event(
        db,
        user_id=int(job.user_id),
        session_id=int(job.session_id),
        kind="status",
        body=str(job.status),
    )
    for sentence in sentences:
        publish_event(
            db,
            user_id=int(job.user_id),
            session_id=int(job.session_id),
            kind="message",
            body=sentence,
        )


def recover_expired(db: Session, job: TaskRun, *, now: datetime) -> str:
    """Park, mark unknown, or allow one replacement. Park and unknown commit."""
    if job.hold_until is None or job.hold_until >= now or job.status != "running":
        return "hold"
    running = _running_calls(db, job.id)
    if not running:
        return "pending"
    decisions: list[tuple[ToolCall, str]] = []
    for call in running:
        rows = _attempts(db, call.id)
        latest = rows[-1] if rows else None
        delivery = None if latest is None else db.get(TaskDelivery, latest.delivery_id)
        handed = latest is not None and latest.handed_off_at is not None
        exited = delivery is not None and delivery.exited_at is not None
        handed_count = sum(1 for row in rows if row.handed_off_at is not None)
        decisions.append(
            (
                call,
                recovery_decision(
                    name=call.name,
                    handed_off=handed,
                    exited=exited,
                    handed_count=handed_count,
                ),
            )
        )
    if any(kind == "park" for _call, kind in decisions):
        sentences: list[str] = []
        for call, kind in decisions:
            if kind == "park" and call.unresolved_at is None:
                call.unresolved_at = now
                _publish_ack_card(db, job, call)
                sentences.append(unresolved_sentence(call.tool_call_id))
        job.status = "waiting_person"
        job.continuation_due = False
        _publish_visible(db, job, sentences)
        db.commit()
        return "park"
    replaced = False
    unknown_steps: set[int] = set()
    wrote_unknown = False
    for call, kind in decisions:
        if kind == "replace":
            call.state = "pending"
            call.unresolved_at = None
            replaced = True
            unknown_steps.add(int(call.task_step_id))
            continue
        if kind == "unknown":
            _apply_unknown(db, call)
            if not wrote_unknown:
                _write_unknown_message(db, job)
                wrote_unknown = True
            unknown_steps.add(int(call.task_step_id))
    for step_id in unknown_steps:
        _align_phase(db, step_id)
    if replaced:
        db.flush()
        if wrote_unknown:
            from app.services.resume import publish_event

            publish_event(
                db,
                user_id=int(job.user_id),
                session_id=int(job.session_id),
                kind="message",
                body=UNKNOWN_SENTENCE,
            )
        return "replace"
    job.status = "waiting_person"
    job.continuation_due = False
    _publish_visible(db, job, [UNKNOWN_SENTENCE] if wrote_unknown else [])
    db.commit()
    return "unknown"


def acknowledge(
    db: Session,
    *,
    user_id: int,
    generation: int,
    call_id: str | None,
    submitted_control: bool,
) -> bool:
    job = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == int(user_id), TaskRun.generation == int(generation), TaskRun.kind == "job")
        .with_for_update()
        .one_or_none()
    )
    if job is None:
        db.rollback()
        return False
    parked = (
        db.query(ToolCall)
        .join(TaskStep, ToolCall.task_step_id == TaskStep.id)
        .filter(
            TaskStep.task_run_id == job.id,
            ToolCall.state == "running",
            ToolCall.unresolved_at.isnot(None),
        )
        .one_or_none()
    )
    if parked is None or not acknowledgement_authorizes(submitted_control, call_id, parked.tool_call_id):
        db.rollback()
        return False
    if job.effect_ack_at is not None:
        db.rollback()
        return False
    step = db.get(TaskStep, parked.task_step_id)
    db.add(
        ToolCall(
            user_id=job.user_id,
            task_step_id=step.id,
            tool_call_id=uuid4().hex,
            position=int(parked.position) + 1,
            name=parked.name,
            arguments=parked.arguments,
            state="pending",
            claim_token=None,
            intent_version=int(job.intent_version or 1),
            result=None,
        )
    )
    job.effect_ack_at = _now()
    db.add(
        Message(
            session_id=int(job.session_id),
            role="assistant",
            content=unresolved_sentence(parked.tool_call_id),
            reasoning=None,
            citations="",
            schedule_card="",
            artifact="",
        )
    )
    db.commit()
    return True


def mark_legacy_unresolved(db: Session, *, dry_run: bool) -> dict:
    """Mark pre-owner running calls unresolved. Does not mint an owner."""
    calls = (
        db.query(ToolCall)
        .filter(ToolCall.state == "running", ToolCall.owner_token.is_(None), ToolCall.unresolved_at.is_(None))
        .all()
    )
    job_ids = {
        int(step.task_run_id)
        for step in db.query(TaskStep)
        .filter(TaskStep.id.in_([call.task_step_id for call in calls] or [-1]))
        .all()
    }
    jobs = (
        db.query(TaskRun)
        .filter(TaskRun.id.in_(job_ids or [-1]), TaskRun.kind == "job", TaskRun.status == "running")
        .all()
    )
    report = {"calls": len(calls), "jobs": len(jobs), "applied": False}
    if dry_run:
        db.rollback()
        return report
    stamp = _now()
    for call in calls:
        call.unresolved_at = stamp
    for job in jobs:
        job.status = "waiting_person"
        job.continuation_due = False
    db.flush()
    report["applied"] = True
    return report


def delivery_matches(db: Session, call: ToolCall) -> bool:
    delivery_id = current_delivery()
    if not delivery_id or not call.owner_token or call.attempt is None:
        return False
    row = (
        db.query(ToolCallAttempt)
        .filter(
            ToolCallAttempt.tool_call_pk == call.id,
            ToolCallAttempt.owner_token == call.owner_token,
            ToolCallAttempt.attempt == call.attempt,
        )
        .one_or_none()
    )
    return row is not None and row.delivery_id == delivery_id
