from __future__ import annotations

from urllib.parse import urlsplit

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.services.deepseek_search import FetchPage, ToolError, deepseek_fetch, deepseek_search

_WEB_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 128},
        "ori_question": {"type": "string", "minLength": 1, "maxLength": 128},
    },
    "required": ["query"],
}

_FETCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "maxLength": 8192},
    },
    "required": ["url"],
}


def _filename(url: str, title: str) -> str:
    host = urlsplit(url).hostname if url else ""
    if host:
        return host
    if title:
        return title
    return "web"


def execute_web(_ctx: ToolContext, args: dict) -> ToolResult:
    payload = args if isinstance(args, dict) else {}
    query = payload.get("query")
    ori_question = payload["ori_question"] if "ori_question" in payload else None
    page = deepseek_search(query, ori_question=ori_question)
    shown = query if isinstance(query, str) else ""
    steps = [f'正在搜索:“{shown}”']
    if isinstance(page, ToolError):
        return ToolResult(
            ok=False,
            denied=False,
            evidence=[],
            direct_answer=page.message,
            thinking_steps=steps,
        )
    if not page.results:
        return ToolResult(
            ok=True,
            denied=False,
            evidence=[],
            direct_answer="没有找到结果。",
            thinking_steps=steps,
        )
    evidence = [_hit_evidence(hit) for hit in page.results]
    return ToolResult(ok=True, denied=False, evidence=evidence, thinking_steps=steps)


def _hit_evidence(hit) -> dict:
    return {
        "index": 0,
        "source_type": "web",
        "tool": "web",
        "filename": _filename(hit.url, hit.title),
        "title": hit.title,
        "url": hit.url,
        "snippet": hit.snippet,
        "summary": hit.summary,
        "content": hit.content,
    }


def execute_web_fetch(_ctx: ToolContext, args: dict) -> ToolResult:
    payload = args if isinstance(args, dict) else {}
    url = payload.get("url")
    page = deepseek_fetch(url)
    if isinstance(page, ToolError):
        return ToolResult(ok=False, denied=False, evidence=[], direct_answer=page.message)
    return ToolResult(
        ok=True,
        denied=False,
        evidence=[_fetch_evidence(url if isinstance(url, str) else "", page)],
        direct_answer="",
    )


def _fetch_evidence(url: str, page: FetchPage) -> dict:
    return {
        "title": page.title,
        "url": url,
        "content": page.content,
        "source_type": "web",
        "tool": "web_fetch",
    }


register(
    ToolSpec(
        name="web",
        risk="medium",
        effect="read",
        local=False,
        execute=execute_web,
        description="Search the open web for fresh facts",
        parameters=_WEB_PARAMETERS,
    )
)

register(
    ToolSpec(
        name="web_fetch",
        risk="medium",
        effect="read",
        local=False,
        execute=execute_web_fetch,
        description="Fetch one web page by URL",
        parameters=_FETCH_PARAMETERS,
    )
)
