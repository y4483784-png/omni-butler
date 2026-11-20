"""Celery prefork parent-reap probe.

This file is not imported by the application. It talks to Redis database 15
and a queue named ``probe``. It does not publish to the production ``default``
queue and it does not start the compose worker.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from celery import Celery
from celery.signals import worker_init
from celery.worker.request import Request

BROKER = os.environ.get("PROBE_BROKER", "redis://127.0.0.1:6379/15")
APP = Celery("prefork_reap_probe", broker=BROKER)
APP.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_default_queue="probe",
    broker_connection_retry_on_startup=True,
)

ROOT = Path(os.environ.get("PROBE_ROOT", "/tmp/loop-owner-prefork"))
DELIVERY_DB = ROOT / "delivery.sqlite"
EXIT_DB = ROOT / "exit.sqlite"
RELEASE_DIR = ROOT / "release"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _delivery_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            pid INTEGER NOT NULL,
            handed_off INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            policy TEXT,
            UNIQUE (request_id, pid)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _exit_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            pid INTEGER NOT NULL,
            detail TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE (request_id, pid)
        )
        """
    )
    conn.commit()


def _record_exit(request_id: str, pid: int | None, detail: str) -> None:
    if pid is None:
        raise OSError("parent reap did not name the child pid")
    conn = _connect(EXIT_DB)
    try:
        _exit_schema(conn)
        conn.execute(
            """
            INSERT INTO exits (request_id, pid, detail, recorded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (request_id, pid) DO NOTHING
            """,
            (str(request_id), int(pid), detail, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _note(key: str, value: str) -> None:
    conn = _connect(DELIVERY_DB)
    try:
        _delivery_schema(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return f"{time.time():.6f}"


@worker_init.connect
def _register_hook(**_kwargs) -> None:
    if os.environ.get("PROBE_HOOK") == "raise-register":
        raise RuntimeError("hook registration refused")
    if os.environ.get("PROBE_HOOK") == "fail-register":
        (ROOT / "register-refused").write_text("refused", encoding="utf-8")
        os._exit(78)
    original = Request.on_failure

    def wrapped(self, exc_info, send_failed_event=True, return_ok=False):
        try:
            exc = getattr(exc_info, "exception", None)
            if type(exc).__name__ == "ExceptionWithTraceback":
                exc = getattr(exc, "exc", exc)
            name = type(exc).__name__ if exc is not None else type(exc_info).__name__
            with (ROOT / "hook-trace.txt").open("a", encoding="utf-8") as handle:
                handle.write(f"name={name} pid={self.worker_pid} id={self.id} text={exc}\n")
            if name == "WorkerLostError":
                _record_exit(self.id, self.worker_pid, str(exc))
        except Exception as hook_exc:
            with (ROOT / "hook-trace.txt").open("a", encoding="utf-8") as handle:
                handle.write(f"hook_error {type(hook_exc).__name__}: {hook_exc}\n")
            _note("hook_error", f"{type(hook_exc).__name__}: {hook_exc}")
        finally:
            return original(
                self,
                exc_info,
                send_failed_event=send_failed_event,
                return_ok=return_ok,
            )

    Request.on_failure = wrapped
    _note("hook_ready", "1")


@APP.task(bind=True, acks_late=True, reject_on_worker_lost=True, name="probe.deliver")
def deliver(self, owner: str, phase: str) -> str:
    pid = os.getpid()
    handed_off = 1 if phase == "after" else 0
    conn = _connect(DELIVERY_DB)
    try:
        _delivery_schema(conn)
        prior = conn.execute(
            "SELECT pid, handed_off FROM deliveries WHERE request_id = ? AND pid <> ?",
            (str(self.request.id), pid),
        ).fetchall()
        exits = _exits_for(str(self.request.id))
        policy = "unobserved"
        if any(int(row["pid"]) == pid for row in exits):
            policy = "marked_dead"
        elif prior and exits and not any(int(row["handed_off"]) for row in prior):
            policy = "replacement_allowed"
        elif prior and exits:
            policy = "no_external_replay"
        conn.execute(
            """
            INSERT INTO deliveries (request_id, owner, pid, handed_off, started_at, policy)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(self.request.id), owner, pid, handed_off, _now(), policy),
        )
        conn.commit()
    finally:
        conn.close()
    flag = RELEASE_DIR / f"{owner}.go"
    deadline = time.time() + 20
    while time.time() < deadline:
        if flag.exists():
            return "released"
        time.sleep(0.05)
    return "timeout"


def _exits_for(request_id: str) -> list[sqlite3.Row]:
    if not EXIT_DB.exists() or EXIT_DB.is_dir():
        return []
    try:
        conn = _connect(EXIT_DB)
    except (OSError, sqlite3.Error):
        return []
    try:
        _exit_schema(conn)
        return list(conn.execute("SELECT * FROM exits WHERE request_id = ?", (request_id,)).fetchall())
    finally:
        conn.close()


def _rows() -> list[sqlite3.Row]:
    conn = _connect(DELIVERY_DB)
    try:
        _delivery_schema(conn)
        return list(conn.execute("SELECT * FROM deliveries ORDER BY id").fetchall())
    finally:
        conn.close()


def _exits() -> list[sqlite3.Row]:
    if not EXIT_DB.exists() or EXIT_DB.is_dir():
        return []
    conn = _connect(EXIT_DB)
    try:
        _exit_schema(conn)
        return list(conn.execute("SELECT * FROM exits ORDER BY id").fetchall())
    finally:
        conn.close()


def _meta(key: str) -> str | None:
    conn = _connect(DELIVERY_DB)
    try:
        _delivery_schema(conn)
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])
    finally:
        conn.close()


def _reset() -> None:
    if ROOT.exists():
        for child in sorted(ROOT.iterdir(), reverse=True):
            if child.is_dir():
                for nested in child.iterdir():
                    if nested.is_file():
                        nested.chmod(0o644)
                        nested.unlink()
                child.rmdir()
            else:
                child.chmod(0o644)
                child.unlink()
        ROOT.rmdir()
    ROOT.mkdir(parents=True)
    RELEASE_DIR.mkdir()
    os.environ["PROBE_ROOT"] = str(ROOT)
    os.environ.pop("PROBE_FAIL_EXIT", None)
    conn = _connect(DELIVERY_DB)
    try:
        _delivery_schema(conn)
    finally:
        conn.close()
    conn = _connect(EXIT_DB)
    try:
        _exit_schema(conn)
    finally:
        conn.close()


def _flush_broker() -> None:
    subprocess.run(["redis-cli", "-n", "15", "FLUSHDB"], check=True, capture_output=True)


def _start_worker(concurrency: int, hook: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PROBE_ROOT"] = str(ROOT)
    env["PROBE_HOOK"] = hook
    env["PROBE_BROKER"] = BROKER
    log_handle = open(ROOT / "worker.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "prefork_reap_probe",
            "worker",
            "-Q",
            "probe",
            "-P",
            "prefork",
            "-c",
            str(concurrency),
            "--without-heartbeat",
            "--without-mingle",
            "--without-gossip",
            "-l",
            "warning",
        ],
        cwd=str(Path(__file__).resolve().parent),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    proc._log_handle = log_handle  # type: ignore[attr-defined]
    return proc


def _stop_worker(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
    handle = getattr(proc, "_log_handle", None)
    if handle is not None:
        handle.close()
    path = ROOT / "worker.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _wait_hook(timeout: float = 25) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if DELIVERY_DB.exists() and _meta("hook_ready") == "1":
            return
        time.sleep(0.1)
    raise RuntimeError("worker did not register the parent hook")


def _wait_owner(owner: str, timeout: float = 15) -> sqlite3.Row:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in _rows():
            if row["owner"] == owner:
                return row
        time.sleep(0.05)
    raise RuntimeError(f"delivery for {owner} did not start")


def _wait_exit(pid: int, timeout: float = 10) -> sqlite3.Row:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in _exits():
            if int(row["pid"]) == int(pid):
                return row
        time.sleep(0.05)
    raise RuntimeError(f"parent did not record exit for pid {pid}")


def _release(owner: str) -> None:
    (RELEASE_DIR / f"{owner}.go").write_text("go", encoding="utf-8")


def _send(owner: str, phase: str) -> None:
    deliver.apply_async(args=[owner, phase], queue="probe")


def _kill(pid: int) -> None:
    os.kill(int(pid), signal.SIGKILL)


def _case_after() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("after-owner", "after")
        first = _wait_owner("after-owner")
        _kill(int(first["pid"]))
        exit_row = _wait_exit(int(first["pid"]))
        second = None
        deadline = time.time() + 10
        while time.time() < deadline:
            matches = [row for row in _rows() if row["owner"] == "after-owner" and int(row["pid"]) != int(first["pid"])]
            if matches:
                second = matches[0]
                break
            time.sleep(0.05)
        if second is None:
            raise RuntimeError("redelivery did not start a second child")
        assert int(second["pid"]) != int(first["pid"])
        assert second["request_id"] == first["request_id"]
        assert second["policy"] == "no_external_replay"
        assert not any(int(row["pid"]) == int(second["pid"]) for row in _exits())
        assert float(exit_row["recorded_at"]) < float(second["started_at"])
        assert "SIGKILL" in exit_row["detail"]
        _release("after-owner")
        return {
            "case": "after_handoff",
            "request_id": first["request_id"],
            "dead_pid": int(first["pid"]),
            "live_pid": int(second["pid"]),
            "policy": second["policy"],
            "exit_before_redelivery": True,
        }
    finally:
        _stop_worker(proc)


def _case_before() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("before-owner", "before")
        first = _wait_owner("before-owner")
        assert int(first["handed_off"]) == 0
        _kill(int(first["pid"]))
        _wait_exit(int(first["pid"]))
        second = None
        deadline = time.time() + 10
        while time.time() < deadline:
            matches = [row for row in _rows() if row["owner"] == "before-owner" and int(row["pid"]) != int(first["pid"])]
            if matches:
                second = matches[0]
                break
            time.sleep(0.05)
        if second is None:
            raise RuntimeError("replacement delivery did not start")
        assert second["policy"] == "replacement_allowed"
        assert not any(int(row["pid"]) == int(second["pid"]) for row in _exits())
        _release("before-owner")
        return {"case": "before_handoff", "policy": second["policy"], "same_request": second["request_id"] == first["request_id"]}
    finally:
        _stop_worker(proc)


def _case_duplicate() -> dict:
    proc = _start_worker(2, "on")
    try:
        _wait_hook()
        _send("live-a", "after")
        _send("live-b", "after")
        first = _wait_owner("live-a")
        other = _wait_owner("live-b")
        _kill(int(first["pid"]))
        _wait_exit(int(first["pid"]))
        time.sleep(0.4)
        assert not any(int(row["pid"]) == int(other["pid"]) for row in _exits())
        redelivered = [
            row for row in _rows() if row["request_id"] == first["request_id"] and int(row["pid"]) != int(first["pid"])
        ]
        assert redelivered, "same request id was not delivered to a new pid"
        assert not any(int(row["pid"]) == int(redelivered[0]["pid"]) for row in _exits())
        assert int(redelivered[0]["pid"]) != int(other["pid"])
        _release("live-a")
        _release("live-b")
        return {
            "case": "duplicate_while_live",
            "dead_pid": int(first["pid"]),
            "other_live_pid": int(other["pid"]),
            "redelivery_pid": int(redelivered[0]["pid"]),
            "other_marked_dead": False,
        }
    finally:
        _stop_worker(proc)


def _case_hook_fails() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("hook-owner", "after")
        first = _wait_owner("hook-owner")
        EXIT_DB.chmod(0)
        _kill(int(first["pid"]))
        second = None
        deadline = time.time() + 10
        while time.time() < deadline:
            matches = [row for row in _rows() if row["owner"] == "hook-owner" and int(row["pid"]) != int(first["pid"])]
            if matches:
                second = matches[0]
                break
            time.sleep(0.05)
        if second is None:
            raise RuntimeError("hook failure swallowed the requeue")
        assert _meta("hook_error")
        EXIT_DB.chmod(0o644)
        assert _exits() == []
        assert not any(int(row["pid"]) == int(second["pid"]) for row in _exits())
        _release("hook-owner")
        return {"case": "hook_write_fails", "requeued": True, "exit_rows": 0, "hook_error": _meta("hook_error")}
    finally:
        if EXIT_DB.exists():
            EXIT_DB.chmod(0o644)
        _stop_worker(proc)


def _case_parent_killed() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("orphan-owner", "after")
        first = _wait_owner("orphan-owner")
        os.kill(proc.pid, signal.SIGKILL)
        time.sleep(0.8)
        assert _exits() == []
        child_alive = True
        try:
            os.kill(int(first["pid"]), 0)
        except OSError:
            child_alive = False
        return {
            "case": "parent_sigkill",
            "exit_rows": 0,
            "child_still_alive": child_alive,
            "policy": "unobserved",
        }
    finally:
        _stop_worker(proc)


def _case_register_raise() -> dict:
    proc = _start_worker(1, "raise-register")
    try:
        time.sleep(3)
        alive = proc.poll() is None
        assert alive
        return {"case": "hook_register_raise", "worker_still_alive": True}
    finally:
        if proc.poll() is None:
            _stop_worker(proc)


def _case_register_fails() -> dict:
    proc = _start_worker(1, "fail-register")
    try:
        deadline = time.time() + 15
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
        code_before_send = proc.poll()
        _send("unregistered-owner", "before")
        time.sleep(1.0)
        consumed = any(row["owner"] == "unregistered-owner" for row in _rows())
        assert (ROOT / "register-refused").read_text(encoding="utf-8") == "refused"
        assert code_before_send == 78
        assert not consumed
        return {
            "case": "hook_register_fails",
            "returncode": code_before_send,
            "consumed": False,
        }
    finally:
        if proc.poll() is None:
            _stop_worker(proc)


def main() -> int:
    failures: list[str] = []
    results: list[dict] = []
    cases = (
        ("after", _case_after),
        ("before", _case_before),
        ("duplicate", _case_duplicate),
        ("hook", _case_hook_fails),
        ("parent", _case_parent_killed),
        ("register-raise", _case_register_raise),
        ("register", _case_register_fails),
    )
    only = os.environ.get("PROBE_ONLY")
    for name, func in cases:
        if only and name != only:
            continue
        _flush_broker()
        _reset()
        try:
            results.append(func())
            print(f"PASS {name} {results[-1]}")
        except Exception as exc:
            trace = ROOT / "hook-trace.txt"
            log = ROOT / "worker.log"
            extra = trace.read_text(encoding="utf-8", errors="replace") if trace.exists() else "no hook trace"
            worker = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else "no worker log"
            failures.append(f"{name}: {type(exc).__name__}: {exc} | {extra}")
            print(f"FAIL {name} {type(exc).__name__}: {exc}")
            print(f"TRACE {extra}")
            print(f"LOG {worker}")
    print("RESULTS")
    for item in results:
        print(item)
    if failures:
        print("FAILURES")
        for item in failures:
            print(item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
