from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.core.db import maintenance_session
from app.models.models import TASK_RUN_STATUSES, TaskRun
from app.services import task_run
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

CHECK_INTERVAL = timedelta(seconds=60)
STALL_FLOOR = timedelta(seconds=60)
ORDINARY_STALL = timedelta(minutes=20)

TaskStatus = Literal[
    "running",
    "waiting_person",
    "waiting_resource",
    "completed",
    "stopped",
]

_EFFECT: dict[str, str] = {
    "running": "stop",
    "waiting_person": "leave",
    "waiting_resource": "leave",
    "completed": "leave",
    "stopped": "leave",
}


def _validate_effect() -> None:
    if set(_EFFECT) != set(TASK_RUN_STATUSES):
        raise RuntimeError("watchdog effect table statuses drifted")
    for status, effect in _EFFECT.items():
        if effect not in {"stop", "leave"}:
            raise RuntimeError("watchdog effect is unknown")
        if status == "running":
            if effect != "stop":
                raise RuntimeError("running watchdog effect is not stop")
        elif effect != "leave":
            raise RuntimeError("non-running watchdog effect is not leave")
    if ORDINARY_STALL < STALL_FLOOR or ORDINARY_STALL < CHECK_INTERVAL:
        raise RuntimeError("ordinary stall is below the floor")


_validate_effect()


@dataclass(frozen=True)
class GenerationKey:
    user_id: int
    generation: int


@dataclass(frozen=True)
class RunView:
    key: GenerationKey
    status: TaskStatus
    created_at: datetime


@dataclass(frozen=True)
class SweepLedger:
    previous_tick: datetime | None
    credit: Mapping[GenerationKey, timedelta]


@dataclass(frozen=True)
class Decision:
    stops: tuple[GenerationKey, ...]


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _sleep_gap(previous_tick: datetime | None, now: datetime) -> timedelta:
    # The whole gap is sleep credit, and only when it is strictly longer than
    # three check intervals. Exactly three intervals adds nothing. The credit
    # stays on the ledger for the next sweep.
    if previous_tick is None:
        return timedelta(0)
    current = _as_utc(now)
    previous = _as_utc(previous_tick)
    if current < previous:
        return timedelta(0)
    gap = current - previous
    if gap > 3 * CHECK_INTERVAL:
        return gap
    return timedelta(0)


def _silence(created_at: datetime, now: datetime, credit: timedelta) -> timedelta:
    return max(timedelta(0), _as_utc(now) - _as_utc(created_at) - credit)


def decide(
    runs: Sequence[RunView],
    *,
    now: datetime,
    ledger: SweepLedger,
) -> tuple[Decision, SweepLedger]:
    added = _sleep_gap(ledger.previous_tick, now)
    credit: dict[GenerationKey, timedelta] = {}
    stops: set[GenerationKey] = set()
    for run in runs:
        credit[run.key] = ledger.credit.get(run.key, timedelta(0)) + added
        if _EFFECT[run.status] != "stop":
            continue
        if _silence(run.created_at, now, credit[run.key]) >= ORDINARY_STALL:
            stops.add(run.key)
    ordered = tuple(sorted(stops, key=lambda key: (key.user_id, key.generation)))
    return Decision(stops=ordered), SweepLedger(previous_tick=now, credit=credit)


def load_runs() -> tuple[RunView, ...]:
    db = maintenance_session()
    try:
        views: list[RunView] = []
        for row in db.query(TaskRun).all():
            if row.created_at is None or row.status not in _EFFECT:
                continue
            views.append(
                RunView(
                    key=GenerationKey(user_id=int(row.user_id), generation=int(row.generation)),
                    status=row.status,
                    created_at=row.created_at,
                )
            )
        return tuple(views)
    finally:
        db.close()


def run_sweep(now: datetime, ledger: SweepLedger) -> SweepLedger:
    decision, updated = decide(load_runs(), now=now, ledger=ledger)
    for key in decision.stops:
        try:
            fresh = task_run.get(user_id=key.user_id, generation=key.generation)
            if fresh is None or _EFFECT[fresh.status] != "stop":
                continue
            task_run.stop(user_id=key.user_id, generation=key.generation)
        except Exception:
            logger.exception(
                "watchdog stop failed user_id=%s generation=%s",
                key.user_id,
                key.generation,
            )
    return updated


_ledger = SweepLedger(previous_tick=None, credit={})


@celery_app.task(name="app.tasks.watchdog.sweep_task", queue="maintenance")
def sweep_task() -> None:
    global _ledger
    _ledger = run_sweep(datetime.now(timezone.utc), _ledger)
