"""Knowledge-base retrieval tool."""

from __future__ import annotations

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.rag.retrieval import build_search_query, retrieve


def execute_kb(ctx: ToolContext, _args: dict) -> ToolResult:
    steps = [f"第 {ctx.iteration} 轮：检索知识库"]
    hits = retrieve(
        ctx.db,
        build_search_query(ctx.message, ctx.history),
        user_id=ctx.user_id,
        document_ids=ctx.document_ids or None,
    )
    steps.append(f"知识库命中 {len(hits)} 条片段")
    evidence = []
    for h in hits:
        evidence.append(
            {
                "index": 0,
                "source_type": "kb",
                "tool": "kb",
                "filename": h.filename,
                "title": h.filename,
                "snippet": h.snippet,
                "content": h.content,
                "heading": h.heading,
                "page": h.page,
                "url": "",
            }
        )
    return ToolResult(ok=True, evidence=evidence, thinking_steps=steps, risk="low")


register(
    ToolSpec(
        name="kb",
        risk="low",
        execute=execute_kb,
        description="Retrieve passages from the local knowledge base",
    )
)
