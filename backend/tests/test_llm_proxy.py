"""Custom httpx clients must receive the container proxy explicitly."""

from __future__ import annotations

import asyncio

import httpx
import openai
import pytest

from app.core import llm as llm_mod

_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)


def _capture_clients(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    # DefaultHttpxClient subclasses httpx.Client at import. Do that before the wrap.
    openai.OpenAI
    captured: dict[str, dict] = {}

    class RecordingClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            captured["sync"] = kwargs
            super().__init__(*args, **kwargs)

    class RecordingAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            captured["async"] = kwargs
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", RecordingClient)
    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    monkeypatch.setattr(llm_mod, "_sync_client", None)
    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    return captured


@pytest.mark.unit
def test_httpx_clients_use_https_proxy_and_keep_limits(monkeypatch: pytest.MonkeyPatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://lower-priority.example:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8888")
    captured = _capture_clients(monkeypatch)

    sync = llm_mod._get_sync_client()
    sync.close()

    async def open_async():
        client = llm_mod._get_async_client()
        await client.close()

    asyncio.run(open_async())

    assert captured["sync"]["proxy"] == "http://proxy.example:8888"
    assert captured["async"]["proxy"] == "http://proxy.example:8888"
    assert captured["sync"]["limits"].max_connections == 50
    assert captured["sync"]["limits"].max_keepalive_connections == 10
    assert captured["async"]["limits"].max_connections == 100
    assert captured["async"]["limits"].max_keepalive_connections == 20


@pytest.mark.unit
@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"HTTP_PROXY": "http://b", "https_proxy": "http://c", "http_proxy": "http://d"}, "http://b"),
        ({"https_proxy": "http://c", "http_proxy": "http://d"}, "http://c"),
        ({"http_proxy": "http://d"}, "http://d"),
    ],
)
def test_httpx_clients_fall_through_proxy_env_names(monkeypatch: pytest.MonkeyPatch, env, expected):
    _clear_proxy_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    captured = _capture_clients(monkeypatch)

    sync = llm_mod._get_sync_client()
    sync.close()

    assert captured["sync"]["proxy"] == expected


@pytest.mark.unit
def test_httpx_clients_omit_proxy_when_env_is_blank(monkeypatch: pytest.MonkeyPatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "   ")
    captured = _capture_clients(monkeypatch)

    sync = llm_mod._get_sync_client()
    sync.close()

    assert captured["sync"].get("proxy") in (None, "")
