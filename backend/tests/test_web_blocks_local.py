from __future__ import annotations

import pytest

from app.agents.harness import gateway
from app.agents.harness.surface import ServerSurface, surface_for_login
from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import _REGISTRY, ToolSpec, ensure_builtin_tools, get_tool, register
from app.api.chat import ChatRequest

DENIAL = "服务端执行面是 web，拒绝本地工具"
CHAT_FIELDS = {
    "session_id",
    "message",
    "use_kb",
    "document_ids",
    "regenerate_message_id",
}


@pytest.fixture
def probe_calls():
    calls = [0]

    def execute(_ctx: ToolContext, _args: dict) -> ToolResult:
        calls[0] += 1
        return ToolResult(ok=True)

    ensure_builtin_tools()
    register(
        ToolSpec(
            name="local_probe",
            risk="high",
            execute=execute,
            effect="read",
            local=True,
        )
    )
    try:
        yield calls
    finally:
        _REGISTRY.pop("local_probe", None)


def _desktop_body(**extra: object) -> dict:
    return {
        "session_id": 1,
        "message": "在桌面执行",
        "desktop": "desktop",
        "surface": "desktop",
        **extra,
    }


def test_web_session_denies_local_probe(probe_calls: list[int]) -> None:
    surface = surface_for_login()
    assert surface is ServerSurface.WEB
    assert not isinstance(surface, str)
    with pytest.raises(ValueError):
        ServerSurface("desktop")
    ctx = ToolContext(db=None, message="hi", history=[], user_id=1, surface=surface)
    result = gateway.invoke("local_probe", ctx, {})
    assert result.denied is True
    assert result.deny_reason == DENIAL
    assert probe_calls[0] == 0


def test_desktop_body_without_token_still_denies(probe_calls: list[int]) -> None:
    parsed = ChatRequest.model_validate(_desktop_body())
    assert set(parsed.model_fields) == CHAT_FIELDS
    ctx = ToolContext(db=None, message=parsed.message, history=[], surface="desktop")
    result = gateway.invoke("local_probe", ctx, {})
    assert result.denied is True
    assert result.deny_reason == DENIAL
    assert probe_calls[0] == 0


def test_desktop_body_with_token_still_denies(probe_calls: list[int]) -> None:
    parsed = ChatRequest.model_validate(_desktop_body(confirm_token="tok"))
    assert "confirm_token" not in parsed.model_fields
    assert set(parsed.model_fields) == CHAT_FIELDS
    ctx = ToolContext(db=None, message=parsed.message, history=[], surface="desktop")
    result = gateway.invoke("local_probe", ctx, {"confirm_token": "tok"})
    assert result.denied is True
    assert result.deny_reason == DENIAL
    assert probe_calls[0] == 0


def test_shell_stays_on_the_whitelist(probe_calls: list[int]) -> None:
    ctx = ToolContext(db=None, message="hi", history=[], surface=surface_for_login())
    result = gateway.invoke("shell", ctx, {})
    assert result.denied is True
    assert "工具不在白名单" in result.deny_reason
    assert "拒绝本地工具" not in result.deny_reason
    assert probe_calls[0] == 0


def test_builtin_tools_are_not_local(probe_calls: list[int]) -> None:
    assert probe_calls[0] == 0
    for name in ("kb", "web", "calendar", "sandbox"):
        spec = get_tool(name)
        assert spec is not None
        assert spec.local is False
