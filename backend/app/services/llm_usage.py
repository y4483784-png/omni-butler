from __future__ import annotations

import logging
from typing import Any

from app.core.db import SessionLocal, set_rls_context
from app.core.generation_stop import current_key
from app.models.models import LlmCall

logger = logging.getLogger(__name__)


def usage_count(usage: Any, key: str) -> int | None:
    if usage is None:
        return None
    value = getattr(usage, key, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def record_response_usage(*, role: str, model: str, usage: Any) -> None:
    key = current_key()
    user_id = None if key is None else key.user_id
    generation = None if key is None else key.generation
    prompt_tokens = usage_count(usage, "prompt_tokens")
    completion_tokens = usage_count(usage, "completion_tokens")
    db = SessionLocal()
    try:
        if user_id is not None:
            set_rls_context(db, user_id)
        db.add(
            LlmCall(
                user_id=user_id,
                generation=generation,
                role=role,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("llm usage row was not stored")
    finally:
        db.close()
