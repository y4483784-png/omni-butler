"""Redis infrastructure: fail-open cache and distributed rate/concurrency limits.

Redis is an optimisation and coordination layer, never the source of truth.
When it is unavailable, cache operations miss and limiters allow requests
through so chat/RAG remain usable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Iterator

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_retry_after = 0.0

_ACQUIRE_SLOT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local lim = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) >= lim then
  return 0
end
redis.call('ZADD', key, expires, token)
redis.call('PEXPIRE', key, math.max(1000, expires - now))
return 1
"""

_RELEASE_SLOT_LUA = "return redis.call('ZREM', KEYS[1], ARGV[1])"

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local start = tonumber(ARGV[2])
local lim = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', start)
local count = redis.call('ZCARD', key)
if count >= lim then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = 1
  if oldest[2] then retry = math.max(1, math.ceil((tonumber(oldest[2]) + ttl - now) / 1000)) end
  return {0, count, retry}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, ttl)
return {1, count + 1, 0}
"""


def get_redis() -> Any | None:
    """Return a decoded Redis client, or None during a short fail-open cooldown."""
    global _client, _retry_after
    if not settings.redis_enabled:
        return None
    now = time.monotonic()
    if _client is not None:
        return _client
    if now < _retry_after:
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=1.0,
            health_check_interval=30,
        )
        client.ping()
        _client = client
        return client
    except Exception as exc:
        _retry_after = now + 10.0
        logger.warning("Redis unavailable; cache/limits fail open: %s", exc)
        return None


def reset_redis_for_tests() -> None:
    global _client, _retry_after
    _client = None
    _retry_after = 0.0


def _mark_failed(exc: Exception) -> None:
    global _client, _retry_after
    _client = None
    _retry_after = time.monotonic() + 10.0
    logger.warning("Redis operation failed; entering fail-open cooldown: %s", exc)


def stable_key(namespace: str, payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"omni:{namespace}:{digest}"


def json_get(key: str) -> Any | None:
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception as exc:
        _mark_failed(exc)
        return None


def json_set(key: str, value: Any, *, ttl: int) -> bool:
    client = get_redis()
    if client is None or ttl <= 0:
        return False
    try:
        client.setex(
            key,
            int(ttl),
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        )
        return True
    except Exception as exc:
        _mark_failed(exc)
        return False


def model_concurrency_limit(model: str) -> int:
    """Resolve ``model=limit`` overrides, falling back to a safe global default."""
    wanted = (model or "").strip().lower()
    for item in (settings.llm_model_concurrency_limits or "").split(","):
        name, sep, raw_limit = item.strip().partition("=")
        if sep and name.strip().lower() == wanted:
            try:
                return max(1, int(raw_limit))
            except ValueError:
                break
    return max(1, int(settings.llm_concurrency_default))


def _try_acquire(client: Any, key: str, token: str, limit: int, lease_ms: int) -> bool:
    now_ms = int(time.time() * 1000)
    return bool(
        client.eval(
            _ACQUIRE_SLOT_LUA,
            1,
            key,
            now_ms,
            now_ms + lease_ms,
            limit,
            token,
        )
    )


@contextmanager
def model_slot(model: str, *, lease_seconds: int | None = None) -> Iterator[None]:
    """Wait for a distributed model slot; fail open if Redis itself fails."""
    client = get_redis()
    if client is None:
        yield
        return
    token = uuid.uuid4().hex
    key = stable_key("model-slots", {"model": (model or "default").lower()})
    limit = model_concurrency_limit(model)
    lease_ms = 1000 * int(lease_seconds or settings.llm_slot_lease_seconds)
    acquired = False
    try:
        while not acquired:
            try:
                acquired = _try_acquire(client, key, token, limit, lease_ms)
            except Exception as exc:
                _mark_failed(exc)
                break
            if not acquired:
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                client.eval(_RELEASE_SLOT_LUA, 1, key, token)
            except Exception:
                pass


@asynccontextmanager
async def async_model_slot(model: str, *, lease_seconds: int | None = None):
    """Async counterpart that does not block the event loop while queued."""
    client = get_redis()
    if client is None:
        yield
        return
    token = uuid.uuid4().hex
    key = stable_key("model-slots", {"model": (model or "default").lower()})
    limit = model_concurrency_limit(model)
    lease_ms = 1000 * int(lease_seconds or settings.llm_slot_lease_seconds)
    acquired = False
    try:
        while not acquired:
            try:
                acquired = await asyncio.to_thread(
                    _try_acquire, client, key, token, limit, lease_ms
                )
            except Exception as exc:
                _mark_failed(exc)
                break
            if not acquired:
                await asyncio.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                await asyncio.to_thread(client.eval, _RELEASE_SLOT_LUA, 1, key, token)
            except Exception:
                pass


def sliding_window_allow(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int, int]:
    """Return (allowed, current_count, retry_after_seconds). Fail open."""
    if limit <= 0 or window_seconds <= 0:
        return True, 0, 0
    client = get_redis()
    if client is None:
        return True, 0, 0
    now_ms = int(time.time() * 1000)
    ttl_ms = int(window_seconds * 1000)
    key = stable_key("rate", {"scope": scope})
    try:
        result = client.eval(
            _SLIDING_WINDOW_LUA,
            1,
            key,
            now_ms,
            now_ms - ttl_ms,
            int(limit),
            f"{now_ms}:{uuid.uuid4().hex}",
            ttl_ms,
        )
        return bool(result[0]), int(result[1]), int(result[2])
    except Exception as exc:
        _mark_failed(exc)
        return True, 0, 0


async def sliding_window_allow_async(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int, int]:
    """Same as sliding_window_allow, off the event loop."""
    return await asyncio.to_thread(
        sliding_window_allow,
        scope,
        limit=limit,
        window_seconds=window_seconds,
    )
