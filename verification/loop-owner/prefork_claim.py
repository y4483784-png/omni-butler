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
        log_path = root / "worker.log"
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
        try:
            deadline = time.time() + 30
            while time.time() < deadline and not (mailbox / "ready").exists():
                if proc.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
                time.sleep(0.1)
            if not (mailbox / "ready").exists():
                raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
            from prefork_claim_app import touch

            touch.delay()
            deadline = time.time() + 30
            while time.time() < deadline and not (mailbox / "claimed").exists():
                if proc.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
                time.sleep(0.1)
            if not (mailbox / "claimed").exists():
                raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
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
            print(
                "PASS production_claim",
                {
                    "delivery_id": claimed,
                    "incarnation": child_incarnation,
                    "child_pid": child_pid,
                },
            )
            return 0
        finally:
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
            log_handle.close()
            engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL production_claim {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
