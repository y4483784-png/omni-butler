"""Request correlation IDs (Open WebUI / asgi-correlation-id pattern).

Pure ASGI middleware so SSE streams are not buffered by BaseHTTPMiddleware.
Accepts inbound ``X-Request-ID`` (nginx ``$request_id`` or a client UUID).
Otherwise generates a 32-char hex id. Stored in a contextvar for logs/audit
and echoed on every response.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("omni_request_id", default="")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def get_request_id() -> str:
    return _request_id.get() or ""


def bind_request_id(value: str) -> None:
    _request_id.set(value)


def _normalize(raw: str | None) -> str:
    candidate = (raw or "").strip()
    if candidate and _SAFE_ID.match(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header_map = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        rid = _normalize(header_map.get("x-request-id"))
        token = _request_id.set(rid)

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_id.reset(token)


class RequestIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


_old_record_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _old_record_factory(*args, **kwargs)
    if not getattr(record, "request_id", None):
        record.request_id = get_request_id() or "-"
    return record


def configure_request_id_logging() -> None:
    """Attach request_id to every LogRecord so uvicorn handlers do not crash."""
    logging.setLogRecordFactory(_record_factory)
    root = logging.getLogger()
    if not any(isinstance(f, RequestIdLogFilter) for f in root.filters):
        root.addFilter(RequestIdLogFilter())
    fmt = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format=fmt)
        return
    formatter = logging.Formatter(fmt)
    for handler in root.handlers:
        handler.setFormatter(formatter)
