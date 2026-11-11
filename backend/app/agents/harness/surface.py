from __future__ import annotations

import enum


class ServerSurface(enum.Enum):
    WEB = "web"


def surface_for_login(user_id: int) -> ServerSurface:
    """The authenticated user has no install package, so the session is web."""
    return ServerSurface.WEB


def coerce_surface(value: object) -> ServerSurface:
    if isinstance(value, ServerSurface):
        return value
    return ServerSurface.WEB


def local_tool_denial(*, local: bool, surface: object) -> str | None:
    if not local:
        return None
    return "服务端执行面是 " + coerce_surface(surface).value + "，拒绝本地工具"
