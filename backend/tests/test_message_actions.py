"""Tests for message feedback and regenerate."""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.db import SessionLocal, init_db
from app.models.models import Message
from app.models.models import Session as ChatSession


def _seed_turns() -> tuple[int, int, int]:
    init_db()
    db = SessionLocal()
    try:
        sess = ChatSession(user_id=1, title="测试")
        db.add(sess)
        db.flush()
        u1 = Message(session_id=sess.id, role="user", content="你好")
        a1 = Message(session_id=sess.id, role="assistant", content="旧回复")
        u2 = Message(session_id=sess.id, role="user", content="第二问")
        a2 = Message(session_id=sess.id, role="assistant", content="后续回复")
        db.add_all([u1, a1, u2, a2])
        db.commit()
        db.refresh(a1)
        db.refresh(a2)
        return sess.id, a1.id, a2.id
    finally:
        db.close()


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for part in body.split("\n\n"):
        for line in part.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_get_messages_includes_id_and_feedback(auth_client: TestClient):
    sid, a1_id, _ = _seed_turns()
    r = auth_client.get(f"/api/sessions/{sid}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 4
    assert msgs[1]["id"] == a1_id
    assert msgs[1]["feedback"] is None


def test_feedback_assistant_up_down_clear(auth_client: TestClient):
    _, a1_id, _ = _seed_turns()
    r = auth_client.patch(f"/api/messages/{a1_id}/feedback", json={"rating": "up"})
    assert r.status_code == 200
    assert r.json()["feedback"] == "up"

    r2 = auth_client.patch(f"/api/messages/{a1_id}/feedback", json={"rating": "down"})
    assert r2.json()["feedback"] == "down"

    r3 = auth_client.patch(f"/api/messages/{a1_id}/feedback", json={"rating": None})
    assert r3.json()["feedback"] is None


def test_feedback_rejects_user_message(auth_client: TestClient):
    sid, _, _ = _seed_turns()
    db = SessionLocal()
    try:
        user = (
            db.query(Message)
            .filter(Message.session_id == sid, Message.role == "user")
            .order_by(Message.id)
            .first()
        )
        uid = user.id
    finally:
        db.close()

    r = auth_client.patch(f"/api/messages/{uid}/feedback", json={"rating": "up"})
    assert r.status_code == 400


async def _fake_stream(*_args, **_kwargs):
    yield "新"


def test_regenerate_truncates_later_and_replaces_assistant(auth_client: TestClient):
    sid, a1_id, a2_id = _seed_turns()

    with patch("app.api.chat.stream_chat", side_effect=_fake_stream):
        r = auth_client.post(
            "/api/chat",
            json={
                "session_id": sid,
                "message": "",
                "regenerate_message_id": a1_id,
            },
        )

    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert any(e.get("type") == "token" for e in events)

    db = SessionLocal()
    try:
        rows = (
            db.query(Message)
            .filter(Message.session_id == sid)
            .order_by(Message.id)
            .all()
        )
        assert len(rows) == 2  # user1 + new assistant (u2/a2 truncated)
        assert rows[0].content == "你好"
        assert rows[1].role == "assistant"
        assert rows[1].content == "新"
        assert db.get(Message, a2_id) is None
    finally:
        db.close()


if __name__ == "__main__":
    test_get_messages_includes_id_and_feedback()
    test_feedback_assistant_up_down_clear()
    test_feedback_rejects_user_message()
    test_regenerate_truncates_later_and_replaces_assistant()
    print("ok")
