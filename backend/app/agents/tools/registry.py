"""Tool registry: name → ToolSpec."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.agents.harness.types import ToolContext, ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: str
    execute: Callable[[ToolContext, dict], ToolResult]
    description: str = ""


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> None:
    _REGISTRY[spec.name] = spec


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def all_tools() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def ensure_builtin_tools() -> None:
    """Idempotent import of built-in tool modules (registers on import)."""
    if _REGISTRY:
        return
    from app.agents.tools import calendar_tool as _cal  # noqa: F401
    from app.agents.tools import kb as _kb  # noqa: F401
    from app.agents.tools import sandbox_tool as _sb  # noqa: F401
    from app.agents.tools import web as _web  # noqa: F401
