from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.agents.harness.constitution import load_constitution
from app.core.db import SessionLocal, set_rls_context
from app.models.models import TASK_RUN_STATUSES, Session as ChatSession, TaskRun

_EVENTS = ("finish", "wait_person", "wait_resource", "stop")
_OPEN = tuple(status for status in TASK_RUN_STATUSES if status not in {"completed", "stopped"})

_MOVES: dict[str, dict[str, tuple[str, str | None]]] = {
    "running": {
        "finish": ("write", "completed"),
        "wait_person": ("write", "waiting_person"),
        "wait_resource": ("write", "waiting_resource"),
        "stop": ("write", "stopped"),
    },
    "waiting_person": {
        "finish": ("keep", None),
        "wait_person": ("keep", None),
        "wait_resource": ("reject", None),
        "stop": ("write", "stopped"),
    },
    "waiting_resource": {
        "finish": ("keep", None),
        "wait_person": ("reject", None),
        "wait_resource": ("keep", None),
        "stop": ("write", "stopped"),
    },
    "completed": {
        "finish": ("keep", None),
        "wait_person": ("reject", None),
        "wait_resource": ("reject", None),
        "stop": ("keep", None),
    },
    "stopped": {
        "finish": ("reject", None),
        "wait_person": ("reject", None),
        "wait_resource": ("reject", None),
        "stop": ("keep", None),
    },
}


def _validate_moves() -> None:
    if set(_MOVES) != set(TASK_RUN_STATUSES):
        raise RuntimeError("task run move table statuses drifted")
    for status, edges in _MOVES.items():
        if set(edges) != set(_EVENTS):
            raise RuntimeError(f"task run events drifted for {status}")
        for effect, target in edges.values():
            if target == "running":
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


def _to_turn(row: TaskRun) -> Turn:
    tools = json.loads(row.allowed_tools)
    return Turn(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        generation=row.generation,
        status=row.status,
        allowed_tools=tuple(tools),
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


def _turn_key_conflict(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    if "uq_task_runs_user_generation" in message:
        return True
    return "task_runs.user_id" in message and "generation" in message


def _insert_running(db, *, user_id: int, session_id: int) -> Turn:
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
    for row in rows:
        if row.session_id != session_id or row.status not in _OPEN or is_card_action(row.allowed_tools):
            continue
        _apply_event(db, row, "stop")
    generation = max((row.generation for row in rows), default=0) + 1
    allowed_tools = json.dumps(list(load_constitution()["allowed_tools"]))
    created = TaskRun(
        user_id=user_id,
        session_id=session_id,
        generation=generation,
        status="running",
        allowed_tools=allowed_tools,
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


def begin_turn(*, user_id: int, session_id: int) -> Turn:
    conflict: IntegrityError | None = None
    for _ in range(2):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            turn = _insert_running(db, user_id=user_id, session_id=session_id)
            db.commit()
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
        return turn
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finish(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="finish")


def wait_for_person(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="wait_person")


def wait_for_resource(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="wait_resource")


def stop(*, user_id: int, generation: int) -> Turn:
    return _mutate(user_id=user_id, generation=generation, event="stop")


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
        return Turn(
            id=row.id,
            user_id=row.user_id,
            session_id=row.session_id,
            generation=row.generation,
            status=row.status,
            allowed_tools=tuple(parsed),
        )
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
