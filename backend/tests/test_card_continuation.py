from __future__ import annotations

import json

from app.core.auth import hash_password
from app.core.db import SessionLocal
from app.models.models import Message, ScheduleConfirmClaim, Session as ChatSession, TaskRun, User
from app.services import task_run
from fastapi.testclient import TestClient
from tests.test_schedule_confirm_claims import _admin_id, _body, _count, _delete_title, _session

_SLOT = {"start_at": "2026-11-04T15:00:00", "end_at": "2026-11-04T16:00:00"}


def _runs(token: str) -> tuple[ScheduleConfirmClaim, list[TaskRun]]:
    db = SessionLocal()
    try:
        claim = db.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.token == token).one()
        ids = json.loads(claim.generation_ids or "[]")
        rows = db.query(TaskRun).filter(TaskRun.id.in_(ids)).all() if ids else []
        return claim, rows
    finally:
        db.close()


def _waiting(user_id: int, session_id: int) -> list[TaskRun]:
    db = SessionLocal()
    try:
        return (
            db.query(TaskRun)
            .filter(
                TaskRun.user_id == user_id,
                TaskRun.session_id == session_id,
                TaskRun.status == "waiting_person",
            )
            .all()
        )
    finally:
        db.close()


def _store_card(session_id: int, card: dict) -> int:
    db = SessionLocal()
    try:
        row = Message(
            session_id=session_id,
            role="assistant",
            content="只读日程",
            schedule_card=json.dumps(card, ensure_ascii=False),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _read_card(session_id: int, *, status: str = "completed", mode: str = "list", slots=None, items=None, expires_at: str = "") -> tuple[int, int]:
    user_id = _admin_id()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    if status == "completed":
        task_run.finish(user_id=user_id, generation=turn.generation)
    elif status == "stopped":
        task_run.stop(user_id=user_id, generation=turn.generation)
    card = {
        "mode": mode,
        "phase": "info",
        "suggested_slots": slots if slots is not None else [_SLOT],
        "items": items or [],
        "task_generation": turn.generation,
        "expires_at": expires_at,
    }
    return _store_card(session_id, card), turn.generation


def _click(client: TestClient, session_id: int, message_id: int, **extra) -> object:
    payload = {
        "session_id": session_id,
        "source_message_id": message_id,
        "action": "create",
        "title": "下午三点",
        "start_at": _SLOT["start_at"],
        "end_at": _SLOT["end_at"],
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
    }
    payload.update(extra)
    return client.post("/api/calendar/confirm-claims", json=payload)


def test_completed_list_click_confirms_once_without_a_live_session_task(plain_client: TestClient):
    title = "卡片续接下午三点"
    session_id = _session()
    message_id, _generation = _read_card(session_id)
    try:
        opened = _click(plain_client, session_id, message_id)
        assert opened.status_code == 200, opened.text
        token = opened.json()["confirm_token"]
        again = _click(plain_client, session_id, message_id)
        assert again.status_code == 200, again.text
        assert again.json()["confirm_token"] == token
        actions = _waiting(_admin_id(), session_id)
        assert len(actions) == 1
        assert actions[0].status == "waiting_person"
        _claim, bound = _runs(token)
        assert [row.id for row in bound] == [actions[0].id]
        body = _body(title, token, start_at=_SLOT["start_at"], end_at=_SLOT["end_at"])
        missing = plain_client.post("/api/calendar", json=_body(title, start_at=_SLOT["start_at"], end_at=_SLOT["end_at"]))
        assert missing.status_code == 409, missing.text
        assert _count(title) == 0
        saved = plain_client.post("/api/calendar", json=body)
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
        repeat = plain_client.post("/api/calendar", json=body)
        assert repeat.status_code == 409, repeat.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_unrelated_live_task_is_not_the_authorization(plain_client: TestClient):
    title = "卡片续接不借用"
    session_id = _session()
    message_id, _generation = _read_card(session_id)
    unrelated = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    try:
        opened = _click(plain_client, session_id, message_id)
        assert opened.status_code == 200, opened.text
        token = opened.json()["confirm_token"]
        _claim, bound = _runs(token)
        assert unrelated.id not in [row.id for row in bound]
        assert all(row.status == "waiting_person" for row in bound)
        task_run.stop(user_id=_admin_id(), generation=unrelated.generation)
        saved = plain_client.post(
            "/api/calendar",
            json=_body(title, token, start_at=_SLOT["start_at"], end_at=_SLOT["end_at"]),
        )
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
        assert task_run.get(user_id=_admin_id(), generation=unrelated.generation).status == "stopped"
    finally:
        _delete_title(title)


def test_a_new_chat_turn_does_not_stop_the_card_action(plain_client: TestClient):
    session_id = _session()
    message_id, _generation = _read_card(session_id)
    opened = _click(plain_client, session_id, message_id)
    assert opened.status_code == 200, opened.text
    token = opened.json()["confirm_token"]
    _claim, bound = _runs(token)
    action_generation = bound[0].generation
    typed = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)
    assert typed.generation != action_generation
    assert task_run.get(user_id=_admin_id(), generation=action_generation).status == "waiting_person"
    assert task_run.get(user_id=_admin_id(), generation=typed.generation).status == "running"


def test_changed_time_and_stopped_action_do_not_write(plain_client: TestClient):
    title = "卡片续接改时间"
    session_id = _session()
    message_id, _generation = _read_card(session_id)
    try:
        forged = _click(
            plain_client,
            session_id,
            message_id,
            start_at="2026-11-04T18:00:00",
            end_at="2026-11-04T19:00:00",
        )
        assert forged.status_code == 409, forged.text
        assert _waiting(_admin_id(), session_id) == []
        opened = _click(plain_client, session_id, message_id)
        assert opened.status_code == 200, opened.text
        token = opened.json()["confirm_token"]
        edited = _click(
            plain_client,
            session_id,
            message_id,
            start_at="2026-11-04T18:00:00",
            end_at="2026-11-04T19:00:00",
            title="改后的下午",
        )
        assert edited.status_code == 200, edited.text
        edited_token = edited.json()["confirm_token"]
        assert edited_token != token
        assert len(_waiting(_admin_id(), session_id)) == 1
        rejected = plain_client.post(
            "/api/calendar",
            json=_body(title, token, start_at="2026-11-04T18:00:00", end_at="2026-11-04T19:00:00"),
        )
        assert rejected.status_code == 409, rejected.text
        assert _count(title) == 0
        _claim, bound = _runs(token)
        task_run.stop(user_id=_admin_id(), generation=bound[0].generation)
        stopped = plain_client.post(
            "/api/calendar",
            json=_body(title, token, start_at=_SLOT["start_at"], end_at=_SLOT["end_at"]),
        )
        assert stopped.status_code == 409, stopped.text
        assert _count(title) == 0
        stopped_edit = plain_client.post(
            "/api/calendar",
            json=_body("改后的下午", edited_token, start_at="2026-11-04T18:00:00", end_at="2026-11-04T19:00:00"),
        )
        assert stopped_edit.status_code == 409, stopped_edit.text
    finally:
        _delete_title(title)
        _delete_title("改后的下午")


def test_stopped_expired_and_foreign_sources_do_not_issue(plain_client: TestClient):
    session_id = _session()
    stopped_id, _generation = _read_card(session_id, status="stopped")
    stopped = _click(plain_client, session_id, stopped_id)
    assert stopped.status_code == 409, stopped.text
    expired_id, _generation = _read_card(session_id, expires_at="2020-01-01T00:00:00Z")
    expired = _click(plain_client, session_id, expired_id)
    assert expired.status_code == 409, expired.text
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.external_id == "gate-other-user").one_or_none()
        if other is None:
            other = User(
                external_id="gate-other-user",
                name="别人",
                password_hash=hash_password("other-pass"),
                is_active=1,
                is_admin=0,
            )
            db.add(other)
            db.commit()
            db.refresh(other)
        foreign_session = ChatSession(user_id=other.id, title="别人的会话")
        db.add(foreign_session)
        db.commit()
        db.refresh(foreign_session)
        foreign_id = _store_card(
            foreign_session.id,
            {
                "mode": "list",
                "phase": "info",
                "suggested_slots": [_SLOT],
                "items": [],
                "expires_at": "",
            },
        )
    finally:
        db.close()
    foreign = _click(plain_client, session_id, foreign_id)
    assert foreign.status_code == 409, foreign.text
    assert _waiting(_admin_id(), session_id) == []


def test_rsvp_click_confirms_once_on_its_own_task(plain_client: TestClient, monkeypatch):
    session_id = _session()
    message_id, _generation = _read_card(
        session_id,
        slots=[],
        items=[{"title": "评审", "external_id": "evt-rsvp-1", "rsvp": "needsAction"}],
    )
    unrelated = task_run.begin_turn(user_id=_admin_id(), session_id=session_id)

    def _remote(_db, _user_id):
        return True

    def _answer(db, _user_id, *, external_id, status):
        db.commit()
        return {"external_id": external_id, "rsvp": status}

    monkeypatch.setattr("app.api.calendar.calendar_writes_remote", _remote)
    monkeypatch.setattr("app.api.calendar.answer_invitation", _answer)
    opened = _click(
        plain_client,
        session_id,
        message_id,
        action="rsvp",
        title="",
        start_at="",
        end_at="",
        external_id="evt-rsvp-1",
        response="accepted",
    )
    assert opened.status_code == 200, opened.text
    token = opened.json()["confirm_token"]
    again = _click(
        plain_client,
        session_id,
        message_id,
        action="rsvp",
        title="",
        start_at="",
        end_at="",
        external_id="evt-rsvp-1",
        response="accepted",
    )
    assert again.json()["confirm_token"] == token
    assert len(_waiting(_admin_id(), session_id)) == 1
    _claim, bound = _runs(token)
    assert unrelated.id not in [row.id for row in bound]
    missing = plain_client.post(
        "/api/calendar/google/rsvp",
        json={"external_id": "evt-rsvp-1", "response": "accepted"},
    )
    assert missing.status_code == 409, missing.text
    altered = _click(
        plain_client,
        session_id,
        message_id,
        action="rsvp",
        external_id="evt-other",
        response="accepted",
    )
    assert altered.status_code == 409, altered.text
    task_run.stop(user_id=_admin_id(), generation=unrelated.generation)
    saved = plain_client.post(
        "/api/calendar/google/rsvp",
        json={"external_id": "evt-rsvp-1", "response": "accepted", "confirm_token": token},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["rsvp"] == "accepted"
    repeat = plain_client.post(
        "/api/calendar/google/rsvp",
        json={"external_id": "evt-rsvp-1", "response": "accepted", "confirm_token": token},
    )
    assert repeat.status_code == 409, repeat.text
    assert task_run.get(user_id=_admin_id(), generation=unrelated.generation).status == "stopped"


def test_direct_create_still_uses_its_own_turn(plain_client: TestClient):
    title = "卡片续接直接创建"
    body = _body(title)
    from tests.test_schedule_confirm_claims import _open

    token, _session_id, turn = _open(plain_client, body)
    try:
        saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
        task_run.stop(user_id=_admin_id(), generation=turn.generation)
    finally:
        _delete_title(title)
