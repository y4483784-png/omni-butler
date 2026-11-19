from __future__ import annotations

import threading
import time
from uuid import uuid4

from app.core.db import SessionLocal, set_rls_context
from app.models.models import Session as ChatSession
from app.models.models import SessionCursor, SessionEvent, User
from app.services.resume import publish_event, read_snapshot


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"resume-{uuid4().hex}"
        user = User(external_id=external_id, name=external_id, is_active=1, password_hash="")
        db.add(user)
        db.commit()
        db.refresh(user)
        return int(user.id)
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="resume")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def test_seq_100_blocks_the_publisher_of_seq_101():
    user_id = _user()
    session_id = _session(user_id)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        db.add(SessionCursor(session_id=session_id, user_id=user_id, published_seq=99))
        db.commit()
    finally:
        db.close()

    first = SessionLocal()
    set_rls_context(first, user_id)
    allocated = publish_event(
        first,
        user_id=user_id,
        session_id=session_id,
        kind="status",
        body="held",
    )
    assert allocated == 100

    started = threading.Event()
    finished = threading.Event()
    box: dict = {}

    def second() -> None:
        other = SessionLocal()
        try:
            set_rls_context(other, user_id)
            started.set()
            box["seq"] = publish_event(
                other,
                user_id=user_id,
                session_id=session_id,
                kind="status",
                body="next",
            )
            other.commit()
        except Exception as exc:
            box["error"] = exc
            other.rollback()
        finally:
            other.close()
            finished.set()

    thread = threading.Thread(target=second)
    thread.start()
    assert started.wait(2)
    time.sleep(0.4)
    assert not finished.is_set()
    assert "seq" not in box
    first.commit()
    first.close()
    assert finished.wait(5)
    thread.join(timeout=5)
    assert "error" not in box
    assert box["seq"] == 101

    check = SessionLocal()
    try:
        set_rls_context(check, user_id)
        rows = (
            check.query(SessionEvent)
            .filter(SessionEvent.session_id == session_id)
            .order_by(SessionEvent.seq.asc())
            .all()
        )
        stored = [int(row.seq) for row in rows]
        event_ids = [int(row.id) for row in rows]
        cursor = check.get(SessionCursor, session_id)
        published = int(cursor.published_seq)
        snapshot = read_snapshot(check, user_id=user_id, session_id=session_id)
        check.commit()
    finally:
        check.close()
    assert stored == [100, 101]
    assert published == 101
    assert snapshot["cursor"] == published
    assert snapshot["cursor"] not in event_ids
