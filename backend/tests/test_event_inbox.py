"""Event inbox behavior through the internal submit route."""

from __future__ import annotations

from uuid import uuid4

from app.core.auth import hash_password
from app.core.db import SessionLocal
from app.models.models import CalendarEvent, EventReceipt, ScheduleConfirmClaim, Session as ChatSession, TaskRun, User
from app.services import task_run
from app.services.event_inbox import EventRejected, note_external_success
from fastapi.testclient import TestClient
from tests.test_schedule_confirm_claims import _admin_id, _body, _open, _session


def _delivery() -> str:
    return f"del-{uuid4()}"


def _card(title: str, **times: str) -> dict:
    return {
        "title": title,
        "start_at": times.get("start_at", "2026-11-04T10:00:00"),
        "end_at": times.get("end_at", "2026-11-04T11:00:00"),
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
    }


def _submit(client: TestClient, session_id: int, delivery_id: str, **extra):
    payload = {
        "source": extra.pop("source", "internal"),
        "delivery_id": delivery_id,
        "session_id": session_id,
        "body": extra.pop("body", "有一封需要处理的信"),
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


def _receipt_rows(user_id: int, delivery_id: str) -> list[tuple[int, str, str]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(EventReceipt)
            .filter(EventReceipt.user_id == user_id, EventReceipt.delivery_id == delivery_id)
            .all()
        )
        return [(row.id, row.state, row.route) for row in rows]
    finally:
        db.close()


def _claim_status(token: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        return None if row is None else row.status
    finally:
        db.close()


def _open_tokens(session_id: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ScheduleConfirmClaim)
            .filter(
                ScheduleConfirmClaim.session_id == session_id,
                ScheduleConfirmClaim.status == "open",
            )
            .all()
        )
        return [row.token for row in rows]
    finally:
        db.close()


def _calendar_count(title: str) -> int:
    db = SessionLocal()
    try:
        return db.query(CalendarEvent).filter(CalendarEvent.title == title).count()
    finally:
        db.close()


def test_same_delivery_id_yields_one_receipt(plain_client: TestClient):
    session_id = _session()
    delivery = _delivery()
    first = _submit(plain_client, session_id, delivery)
    second = _submit(
        plain_client,
        session_id,
        delivery,
        body="另一份正文",
        card=_card("第二次不该开任务"),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["created"] is False
    assert second.json()["state"] == first.json()["state"]
    assert len(_receipt_rows(_admin_id(), delivery)) == 1
    assert len(_generations(session_id)) == 1
    assert _calendar_count("第二次不该开任务") == 0


def test_running_task_passthrough_keeps_the_generation(plain_client: TestClient):
    session_id = _session()
    turn = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    before = _generations(session_id)
    saved = _submit(
        plain_client,
        session_id,
        _delivery(),
        generation=turn.generation,
        card=_card("直通不写"),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "passthrough"
    assert body["state"] == "received"
    assert body["generation"] == turn.generation
    assert body["state"] != "external_success"
    assert _generations(session_id) == before
    assert _open_tokens(session_id) == []
    assert _calendar_count("直通不写") == 0


def test_completed_task_opens_a_new_generation(plain_client: TestClient):
    session_id = _session()
    turn = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    finished = task_run.finish(user_id=_admin_id(), generation=turn.generation)
    assert finished.status == "completed"
    saved = _submit(plain_client, session_id, _delivery(), generation=turn.generation)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["state"] == "dispatched"
    assert body["route"] == "opened"
    assert body["generation"] != turn.generation
    rows = _generations(session_id)
    assert rows[0] == (turn.generation, "completed")
    assert rows[1][0] == body["generation"]
    assert rows[1][1] == "running"
    assert _calendar_count("完成后再开") == 0


def test_stopped_task_does_not_write(plain_client: TestClient):
    session_id = _session()
    turn = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    stopped = task_run.stop(user_id=_admin_id(), generation=turn.generation)
    assert stopped.status == "stopped"
    delivery = _delivery()
    title = "停止后不写"
    saved = _submit(
        plain_client,
        session_id,
        delivery,
        generation=turn.generation,
        card=_card(title),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "stopped"
    assert body["state"] == "received"
    assert body["state"] != "external_success"
    assert body["claim_token"] is None
    assert body["generation"] == turn.generation
    assert _generations(session_id) == [(turn.generation, "stopped")]
    assert _open_tokens(session_id) == []
    assert _calendar_count(title) == 0
    db = SessionLocal()
    try:
        try:
            note_external_success(
                db,
                user_id=_admin_id(),
                source="internal",
                delivery_id=delivery,
                remote_request_id="remote-stopped",
            )
        except EventRejected as exc:
            assert exc.status == 409
        else:
            raise AssertionError("stopped receipt was marked external_success")
    finally:
        db.close()
    assert _receipt_rows(_admin_id(), delivery)[0][1] == "received"


def test_same_fingerprint_keeps_the_open_claim(plain_client: TestClient):
    title = f"保持指纹-{uuid4().hex[:8]}"
    card = _card(title)
    token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    saved = _submit(plain_client, session_id, _delivery(), generation=turn.generation, card=card)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "kept"
    assert body["claim_token"] == token
    assert body["open_claims"] == 1
    assert _open_tokens(session_id) == [token]
    assert _claim_status(token) == "open"
    assert _calendar_count(title) == 0


def test_different_fingerprint_reissues_one_card(plain_client: TestClient):
    title = f"旧卡-{uuid4().hex[:8]}"
    replacement = f"新卡-{uuid4().hex[:8]}"
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


def test_after_reissue_a_different_event_only_queues(plain_client: TestClient):
    title = f"排队旧卡-{uuid4().hex[:8]}"
    first = f"排队新卡-{uuid4().hex[:8]}"
    second = f"排队再来-{uuid4().hex[:8]}"
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


def test_repeated_delivery_while_waiting_does_nothing(plain_client: TestClient):
    title = f"重复投递-{uuid4().hex[:8]}"
    token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    delivery = _delivery()
    first = _submit(
        plain_client,
        session_id,
        delivery,
        generation=turn.generation,
        card=_card(f"{title}-改"),
    )
    assert first.status_code == 200, first.text
    again = _submit(
        plain_client,
        session_id,
        delivery,
        generation=turn.generation,
        card=_card(f"{title}-又改"),
    )
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["route"] == first.json()["route"]
    assert _open_tokens(session_id) == [first.json()["claim_token"]]
    assert _claim_status(token) == "cancelled"
    assert len(_receipt_rows(_admin_id(), delivery)) == 1


def test_noise_creates_no_task_and_no_card(plain_client: TestClient):
    session_id = _session()
    saved = _submit(
        plain_client,
        session_id,
        _delivery(),
        kind="unsubscribe",
        decision="act",
        body="请退订",
        card=_card("噪声不该出卡"),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["route"] == "noise"
    assert body["state"] == "received"
    assert body["generation"] is None
    assert body["claim_token"] is None
    assert _generations(session_id) == []
    assert _open_tokens(session_id) == []
    assert _calendar_count("噪声不该出卡") == 0


def test_ignore_does_not_execute(plain_client: TestClient):
    session_id = _session()
    turn = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    before = _generations(session_id)
    saved = _submit(
        plain_client,
        session_id,
        _delivery(),
        decision="ignore",
        generation=turn.generation,
        card=_card("忽略不执行"),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["route"] == "held"
    assert saved.json()["state"] == "received"
    assert saved.json()["generation"] is None
    assert _generations(session_id) == before
    assert _open_tokens(session_id) == []


def test_other_user_has_a_separate_receipt(plain_client: TestClient):
    title = f"隔离-{uuid4().hex[:8]}"
    token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    delivery = _delivery()
    admin = _submit(plain_client, session_id, delivery, generation=turn.generation, card=_card(title))
    assert admin.status_code == 200, admin.text
    assert admin.json()["route"] == "kept"
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.external_id == "inbox-other-user").one_or_none()
        if other is None:
            other = User(
                external_id="inbox-other-user",
                name="别人",
                password_hash=hash_password("other-pass"),
                is_active=1,
                is_admin=0,
            )
            db.add(other)
            db.commit()
            db.refresh(other)
        other_session = ChatSession(user_id=other.id, title="别人的会话")
        db.add(other_session)
        db.commit()
        db.refresh(other_session)
        other_id = other.id
        other_session_id = other_session.id
    finally:
        db.close()
    from app.main import app

    other_client = TestClient(app)
    logged = other_client.post(
        "/api/auth/login",
        json={"username": "inbox-other-user", "password": "other-pass"},
    )
    assert logged.status_code == 200, logged.text
    stolen = _submit(other_client, session_id, delivery, generation=turn.generation, card=_card("别人的卡"))
    assert stolen.status_code == 404, stolen.text
    own = _submit(other_client, other_session_id, delivery, card=_card("别人的卡"))
    assert own.status_code == 200, own.text
    assert own.json()["id"] != admin.json()["id"]
    assert own.json()["created"] is True
    assert _claim_status(token) == "open"
    assert _open_tokens(session_id) == [token]
    db = SessionLocal()
    try:
        rows = db.query(EventReceipt).filter(EventReceipt.delivery_id == delivery).all()
        owners = {row.user_id for row in rows}
    finally:
        db.close()
    assert owners == {_admin_id(), other_id}


def test_external_success_requires_the_other_side_accepted(plain_client: TestClient):
    title = f"外部成功-{uuid4().hex[:8]}"
    _token, session_id, turn = _open(plain_client, _body(title))
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    delivery = _delivery()
    saved = _submit(
        plain_client,
        session_id,
        delivery,
        generation=turn.generation,
        card=_card(f"{title}-换"),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"] == "received"
    db = SessionLocal()
    try:
        try:
            note_external_success(
                db,
                user_id=_admin_id(),
                source="internal",
                delivery_id=delivery,
                remote_request_id="remote-1",
            )
        except EventRejected as exc:
            assert exc.status == 409
        else:
            raise AssertionError("open claim was marked external_success")
        claim = db.get(ScheduleConfirmClaim, saved.json()["claim_token"])
        claim.status = "written"
        claim.remote_request_id = "remote-1"
        db.commit()
        marked = note_external_success(
            db,
            user_id=_admin_id(),
            source="internal",
            delivery_id=delivery,
            remote_request_id="remote-1",
        )
        assert marked.state == "external_success"
        repeat = note_external_success(
            db,
            user_id=_admin_id(),
            source="internal",
            delivery_id=delivery,
            remote_request_id="remote-1",
        )
        assert repeat.state == "external_success"
        assert _calendar_count(title) == 0
    finally:
        db.close()


def test_anonymous_submit_is_rejected():
    from app.main import app

    client = TestClient(app)
    saved = client.post(
        "/api/events/inbox",
        json={
            "source": "internal",
            "delivery_id": _delivery(),
            "session_id": 1,
            "body": "未登录",
            "decision": "act",
        },
    )
    assert saved.status_code == 401


def test_chat_sources_are_rejected(plain_client: TestClient):
    session_id = _session()
    saved = _submit(plain_client, session_id, _delivery(), source="slack")
    assert saved.status_code == 422, saved.text
    assert _generations(session_id) == []
