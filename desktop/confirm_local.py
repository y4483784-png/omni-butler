from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from desktop.executor import open_with_xdg, perform, run_argv

_PROBES = {
    "desktop.open": ("/tmp/ev-desktop-probe.txt", []),
    "desktop.run": ("/usr/bin/true", []),
}


def confirm_local(transport, offer, *, open_file, run_program) -> bool:
    committed = transport.post(
        "/api/desktop/commit",
        {
            "confirm_token": offer["confirm_token"],
            "action": offer["action"],
            "local_target": offer["local_target"],
            "local_args": list(offer.get("local_args") or ()),
        },
    )
    if not perform(committed, open_file=open_file, run_program=run_program):
        return False
    transport.post(
        "/api/desktop/finish",
        {
            "confirm_token": offer["confirm_token"],
            "request_id": committed.get("request_id") or "",
        },
    )
    return True


class UrllibTransport:
    def __init__(self, base: str, token: str, cookie: str) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.cookie = cookie

    def post(self, path: str, body: dict) -> dict:
        return self._send("POST", path, body)

    def get(self, path: str) -> object:
        return self._send("GET", path, None)

    def _send(self, method: str, path: str, body: dict | None) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"X-Omni-Package": self.token, "Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


def _session_id(transport: UrllibTransport) -> int:
    raw = os.environ.get("DESKTOP_SESSION_ID", "").strip()
    if raw:
        return int(raw)
    rows = transport.get("/api/sessions")
    if isinstance(rows, list) and rows:
        return int(rows[0]["id"])
    created = transport.post("/api/sessions", {})
    return int(created["id"])


def _transport() -> UrllibTransport:
    return UrllibTransport(
        os.environ.get("DESKTOP_API_URL", "http://127.0.0.1:8001"),
        os.environ.get("DESKTOP_PACKAGE_TOKEN", ""),
        os.environ.get("DESKTOP_COOKIE", ""),
    )


def propose_probe(transport: UrllibTransport, action: str) -> dict:
    target, local_args = _PROBES[action]
    session_id = _session_id(transport)
    return transport.post(
        "/api/desktop/propose",
        {
            "session_id": session_id,
            "action": action,
            "local_target": target,
            "local_args": local_args,
        },
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    command = args[1] if len(args) > 1 else ""
    transport = _transport()
    try:
        if command == "propose":
            action = args[2] if len(args) > 2 else ""
            if action not in _PROBES:
                print("本地动作无效", file=sys.stderr)
                return 2
            json.dump(propose_probe(transport, action), sys.stdout)
            return 0
        if command == "confirm":
            offer = json.loads(sys.stdin.read() or "{}")
            opened = confirm_local(transport, offer, open_file=open_with_xdg, run_program=run_argv)
            return 0 if opened else 1
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    print("本地动作无效", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
