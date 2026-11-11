from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from driver import run_task

logger = logging.getLogger("errand-compose")
_SEALED = frozenset({"password", "pan", "cvv", "cookie", "secret", "pay", "login"})
_TOKEN = os.environ.get("ERRAND_COMPOSE_TOKEN", "").strip()


def _public(result: dict) -> dict:
    safe = {key: value for key, value in result.items() if str(key).strip().casefold() not in _SEALED}
    clicks = safe.get("clicks_after_wall", 0)
    try:
        clicks = int(clicks)
    except (TypeError, ValueError):
        clicks = 0
    return {
        "outcome": str(safe.get("outcome") or "unknown"),
        "order_id": str(safe.get("order_id") or ""),
        "told": str(safe.get("told") or ""),
        "clicks_after_wall": clicks,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PUT(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def do_OPTIONS(self) -> None:
        self._route()

    def log_message(self, fmt: str, *args) -> None:
        return

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if self.command == "GET" and path == "/health":
            self._send(200, {"status": "ok"})
            return
        if self.command != "POST" or path != "/tasks":
            self._send(404, {"detail": "not found"})
            return
        if _TOKEN and not _same(self.headers.get("token"), _TOKEN):
            self._send(401, {"detail": "unauthorized"})
            return
        task = self._body()
        if not isinstance(task, dict) or task.get("kind") not in {"place", "lookup"}:
            self._send(404, {"detail": "not found"})
            return
        try:
            result = run_task(task)
        except Exception:
            logger.info("errand compose failed")
            result = {"outcome": "unknown", "order_id": "", "told": "", "clicks_after_wall": 0}
        public = _public(result if isinstance(result, dict) else {})
        logger.info("outcome=%s order_id=%s", public["outcome"], public["order_id"])
        self._send(200, public)

    def _body(self) -> object:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _send(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _same(left: str | None, right: str) -> bool:
    import hmac

    given = left or ""
    return hmac.compare_digest(given.encode("utf-8"), right.encode("utf-8"))


def main() -> None:
    port = int(os.environ.get("PORT") or "8010")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
