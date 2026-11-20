"""Prefork parent and child claim one production delivery.

The reap probe does not import delivery_bind. This process does.
It does not consume the production loop or default queues.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HERE))


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def _launch(env: dict[str, str], log_path: Path):
    log_handle = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "prefork_claim_app",
            "worker",
            "-Q",
            "claimprobe",
            "-P",
            "prefork",
            "-c",
            "1",
            "--without-heartbeat",
            "--without-mingle",
            "--without-gossip",
            "-l",
            "warning",
        ],
        cwd=str(HERE),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return proc, log_handle


def _wait_for(path: Path, proc: subprocess.Popen, log_path: Path, seconds: float = 30) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and not path.exists():
        if proc.poll() is not None:
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
        time.sleep(0.1)
    if not path.exists():
        raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))


def _prepare(root: Path, broker: str):
    database = root / "claim.db"
    mailbox = root / "mailbox"
    mailbox.mkdir()
    os.environ["DATABASE_URL"] = f"sqlite:///{database}"
    os.environ["DATABASE_MIGRATE_URL"] = ""
    os.environ["OMNI_DELIVERY_DIR"] = str(mailbox)
    import redis
    from sqlalchemy import create_engine

    from app.core.db import Base
    import app.models.models  # noqa: F401

    engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    client = redis.Redis.from_url(broker)
    try:
        client.flushdb()
    finally:
        client.close()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(HERE), env.get("PYTHONPATH", "")])
    return database, mailbox, engine, env


def _assert_claim(mailbox: Path, engine, log_path: Path) -> dict:
    claimed = (mailbox / "claimed").read_text(encoding="utf-8").strip()
    child_incarnation = (mailbox / "child-incarnation").read_text(encoding="utf-8").strip()
    parent_incarnation = (mailbox / "ready").read_text(encoding="utf-8").strip()
    child_pid = int((mailbox / "child-pid").read_text(encoding="utf-8").strip())
    from app.models.models import TaskDelivery
    from sqlalchemy.orm import Session

    engine.dispose()
    session = Session(bind=engine)
    try:
        rows = session.query(TaskDelivery).all()
    finally:
        session.close()
    if len(rows) != 1:
        raise RuntimeError(f"expected one delivery, saw {len(rows)}")
    row = rows[0]
    if not claimed or claimed != row.delivery_id:
        names = sorted(path.name for path in mailbox.iterdir())
        log_text = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise RuntimeError(
            f"child claimed {claimed!r}, parent minted {row.delivery_id} "
            f"incarnation {parent_incarnation} pid {child_pid} files {names} log {log_text}"
        )
    if child_incarnation != parent_incarnation or child_incarnation != row.worker_incarnation:
        raise RuntimeError(
            f"incarnation parent {parent_incarnation} child {child_incarnation} row {row.worker_incarnation}"
        )
    if int(row.child_pid) != child_pid:
        raise RuntimeError(f"pid row {row.child_pid} child {child_pid}")
    mailbox_file = mailbox / f"{parent_incarnation}-{child_pid}"
    if claimed not in mailbox_file.read_text(encoding="utf-8"):
        raise RuntimeError("mailbox file does not contain the claimed delivery")
    return {
        "delivery_id": claimed,
        "incarnation": child_incarnation,
        "child_pid": child_pid,
    }


def _send_and_claim(mailbox: Path, engine, env: dict[str, str], log_path: Path) -> dict:
    proc, log_handle = _launch(env, log_path)
    try:
        _wait_for(mailbox / "ready", proc, log_path)
        from prefork_claim_app import touch

        touch.delay()
        _wait_for(mailbox / "claimed", proc, log_path)
        return _assert_claim(mailbox, engine, log_path)
    finally:
        _stop(proc)
        log_handle.close()
        engine.dispose()


def _restart_claim(broker: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="omni-prefork-restart-") as tmp:
        root = Path(tmp)
        _database, mailbox, engine, env = _prepare(root, broker)
        prior = "prior-parent-incarnation"
        (mailbox / "incarnation").write_text(prior + "\n", encoding="utf-8")
        old_mailbox = mailbox / f"{prior}-1"
        old_text = "old-delivery old-request\n"
        old_mailbox.write_text(old_text, encoding="utf-8")
        first_log = root / "worker-1.log"
        first, first_handle = _launch(env, first_log)
        try:
            _wait_for(mailbox / "ready", first, first_log)
            ready_first = (mailbox / "ready").read_text(encoding="utf-8").strip()
            filed = (mailbox / "incarnation").read_text(encoding="utf-8").strip()
            if ready_first == prior or filed != ready_first:
                raise RuntimeError(f"first parent reused {prior} ready {ready_first} file {filed}")
        finally:
            _stop(first)
            first_handle.close()
        (mailbox / "ready").unlink()
        second_log = root / "worker-2.log"
        second, second_handle = _launch(env, second_log)
        try:
            deadline = time.time() + 30
            ready_second = ""
            while time.time() < deadline:
                if second.poll() is not None:
                    raise RuntimeError(second_log.read_text(encoding="utf-8", errors="replace"))
                if (mailbox / "ready").exists():
                    ready_second = (mailbox / "ready").read_text(encoding="utf-8").strip()
                    if ready_second and ready_second not in {prior, ready_first}:
                        break
                time.sleep(0.1)
            if ready_second in {"", prior, ready_first}:
                raise RuntimeError(f"second parent ready {ready_second!r} first {ready_first} prior {prior}")
            from prefork_claim_app import touch

            touch.delay()
            _wait_for(mailbox / "claimed", second, second_log)
            result = _assert_claim(mailbox, engine, second_log)
            if result["incarnation"] != ready_second:
                raise RuntimeError(f"child incarnation {result['incarnation']} parent {ready_second}")
            if result["delivery_id"] == "old-delivery":
                raise RuntimeError("child claimed the old delivery")
            if old_mailbox.read_text(encoding="utf-8") != old_text:
                raise RuntimeError("old mailbox line was rewritten")
            stale = []
            for path in mailbox.iterdir():
                if not (path.name.startswith(prior) or path.name.startswith(f"{ready_first}-")):
                    continue
                if result["delivery_id"] in path.read_text(encoding="utf-8"):
                    stale.append(path.name)
            if stale:
                raise RuntimeError(f"new delivery was written on an old mailbox {stale}")
            print("PASS restart_claim", {"prior": prior, "first": ready_first, **result})
            return result
        finally:
            _stop(second)
            second_handle.close()
            engine.dispose()


def main() -> int:
    broker = os.environ.get("PROBE_BROKER", "redis://127.0.0.1:6379/14")
    os.environ["PROBE_BROKER"] = broker
    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "false"
    os.environ["REDIS_ENABLED"] = "false"
    os.environ.setdefault("SECRET_KEY", "test-secret-key")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")
    os.environ["VISION_OCR_ENABLED"] = "false"
    with tempfile.TemporaryDirectory(prefix="omni-prefork-claim-") as tmp:
        root = Path(tmp)
        _database, mailbox, engine, env = _prepare(root, broker)
        result = _send_and_claim(mailbox, engine, env, root / "worker.log")
        print("PASS production_claim", result)
    _restart_claim(broker)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL production_claim {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
