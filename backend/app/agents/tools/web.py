"""Web search tool."""

from __future__ import annotations

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.services.web_search import citation_filename, search_planned


def execute_web(ctx: ToolContext, _args: dict) -> ToolResult:
    out = search_planned(ctx.message, iteration=ctx.iteration)
    n = len(out.results)
    query = (out.query_used or ctx.message or "").strip() or "相关资讯"
    # PRD 3.3.1 / Perplexity-style: 正在搜索 → 正在阅读 → 正在总结
    steps = [f'正在搜索:“{query}”']
    steps.append(f"正在阅读 {n} 个网页")
    if out.error and not out.results:
        steps.append(f"搜索未成功：{out.error}")
    else:
        steps.append("正在总结")
    evidence = []
    for h in out.results:
        evidence.append(
            {
                "index": 0,
                "source_type": "web",
                "tool": "web",
                "filename": citation_filename(h),
                "title": h.title,
                "snippet": (h.snippet[:400] if h.snippet else h.title),
                "content": h.snippet,
                "url": h.url,
                "publish_date": h.publish_date,
            }
        )
    return ToolResult(ok=bool(evidence) or not out.error, evidence=evidence, thinking_steps=steps, risk="medium")


register(
    ToolSpec(
        name="web",
        risk="medium",
        execute=execute_web,
        description="Search the open web for fresh facts",
    )
)
