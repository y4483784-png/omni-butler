"""DeepSeek Search and Fetch clients (POST /v1/tools/search and /v1/tools/fetch)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

_QUERY_ERROR = "检索词需要 1 到 128 个字符，且不能全是空白。"
_ORI_ERROR = "原问题需要 1 到 128 个字符，且不能全是空白。"
_URL_ERROR = "链接需要是不带账号的 http 或 https 地址。"


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    summary: str
    content: str


@dataclass(frozen=True)
class SearchPage:
    results: tuple[SearchHit, ...]


@dataclass(frozen=True)
class FetchPage:
    title: str
    content: str


@dataclass(frozen=True)
class ToolError:
    message: str


def _bounded(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and not value.isspace()


def _count() -> int:
    raw = settings.web_search_count
    if type(raw) is int and 1 <= raw <= 50:
        return raw
    return 5


def _text(item: dict, key: str) -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def _http_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 8192:
        return False
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return True


def _status_message(response: httpx.Response, kind: str) -> str:
    detail = ""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                detail = message.strip()
    if detail:
        return f"{kind}失败：HTTP {response.status_code} {detail}"
    return f"{kind}失败：HTTP {response.status_code}"


def _post(path: str, body: dict, *, kind: str) -> httpx.Response | ToolError:
    url = f"{settings.llm_base_url.rstrip('/')}/v1/tools/{path}"
    try:
        with httpx.Client(timeout=settings.llm_timeout) as client:
            response = client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
    except Exception as exc:
        return ToolError(f"{kind}失败：{type(exc).__name__}")
    if response.status_code != 200:
        return ToolError(_status_message(response, kind))
    return response


def _payload(response: httpx.Response, kind: str) -> dict | ToolError:
    try:
        payload = response.json()
    except Exception:
        return ToolError(f"{kind}结果格式无效。")
    if not isinstance(payload, dict):
        return ToolError(f"{kind}结果格式无效。")
    return payload


def deepseek_search(query: object, *, ori_question: object = None) -> SearchPage | ToolError:
    if not isinstance(query, str) or not _bounded(query):
        return ToolError(_QUERY_ERROR)
    if ori_question is not None and not _bounded(ori_question):
        return ToolError(_ORI_ERROR)
    body: dict[str, object] = {"query": query, "count": _count(), "need_content": True}
    if isinstance(ori_question, str):
        body["ori_question"] = ori_question
    posted = _post("search", body, kind="检索")
    if isinstance(posted, ToolError):
        return posted
    payload = _payload(posted, "检索")
    if isinstance(payload, ToolError):
        return payload
    results = payload.get("results")
    if not isinstance(results, list):
        return ToolError("检索结果格式无效。")
    hits: list[SearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            return ToolError("检索结果格式无效。")
        hits.append(
            SearchHit(
                title=_text(item, "title"),
                url=_text(item, "url"),
                snippet=_text(item, "snippet"),
                summary=_text(item, "summary"),
                content=_text(item, "content"),
            )
        )
    return SearchPage(results=tuple(hits))


def deepseek_fetch(url: object) -> FetchPage | ToolError:
    if not isinstance(url, str) or not _http_url(url):
        return ToolError(_URL_ERROR)
    posted = _post("fetch", {"url": url}, kind="读取")
    if isinstance(posted, ToolError):
        return posted
    payload = _payload(posted, "读取")
    if isinstance(payload, ToolError):
        return payload
    return FetchPage(title=_text(payload, "title"), content=_text(payload, "content"))
