"""Show the resume-lock harness fault that CI hit, without weakening the suite.

The old continue/consume test returned from ``_wait_for_lock`` before
``release.set()`` and before ``join``. A failed assertion therefore left the
gated transaction open. The next test's ``drop_all`` then deadlocked with that
session. This script runs that control flow against Postgres and then the
``finally`` release. It does not import the application.
"""

from __future__ import annotations

import sys
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

URL = "postgresql+psycopg://omni:omni@127.0.0.1/omni_lock"


def _old_gate_then_drop() -> str:
    engine = create_engine(URL)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS harness_gate"))
        conn.execute(text("CREATE TABLE harness_gate (id integer primary key)"))
        conn.execute(text("INSERT INTO harness_gate (id) VALUES (1)"))
        conn.commit()
    release = threading.Event()

    def hold() -> None:
        conn = engine.connect()
        trans = conn.begin()
        conn.execute(text("SELECT id FROM harness_gate WHERE id = 1 FOR UPDATE"))
        release.wait(30)
        trans.rollback()
        conn.close()

    thread = threading.Thread(target=hold)
    thread.start()
    # The old test asserted here and returned on failure. It did not set release.
    drop = engine.connect()
    drop.execute(text("SET lock_timeout = '1s'"))
    try:
        drop.execute(text("DROP TABLE harness_gate"))
        drop.commit()
        outcome = "drop_succeeded_while_holder_alive"
    except OperationalError as exc:
        drop.rollback()
        outcome = f"{type(exc.orig).__name__}: {str(exc.orig).splitlines()[0]}"
    finally:
        drop.close()
        release.set()
        thread.join(5)
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS harness_gate"))
            conn.commit()
        engine.dispose()
    return outcome


def _finally_releases_then_drop() -> str:
    engine = create_engine(URL)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE harness_gate (id integer primary key)"))
        conn.execute(text("INSERT INTO harness_gate (id) VALUES (1)"))
        conn.commit()
    release = threading.Event()
    blocked = threading.Event()

    def hold() -> None:
        conn = engine.connect()
        trans = conn.begin()
        conn.execute(text("SELECT id FROM harness_gate WHERE id = 1 FOR UPDATE"))
        blocked.set()
        release.wait(5)
        trans.rollback()
        conn.close()

    thread = threading.Thread(target=hold)
    thread.start()
    assert blocked.wait(5)
    try:
        try:
            raise AssertionError("gate failed")
        finally:
            release.set()
            thread.join(5)
    except AssertionError:
        pass
    drop = engine.connect()
    try:
        drop.execute(text("DROP TABLE harness_gate"))
        drop.commit()
        outcome = "drop_succeeded"
    finally:
        drop.close()
        engine.dispose()
    return outcome


def main() -> int:
    old = _old_gate_then_drop()
    print(f"OLD {old}")
    fixed = _finally_releases_then_drop()
    print(f"FINALLY {fixed}")
    if "lock timeout" not in old.lower() and "deadlock" not in old.lower():
        return 1
    if fixed != "drop_succeeded":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
