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
import weakref
from typing import Any

import httpx

from app.core.config import settings
from app.core.redis import async_model_slot, model_slot

logger = logging.getLogger(__name__)

_loop_clients: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()
_sync_client: Any | None = None


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
    """Raised when schema-constrained completion fails (no silent fallback)."""


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


async def stream_chat(messages: list[dict], model: str | None = None):
    """Yield text deltas for an assistant reply."""
    if not settings.llm_api_key:
        async for ch in _mock_stream(messages):
            yield ch
        return

    client = _get_async_client()
    model_name = model or resolved_chat_model()
    async with async_model_slot(model_name):
        resp = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=True,
        )
        async for chunk in resp:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta


def complete_text(
    messages: list[dict],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    timeout: float | None = None,
) -> str:
    """Non-streaming text completion for eval / batch answer generation.

    Uses ``settings.llm_timeout`` (default 120s) rather than the short sync
    client timeout used by ``complete_json`` classifiers.
    """
    if not settings.llm_api_key:
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

    # 智谱要求 temperature 最多 2 位小数
    temp = round(float(temperature), 2)
    req_timeout = float(timeout if timeout is not None else settings.llm_timeout)
    client = _get_sync_client()
    model_name = model or settings.llm_model
    try:
        with model_slot(model_name):
            resp = client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=False,
                temperature=temp,
                timeout=req_timeout,
            )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        # Timeout / rate-limit are expected under batch eval; keep logs short
        name = type(exc).__name__
        if "Timeout" in name or "RateLimit" in name:
            logger.warning("complete_text %s: %s", name, exc)
        else:
            logger.exception("complete_text failed")
        return ""


def complete_json(messages: list[dict], model: str | None = None) -> dict | None:
    """Non-streaming JSON completion for short classifiers (intent router)."""
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
    except Exception:
        return None


async def complete_json_async(messages: list[dict], model: str | None = None) -> dict | None:
    """Async wrapper; uses shared async client (non-streaming)."""
    if not settings.llm_api_key:
        return None

    client = _get_async_client()
    model_name = model or resolved_planner_model()
    try:
        async with async_model_slot(model_name):
            resp = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=False,
                temperature=0,
                response_format={"type": "json_object"},
            )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_json_object(raw)
    except Exception:
        return None


def complete_json_schema(
    messages: list[dict],
    *,
    schema: dict[str, Any],
    name: str,
    model: str | None = None,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Schema-constrained JSON completion. Raises LLMStructuredError on failure.

    Uses OpenAI-compatible ``response_format=json_schema`` (Zhipu GLM supports it).
    Retries up to ``max_attempts`` (default ``settings.router_max_attempts``), feeding
    validation/parse errors back to the model. Never returns None / silent fallback.
    """
    if not settings.llm_api_key:
        raise LLMStructuredError("未配置 LLM_API_KEY，无法进行工具规划")

    attempts = int(max_attempts if max_attempts is not None else settings.router_max_attempts)
    attempts = max(1, attempts)
    client = _get_sync_client()
    model_name = model or resolved_router_model()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }
    msgs = list(messages)
    last_err = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            with model_slot(model_name):
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=msgs,
                    stream=False,
                    temperature=0.0,
                    response_format=response_format,
                    timeout=min(settings.llm_timeout, 60.0),
                )
            raw = (resp.choices[0].message.content or "").strip()
            obj = _parse_json_object(raw)
            if obj is None:
                last_err = f"invalid JSON: {(raw or '')[:200]}"
                msgs = list(messages) + [
                    {"role": "assistant", "content": raw or ""},
                    {
                        "role": "user",
                        "content": (
                            f"输出无法解析为 JSON 对象。错误：{last_err}\n"
                            "请严格按 schema 重新输出完整 JSON，不要 Markdown。"
                        ),
                    },
                ]
                continue
            return obj
        except LLMStructuredError:
            raise
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.warning("complete_json_schema attempt %s/%s failed: %s", attempt, attempts, last_err)
            if attempt >= attempts:
                break
            msgs = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        f"上一次调用失败：{last_err}\n"
                        "请严格按 schema 重新输出完整 JSON，不要 Markdown。"
                    ),
                },
            ]
    raise LLMStructuredError(f"schema 约束输出失败（{attempts} 次）：{last_err}")


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
