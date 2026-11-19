"""Web tool registration.

The answer request carries DeepSeek web_search. This plug must not call the
Zhipu search client, or one turn would search twice.
"""

from __future__ import annotations

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register


def execute_web(ctx: ToolContext, _args: dict) -> ToolResult:
    del ctx, _args
    return ToolResult(
        ok=True,
        evidence=[],
        thinking_steps=["网页搜索改由回答请求附带，这里不再另发"],
        risk="medium",
    )


register(
    ToolSpec(
        name="web",
        risk="medium",
        effect="read",
        execute=execute_web,
        description="Search the open web for fresh facts",
    )
)
