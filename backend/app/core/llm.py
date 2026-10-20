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
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Literal, TypeVar

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


class LLMStructuredError(RuntimeError):
    """Raised when json object completion fails validation (no silent fallback)."""


class RoleConfigError(RuntimeError):
    """A role's model is empty, or its thinking switch is not on or off."""


class ToolsRefused(RuntimeError):
    """Caller passed tools or a tool turn. The request was not sent."""


class Role(str, Enum):
    CHAT = "chat"
    PLANNER = "planner"
    ROUTER = "router"
    JUDGE = "judge"


@dataclass(frozen=True)
class RoleSpec:
    model_attr: str
    thinking_attr: str
    model_env: str
    thinking_env: str


ROLE_SPECS: dict[Role, RoleSpec] = {
    Role.CHAT: RoleSpec("chat_model", "chat_thinking", "CHAT_MODEL", "CHAT_THINKING"),
    Role.PLANNER: RoleSpec("planner_model", "planner_thinking", "PLANNER_MODEL", "PLANNER_THINKING"),
    Role.ROUTER: RoleSpec("router_model", "router_thinking", "ROUTER_MODEL", "ROUTER_THINKING"),
    Role.JUDGE: RoleSpec("judge_model", "judge_thinking", "JUDGE_MODEL", "JUDGE_THINKING"),
}


@dataclass(frozen=True)
class RoleCall:
    role: Role
    model: str
    thinking: bool


@dataclass(frozen=True)
class CacheIdentity:
    model: str
    thinking: str


class FinishClass(str, Enum):
    STOP = "stop"
    STRUCTURED = "structured"
    INFRA = "infra"


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class AnswerDelta:
    text: str


StreamDelta = ReasoningDelta | AnswerDelta


@dataclass(frozen=True)
class Spoken:
    content: str
    reasoning: str
    prompt_tokens: int | None = None


def _parse_role(role: Role, source: Any) -> RoleCall:
    spec = ROLE_SPECS[role]
    model = str(getattr(source, spec.model_attr) or "").strip()
    thinking = str(getattr(source, spec.thinking_attr) or "").strip()
    if not model:
        raise RoleConfigError(f"{spec.model_env} is empty")
    if thinking not in ("on", "off"):
        raise RoleConfigError(f"{spec.thinking_env} must be on or off")
    return RoleCall(role=role, model=model, thinking=thinking == "on")


def assert_role_config(source: Any | None = None) -> None:
    target = settings if source is None else source
    for role in ROLE_SPECS:
        _parse_role(role, target)


def resolve(role: Role) -> RoleCall:
    return _parse_role(role, settings)


def cache_identity(role: Role) -> CacheIdentity:
    call = resolve(role)
    return CacheIdentity(model=call.model, thinking="on" if call.thinking else "off")


def classify_finish(reason: str | None, *, thinking: bool, role: Role) -> FinishClass:
    if reason is None or reason == "stop":
        return FinishClass.STOP
    if reason in ("insufficient_system_resource", "aborted"):
        return FinishClass.INFRA
    if reason == "length" and (thinking or role is Role.JUDGE):
        return FinishClass.INFRA
    return FinishClass.STRUCTURED


def message_parts(message: Any) -> tuple[str, str]:
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        content = ""
    reasoning = getattr(message, "reasoning_content", None)
    if not isinstance(reasoning, str):
        extra = getattr(message, "model_extra", None)
        found = extra.get("reasoning_content") if isinstance(extra, dict) else None
        reasoning = found if isinstance(found, str) else ""
    return content, reasoning


def _refuse_outbound(messages: list[dict], tools: Any) -> None:
    if tools is not None:
        raise ToolsRefused("tools are not sent")
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if (
            "reasoning_content" in message
            or message.get("role") == "tool"
            or "tool_calls" in message
        ):
            raise ToolsRefused("message carries tool or reasoning fields")


def _wire_messages(messages: list[dict]) -> list[dict[str, str]]:
    wire: list[dict[str, str]] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else ""
        role = message.get("role") if isinstance(message, dict) else ""
        wire.append(
            {
                "role": role if isinstance(role, str) else "",
                "content": content if isinstance(content, str) else "",
            }
        )
    return wire


def _build_request(
    call: RoleCall,
    messages: list[dict],
    *,
    kind: Literal["text", "json"],
    stream: bool,
    tools: Any = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    _refuse_outbound(messages, tools)
    body: dict[str, Any] = {
        "model": call.model,
        "messages": _wire_messages(messages),
        "stream": stream,
        "extra_body": {"thinking": {"type": "enabled" if call.thinking else "disabled"}},
        "reasoning_effort": "high" if call.thinking else "none",
    }
    if kind == "json":
        body["response_format"] = {"type": "json_object"}
        if not call.thinking:
            body["temperature"] = 0
    if timeout is not None:
        body["timeout"] = timeout
    return body


def answer_pair(
    *,
    spoken: Spoken | None = None,
    canned: str | None = None,
    prior_content: str | None = None,
    prior_reasoning: str | None = None,
) -> dict[str, str | None]:
    if spoken is not None:
        return {"direct_answer": spoken.content, "answer_reasoning": spoken.reasoning}
    if canned is not None:
        return {"direct_answer": canned, "answer_reasoning": ""}
    return {"direct_answer": prior_content, "answer_reasoning": prior_reasoning}


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


def _record_call(prompt_tokens: int | None, started: float) -> None:
    dt = time.monotonic() - started
    _STATS["calls"] += 1
    _STATS["prompt_tokens"] += int(prompt_tokens or 0)
    _STATS["latency_s_total"] += dt
    _STATS["latency_s_max"] = max(_STATS["latency_s_max"], dt)


def _mock_text(messages: list[dict]) -> str:
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    if system and "【证据池】" in system:
        return (
            f"（演示模式）根据证据，关于「{last_user}」的简要说明如下：[1]\n"
            "配置 LLM_API_KEY 后将由大模型基于证据生成完整回答。"
        )
    if system and ("未取得任何可用证据" in system or "未检索到" in system):
        return "当前未检索到与问题匹配的相关内容。"
    return f"（演示模式）已收到：{last_user}"


def _raise_infra(exc: BaseException) -> None:
    _STATS["infra_errors"] += 1
    if isinstance(exc, LLMInfraError):
        raise exc
    raise LLMInfraError(f"{type(exc).__name__}: {exc}") from exc


async def stream_chat(
    messages: list[dict],
    *,
    role: Role,
    usage_out: dict[str, Any] | None = None,
    tools: Any = None,
):
    """Yield reasoning and answer deltas for one role.

    ``usage_out`` receives ``prompt_tokens`` when the stream includes usage.
    A gateway that rejects ``stream_options`` is retried once without it.
    """
    _refuse_outbound(messages, tools)
    if not settings.llm_api_key:
        async for ch in _mock_stream(messages):
            if ch:
                yield AnswerDelta(ch)
        return

    call = resolve(role)
    body = _build_request(call, messages, kind="text", stream=True, tools=tools)
    if usage_out is not None:
        body["stream_options"] = {"include_usage": True}
    client = _get_async_client()
    async with async_model_slot(call.model):
        try:
            resp = await client.chat.completions.create(**body)
        except Exception:
            if usage_out is not None and "stream_options" in body:
                logger.debug("stream_chat retry without stream_options")
                body.pop("stream_options", None)
                resp = await client.chat.completions.create(**body)
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
            choice = choices[0]
            finish = getattr(choice, "finish_reason", None)
            if finish:
                kind = classify_finish(finish, thinking=call.thinking, role=role)
                if kind is FinishClass.INFRA:
                    raise LLMInfraError(f"finish_reason={finish}")
                if kind is not FinishClass.STOP:
                    raise LLMStructuredError(f"finish_reason={finish}")
            delta = getattr(choice, "delta", None)
            content, reasoning = message_parts(delta)
            if reasoning:
                yield ReasoningDelta(reasoning)
            if content:
                yield AnswerDelta(content)


def complete_text(
    messages: list[dict],
    *,
    role: Role,
    timeout: float | None = None,
    retries: int = 0,
    raise_on_error: bool = False,
    tools: Any = None,
) -> Spoken:
    _refuse_outbound(messages, tools)
    if not settings.llm_api_key:
        return Spoken(_mock_text(messages), "", None)

    call = resolve(role)
    req_timeout = float(timeout if timeout is not None else settings.llm_timeout)
    body = _build_request(call, messages, kind="text", stream=False, tools=tools, timeout=req_timeout)
    client = _get_sync_client()
    attempts = 1 + max(0, int(retries))
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            with model_slot(call.model):
                resp = client.chat.completions.create(**body)
        except Exception as exc:
            if is_llm_infra_exception(exc) and attempt < attempts:
                _STATS["retries"] += 1
                delay = min(30.0, 2.0 ** attempt)
                logger.warning(
                    "complete_text %s (attempt %d/%d), retry in %.0fs",
                    type(exc).__name__,
                    attempt,
                    attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            if raise_on_error:
                _raise_infra(exc)
            _STATS["infra_errors"] += 1
            logger.warning("complete_text %s: %s", type(exc).__name__, exc)
            return Spoken("", "", None)
        choice = resp.choices[0]
        finish = classify_finish(
            getattr(choice, "finish_reason", None),
            thinking=call.thinking,
            role=role,
        )
        content, reasoning = message_parts(getattr(choice, "message", None))
        prompt_tokens = _usage_prompt_tokens(getattr(resp, "usage", None))
        if finish is FinishClass.STOP:
            _record_call(prompt_tokens, started)
            return Spoken(content.strip(), reasoning, prompt_tokens)
        reason = getattr(choice, "finish_reason", None)
        if finish is FinishClass.INFRA:
            if raise_on_error:
                _raise_infra(LLMInfraError(f"finish_reason={reason}"))
            _STATS["infra_errors"] += 1
            return Spoken("", "", None)
        return Spoken("", "", None)
    return Spoken("", "", None)


def complete_json(
    messages: list[dict],
    *,
    role: Role,
    raise_on_error: bool = False,
    tools: Any = None,
) -> dict | None:
    """JSON object for a planner or judge. Reasoning is discarded here.

    Returns None on a parse miss so production classifiers keep their fallbacks.
    ``raise_on_error`` turns transport and infra finishes into ``LLMInfraError``.
    """
    _refuse_outbound(messages, tools)
    if not settings.llm_api_key:
        return None

    call = resolve(role)
    body = _build_request(call, messages, kind="json", stream=False, tools=tools)
    client = _get_sync_client()
    try:
        with model_slot(call.model):
            resp = client.chat.completions.create(**body)
    except Exception as exc:
        if raise_on_error:
            _raise_infra(exc)
        return None
    choice = resp.choices[0]
    finish = classify_finish(
        getattr(choice, "finish_reason", None),
        thinking=call.thinking,
        role=role,
    )
    content, _reasoning = message_parts(getattr(choice, "message", None))
    if finish is FinishClass.INFRA:
        if raise_on_error:
            _raise_infra(LLMInfraError(f"finish_reason={getattr(choice, 'finish_reason', None)}"))
        _STATS["infra_errors"] += 1
        return None
    if finish is not FinishClass.STOP:
        return None
    return _parse_json_object(content.strip())


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
    *,
    role: Role,
    tools: Any = None,
) -> T:
    """Parse one json_object completion into ``result_model``.

    Retries once when the body is empty, truncated, or invalid. Transport and
    infra finishes raise ``LLMInfraError`` on the call that failed.
    """
    if not settings.llm_api_key:
        raise LLMStructuredError("未配置 LLM_API_KEY，无法进行工具规划")

    call = resolve(role)
    client = _get_sync_client()
    example = _one_line_example(result_model)
    timeout = min(settings.llm_timeout, 60.0)
    msgs = list(messages)

    for attempt in (1, 2):
        body = _build_request(
            call,
            msgs,
            kind="json",
            stream=False,
            tools=tools,
            timeout=timeout,
        )
        try:
            with model_slot(call.model):
                resp = client.chat.completions.create(**body)
        except (ToolsRefused, RoleConfigError):
            raise
        except Exception as exc:
            if is_llm_infra_exception(exc):
                _raise_infra(exc)
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
        content, _reasoning = message_parts(getattr(choice, "message", None))
        finish_reason = getattr(choice, "finish_reason", None)
        finish = classify_finish(finish_reason, thinking=call.thinking, role=role)
        if finish is FinishClass.INFRA:
            _raise_infra(LLMInfraError(f"finish_reason={finish_reason}"))
        if finish is not FinishClass.STOP or not content.strip():
            reason = (
                f"finish_reason={finish_reason}"
                if finish is not FinishClass.STOP
                else "empty content"
            )
            msgs = _fail_or_retry(attempt, messages, content, reason, example)
            continue

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            msgs = _fail_or_retry(attempt, messages, content, f"invalid JSON: {exc.msg}", example, exc)
            continue

        if not isinstance(parsed, dict):
            msgs = _fail_or_retry(attempt, messages, content, "JSON value is not an object", example)
            continue

        try:
            return result_model.model_validate(parsed)
        except ValidationError as exc:
            msgs = _fail_or_retry(attempt, messages, content, str(exc), example, exc)

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


assert_role_config()
