"""Decisions the call owner can make without a database session."""

from __future__ import annotations

EXTERNAL = frozenset({"web", "web_fetch", "sandbox"})
LOOP_QUEUE = "loop"
BACKLOG_QUEUE = "default"
BASE_QUEUES = ("ingest", "maintenance")

UNRESOLVED_SENTENCE = (
    "The result of call {call_id} is not known, and no exit was observed. "
    "A new attempt can duplicate an effect that may already have been accepted."
)
UNKNOWN_SENTENCE = (
    "The result is not known. The worker exit was observed after the call was handed off."
)
ACK_CONTROL = "acknowledge-unknown"


def unresolved_sentence(call_id: str) -> str:
    return UNRESOLVED_SENTENCE.format(call_id=call_id)


def acknowledgement_authorizes(
    submitted_control: bool,
    submitted_call_id: str | None,
    parked_call_id: str,
) -> bool:
    return bool(submitted_control) and submitted_call_id == parked_call_id and bool(parked_call_id)


def worker_queues(*, consume_loop: bool = False) -> tuple[str, ...]:
    """Queues a worker may listen to. The old default backlog is never included."""
    if consume_loop:
        return BASE_QUEUES + (LOOP_QUEUE,)
    return BASE_QUEUES


def recovery_decision(
    *,
    name: str,
    handed_off: bool,
    exited: bool,
    handed_count: int,
) -> str:
    """What to do with one running attempt whose hold has expired.

    park: no observed exit. replace: the request was not made, or kb may try once.
    unknown: an external call was handed off and the child was reaped.
    """
    if not exited:
        return "park"
    if not handed_off:
        return "replace"
    if name == "kb" and handed_count < 2:
        return "replace"
    if name in EXTERNAL:
        return "unknown"
    return "park"
