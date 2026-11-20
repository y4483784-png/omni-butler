"""Celery prefork parent-reap probe.

This file is not imported by the application. It talks to Redis database 15
and a queue named ``probe``. It does not publish to the production ``default``
queue and it does not start the compose worker.

A delivery is one pool-task execution. The parent mints its id. Call attempts
reference that id. Exit updates that delivery only.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
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
RELEASE_DIR = ROOT / "release"
INCARNATION_FILE = ROOT / "incarnation"


def _connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _install(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            delivery_id TEXT PRIMARY KEY,
            incarnation TEXT NOT NULL,
            request_id TEXT NOT NULL,
            child_pid INTEGER NOT NULL,
            minted_by TEXT NOT NULL,
            started_at TEXT NOT NULL,
            exited_at TEXT,
            exitcode TEXT,
            UNIQUE (incarnation, request_id, child_pid)
        );
        CREATE TABLE IF NOT EXISTS attempts (
            call_id TEXT NOT NULL,
            owner_token TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
            handed_off INTEGER NOT NULL,
            policy TEXT,
            label TEXT NOT NULL,
            PRIMARY KEY (call_id, owner_token, attempt)
        );
        CREATE TABLE IF NOT EXISTS effects (
            call_id TEXT NOT NULL,
            owner_token TEXT,
            attempt INTEGER,
            body TEXT NOT NULL,
            attribution TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS effects_attributed
            ON effects (call_id, owner_token, attempt)
            WHERE owner_token IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS effects_unattributed
            ON effects (call_id, body)
            WHERE owner_token IS NULL;
        CREATE TABLE IF NOT EXISTS effect_conflicts (
            call_id TEXT NOT NULL,
            owner_token TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            body TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS deliveries_parent_only
        BEFORE INSERT ON deliveries
        WHEN NEW.minted_by != 'parent'
        BEGIN
            SELECT RAISE(ABORT, 'child cannot mint a delivery');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS refuse_exit
        BEFORE UPDATE OF exited_at ON deliveries
        WHEN EXISTS (SELECT 1 FROM meta WHERE key = 'fail_exit' AND value = '1')
        BEGIN
            SELECT RAISE(ABORT, 'exit database unavailable');
        END
        """
    )
    conn.commit()


def _now() -> str:
    return f"{time.time():.6f}"


def _incarnation() -> str:
    return INCARNATION_FILE.read_text(encoding="utf-8").strip()


def _note(key: str, value: str) -> None:
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _meta(key: str) -> str | None:
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])
    finally:
        conn.close()


def _mint_delivery(request_id: str, pid: int) -> None:
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        conn.execute(
            """
            INSERT INTO deliveries (
                delivery_id, incarnation, request_id, child_pid, minted_by, started_at
            ) VALUES (?, ?, ?, ?, 'parent', ?)
            ON CONFLICT (incarnation, request_id, child_pid) DO NOTHING
            """,
            (uuid.uuid4().hex, _incarnation(), str(request_id), int(pid), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _record_exit(request_id: str, pid: int | None, detail: str) -> None:
    if pid is None:
        raise OSError("parent reap did not name the child pid")
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        updated = conn.execute(
            """
            UPDATE deliveries
            SET exited_at = ?, exitcode = ?
            WHERE incarnation = ? AND request_id = ? AND child_pid = ? AND exited_at IS NULL
            """,
            (_now(), detail, _incarnation(), str(request_id), int(pid)),
        )
        if updated.rowcount != 1:
            raise OSError(f"exit matched {updated.rowcount} deliveries")
        conn.commit()
    finally:
        conn.close()


@worker_init.connect
def _register_hook(**_kwargs) -> None:
    if os.environ.get("PROBE_HOOK") == "raise-register":
        raise RuntimeError("hook registration refused")
    if os.environ.get("PROBE_HOOK") == "fail-register":
        (ROOT / "register-refused").write_text("refused", encoding="utf-8")
        os._exit(78)
    INCARNATION_FILE.write_text(uuid.uuid4().hex, encoding="utf-8")
    original_accept = Request.on_accepted
    original_failure = Request.on_failure

    def accept(self, pid, time_accepted):
        result = original_accept(self, pid, time_accepted)
        _mint_delivery(self.id, int(pid))
        return result

    def failed(self, exc_info, send_failed_event=True, return_ok=False):
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
            return original_failure(
                self,
                exc_info,
                send_failed_event=send_failed_event,
                return_ok=return_ok,
            )

    Request.on_accepted = accept
    Request.on_failure = failed
    _note("hook_ready", "1")


def _lookup_delivery(request_id: str, pid: int) -> sqlite3.Row | None:
    deadline = time.time() + 5
    while time.time() < deadline:
        conn = _connect(DELIVERY_DB)
        try:
            _install(conn)
            row = conn.execute(
                """
                SELECT * FROM deliveries
                WHERE incarnation = ? AND request_id = ? AND child_pid = ?
                """,
                (_incarnation(), str(request_id), int(pid)),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            return row
        time.sleep(0.05)
    return None


def _policy(conn: sqlite3.Connection, delivery_id: str, request_id: str) -> str:
    priors = conn.execute(
        """
        SELECT delivery_id, exited_at FROM deliveries
        WHERE request_id = ? AND delivery_id != ?
        """,
        (request_id, delivery_id),
    ).fetchall()
    exited = [row["delivery_id"] for row in priors if row["exited_at"] is not None]
    if not exited:
        return "unobserved"
    handed = conn.execute(
        f"""
        SELECT 1 FROM attempts
        WHERE delivery_id IN ({",".join("?" for _ in exited)}) AND handed_off = 1
        LIMIT 1
        """,
        exited,
    ).fetchone()
    if handed is None:
        return "replacement_allowed"
    return "no_external_replay"


@APP.task(bind=True, acks_late=True, reject_on_worker_lost=True, name="probe.deliver")
def deliver(self, owner: str, phase: str, calls: list[str] | None = None) -> str:
    names = list(calls) if calls else [f"{owner}-call"]
    row = _lookup_delivery(str(self.request.id), os.getpid())
    if row is None:
        _note("child_unbound", owner)
        return "unbound"
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        policy = _policy(conn, str(row["delivery_id"]), str(self.request.id))
        owner_token = uuid.uuid4().hex
        handed_off = 1 if phase == "after" else 0
        for call_id in names:
            conn.execute(
                """
                INSERT INTO attempts (
                    call_id, owner_token, attempt, delivery_id, handed_off, policy, label
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
                """,
                (call_id, owner_token, str(row["delivery_id"]), handed_off, policy, owner),
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


def _attempt_rows(label: str | None = None) -> list[sqlite3.Row]:
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        sql = """
            SELECT a.label, a.call_id, a.owner_token, a.attempt, a.handed_off, a.policy,
                   d.delivery_id, d.incarnation, d.request_id, d.child_pid,
                   d.exited_at, d.exitcode, d.started_at
            FROM attempts a
            JOIN deliveries d ON d.delivery_id = a.delivery_id
        """
        if label is None:
            return list(conn.execute(sql).fetchall())
        return list(conn.execute(sql + " WHERE a.label = ?", (label,)).fetchall())
    finally:
        conn.close()


def _deliveries() -> list[sqlite3.Row]:
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
        return list(conn.execute("SELECT * FROM deliveries ORDER BY started_at").fetchall())
    finally:
        conn.close()


def _wait_attempts(label: str, count: int, timeout: float = 15) -> list[sqlite3.Row]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _attempt_rows(label)
        if len(rows) >= count:
            return rows
        time.sleep(0.05)
    raise RuntimeError(f"{label} did not record {count} attempts")


def _wait_delivery_exit(delivery_id: str, timeout: float = 10) -> sqlite3.Row:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in _deliveries():
            if row["delivery_id"] == delivery_id and row["exited_at"] is not None:
                return row
        time.sleep(0.05)
    raise RuntimeError(f"parent did not record exit for delivery {delivery_id}")


def _wait_other_delivery(request_id: str, delivery_id: str, timeout: float = 10) -> sqlite3.Row:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in _deliveries():
            if row["request_id"] == request_id and row["delivery_id"] != delivery_id:
                return row
        time.sleep(0.05)
    raise RuntimeError("redelivery did not mint a second delivery")


def _release(owner: str) -> None:
    (RELEASE_DIR / f"{owner}.go").write_text("go", encoding="utf-8")


def _send(owner: str, phase: str, calls: list[str] | None = None) -> None:
    deliver.apply_async(args=[owner, phase, calls], queue="probe")


def _kill(pid: int) -> None:
    os.kill(int(pid), signal.SIGKILL)


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
    conn = _connect(DELIVERY_DB)
    try:
        _install(conn)
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
        if DELIVERY_DB.exists() and _meta("hook_ready") == "1" and INCARNATION_FILE.exists():
            return
        time.sleep(0.1)
    raise RuntimeError("worker did not register the parent hook")


def _case_after() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("after-owner", "after")
        first_attempts = _wait_attempts("after-owner", 1)
        first = first_attempts[0]
        _kill(int(first["child_pid"]))
        dead = _wait_delivery_exit(str(first["delivery_id"]))
        second = _wait_other_delivery(str(first["request_id"]), str(first["delivery_id"]))
        deadline = time.time() + 10
        live_attempts = []
        while time.time() < deadline:
            live_attempts = [
                row for row in _attempt_rows("after-owner") if row["delivery_id"] == second["delivery_id"]
            ]
            if live_attempts:
                break
            time.sleep(0.05)
        if not live_attempts:
            raise RuntimeError("redelivery wrote no attempt")
        assert second["delivery_id"] != first["delivery_id"]
        assert second["request_id"] == first["request_id"]
        assert second["incarnation"] == first["incarnation"]
        assert int(second["child_pid"]) != int(first["child_pid"])
        assert second["exited_at"] is None
        assert live_attempts[0]["policy"] == "no_external_replay"
        assert live_attempts[0]["owner_token"] != first["owner_token"]
        assert float(dead["exited_at"]) < float(second["started_at"])
        assert "SIGKILL" in str(dead["exitcode"])
        _release("after-owner")
        return {
            "case": "after_handoff",
            "request_id": first["request_id"],
            "dead_delivery": first["delivery_id"],
            "live_delivery": second["delivery_id"],
            "policy": live_attempts[0]["policy"],
            "exit_before_redelivery": True,
        }
    finally:
        _stop_worker(proc)


def _case_before() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("before-owner", "before")
        first = _wait_attempts("before-owner", 1)[0]
        assert int(first["handed_off"]) == 0
        _kill(int(first["child_pid"]))
        _wait_delivery_exit(str(first["delivery_id"]))
        second = _wait_other_delivery(str(first["request_id"]), str(first["delivery_id"]))
        deadline = time.time() + 10
        live = []
        while time.time() < deadline:
            live = [row for row in _attempt_rows("before-owner") if row["delivery_id"] == second["delivery_id"]]
            if live:
                break
            time.sleep(0.05)
        if not live:
            raise RuntimeError("replacement delivery wrote no attempt")
        assert live[0]["policy"] == "replacement_allowed"
        assert second["exited_at"] is None
        _release("before-owner")
        return {
            "case": "before_handoff",
            "policy": live[0]["policy"],
            "same_request": second["request_id"] == first["request_id"],
            "distinct_delivery": second["delivery_id"] != first["delivery_id"],
        }
    finally:
        _stop_worker(proc)


def _case_duplicate() -> dict:
    proc = _start_worker(2, "on")
    try:
        _wait_hook()
        _send("live-a", "after")
        _send("live-b", "after")
        first = _wait_attempts("live-a", 1)[0]
        other = _wait_attempts("live-b", 1)[0]
        _kill(int(first["child_pid"]))
        _wait_delivery_exit(str(first["delivery_id"]))
        second = _wait_other_delivery(str(first["request_id"]), str(first["delivery_id"]))
        other_now = [row for row in _deliveries() if row["delivery_id"] == other["delivery_id"]][0]
        assert other_now["exited_at"] is None
        assert second["exited_at"] is None
        assert second["delivery_id"] != other["delivery_id"]
        _release("live-a")
        _release("live-b")
        return {
            "case": "duplicate_while_live",
            "dead_delivery": first["delivery_id"],
            "other_live_delivery": other["delivery_id"],
            "redelivery_delivery": second["delivery_id"],
            "other_marked_dead": False,
        }
    finally:
        _stop_worker(proc)


def _case_siblings() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("sib", "after", ["call-a", "call-b"])
        rows = _wait_attempts("sib", 2)
        assert {row["call_id"] for row in rows} == {"call-a", "call-b"}
        assert len({row["delivery_id"] for row in rows}) == 1
        assert len({row["owner_token"] for row in rows}) == 1
        delivery_id = str(rows[0]["delivery_id"])
        request_id = str(rows[0]["request_id"])
        _kill(int(rows[0]["child_pid"]))
        dead = _wait_delivery_exit(delivery_id)
        second = _wait_other_delivery(request_id, delivery_id)
        assert second["exited_at"] is None
        assert second["incarnation"] == rows[0]["incarnation"]
        still = [row for row in _attempt_rows("sib") if row["delivery_id"] == delivery_id]
        assert {row["call_id"] for row in still} == {"call-a", "call-b"}
        assert "SIGKILL" in str(dead["exitcode"])
        _release("sib")
        return {
            "case": "two_calls_one_delivery",
            "delivery_id": delivery_id,
            "calls": ["call-a", "call-b"],
            "redelivery_delivery": second["delivery_id"],
            "redelivery_exited": False,
        }
    finally:
        _stop_worker(proc)


def _case_hook_fails() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("hook-owner", "after")
        first = _wait_attempts("hook-owner", 1)[0]
        _note("fail_exit", "1")
        _kill(int(first["child_pid"]))
        second = _wait_other_delivery(str(first["request_id"]), str(first["delivery_id"]))
        first_now = [row for row in _deliveries() if row["delivery_id"] == first["delivery_id"]][0]
        assert first_now["exited_at"] is None
        assert second["exited_at"] is None
        assert _meta("hook_error")
        _release("hook-owner")
        return {"case": "hook_write_fails", "requeued": True, "dead_delivery_exited": False, "hook_error": _meta("hook_error")}
    finally:
        _stop_worker(proc)


def _case_parent_killed() -> dict:
    proc = _start_worker(1, "on")
    try:
        _wait_hook()
        _send("orphan-owner", "after")
        first = _wait_attempts("orphan-owner", 1)[0]
        os.kill(proc.pid, signal.SIGKILL)
        time.sleep(0.8)
        row = [item for item in _deliveries() if item["delivery_id"] == first["delivery_id"]][0]
        assert row["exited_at"] is None
        child_alive = True
        try:
            os.kill(int(first["child_pid"]), 0)
        except OSError:
            child_alive = False
        return {
            "case": "parent_sigkill",
            "delivery_id": first["delivery_id"],
            "exited": False,
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
        assert (ROOT / "register-refused").read_text(encoding="utf-8") == "refused"
        assert code_before_send == 78
        assert _deliveries() == []
        assert _attempt_rows() == []
        return {"case": "hook_register_fails", "returncode": code_before_send, "consumed": False}
    finally:
        if proc.poll() is None:
            _stop_worker(proc)


def acknowledgement_authorizes(submitted_control: bool, submitted_call_id: str | None, parked_call_id: str) -> bool:
    """True only for the acknowledgement control showing this parked call.

    An ordinary message does not set ``submitted_control``. A call id copied
    from message text, or filled in by the driver, is not that control.
    """
    if not submitted_control:
        return False
    if submitted_call_id is None or submitted_call_id == "":
        return False
    return submitted_call_id == parked_call_id


def _record_effect(conn: sqlite3.Connection, call_id: str, owner: str | None, attempt: int | None, body: str) -> str:
    if owner is None:
        existing = conn.execute(
            "SELECT body FROM effects WHERE call_id = ? AND owner_token IS NULL AND body = ?",
            (call_id, body),
        ).fetchone()
        if existing is not None:
            return "duplicate"
        conn.execute(
            "INSERT INTO effects (call_id, owner_token, attempt, body, attribution) VALUES (?, NULL, NULL, ?, 'unattributed')",
            (call_id, body),
        )
        return "stored"
    existing = conn.execute(
        "SELECT body FROM effects WHERE call_id = ? AND owner_token = ? AND attempt = ?",
        (call_id, owner, attempt),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO effects (call_id, owner_token, attempt, body, attribution)
            VALUES (?, ?, ?, ?, 'attributed')
            """,
            (call_id, owner, attempt, body),
        )
        return "stored"
    if existing["body"] == body:
        return "same"
    conn.execute(
        "INSERT INTO effect_conflicts (call_id, owner_token, attempt, body) VALUES (?, ?, ?, ?)",
        (call_id, owner, attempt, body),
    )
    return "rejected"


def _synthetic() -> dict:
    conn = _connect(":memory:")
    try:
        _install(conn)
        conn.execute(
            """
            INSERT INTO deliveries (
                delivery_id, incarnation, request_id, child_pid, minted_by, started_at
            ) VALUES ('del-a', 'inc-a', 'req', 100, 'parent', '1'),
                     ('del-b', 'inc-b', 'req', 100, 'parent', '2'),
                     ('del-c', 'inc-a', 'req-2', 7, 'parent', '3')
            """
        )
        both = conn.execute(
            "SELECT delivery_id FROM deliveries WHERE request_id = 'req' AND child_pid = 100"
        ).fetchall()
        assert {row["delivery_id"] for row in both} == {"del-a", "del-b"}
        updated = conn.execute(
            """
            UPDATE deliveries SET exited_at = '9', exitcode = 'signal 9 (SIGKILL)'
            WHERE incarnation = 'inc-a' AND request_id = 'req' AND child_pid = 100 AND exited_at IS NULL
            """
        )
        assert updated.rowcount == 1
        conn.commit()
        dead = conn.execute("SELECT exited_at FROM deliveries WHERE delivery_id = 'del-a'").fetchone()
        live = conn.execute("SELECT exited_at FROM deliveries WHERE delivery_id = 'del-b'").fetchone()
        assert dead["exited_at"] == "9"
        assert live["exited_at"] is None
        forged = False
        try:
            conn.execute(
                """
                INSERT INTO deliveries (
                    delivery_id, incarnation, request_id, child_pid, minted_by, started_at
                ) VALUES ('forged', 'inc-a', 'req-x', 1, 'child', '4')
                """
            )
        except sqlite3.IntegrityError:
            forged = True
            conn.rollback()
        assert forged
        missing = False
        try:
            conn.execute(
                """
                INSERT INTO attempts (
                    call_id, owner_token, attempt, delivery_id, handed_off, policy, label
                ) VALUES ('call-x', 'owner-x', 1, 'missing', 0, NULL, 'x')
                """
            )
        except sqlite3.IntegrityError:
            missing = True
            conn.rollback()
        assert missing
        conn.execute(
            """
            INSERT INTO attempts (
                call_id, owner_token, attempt, delivery_id, handed_off, policy, label
            ) VALUES ('call-a', 'owner-1', 1, 'del-c', 1, NULL, 'sib'),
                     ('call-b', 'owner-1', 1, 'del-c', 0, NULL, 'sib')
            """
        )
        conn.execute(
            """
            UPDATE deliveries SET exited_at = '9', exitcode = 'signal 9 (SIGKILL)'
            WHERE incarnation = 'inc-a' AND request_id = 'req-2' AND child_pid = 7
            """
        )
        sibling_rows = conn.execute(
            "SELECT call_id, delivery_id FROM attempts WHERE delivery_id = 'del-c' ORDER BY call_id"
        ).fetchall()
        assert [row["call_id"] for row in sibling_rows] == ["call-a", "call-b"]
        assert conn.execute("SELECT count(*) AS n FROM deliveries WHERE request_id = 'req-2'").fetchone()["n"] == 1
        assert _record_effect(conn, "call-a", "owner-1", 1, "first") == "stored"
        assert _record_effect(conn, "call-a", "owner-1", 1, "first") == "same"
        assert _record_effect(conn, "call-a", "owner-1", 1, "other") == "rejected"
        kept = conn.execute(
            "SELECT body FROM effects WHERE call_id = 'call-a' AND owner_token = 'owner-1'"
        ).fetchone()
        assert kept["body"] == "first"
        assert conn.execute("SELECT count(*) AS n FROM effect_conflicts").fetchone()["n"] == 1
        assert _record_effect(conn, "call-a", None, None, "late") == "stored"
        assert _record_effect(conn, "call-a", None, None, "late") == "duplicate"
        owners = conn.execute(
            "SELECT owner_token FROM effects WHERE attribution = 'unattributed'"
        ).fetchone()
        assert owners["owner_token"] is None
        parked = "call-parked"
        message_text = parked
        ordinary = acknowledgement_authorizes(False, None, parked)
        autofill = acknowledgement_authorizes(False, message_text, parked)
        mismatch = acknowledgement_authorizes(True, "call-other", parked)
        explicit = acknowledgement_authorizes(True, parked, parked)
        assert ordinary is False
        assert autofill is False
        assert mismatch is False
        assert explicit is True
        conn.commit()
        return {
            "case": "synthetic_identity",
            "request_pid_rows": 2,
            "exact_delivery_marked": "del-a",
            "reused_pid_left_live": "del-b",
            "child_mint_rejected": True,
            "unknown_delivery_rejected": True,
            "sibling_calls": ["call-a", "call-b"],
            "first_body": "first",
            "conflict_audited": True,
            "unattributed_owner": None,
            "ordinary_message_authorizes": ordinary,
            "autofill_authorizes": autofill,
            "explicit_ack_authorizes": explicit,
        }
    finally:
        conn.close()


def main() -> int:
    failures: list[str] = []
    results: list[dict] = []
    try:
        results.append(_synthetic())
        print(f"PASS synthetic {results[-1]}")
    except Exception as exc:
        failures.append(f"synthetic: {type(exc).__name__}: {exc}")
        print(f"FAIL synthetic {type(exc).__name__}: {exc}")
    cases = (
        ("after", _case_after),
        ("before", _case_before),
        ("duplicate", _case_duplicate),
        ("siblings", _case_siblings),
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
            worker = log.read_text(encoding="utf-8", errors="replace")[-1200:] if log.exists() else "no worker log"
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
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
