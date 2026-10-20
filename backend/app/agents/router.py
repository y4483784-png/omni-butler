from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.llm import (
    LLMInfraError,
    LLMStructuredError,
    Role,
    cache_identity,
    complete_json_object,
    llm_infra_backoff_seconds,
)
from app.core.prompts import plan_user, router_system, today_str
from app.core.redis import json_get, json_set, stable_key

logger = logging.getLogger(__name__)
# Bump when the cached decision shape changes. Prompt edits invalidate on their
# own: the Redis key includes router_prompt_sha() (same hash as the disk cache).
_ROUTER_CACHE_VERSION = 3

Intent = Literal["chat", "rag", "web_search", "calendar", "data_analysis"]

# Tier 1: exact full-string match only (after strip), short greetings.
_CERTAIN_CHAT = frozenset(
    {
        "你好",
        "您好",
        "hi",
        "hello",
        "在吗",
        "谢谢",
        "好的",
        "嗯",
        "嗯嗯",
        "收到",
    }
)

# Tier 1: unambiguous document deixis substrings (explicit pointing only).
_CERTAIN_KB = ("知识库", "文档里", "这份文档", "上传的", "附件")


class RouterDecision(BaseModel):
    reasoning: str = Field(default="", description="Brief Chinese rationale first")
    needs_kb: bool = False
    needs_web: bool = False
    needs_calendar: bool = False
    needs_sandbox: bool = False
    needs_freshness: bool = False
    confidence: Literal["high", "low"] = "high"


class RouterReply(BaseModel):
    reasoning: str
    needs_kb: bool
    needs_web: bool
    needs_calendar: bool
    needs_sandbox: bool
    needs_freshness: bool
    confidence: Literal["high", "low"]


class RouterError(RuntimeError):
    """Tool planning failed; callers must surface to the user (no silent chat fallback)."""


class RouterInfraError(RouterError, LLMInfraError):
    """Rate-limit, timeout, or transport failure while planning tools.

    Subclasses both so chat still surfaces a router error, and eval treats the
    failure as infrastructure (out of the accuracy / core-completion denominator).
    """


def _decision_to_plan(d: RouterDecision) -> dict[str, Any]:
    pending: list[str] = []
    if d.needs_kb:
        pending.append("kb")
    if d.needs_web:
        pending.append("web")
    if d.needs_calendar:
        pending.append("calendar")
    if d.needs_sandbox:
        pending.append("sandbox")
    return {
        "pending_tools": pending,
        "needs_freshness": d.needs_freshness,
        "needs_kb": d.needs_kb,
        "needs_web": d.needs_web,
        "needs_calendar": d.needs_calendar,
        "needs_sandbox": d.needs_sandbox,
        "confidence": d.confidence,
        "reasoning": d.reasoning,
    }


def _apply_availability(
    d: RouterDecision,
    *,
    has_kb_docs: bool,
    has_tabular_docs: bool,
) -> RouterDecision:
    """Objective clamps only — never override LLM with keyword heuristics."""
    data = d.model_dump()
    if not has_kb_docs:
        data["needs_kb"] = False
    if not has_tabular_docs:
        data["needs_sandbox"] = False
    return RouterDecision.model_validate(data)


def _tier0_forced(
    *,
    forced_kb: bool,
    document_ids: list[int] | None,
    has_kb_docs: bool,
) -> RouterDecision | None:
    if (forced_kb or document_ids) and has_kb_docs:
        return RouterDecision(
            reasoning="用户强制知识库或指定了文档",
            needs_kb=True,
            confidence="high",
        )
    return None


def _tier1_certain(message: str, *, has_kb_docs: bool) -> RouterDecision | None:
    text = (message or "").strip()
    if not text:
        return RouterDecision(reasoning="空消息", confidence="high")
    # Exact greeting set (case-insensitive for Latin)
    key = text.lower() if text.isascii() else text
    if len(text) <= 6 and key in {c.lower() if c.isascii() else c for c in _CERTAIN_CHAT}:
        return RouterDecision(reasoning="确定性闲聊短句", confidence="high")
    if has_kb_docs and any(tok in text for tok in _CERTAIN_KB):
        return RouterDecision(
            reasoning="确定性文档指代",
            needs_kb=True,
            confidence="high",
        )
    return None


def _llm_decide(
    message: str,
    history: list[dict],
    *,
    has_kb_docs: bool,
    has_tabular_docs: bool,
    context_line: str = "",
) -> RouterDecision:
    system = router_system(has_kb_docs=has_kb_docs, has_tabular_docs=has_tabular_docs)
    user = plan_user(
        message,
        history,
        turns=int(settings.context_router_turns),
        max_chars=int(settings.context_router_chars),
        context_line=context_line,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    max_attempts = max(1, int(settings.router_max_attempts))
    for attempt in range(1, max_attempts + 1):
        try:
            reply = complete_json_object(messages, RouterReply, role=Role.ROUTER)
            return RouterDecision.model_validate(reply.model_dump())
        except LLMInfraError as exc:
            if attempt >= max_attempts:
                raise RouterInfraError(f"工具规划失败：{exc}") from exc
            delay = llm_infra_backoff_seconds(attempt)
            logger.warning(
                "router infra backoff %.0fs (attempt %s/%s): %s",
                delay,
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(delay)
        except LLMStructuredError as exc:
            raise RouterError(f"工具规划失败：{exc}") from exc
    raise RouterError("工具规划失败")


def router_prompt_sha() -> str:
    """Short hash of ``ROUTER_SYSTEM``. Disk and Redis caches share this prefix."""
    from app.core.prompts import ROUTER_SYSTEM

    return hashlib.sha256(ROUTER_SYSTEM.encode("utf-8")).hexdigest()[:12]


def router_cache_key(
    plan_text: str,
    *,
    has_kb_docs: bool,
    has_tabular_docs: bool,
    model: str,
    thinking: str,
    prompt_sha: str | None = None,
    today: str | None = None,
) -> str:
    """Redis key for one route decision. Includes the prompt fingerprint."""
    return stable_key(
        "route",
        {
            "v": _ROUTER_CACHE_VERSION,
            "date": today_str() if today is None else today,
            "prompt_sha": router_prompt_sha() if prompt_sha is None else prompt_sha,
            "plan_user": plan_text,
            "has_kb_docs": has_kb_docs,
            "has_tabular_docs": has_tabular_docs,
            "model": model,
            "thinking": thinking,
        },
    )


def router_redis_cache_active() -> bool:
    """Production caches route decisions. Eval must not read or write them.

    ``--profile full`` skips the disk cache and would otherwise copy a stale
    Redis hit into ``router_cache.json`` under the new prompt fingerprint.
    Services eval keeps Redis for embeddings and rate limits; only this cache
    is bypassed. venv eval also sets ``REDIS_ENABLED=false``.
    """
    if os.environ.get("EVAL_SKIP_ROUTER_REDIS") == "1":
        return False
    if os.environ.get("EVAL_BOOTSTRAPPED") == "1":
        return False
    return True


def router_failure_is_infra(exc: BaseException) -> bool:
    """True when ``exc`` or a cause/context is a rate-limit, timeout, or transport error."""
    from app.core.llm import is_llm_infra_exception

    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if is_llm_infra_exception(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def route(
    message: str,
    history: list[dict] | None = None,
    *,
    has_kb_docs: bool = False,
    forced_kb: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
    context_line: str = "",
) -> RouterDecision:
    """Single decision entry. Raises RouterError when LLM path fails."""
    hist = list(history or [])
    forced = _tier0_forced(
        forced_kb=forced_kb,
        document_ids=document_ids,
        has_kb_docs=has_kb_docs,
    )
    if forced is not None:
        return _apply_availability(
            forced, has_kb_docs=has_kb_docs, has_tabular_docs=has_tabular_docs
        )

    certain = _tier1_certain(message, has_kb_docs=has_kb_docs)
    if certain is not None:
        return _apply_availability(
            certain, has_kb_docs=has_kb_docs, has_tabular_docs=has_tabular_docs
        )

    plan_text = plan_user(
        message,
        hist,
        turns=int(settings.context_router_turns),
        max_chars=int(settings.context_router_chars),
        context_line=context_line,
    )
    use_cache = router_redis_cache_active()
    cache_key = ""
    decided: RouterDecision | None = None
    if use_cache:
        identity = cache_identity(Role.ROUTER)
        cache_key = router_cache_key(
            plan_text,
            has_kb_docs=has_kb_docs,
            has_tabular_docs=has_tabular_docs,
            model=identity.model,
            thinking=identity.thinking,
        )
        cached = json_get(cache_key)
        if isinstance(cached, dict):
            try:
                decided = RouterDecision.model_validate(cached)
            except ValidationError:
                logger.warning("ignoring invalid cached router decision")
    if decided is None:
        decided = _llm_decide(
            message,
            hist,
            has_kb_docs=has_kb_docs,
            has_tabular_docs=has_tabular_docs,
            context_line=context_line,
        )
        if use_cache:
            json_set(cache_key, decided.model_dump(), ttl=settings.router_cache_ttl)
    return _apply_availability(
        decided, has_kb_docs=has_kb_docs, has_tabular_docs=has_tabular_docs
    )


def plan_tools(
    message: str,
    history: list[dict],
    *,
    has_kb_docs: bool,
    forced_kb: bool,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
    context_line: str = "",
) -> dict[str, Any]:
    """Ordered tool queue from a single ``route()`` call."""
    decision = route(
        message,
        history,
        has_kb_docs=has_kb_docs,
        forced_kb=forced_kb,
        has_tabular_docs=has_tabular_docs,
        document_ids=document_ids,
        context_line=context_line,
    )
    plan = _decision_to_plan(decision)
    logger.debug(
        "router plan tools=%s confidence=%s reason=%s",
        plan["pending_tools"],
        plan["confidence"],
        (plan["reasoning"] or "")[:80],
    )
    return plan


def plan_needs(
    message: str,
    history: list[dict],
    *,
    has_kb_docs: bool,
    forced_kb: bool,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
    context_line: str = "",
) -> dict[str, bool]:
    p = plan_tools(
        message,
        history,
        has_kb_docs=has_kb_docs,
        forced_kb=forced_kb,
        has_tabular_docs=has_tabular_docs,
        document_ids=document_ids,
        context_line=context_line,
    )
    return {
        "needs_kb": bool(p["needs_kb"]),
        "needs_web": bool(p["needs_web"]),
        "needs_calendar": bool(p.get("needs_calendar")),
        "needs_sandbox": bool(p.get("needs_sandbox")),
        "needs_freshness": bool(p["needs_freshness"]),
    }


def intent_from_decision(d: RouterDecision | dict[str, Any]) -> Intent:
    if isinstance(d, RouterDecision):
        needs_sandbox = d.needs_sandbox
        needs_kb = d.needs_kb
        needs_calendar = d.needs_calendar
        needs_web = d.needs_web
    else:
        needs_sandbox = bool(d.get("needs_sandbox"))
        needs_kb = bool(d.get("needs_kb"))
        needs_calendar = bool(d.get("needs_calendar"))
        needs_web = bool(d.get("needs_web"))
    if needs_sandbox:
        return "data_analysis"
    if needs_kb:
        return "rag"
    if needs_calendar:
        return "calendar"
    if needs_web:
        return "web_search"
    return "chat"


def classify_intent(
    message: str,
    history: list[dict],
    *,
    has_kb_docs: bool,
    has_tabular_docs: bool = False,
    forced_kb: bool = False,
    document_ids: list[int] | None = None,
    context_line: str = "",
) -> Intent:
    decision = route(
        message,
        history,
        has_kb_docs=has_kb_docs,
        forced_kb=forced_kb,
        has_tabular_docs=has_tabular_docs,
        document_ids=document_ids,
        context_line=context_line,
    )
    return intent_from_decision(decision)


__all__ = [
    "Intent",
    "RouterDecision",
    "RouterError",
    "RouterInfraError",
    "route",
    "plan_tools",
    "plan_needs",
    "classify_intent",
    "intent_from_decision",
    "router_prompt_sha",
    "router_cache_key",
    "router_redis_cache_active",
    "router_failure_is_infra",
]
