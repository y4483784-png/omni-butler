from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.auth import hash_password
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.models import EventReceipt, MailAcceptance, Message, ScheduleConfirmClaim, TaskRun, User
from app.services import task_run
from app.services.mail_entry import SendAccepted, SendRejected, SendUnknown, bind_mail_transport
from app.services.schedule_confirm import CONFIRM_DETAIL, write_fingerprint, write_parts
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


class _FakeTransport:
    def __init__(self, result=None, lookup_result: str = "accepted"):
        self.result = SendAccepted("provider-1") if result is None else result
        self.lookup_result = lookup_result
        self.sends = 0
        self.lookups = 0
        self.looked: list[str] = []

    def send(self, draft):
        self.sends += 1
        result = self.result
        if isinstance(result, BaseException):
            raise result
        return result

    def lookup(self, provider_id: str) -> str:
        self.lookups += 1
        self.looked.append(provider_id)
        return self.lookup_result


class _EchoAttempt:
    def __init__(self, bare: bool):
        self.bare = bare
        self.sends = 0
        self.lookups = 0
        self.attempt = ""

    def send(self, draft):
        self.sends += 1
        parts = write_parts("mail.send", draft.as_claim())
        fingerprint = write_fingerprint(parts)
        db = SessionLocal()
        try:
            claim = (
                db.query(ScheduleConfirmClaim)
                .filter(
                    ScheduleConfirmClaim.action == "mail.send",
                    ScheduleConfirmClaim.fingerprint == fingerprint,
                )
                .one()
            )
            self.attempt = claim.remote_request_id or ""
        finally:
            db.close()
        if self.bare:
            return SendAccepted(self.attempt.removeprefix("attempt:"))
        return SendAccepted(self.attempt)

    def lookup(self, provider_id: str) -> str:
        self.lookups += 1
        return "accepted"


def _clear_acceptances() -> None:
    db = SessionLocal()
    try:
        db.query(MailAcceptance).filter(MailAcceptance.user_id == _admin_id()).delete()
        db.commit()
    finally:
        db.close()


@contextmanager
def _use_transport(monkeypatch, transport, name: str, *, selected: str | None = None):
    _clear_acceptances()
    monkeypatch.setattr(settings, "mail_transport", name)
    bind_mail_transport(transport)
    if selected is not None:
        monkeypatch.setattr(settings, "mail_transport", selected)
    try:
        yield transport
    finally:
        bind_mail_transport(None)
        monkeypatch.setattr(settings, "mail_transport", "")


def _claim_remote(token: str) -> tuple[str, str]:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        return row.status, row.remote_request_id or ""
    finally:
        db.close()


def _set_remote(token: str, status: str, remote: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        row.status = status
        row.remote_request_id = remote
        db.commit()
    finally:
        db.close()


def _offer(client: TestClient):
    session_id = _session()
    subject = _subject()
    offered = _propose(client, session_id, subject=subject)
    assert offered.status_code == 200, offered.text
    body = offered.json()
    return session_id, subject, body["token"], body["delivery_id"]


def test_commit_without_a_token_writes_nothing(plain_client: TestClient, monkeypatch):
    _session_id, subject, _token, delivery_id = _offer(plain_client)
    fake = _FakeTransport()
    with _use_transport(monkeypatch, fake, "memory"):
        saved = _send(plain_client, "", subject=subject)
    assert saved.status_code == 409, saved.text
    assert saved.json() == {"detail": "缺少确认，未写入。"}
    assert fake.sends == 0
    assert fake.lookups == 0
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


def _send_phase(session_id: int) -> str:
    db = SessionLocal()
    try:
        rows = db.query(Message).filter(Message.session_id == session_id, Message.role == "assistant").all()
    finally:
        db.close()
    for row in rows:
        try:
            card = json.loads(row.schedule_card or "")
        except json.JSONDecodeError:
            continue
        if isinstance(card, dict) and card.get("mode") == "send":
            return str(card.get("phase") or "")
    return ""


def test_empty_transport_leaves_the_claim_open(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport()

    def fail_seal(*_args, **_kwargs):
        raise AssertionError("note_external_success")

    monkeypatch.setattr("app.services.mail_entry.note_external_success", fail_seal)
    with _use_transport(monkeypatch, fake, "memory", selected=""):
        saved = _send(plain_client, token, subject=subject)
    assert saved.status_code == 409, saved.text
    assert saved.json() == {"detail": "邮箱还没有连接，未发送。", "state": "not_connected"}
    assert fake.sends == 0
    assert fake.lookups == 0
    assert _claim_remote(token) == ("open", "")
    assert _acceptance_rows(_admin_id(), delivery_id) == []
    assert _receipt_state(_admin_id(), delivery_id) == "received"


def test_unregistered_transport_name_does_not_send(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport()
    with _use_transport(monkeypatch, fake, "memory", selected="other"):
        saved = _send(plain_client, token, subject=subject)
    assert saved.status_code == 409, saved.text
    assert saved.json()["state"] == "not_connected"
    assert saved.json()["detail"] == "邮箱还没有连接，未发送。"
    assert fake.sends == 0
    assert _claim_remote(token) == ("open", "")
    assert _acceptance_rows(_admin_id(), delivery_id) == []
    assert _receipt_state(_admin_id(), delivery_id) == "received"


def test_provider_acceptance_is_sent_once(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport()
    with _use_transport(monkeypatch, fake, "memory"):
        sent = _send(plain_client, token, subject=subject)
        assert sent.status_code == 200, sent.text
        assert sent.json()["state"] == "sent"
        again = _send(plain_client, token, subject=subject)
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "sent"
    assert fake.sends == 1
    assert fake.lookups == 0
    assert _acceptance_rows(_admin_id(), delivery_id) == ["provider-1"]
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"
    assert _claim_remote(token) == ("written", "provider-1")


def test_transport_error_stays_unknown(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport(result=RuntimeError("down"))
    with _use_transport(monkeypatch, fake, "memory"):
        failed = _send(plain_client, token, subject=subject)
        status, remote = _claim_remote(token)
        assert failed.status_code == 409, failed.text
        assert failed.json() == {"detail": "结果未知，未再次发送。", "state": "unknown"}
        assert status == "unknown"
        assert remote.startswith("attempt:")
        assert _acceptance_rows(_admin_id(), delivery_id) == []
        assert _receipt_state(_admin_id(), delivery_id) == "received"
        again = _send(plain_client, token, subject=subject)
    assert again.status_code == 409, again.text
    assert again.json()["state"] == "unknown"
    assert fake.sends == 1
    assert fake.lookups == 0
    assert _claim_remote(token) == (status, remote)
    assert _acceptance_rows(_admin_id(), delivery_id) == []


def test_existing_acceptance_seals_without_send(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    _set_remote(token, "unknown", "provider-1")
    fake = _FakeTransport()
    with _use_transport(monkeypatch, fake, "memory"):
        db = SessionLocal()
        try:
            db.add(
                MailAcceptance(
                    user_id=_admin_id(),
                    remote_request_id="provider-1",
                    delivery_id=delivery_id,
                    recipients='["a@example.com"]',
                    subject=subject,
                    body="请查收。",
                )
            )
            db.commit()
        finally:
            db.close()
        saved = _send(plain_client, token, subject=subject)
        again = _send(plain_client, token, subject=subject)
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"] == "sent"
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "sent"
    assert fake.sends == 0
    assert fake.lookups == 0
    assert _acceptance_rows(_admin_id(), delivery_id) == ["provider-1"]
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"
    assert _claim_remote(token) == ("written", "provider-1")


def test_lookup_accepted_repairs_without_send(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    _set_remote(token, "unknown", "provider-1")
    fake = _FakeTransport(lookup_result="accepted")
    with _use_transport(monkeypatch, fake, "memory"):
        saved = _send(plain_client, token, subject=subject)
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"] == "sent"
    assert fake.sends == 0
    assert fake.lookups == 1
    assert fake.looked == ["provider-1"]
    assert _acceptance_rows(_admin_id(), delivery_id) == ["provider-1"]
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"
    assert _claim_remote(token) == ("written", "provider-1")


@pytest.mark.parametrize("lookup_result", ["absent", "unknown"])
def test_lookup_miss_stays_unknown(plain_client: TestClient, monkeypatch, lookup_result: str):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    _set_remote(token, "unknown", "provider-1")
    fake = _FakeTransport(lookup_result=lookup_result)
    with _use_transport(monkeypatch, fake, "memory"):
        saved = _send(plain_client, token, subject=subject)
    assert saved.status_code == 409, saved.text
    assert saved.json() == {"detail": "结果未知，未再次发送。", "state": "unknown"}
    assert fake.sends == 0
    assert fake.lookups == 1
    assert _claim_remote(token) == ("unknown", "provider-1")
    assert _acceptance_rows(_admin_id(), delivery_id) == []
    assert _receipt_state(_admin_id(), delivery_id) == "received"


def test_stored_provider_repairs_without_a_transport(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    _set_remote(token, "unknown", "provider-1")
    fake = _FakeTransport()
    with _use_transport(monkeypatch, fake, "memory", selected=""):
        saved = _send(plain_client, token, subject=subject)
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"] == "sent"
    assert fake.sends == 0
    assert fake.lookups == 0
    assert _acceptance_rows(_admin_id(), delivery_id) == ["provider-1"]
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"
    assert _claim_remote(token) == ("written", "provider-1")


@pytest.mark.parametrize("result", [SendUnknown(), SendAccepted("")])
def test_lost_response_does_not_resend(plain_client: TestClient, monkeypatch, result):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport(result=result)
    with _use_transport(monkeypatch, fake, "memory"):
        lost = _send(plain_client, token, subject=subject)
        status, remote = _claim_remote(token)
        assert lost.status_code == 409, lost.text
        assert lost.json() == {"detail": "结果未知，未再次发送。", "state": "unknown"}
        assert status == "unknown"
        assert remote.startswith("attempt:")
        assert _acceptance_rows(_admin_id(), delivery_id) == []
        again = _send(plain_client, token, subject=subject)
    assert again.status_code == 409, again.text
    assert again.json()["state"] == "unknown"
    assert again.json()["detail"] == "结果未知，未再次发送。"
    assert fake.sends == 1
    assert fake.lookups == 0
    assert _claim_remote(token) == (status, remote)
    assert _acceptance_rows(_admin_id(), delivery_id) == []
    assert _receipt_state(_admin_id(), delivery_id) == "received"


@pytest.mark.parametrize("bare", [False, True])
def test_armed_id_is_not_a_provider_id(plain_client: TestClient, monkeypatch, bare: bool):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _EchoAttempt(bare)
    with _use_transport(monkeypatch, fake, "memory"):
        saved = _send(plain_client, token, subject=subject)
        status, remote = _claim_remote(token)
        assert saved.status_code == 409, saved.text
        assert saved.json()["state"] == "unknown"
        assert saved.json()["detail"] == "结果未知，未再次发送。"
        assert status == "unknown"
        assert remote == fake.attempt
        assert remote.startswith("attempt:")
        assert _acceptance_rows(_admin_id(), delivery_id) == []
        again = _send(plain_client, token, subject=subject)
    assert again.status_code == 409, again.text
    assert fake.sends == 1
    assert fake.lookups == 0
    assert _claim_remote(token) == (status, remote)
    assert _acceptance_rows(_admin_id(), delivery_id) == []


def test_repeat_proposal_keeps_the_unknown_card(plain_client: TestClient, monkeypatch):
    session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport(result=SendUnknown())
    with _use_transport(monkeypatch, fake, "memory"):
        lost = _send(plain_client, token, subject=subject)
        assert lost.status_code == 409, lost.text
        assert _send_phase(session_id) == "unknown"
        again = _propose(plain_client, session_id, subject=subject)
        assert again.status_code == 200, again.text
        assert again.json()["token"] == token
        assert _send_phase(session_id) == "unknown"
        assert fake.sends == 1
        assert fake.lookups == 0
        assert _acceptance_rows(_admin_id(), delivery_id) == []
        assert _receipt_state(_admin_id(), delivery_id) == "received"


def test_rejection_reopens_for_a_new_attempt(plain_client: TestClient, monkeypatch):
    _session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport(result=SendRejected())
    with _use_transport(monkeypatch, fake, "memory"):
        rejected = _send(plain_client, token, subject=subject)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json() == {"detail": "发送被拒绝，未发送。", "state": "rejected"}
        assert rejected.json()["state"] != "sent"
        assert _claim_remote(token) == ("open", "")
        assert _acceptance_rows(_admin_id(), delivery_id) == []
        assert _receipt_state(_admin_id(), delivery_id) == "received"
        fake.result = SendAccepted("provider-1")
        sent = _send(plain_client, token, subject=subject)
    assert sent.status_code == 200, sent.text
    assert sent.json()["state"] == "sent"
    assert fake.sends == 2
    assert _acceptance_rows(_admin_id(), delivery_id) == ["provider-1"]
    assert _claim_remote(token) == ("written", "provider-1")


def test_stop_leaves_the_accepted_send(plain_client: TestClient, monkeypatch):
    session_id, subject, token, delivery_id = _offer(plain_client)
    fake = _FakeTransport()
    with _use_transport(monkeypatch, fake, "memory"):
        sent = _send(plain_client, token, subject=subject)
    assert sent.status_code == 200, sent.text
    assert sent.json()["state"] == "sent"
    stopped = False
    for generation, status in _generations(session_id):
        if status not in {"completed", "stopped"}:
            task_run.stop(user_id=_admin_id(), generation=generation)
            stopped = True
    assert stopped
    assert _acceptance_rows(_admin_id(), delivery_id) == ["provider-1"]
    assert _receipt_state(_admin_id(), delivery_id) == "external_success"
    assert fake.sends == 1


def test_calendar_unknown_sentence_stays():
    assert CONFIRM_DETAIL["unknown"] == "结果未知，未再次写入。请先核对日历。"


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
