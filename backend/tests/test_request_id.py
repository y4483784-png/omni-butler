from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.request_id import RequestIdMiddleware, _normalize, get_request_id


def _app() -> Starlette:
    async def health(_request):
        return PlainTextResponse(get_request_id() or "")

    app = Starlette(routes=[Route("/health", health)])
    app.add_middleware(RequestIdMiddleware)
    return app


def test_normalize_accepts_safe_id():
    assert _normalize("abc12345-trace") == "abc12345-trace"


def test_normalize_rejects_unsafe_id():
    out = _normalize("bad id\ninject")
    assert out != "bad id\ninject"
    assert len(out) >= 8


def test_health_sets_request_id():
    with TestClient(_app()) as client:
        r = client.get("/health")
        assert r.status_code == 200
        rid = r.headers.get("x-request-id")
        assert rid
        assert len(rid) >= 8
        assert r.text == rid


def test_health_echoes_inbound_request_id():
    with TestClient(_app()) as client:
        r = client.get("/health", headers={"X-Request-ID": "abc12345-trace"})
        assert r.headers.get("x-request-id") == "abc12345-trace"
        assert r.text == "abc12345-trace"


def test_rejects_unsafe_request_id():
    with TestClient(_app()) as client:
        r = client.get("/health", headers={"X-Request-ID": "bad id\ninject"})
        assert r.headers.get("x-request-id") != "bad id\ninject"
        assert r.headers.get("x-request-id")
        assert r.text == r.headers.get("x-request-id")
