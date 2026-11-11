from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.core.auth import hash_password
from app.core.db import SessionLocal
from app.models.models import EventReceipt, MailAcceptance, Message, ScheduleConfirmClaim, TaskRun, User
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


def _subject() -> str:
    return f"纪要-{uuid4().hex[:8]}"


def _propose(client: TestClient, session_id: int, **extra):
    payload = {
        "session_id": session_id,
        "to": ["a@example.com"],
        "subject": extra.pop("subject"),
        "body": "请查收。",
    }
    payload.update(extra)
    return client.post("/api/mail/proposal", json=payload)


def _send(client: TestClient, token: str, **extra):
    payload = {
        "confirm_token": token,
        "to": ["a@example.com"],
        "subject": extra.pop("subject"),
        "body": "请查收。",
    }
    payload.update(extra)
    return client.post("/api/mail/send", json=payload)


def _acceptance_rows(user_id: int, delivery_id: str) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(MailAcceptance)
            .filter(MailAcceptance.user_id == user_id, MailAcceptance.delivery_id == delivery_id)
            .all()
        )
        return [row.remote_request_id for row in rows]
    finally:
        db.close()


def _receipt_state(user_id: int, delivery_id: str) -> str:
    db = SessionLocal()
    try:
        row = (
            db.query(EventReceipt)
            .filter(
                EventReceipt.user_id == user_id,
                EventReceipt.source == "mail",
                EventReceipt.delivery_id == delivery_id,
            )
            .one()
        )
        return row.state
    finally:
        db.close()


def _send_card_count(session_id: int) -> int:
    db = SessionLocal()
    try:
        rows = db.query(Message).filter(Message.session_id == session_id, Message.role == "assistant").all()
    finally:
        db.close()
    count = 0
    for row in rows:
        try:
            card = json.loads(row.schedule_card or "")
        except json.JSONDecodeError:
            continue
        if isinstance(card, dict) and card.get("mode") == "send":
            count += 1
    return count


def _mail_claim_count(session_id: int) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ScheduleConfirmClaim)
            .filter(
                ScheduleConfirmClaim.session_id == session_id,
                ScheduleConfirmClaim.action == "mail.send",
            )
            .count()
        )
    finally:
        db.close()


def test_proposal_writes_no_acceptance(plain_client: TestClient):
    session_id = _session()
    offered = _propose(plain_client, session_id, subject=_subject())
    assert offered.status_code == 200, offered.text
    body = offered.json()
    assert body["state"] == "received"
    assert body["route"] == "filed"
    assert body["state"] != "external_success"
    assert body["token"]
    assert _receipt_state(_admin_id(), body["delivery_id"]) == "received"
    assert _acceptance_rows(_admin_id(), body["delivery_id"]) == []
    assert _send_card_count(session_id) == 1


def test_repeat_proposal_returns_the_same_card(plain_client: TestClient):
    session_id = _session()
    subject = _subject()
    first = _propose(plain_client, session_id, subject=subject)
    second = _propose(plain_client, session_id, subject=subject)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["token"] == second.json()["token"]
    assert first.json()["delivery_id"] == second.json()["delivery_id"]
    assert _mail_claim_count(session_id) == 1
    assert _send_card_count(session_id) == 1
    assert _acceptance_rows(_admin_id(), first.json()["delivery_id"]) == []


def test_commit_without_a_token_writes_nothing(plain_client: TestClient):
    session_id = _session()
    subject = _subject()
    offered = _propose(plain_client, session_id, subject=subject)
    assert offered.status_code == 200, offered.text
    delivery_id = offered.json()["delivery_id"]
    saved = _send(plain_client, "", subject=subject)
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "缺少确认，未写入。"
    assert _acceptance_rows(_admin_id(), delivery_id) == []
    assert _receipt_state(_admin_id(), delivery_id) == "received"


def test_mailto_body_stays_received(plain_client: TestClient):
    session_id = _session()
    saved = _submit(plain_client, session_id, _delivery(), body="请打开 mailto:ada@example.com")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["state"] == "received"
    assert body["state"] != "external_success"
    assert body["route"] == "filed"
    assert _acceptance_rows(_admin_id(), saved.json()["delivery_id"]) == []


def test_commit_accepts_once(plain_client: TestClient):
    session_id = _session()
    subject = _subject()
    offered = _propose(plain_client, session_id, subject=subject)
    assert offered.status_code == 200, offered.text
    token = offered.json()["token"]
    delivery_id = offered.json()["delivery_id"]
    sent = _send(plain_client, token, subject=subject)
    assert sent.status_code == 200, sent.text
    assert sent.json()["state"] == "external_success"
    assert len(_acceptance_rows(_admin_id(), delivery_id)) == 1
    again = _send(plain_client, token, subject=subject)
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "external_success"
    assert len(_acceptance_rows(_admin_id(), delivery_id)) == 1


def test_armed_claim_without_a_row_accepts_once(plain_client: TestClient):
    session_id = _session()
    subject = _subject()
    offered = _propose(plain_client, session_id, subject=subject)
    assert offered.status_code == 200, offered.text
    token = offered.json()["token"]
    delivery_id = offered.json()["delivery_id"]
    db = SessionLocal()
    try:
        claim = db.get(ScheduleConfirmClaim, token)
        claim.status = "unknown"
        claim.remote_request_id = f"armed-{uuid4().hex}"
        remote_id = claim.remote_request_id
        db.commit()
    finally:
        db.close()
    sent = _send(plain_client, token, subject=subject)
    assert sent.status_code == 200, sent.text
    assert sent.json()["state"] == "external_success"
    assert _acceptance_rows(_admin_id(), delivery_id) == [remote_id]
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"


def test_stop_leaves_the_accepted_send(plain_client: TestClient):
    session_id = _session()
    subject = _subject()
    offered = _propose(plain_client, session_id, subject=subject)
    token = offered.json()["token"]
    delivery_id = offered.json()["delivery_id"]
    sent = _send(plain_client, token, subject=subject)
    assert sent.status_code == 200, sent.text
    assert sent.json()["state"] == "external_success"
    stopped = False
    for generation, status in _generations(session_id):
        if status not in {"completed", "stopped"}:
            task_run.stop(user_id=_admin_id(), generation=generation)
            stopped = True
    assert stopped
    assert len(_acceptance_rows(_admin_id(), delivery_id)) == 1
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"


def test_mail_entry_does_not_name_google():
    text = (Path(__file__).resolve().parents[1] / "app/services/mail_entry.py").read_text(encoding="utf-8")
    assert "gmail" not in text
    assert "calendar_secrets" not in text
    assert "googleapis" not in text


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
