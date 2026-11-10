from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from app.services import task_run


@dataclass(frozen=True)
class GenerationKey:
    user_id: int
    generation: int


_current: ContextVar[GenerationKey | None] = ContextVar("generation_stop", default=None)


class GenerationStopped(BaseException):
    pass


def bind(*, user_id: int, generation: int) -> Token:
    return _current.set(GenerationKey(user_id=user_id, generation=generation))


def reset(token: Token) -> None:
    _current.reset(token)


def is_stopped() -> bool:
    key = _current.get()
    if key is None:
        return False
    turn = task_run.get(user_id=key.user_id, generation=key.generation)
    return turn is not None and turn.status == "stopped"
