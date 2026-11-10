"""Side-effect contract for a tool call.

`read` may run again. `idempotent_write` may run again only when the external
id stays the same. `non_replayable_write` is not sent again. A later attempt
looks the first one up.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

EFFECTS = ("read", "idempotent_write", "non_replayable_write")

T = TypeVar("T")


def decide_replay(
    effect: str,
    *,
    attempt: int,
    external_id: str,
    prior_external_id: str | None,
) -> str:
    """`send` runs the call. `reconcile` looks it up. `refuse` does neither."""
    if effect not in EFFECTS:
        return "refuse"
    if attempt <= 0:
        return "send"
    if effect == "read":
        return "send"
    if effect == "idempotent_write" and external_id and external_id == prior_external_id:
        return "send"
    if effect == "non_replayable_write":
        return "reconcile"
    return "refuse"


def call_effect(
    effect: str,
    operation: Callable[[str], T],
    *,
    attempt: int,
    external_id: str = "",
    prior_external_id: str | None = None,
) -> tuple[str, T | None]:
    choice = decide_replay(
        effect,
        attempt=attempt,
        external_id=external_id,
        prior_external_id=prior_external_id,
    )
    if choice != "send":
        return choice, None
    return choice, operation(external_id)
