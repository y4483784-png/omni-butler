"""LLM client seam.

Decision (ADR-005, revised): all model calls go through a single async generator
so the underlying provider can be swapped without touching the API layer.

AsyncOpenAI clients are pooled per running event loop (WeakKeyDictionary) with a
shared httpx connection pool — same pattern as OpenAI Agents SDK / httpx docs.
Sync JSON classifiers reuse a module-level sync client with keep-alive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import weakref
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.redis import async_model_slot, model_slot

logger = logging.getLogger(__name__)

_loop_clients: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()
_sync_client: Any | None = None


_STATS: dict[str, Any] = {}


def llm_stats_reset() -> None:
    _STATS.clear()
    _STATS.update(
        {"calls": 0, "retries": 0, "infra_errors": 0, "prompt_tokens": 0, "latency_s_total": 0.0, "latency_s_max": 0.0}
    )


def llm_stats_snapshot() -> dict[str, Any]:
    out = dict(_STATS)
    calls = int(out.get("calls") or 0)
    out["latency_s_mean"] = round(out.get("latency_s_total", 0.0) / calls, 3) if calls else 0.0
    out["latency_s_total"] = round(out.get("latency_s_total", 0.0), 3)
    out["latency_s_max"] = round(out.get("latency_s_max", 0.0), 3)
    return out


llm_stats_reset()


class LLMInfraError(RuntimeError):
    """Rate-limit / timeout that survived retries (eval: infra error, not a model answer)."""


# Exception *type* names, not message text. A schema miss that quotes "timeout"
# in the validation error must stay a model failure.
_INFRA_TYPE_MARKERS = (
    "Timeout",
    "RateLimit",
    "Connection",
    "ConnectError",
    "ServiceUnavailable",
    "InternalServer",
    "RemoteProtocol",
    "APIConnection",
    "ReadError",
    "WriteError",
    "NetworkError",
)
_INFRA_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def is_llm_infra_exception(exc: BaseException) -> bool:
    """True for rate-limit, timeout, and similar transport failures.

    Schema/parse errors (``LLMStructuredError``) are model misses, not infra.
    """
    if isinstance(exc, LLMInfraError):
        return True
    if isinstance(exc, LLMStructuredError):
        return False
    name = type(exc).__name__
    if any(marker in name for marker in _INFRA_TYPE_MARKERS):
        return True
    status = getattr(exc, "status_code", None)
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    return code in _INFRA_STATUS_CODES


def llm_infra_backoff_seconds(attempt: int) -> float:
    """Wait before retrying attempt ``attempt`` (1-based) after rate-limit/timeout.

    Matches the answer-path schedule (2s, 4s, 8s) but caps at 8s. The router
    sits on the user request; two attempts therefore pause once, for 2s.
    """
    return min(8.0, 2.0 ** max(1, int(attempt)))


def resolved_chat_model() -> str:
    return (settings.chat_model or "").strip() or settings.llm_model


def resolved_planner_model() -> str:
    return (settings.planner_model or "").strip() or settings.llm_model


def resolved_router_model() -> str:
    return (
        (settings.router_model or "").strip()
        or (settings.planner_model or "").strip()
        or settings.llm_model
    )


class LLMStructuredError(RuntimeError):
    """Raised when json object completion fails validation (no silent fallback)."""


T = TypeVar("T", bound=BaseModel)


def _get_async_client():
    from openai import AsyncOpenAI

    loop = asyncio.get_running_loop()
    client = _loop_clients.get(loop)
    if client is None:
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            max_retries=max(0, int(settings.llm_max_retries)),
            http_client=http_client,
        )
        _loop_clients[loop] = client
    return client


def _get_sync_client():
    global _sync_client
    from openai import OpenAI

    if _sync_client is None:
        http_client = httpx.Client(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        )
        _sync_client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=min(settings.llm_timeout, 30.0),
            # Keep 0: classifiers/title run on this client and must not stall SSE
            # (PRD retries apply to the async streaming chat client above).
            max_retries=0,
            http_client=http_client,
        )
    return _sync_client


def _usage_prompt_tokens(usage: Any) -> int | None:
    if usage is None:
        return None
    for key in ("prompt_tokens", "input_tokens"):
        val = getattr(usage, key, None)
        if val is None and isinstance(usage, dict):
            val = usage.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                return None
    return None


async def stream_chat(
    messages: list[dict],
    model: str | None = None,
    *,
    usage_out: dict[str, Any] | None = None,
):
    """Yield text deltas for an assistant reply.

    When ``usage_out`` is provided, best-effort fill ``prompt_tokens`` /
    ``completion_tokens`` from stream usage (``stream_options.include_usage``).
    Providers that ignore the option leave ``usage_out`` unchanged.
    """
    if not settings.llm_api_key:
        async for ch in _mock_stream(messages):
            yield ch
        return

    client = _get_async_client()
    model_name = model or resolved_chat_model()
    async with async_model_slot(model_name):
        create_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": True,
        }
        if usage_out is not None:
            create_kwargs["stream_options"] = {"include_usage": True}
        try:
            resp = await client.chat.completions.create(**create_kwargs)
        except Exception:
            # Some gateways reject stream_options; retry without it.
            if usage_out is not None and "stream_options" in create_kwargs:
                logger.debug("stream_chat retry without stream_options")
                create_kwargs.pop("stream_options", None)
                resp = await client.chat.completions.create(**create_kwargs)
            else:
                raise
        async for chunk in resp:
            if usage_out is not None:
                usage = getattr(chunk, "usage", None)
                pt = _usage_prompt_tokens(usage)
                if pt is not None:
                    usage_out["prompt_tokens"] = pt
                ct = None
                if usage is not None:
                    ct = getattr(usage, "completion_tokens", None)
                    if ct is None and isinstance(usage, dict):
                        ct = usage.get("completion_tokens")
                if ct is not None:
                    try:
                        usage_out["completion_tokens"] = int(ct)
                    except (TypeError, ValueError):
                        pass
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = (choices[0].delta.content or "") if choices[0].delta else ""
            if delta:
                yield delta


def complete_text(
    messages: list[dict],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    timeout: float | None = None,
    retries: int = 0,
    raise_on_error: bool = False,
) -> str:
    """Non-streaming text completion for eval / batch answer generation.

    Uses ``settings.llm_timeout`` (default 120s) rather than the short sync
    client timeout used by ``complete_json`` classifiers.
    Default model remains ``settings.llm_model`` for backward compatibility;
    production chat alignment should call ``complete_text_with_usage`` with
    ``resolved_chat_model()`` or omit model there.
    """
    text, _usage = complete_text_with_usage(
        messages,
        model=model or settings.llm_model,
        temperature=temperature,
        timeout=timeout,
        retries=retries,
        raise_on_error=raise_on_error,
    )
    return text


def complete_text_with_usage(
    messages: list[dict],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    timeout: float | None = None,
    retries: int = 0,
    raise_on_error: bool = False,
) -> tuple[str, int | None]:
    """Like ``complete_text``, also returning ``prompt_tokens`` when the API provides usage.

    Default model follows ``resolved_chat_model()`` when ``model`` is omitted so
    context eval aligns with production ``stream_chat``.
    """
    if not settings.llm_api_key:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if system and "【证据池】" in system:
            return (
                f"（演示模式）根据证据，关于「{last_user}」的简要说明如下：[1]\n"
                "配置 LLM_API_KEY 后将由大模型基于证据生成完整回答。"
            ), None
        if system and ("未取得任何可用证据" in system or "未检索到" in system):
            return "当前未检索到与问题匹配的相关内容。", None
        return f"（演示模式）已收到：{last_user}", None

    # 智谱要求 temperature 最多 2 位小数
    temp = round(float(temperature), 2)
    req_timeout = float(timeout if timeout is not None else settings.llm_timeout)
    client = _get_sync_client()
    model_name = model or resolved_chat_model()
    attempts = 1 + max(0, int(retries))
    for attempt in range(1, attempts + 1):
        t0 = time.monotonic()
        try:
            with model_slot(model_name):
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    stream=False,
                    temperature=temp,
                    timeout=req_timeout,
                )
            text = (resp.choices[0].message.content or "").strip()
            pt = _usage_prompt_tokens(getattr(resp, "usage", None))
            dt = time.monotonic() - t0
            _STATS["calls"] += 1
            _STATS["prompt_tokens"] += int(pt or 0)
            _STATS["latency_s_total"] += dt
            _STATS["latency_s_max"] = max(_STATS["latency_s_max"], dt)
            return text, pt
        except Exception as exc:
            name = type(exc).__name__
            transient = "Timeout" in name or "RateLimit" in name
            if transient and attempt < attempts:
                _STATS["retries"] += 1
                delay = min(30.0, 2.0 ** attempt)
                logger.warning("complete_text %s (attempt %d/%d), retry in %.0fs", name, attempt, attempts, delay)
                time.sleep(delay)
                continue
            _STATS["infra_errors"] += 1
            if transient:
                logger.warning("complete_text %s: %s", name, exc)
            else:
                logger.exception("complete_text failed")
            if raise_on_error:
                raise LLMInfraError(f"{name}: {exc}") from exc
            return "", None
    return "", None


def complete_json(
    messages: list[dict],
    model: str | None = None,
    *,
    raise_on_error: bool = False,
) -> dict | None:
    """Non-streaming JSON completion for short classifiers (intent router).

    The default still returns None on failure so production classifiers keep
    their fallbacks. Eval callers pass ``raise_on_error=True`` so a timeout or
    rate limit is an infra error instead of a silently empty plan or grade.
    Invalid JSON stays None: that is a parse miss, not transport failure.
    """
    if not settings.llm_api_key:
        return None

    client = _get_sync_client()
    model_name = model or resolved_planner_model()
    try:
        with model_slot(model_name):
            resp = client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=False,
                temperature=0,
                response_format={"type": "json_object"},
            )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_json_object(raw)
    except Exception as exc:
        if raise_on_error:
            _STATS["infra_errors"] += 1
            raise LLMInfraError(f"{type(exc).__name__}: {exc}") from exc
        return None


def _schema_ref(root: dict, ref: str) -> dict:
    node: Any = root
    for part in ref.lstrip("#/").split("/"):
        if part:
            node = node[part]
    return node


def _example_value(node: Any, root: dict) -> Any:
    if not isinstance(node, dict):
        return None
    if "default" in node:
        return node["default"]
    if "$ref" in node:
        return _example_value(_schema_ref(root, node["$ref"]), root)
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    for key in ("anyOf", "oneOf"):
        options = node.get(key)
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict) and option.get("type") != "null":
                    return _example_value(option, root)
            return None
    typ = node.get("type")
    if isinstance(typ, list):
        for item in typ:
            if item != "null":
                return _example_value({**node, "type": item}, root)
        return None
    if typ == "object" or "properties" in node:
        props = node.get("properties") or {}
        return {key: _example_value(prop, root) for key, prop in props.items()}
    if typ == "array":
        return []
    if typ == "boolean":
        return False
    if typ == "string":
        return ""
    if typ == "integer":
        return 0
    if typ == "number":
        return 0
    if typ == "null":
        return None
    return None


def _one_line_example(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    return json.dumps(_example_value(schema, schema), ensure_ascii=False, separators=(",", ":"))


def _retry_messages(messages: list[dict], assistant: str, error: str, example: str) -> list[dict]:
    return [
        *messages,
        {"role": "assistant", "content": assistant},
        {
            "role": "user",
            "content": f"错误：{error}\n请只输出一行 json 对象，例如：\n{example}",
        },
    ]


def _fail_or_retry(
    attempt: int,
    messages: list[dict],
    assistant: str,
    error: str,
    example: str,
    cause: BaseException | None = None,
) -> list[dict]:
    if attempt >= 2:
        raise LLMStructuredError(f"json 对象校验失败：{error}") from cause
    return _retry_messages(messages, assistant, error, example)


def complete_json_object(
    messages: list[dict],
    result_model: type[T],
    model_name: str,
) -> T:
    """Parse one json_object completion into ``result_model``.

    Retries once when the body is empty, truncated, or invalid. Transport
    failures raise ``LLMInfraError`` on the call that failed.
    """
    if not settings.llm_api_key:
        raise LLMStructuredError("未配置 LLM_API_KEY，无法进行工具规划")

    client = _get_sync_client()
    example = _one_line_example(result_model)
    timeout = min(settings.llm_timeout, 60.0)
    response_format = {"type": "json_object"}
    msgs = list(messages)

    for attempt in (1, 2):
        try:
            with model_slot(model_name):
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=msgs,
                    stream=False,
                    temperature=0.0,
                    response_format=response_format,
                    timeout=timeout,
                )
        except Exception as exc:
            if is_llm_infra_exception(exc):
                _STATS["infra_errors"] += 1
                raise LLMInfraError(f"{type(exc).__name__}: {exc}") from exc
            msgs = _fail_or_retry(
                attempt,
                messages,
                "",
                f"{type(exc).__name__}: {exc}",
                example,
                exc,
            )
            continue

        choice = resp.choices[0]
        raw = choice.message.content
        text = raw if isinstance(raw, str) else ""
        finish = getattr(choice, "finish_reason", None)
        if finish == "length" or not text.strip():
            reason = "finish_reason=length" if finish == "length" else "empty content"
            msgs = _fail_or_retry(attempt, messages, text, reason, example)
            continue

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            msgs = _fail_or_retry(attempt, messages, text, f"invalid JSON: {exc.msg}", example, exc)
            continue

        if not isinstance(parsed, dict):
            msgs = _fail_or_retry(attempt, messages, text, "JSON value is not an object", example)
            continue

        try:
            return result_model.model_validate(parsed)
        except ValidationError as exc:
            msgs = _fail_or_retry(attempt, messages, text, str(exc), example, exc)

    raise LLMStructuredError("json 对象校验失败")


def _parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


async def _mock_stream(messages: list[dict]):
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    if system and "【证据池】" in system:
        reply = (
            "（演示模式 · 证据池问答）\n\n"
            f"根据证据池，关于「{last_user}」的说明如下：[1]\n\n"
            "配置 LLM_API_KEY 后将由大模型基于证据生成完整溯源回答。"
        )
    elif system and ("未取得任何可用证据" in system or "未检索到" in system or "没有检索到" in system):
        reply = "当前未检索到与问题匹配的相关内容。"
    else:
        reply = (
            "（演示模式 · 未配置 LLM_API_KEY）\n\n"
            f"Omni-Butler 已收到你的消息：{last_user}\n\n"
            "配置 .env 中的 LLM_API_KEY 与 LLM_BASE_URL 后，这里会返回真实的大模型流式回答，"
            "并接入意图路由、RAG 检索与工具调用。"
        )
    for ch in reply:
        yield ch
