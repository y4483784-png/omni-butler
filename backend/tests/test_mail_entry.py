from __future__ import annotations

from uuid import uuid4

from app.core.auth import hash_password
from app.core.db import SessionLocal
from app.models.models import EventReceipt, Message, Session as ChatSession, TaskRun, User
from app.services import task_run
from fastapi.testclient import TestClient
from tests.test_event_inbox import _calendar_count, _claim_status, _open_tokens
from tests.test_schedule_confirm_claims import _admin_id, _body, _open, _session


def _delivery() -> str:
    return f"mail-{uuid4()}"


def _card(title: str) -> dict:
    return {
        "title": title,
        "start_at": "2026-11-04T10:00:00",
        "end_at": "2026-11-04T11:00:00",
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
    }


def _submit(client: TestClient, session_id: int, delivery_id: str, **extra):
    payload = {
        "source": "mail",
        "delivery_id": delivery_id,
        "session_id": session_id,
        "body": extra.pop("body", "明天的议程改到三点"),
        "decision": extra.pop("decision", "act"),
    }
    payload.update(extra)
    return client.post("/api/events/inbox", json=payload)


def _generations(session_id: int) -> list[tuple[int, str]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(TaskRun)
            .filter(TaskRun.session_id == session_id)
            .order_by(TaskRun.generation.asc())
            .all()
        )
        return [(row.generation, row.status) for row in rows]
    finally:
        db.close()


def _stored_body(user_id: int, delivery_id: str) -> str:
    db = SessionLocal()
    try:
        row = (
            db.query(EventReceipt)
            .filter(EventReceipt.user_id == user_id, EventReceipt.delivery_id == delivery_id)
            .one()
        )
        return row.body
    finally:
        db.close()


def _receipt_count(user_id: int, delivery_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(EventReceipt)
            .filter(EventReceipt.user_id == user_id, EventReceipt.delivery_id == delivery_id)
            .count()
        )
    finally:
        db.close()


def _message_count(session_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(Message).filter(Message.session_id == session_id).count()
    finally:
        db.close()


def test_one_new_message_is_filed_and_opens_no_task(plain_client: TestClient):
    session_id = _session()
    saved = _submit(plain_client, session_id, _delivery(), card=_card("新邮件不开任务"))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["state"] == "received"
    assert body["route"] == "filed"
    assert body["created"] is True
    assert body["generation"] is None
    assert body["claim_token"] is None
    assert _generations(session_id) == []
    assert _message_count(session_id) == 0
    assert _calendar_count("新邮件不开任务") == 0


def test_same_delivery_keeps_the_first_body(plain_client: TestClient):
    session_id = _session()
    delivery = _delivery()
    first_body = "第一份正文"
    first = _submit(plain_client, session_id, delivery, body=first_body)
    second = _submit(plain_client, session_id, delivery, body="另一份正文", card=_card("重复不该出卡"))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["created"] is False
    assert second.json()["state"] == "received"
    assert second.json()["route"] == "filed"
    assert _receipt_count(_admin_id(), delivery) == 1
    assert _stored_body(_admin_id(), delivery) == first_body
    assert _message_count(session_id) == 0
    assert _calendar_count("重复不该出卡") == 0


def test_other_user_cannot_read_or_file_the_first_session(plain_client: TestClient):
    session_id = _session()
    delivery = _delivery()
    filed = _submit(plain_client, session_id, delivery, body="只有主人能看")
    assert filed.status_code == 200, filed.text
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.external_id == "mail-other-user").one_or_none()
        if other is None:
            other = User(
                external_id="mail-other-user",
                name="别人",
                password_hash=hash_password("other-pass"),
                is_active=1,
                is_admin=0,
            )
            db.add(other)
            db.commit()
            db.refresh(other)
    finally:
        db.close()
    from app.main import app

    other_client = TestClient(app)
    logged = other_client.post(
        "/api/auth/login",
        json={"username": "mail-other-user", "password": "other-pass"},
    )
    assert logged.status_code == 200, logged.text
    hidden = other_client.get(f"/api/sessions/{session_id}/messages")
    assert hidden.status_code == 404, hidden.text
    stolen = _submit(other_client, session_id, _delivery(), body="不该写进别人的会话")
    assert stolen.status_code == 404, stolen.text
    assert _receipt_count(_admin_id(), delivery) == 1
    assert _stored_body(_admin_id(), delivery) == "只有主人能看"


def test_same_fingerprint_keeps_the_open_card(plain_client: TestClient):
    title = f"邮件保持-{uuid4().hex[:8]}"
    card = _card(title)
    token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    saved = _submit(plain_client, session_id, _delivery(), generation=turn.generation, card=card)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "kept"
    assert body["state"] == "received"
    assert body["claim_token"] == token
    assert body["open_claims"] == 1
    assert _open_tokens(session_id) == [token]
    assert _claim_status(token) == "open"
    assert _calendar_count(title) == 0


def test_different_fingerprint_reissues_one_card(plain_client: TestClient):
    title = f"邮件旧卡-{uuid4().hex[:8]}"
    replacement = f"邮件新卡-{uuid4().hex[:8]}"
    token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    saved = _submit(
        plain_client,
        session_id,
        _delivery(),
        generation=turn.generation,
        card=_card(replacement),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "reissued"
    assert body["state"] == "received"
    assert body["open_claims"] == 1
    assert body["claim_token"] not in {None, token}
    assert _claim_status(token) == "cancelled"
    assert _open_tokens(session_id) == [body["claim_token"]]
    assert _calendar_count(title) == 0
    assert _calendar_count(replacement) == 0


def test_a_later_different_event_only_queues(plain_client: TestClient):
    title = f"邮件排队-{uuid4().hex[:8]}"
    first = f"邮件第一张-{uuid4().hex[:8]}"
    second = f"邮件第二张-{uuid4().hex[:8]}"
    _token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    reissued = _submit(
        plain_client,
        session_id,
        _delivery(),
        generation=turn.generation,
        card=_card(first),
    )
    assert reissued.status_code == 200, reissued.text
    kept_token = reissued.json()["claim_token"]
    queued = _submit(
        plain_client,
        session_id,
        _delivery(),
        generation=turn.generation,
        card=_card(second),
    )
    assert queued.status_code == 200, queued.text
    body = queued.json()
    assert body["route"] == "queued"
    assert body["state"] == "received"
    assert body["claim_token"] is None
    assert body["open_claims"] == 1
    assert _open_tokens(session_id) == [kept_token]
    assert _claim_status(kept_token) == "open"
    assert _calendar_count(second) == 0
    assert len(_generations(session_id)) == 1


def test_body_only_mail_while_waiting_queues(plain_client: TestClient):
    title = f"邮件只排队-{uuid4().hex[:8]}"
    token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    saved = _submit(plain_client, session_id, _delivery(), generation=turn.generation, body="只有正文")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "queued"
    assert body["state"] == "received"
    assert body["claim_token"] is None
    assert _open_tokens(session_id) == [token]
    assert _claim_status(token) == "open"


def test_completed_mail_stays_filed(plain_client: TestClient):
    session_id = _session()
    turn = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    finished = task_run.finish(user_id=_admin_id(), generation=turn.generation)
    assert finished.status == "completed"
    saved = _submit(plain_client, session_id, _delivery(), generation=turn.generation)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["state"] == "received"
    assert body["route"] == "filed"
    assert body["generation"] is None
    assert _generations(session_id) == [(turn.generation, "completed")]


def test_outbound_prefix_is_rejected(plain_client: TestClient):
    session_id = _session()
    saved = _submit(plain_client, session_id, "out:not-a-caller-id")
    assert saved.status_code == 422, saved.text
    assert _generations(session_id) == []
    db = SessionLocal()
    try:
        count = db.query(EventReceipt).filter(EventReceipt.delivery_id == "out:not-a-caller-id").count()
    finally:
        db.close()
    assert count == 0
