from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal, set_rls_context
from app.models.models import TASK_RUN_STATUSES, Session as ChatSession, TaskRun

logger = logging.getLogger(__name__)

_EVENTS = ("finish", "wait_person", "wait_resource", "stop", "continue_job")
_OPEN = tuple(status for status in TASK_RUN_STATUSES if status not in {"completed", "stopped"})
_CONTINUE_FROM = frozenset({"waiting_person", "waiting_resource"})

_MOVES: dict[str, dict[str, tuple[str, str | None]]] = {
    "running": {
        "finish": ("write", "completed"),
        "wait_person": ("write", "waiting_person"),
        "wait_resource": ("write", "waiting_resource"),
        "stop": ("write", "stopped"),
        "continue_job": ("reject", None),
    },
    "waiting_person": {
        "finish": ("keep", None),
        "wait_person": ("keep", None),
        "wait_resource": ("reject", None),
        "stop": ("write", "stopped"),
        "continue_job": ("write", "running"),
    },
    "waiting_resource": {
        "finish": ("keep", None),
        "wait_person": ("reject", None),
        "wait_resource": ("keep", None),
        "stop": ("write", "stopped"),
        "continue_job": ("write", "running"),
    },
    "completed": {
        "finish": ("keep", None),
        "wait_person": ("reject", None),
        "wait_resource": ("reject", None),
        "stop": ("keep", None),
        "continue_job": ("reject", None),
    },
    "stopped": {
        "finish": ("reject", None),
        "wait_person": ("reject", None),
        "wait_resource": ("reject", None),
        "stop": ("keep", None),
        "continue_job": ("reject", None),
    },
}


def _validate_moves() -> None:
    if set(_MOVES) != set(TASK_RUN_STATUSES):
        raise RuntimeError("task run move table statuses drifted")
    for status, edges in _MOVES.items():
        if set(edges) != set(_EVENTS):
            raise RuntimeError(f"task run events drifted for {status}")
        for event, (effect, target) in edges.items():
            if target == "running" and not (
                event == "continue_job" and status in _CONTINUE_FROM and effect == "write"
            ):
                raise RuntimeError("task run edge targets running")
            if effect not in {"write", "keep", "reject"}:
                raise RuntimeError("task run effect is unknown")
            if effect == "write" and target not in TASK_RUN_STATUSES:
                raise RuntimeError("task run write target is unknown")
            if effect != "write" and target is not None:
                raise RuntimeError("task run keep or reject carries a target")
    for status in _OPEN:
        effect, target = _MOVES[status]["stop"]
        if effect != "write" or target != "stopped":
            raise RuntimeError("open task run lacks stop to stopped")
    effect, target = _MOVES["completed"]["stop"]
    if effect == "reject" or (effect == "write" and target != "completed"):
        raise RuntimeError("completed stop changes the status")


_validate_moves()


@dataclass(frozen=True)
class Turn:
    id: int
    user_id: int
    session_id: int
    generation: int
    status: str
    allowed_tools: tuple[str, ...]
    kind: str
    goal: str
    web_constraint: str
    web_constraint_message_id: int | None
    intent_version: int
    exec_token: str | None
    exec_epoch: int
    hold_until: datetime | None
    dispatch_token: str | None
    dispatch_until: datetime | None
    continuation_due: bool
    last_progress_at: datetime | None


@dataclass(frozen=True)
class UnfinishedWriteKey:
    user_id: int
    action: str
    fingerprint: str


class LateResult(Exception):
    def __init__(self, turn: Turn) -> None:
        self.turn = turn
        super().__init__(turn.status)


class MissingTurn(Exception):
    pass


class UnownedSession(Exception):
    pass


def _to_turn(row: TaskRun, *, tools: tuple | None = None) -> Turn:
    if tools is None:
        parsed = json.loads(row.allowed_tools)
        tools = tuple(parsed)
    return Turn(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        generation=row.generation,
        status=row.status,
        allowed_tools=tools,
        kind=row.kind or "turn",
        goal=row.goal or "",
        web_constraint=row.web_constraint or "allow",
        web_constraint_message_id=row.web_constraint_message_id,
        intent_version=int(row.intent_version or 1),
        exec_token=row.exec_token,
        exec_epoch=int(row.exec_epoch or 0),
        hold_until=row.hold_until,
        dispatch_token=row.dispatch_token,
        dispatch_until=row.dispatch_until,
        continuation_due=bool(row.continuation_due),
        last_progress_at=row.last_progress_at,
    )


def _load_row(db, *, user_id: int, generation: int) -> TaskRun | None:
    return (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
        .one_or_none()
    )


def _apply_event(db, row: TaskRun, event: str) -> Turn:
    for _ in range(2):
        effect, target = _MOVES[row.status][event]
        if effect == "keep":
            return _to_turn(row)
        if effect == "reject":
            raise LateResult(_to_turn(row))
        from_status = row.status
        result = db.execute(
            update(TaskRun)
            .where(TaskRun.user_id == row.user_id)
            .where(TaskRun.generation == row.generation)
            .where(TaskRun.status == from_status)
            .values(status=target)
        )
        if result.rowcount == 1:
            row.status = target
            if target in {"completed", "stopped"}:
                from app.services.task_images import mark_pending_delete

                mark_pending_delete(db, user_id=row.user_id, task_run_id=row.id)
            return _to_turn(row)
        db.expire(row)
        fresh = _load_row(db, user_id=row.user_id, generation=row.generation)
        if fresh is None or fresh.status == from_status:
            raise MissingTurn()
        row = fresh
    raise MissingTurn()


_CARD_ACTION = "card_action"


def card_action_marker(message_id: int) -> str:
    return json.dumps([f"{_CARD_ACTION}:{int(message_id)}"], separators=(",", ":"))


class BadAllowList(Exception):
    def __init__(self, generation: int) -> None:
        self.generation = generation
        super().__init__(str(generation))


def is_card_action(allowed_tools: str | tuple) -> bool:
    if isinstance(allowed_tools, tuple):
        parsed: object = list(allowed_tools)
    else:
        try:
            parsed = json.loads(allowed_tools or "")
        except json.JSONDecodeError:
            return False
    if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], str):
        return False
    kind, sep, message_id = parsed[0].partition(":")
    return kind == _CARD_ACTION and sep == ":" and message_id.isdigit()


def _purge_quietly(user_id: int) -> None:
    try:
        from app.services.task_images import purge_pending

        purge_pending(user_id=user_id)
    except Exception:
        logger.exception("task image purge failed")


def _turn_key_conflict(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    if "uq_task_runs_user_generation" in message:
        return True
    return "task_runs.user_id" in message and "generation" in message


def _stored_kind(kind: str) -> str:
    return "job" if kind == "job" else "turn"


def _stored_web(web: str) -> str:
    return "forbid" if web == "forbid" else "allow"


def _insert_running(
    db,
    *,
    user_id: int,
    session_id: int,
    stop_others: bool = True,
    kind: str = "turn",
    goal: str = "",
    web_constraint: str = "allow",
    web_constraint_message_id: int | None = None,
) -> Turn:
    owned = (
        db.query(ChatSession.id)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .one_or_none()
    )
    if owned is None:
        raise UnownedSession()
    rows = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id)
        .with_for_update()
        .all()
    )
    if stop_others:
        for row in rows:
            if (
                row.session_id != session_id
                or row.status not in _OPEN
                or is_card_action(row.allowed_tools)
                or row.kind == "job"
            ):
                continue
            _apply_event(db, row, "stop")
    generation = max((row.generation for row in rows), default=0) + 1
    from app.agents.harness.constitution import load_constitution

    allowed_tools = json.dumps(list(load_constitution()["allowed_tools"]))
    created = TaskRun(
        user_id=user_id,
        session_id=session_id,
        generation=generation,
        status="running",
        allowed_tools=allowed_tools,
        kind=_stored_kind(kind),
        goal=goal or "",
        web_constraint=_stored_web(web_constraint),
        web_constraint_message_id=web_constraint_message_id,
        intent_version=1,
        dispatch_epoch=0,
        exec_epoch=0,
        continuation_due=False,
    )
    db.add(created)
    db.flush()
    return _to_turn(created)


def insert_card_action(db, *, user_id: int, session_id: int, message_id: int) -> Turn:
    owned = (
        db.query(ChatSession.id)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .one_or_none()
    )
    if owned is None:
        raise UnownedSession()
    rows = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id)
        .with_for_update()
        .all()
    )
    marker = card_action_marker(message_id)
    for row in rows:
        if row.session_id == session_id and row.allowed_tools == marker and row.status == "waiting_person":
            return _to_turn(row)
    generation = max((row.generation for row in rows), default=0) + 1
    created = TaskRun(
        user_id=user_id,
        session_id=session_id,
        generation=generation,
        status="running",
        allowed_tools=marker,
    )
    db.add(created)
    db.flush()
    return _apply_event(db, created, "wait_person")


def open_alongside(
    db,
    *,
    user_id: int,
    session_id: int,
    kind: str = "turn",
    goal: str = "",
    web_constraint: str = "allow",
    web_constraint_message_id: int | None = None,
) -> Turn:
    """Open one generation without stopping another open generation.

    The caller commits. An event inbox uses this so a new task does not cancel
    work that is already running in the same session. A job passes kind=job.
    """
    return _insert_running(
        db,
        user_id=user_id,
        session_id=session_id,
        stop_others=False,
        kind=kind,
        goal=goal,
        web_constraint=web_constraint,
        web_constraint_message_id=web_constraint_message_id,
    )


def begin_turn(*, user_id: int, session_id: int) -> Turn:
    conflict: IntegrityError | None = None
    for _ in range(2):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            turn = _insert_running(db, user_id=user_id, session_id=session_id)
            db.commit()
            _purge_quietly(user_id)
            return turn
        except IntegrityError as exc:
            db.rollback()
            if not _turn_key_conflict(exc):
                raise
            conflict = exc
        finally:
            db.close()
    assert conflict is not None
    raise conflict


def _mutate(*, user_id: int, generation: int, event: str) -> Turn:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = _load_row(db, user_id=user_id, generation=generation)
        if row is None:
            raise MissingTurn()
        turn = _apply_event(db, row, event)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    if turn.status in {"completed", "stopped"}:
        _purge_quietly(user_id)
    return turn


def finish(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="finish")


def wait_for_person(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="wait_person")


def wait_for_resource(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="wait_resource")


def stop(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="stop")


def continue_job(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="continue_job")


@dataclass(frozen=True)
class IntentApply:
    applied: bool
    task_run_id: int | None = None


def _clock() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _hold_until() -> datetime:
    from app.core.config import settings

    return _clock() + timedelta(seconds=float(settings.llm_timeout))


def _claim_ids(raw: str | None) -> list[int]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    found: list[int] = []
    for item in parsed:
        try:
            found.append(int(item))
        except (TypeError, ValueError):
            continue
    return found


def apply_intent(
    db,
    *,
    user_id: int,
    generation: int,
    text: str,
    web: str,
    web_constraint_message_id: int | None,
) -> IntentApply:
    """One commit. An open job stores the new text and web value. Anything else asks."""
    from app.models.models import ScheduleConfirmClaim

    row = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
        .with_for_update()
        .one_or_none()
    )
    if row is None or row.kind != "job" or row.status not in _OPEN:
        db.rollback()
        return IntentApply(applied=False)
    if row.status == "waiting_resource":
        _apply_event(db, row, "continue_job")
    row.intent_version = int(row.intent_version or 1) + 1
    row.web_constraint = _stored_web(web)
    row.web_constraint_message_id = web_constraint_message_id
    row.exec_token = None
    row.hold_until = None
    row.dispatch_token = None
    row.dispatch_until = None
    row.exec_epoch = int(row.exec_epoch or 0) + 1
    row.goal = f"{row.goal or ''}{text or ''}"
    row.continuation_due = True
    claims = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.user_id == user_id)
        .with_for_update()
        .all()
    )
    for claim in claims:
        if row.id not in _claim_ids(claim.generation_ids):
            continue
        if (claim.remote_request_id or "").strip():
            if claim.status not in {"unknown", "written"}:
                claim.status = "unknown"
            continue
        if claim.status == "open":
            claim.status = "cancelled"
    task_run_id = row.id
    db.commit()
    return IntentApply(applied=True, task_run_id=task_run_id)


def renew_hold(
    db,
    *,
    user_id: int,
    generation: int,
    exec_token: str,
    exec_epoch: int,
    intent_version: int,
) -> int:
    result = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == user_id)
        .where(TaskRun.generation == generation)
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == exec_token)
        .where(TaskRun.exec_epoch == exec_epoch)
        .where(TaskRun.intent_version == intent_version)
        .values(hold_until=_hold_until())
    )
    db.commit()
    return int(result.rowcount or 0)


_LEAVE = frozenset({"waiting_person", "completed", "stopped"})


def release_hold(
    db,
    *,
    user_id: int,
    generation: int,
    exec_token: str,
    exec_epoch: int,
    intent_version: int,
    status: str,
) -> int:
    if status not in _LEAVE:
        return 0
    result = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == user_id)
        .where(TaskRun.generation == generation)
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == exec_token)
        .where(TaskRun.exec_epoch == exec_epoch)
        .where(TaskRun.intent_version == intent_version)
        .values(
            status=status,
            exec_token=None,
            hold_until=None,
            dispatch_token=None,
            dispatch_until=None,
            exec_epoch=TaskRun.exec_epoch + 1,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


_WEB_TOOLS = frozenset({"web", "web_fetch"})


def claim_web_execution(
    db,
    *,
    user_id: int,
    generation: int,
    exec_token: str,
    exec_epoch: int,
    intent_version: int,
    tool: str,
) -> int:
    if tool not in _WEB_TOOLS:
        return 0
    result = db.execute(
        update(TaskRun)
        .where(TaskRun.user_id == user_id)
        .where(TaskRun.generation == generation)
        .where(TaskRun.kind == "job")
        .where(TaskRun.status == "running")
        .where(TaskRun.exec_token == exec_token)
        .where(TaskRun.exec_epoch == exec_epoch)
        .where(TaskRun.intent_version == intent_version)
        .where(TaskRun.web_constraint == "allow")
        .values(last_progress_at=_clock())
    )
    db.commit()
    return int(result.rowcount or 0)


def load_turn(*, user_id: int, task_id: int) -> Turn | None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(TaskRun, task_id)
        if row is None or row.user_id != user_id:
            return None
        try:
            parsed = json.loads(row.allowed_tools or "")
        except json.JSONDecodeError as exc:
            raise BadAllowList(int(row.generation)) from exc
        if not isinstance(parsed, list):
            parsed = []
        return _to_turn(row, tools=tuple(parsed))
    finally:
        db.close()


def get(*, user_id: int, generation: int) -> Turn | None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = _load_row(db, user_id=user_id, generation=generation)
        return None if row is None else _to_turn(row)
    finally:
        db.close()


def list_for_user(*, user_id: int) -> list[Turn]:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        rows = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id)
            .order_by(TaskRun.generation.asc())
            .all()
        )
        return [_to_turn(row) for row in rows]
    finally:
        db.close()
