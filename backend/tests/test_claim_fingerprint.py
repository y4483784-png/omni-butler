from __future__ import annotations

from datetime import datetime
from threading import Thread

from app.core.auth import hash_password
from app.core.db import SessionLocal, set_rls_context
from app.models.models import CalendarEvent, ScheduleConfirmClaim, User
from app.services import task_run
from app.services.schedule_confirm import ScheduleConfirmClosed, consume_schedule_claim, phase_for_stored_card
from fastapi.testclient import TestClient
from tests.test_schedule_confirm_claims import _admin_id, _body, _count, _delete_title, _open, _session


def _claims(token: str) -> list[ScheduleConfirmClaim]:
    db = SessionLocal()
    try:
        return db.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.token == token).all()
    finally:
        db.close()


def _open_rows(user_id: int, action: str, fingerprint: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ScheduleConfirmClaim)
            .filter(
                ScheduleConfirmClaim.user_id == user_id,
                ScheduleConfirmClaim.action == action,
                ScheduleConfirmClaim.fingerprint == fingerprint,
                ScheduleConfirmClaim.status == "open",
            )
            .count()
        )
    finally:
        db.close()


def test_changed_time_rejects_the_old_token(plain_client: TestClient):
    title = "确认指纹改时间"
    original = _body(title)
    changed = _body(title, start_at="2026-11-04T12:00:00", end_at="2026-11-04T13:00:00")
    token, _session_id, _turn = _open(plain_client, original)
    try:
        rejected = plain_client.post("/api/calendar", json={**changed, "confirm_token": token})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"] == "参数已变，旧确认不能写入。"
        assert _count(title) == 0
        saved = plain_client.post("/api/calendar", json={**original, "confirm_token": token})
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_stopped_generation_cannot_write_and_does_not_open_another_card(plain_client: TestClient):
    title = "确认指纹停止"
    body = _body(title)
    token, _session_id, turn = _open(plain_client, body)
    task_run.stop(user_id=_admin_id(), generation=turn.generation)
    saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0
    rows = _claims(token)
    assert len(rows) == 1
    assert rows[0].status == "cancelled"


def test_completed_generation_cannot_consume_an_open_claim(plain_client: TestClient):
    title = "确认指纹做完"
    body = _body(title)
    token, _session_id, turn = _open(plain_client, body)
    finished = task_run.finish(user_id=_admin_id(), generation=turn.generation)
    assert finished.status == "completed"
    saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_waiting_person_stays_able_to_write_after_finish(plain_client: TestClient):
    title = "确认指纹等人"
    body = _body(title)
    token, _session_id, turn = _open(plain_client, body)
    task_run.wait_for_person(user_id=_admin_id(), generation=turn.generation)
    kept = task_run.finish(user_id=_admin_id(), generation=turn.generation)
    assert kept.status == "waiting_person"
    try:
        saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_two_generations_share_one_claim_and_one_stop_leaves_it_usable(plain_client: TestClient):
    title = "确认指纹两代"
    body = _body(title)
    first_session = _session()
    second_session = _session()
    token, _returned, first = _open(plain_client, body, session_id=first_session)
    again, _session_id, second = _open(plain_client, body, session_id=second_session)
    assert again == token
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        assert row.action == "calendar.create"
        linked = __import__("json").loads(row.generation_ids)
    finally:
        db.close()
    assert first.id in linked and second.id in linked
    task_run.stop(user_id=_admin_id(), generation=first.generation)
    try:
        saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
        assert saved.status_code == 200, saved.text
        assert _count(title) == 1
        repeat = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
        assert repeat.status_code == 409, repeat.text
        assert _count(title) == 1
    finally:
        _delete_title(title)


def test_stopping_every_linked_generation_retires_the_claim(plain_client: TestClient):
    title = "确认指纹都停"
    body = _body(title)
    first_session = _session()
    second_session = _session()
    token, _session_id, first = _open(plain_client, body, session_id=first_session)
    _again, _other, second = _open(plain_client, body, session_id=second_session)
    task_run.stop(user_id=_admin_id(), generation=first.generation)
    task_run.stop(user_id=_admin_id(), generation=second.generation)
    saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0
    assert len(_claims(token)) == 1


def test_other_user_cannot_spend_the_token(plain_client: TestClient):
    title = "确认指纹别人"
    body = _body(title)
    token, _session_id, _turn = _open(plain_client, body)
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.external_id == "gate-other-user").one_or_none()
        if other is None:
            db.add(
                User(
                    external_id="gate-other-user",
                    name="别人",
                    password_hash=hash_password("other-pass"),
                    is_active=1,
                    is_admin=0,
                )
            )
            db.commit()
    finally:
        db.close()
    from app.main import app

    other_client = TestClient(app)
    logged = other_client.post(
        "/api/auth/login",
        json={"username": "gate-other-user", "password": "other-pass"},
    )
    assert logged.status_code == 200, logged.text
    saved = other_client.post("/api/calendar", json={**body, "confirm_token": token})
    assert saved.status_code == 409, saved.text
    assert _count(title) == 0


def test_refreshing_a_written_card_does_not_write_again(plain_client: TestClient):
    title = "确认指纹刷新"
    body = _body(title)
    token, session_id, _turn = _open(plain_client, body)
    try:
        saved = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
        assert saved.status_code == 200, saved.text
        db = SessionLocal()
        try:
            shown = phase_for_stored_card(
                db,
                {"phase": "draft", "confirm_token": token, "title": title},
            )
            assert shown is not None
            assert shown["phase"] == "confirmed"
        finally:
            db.close()
        again = plain_client.post("/api/calendar", json={**body, "confirm_token": token})
        assert again.status_code == 409, again.text
        assert _count(title) == 1
        assert session_id
    finally:
        _delete_title(title)


def test_rsvp_without_a_token_is_rejected(plain_client: TestClient):
    saved = plain_client.post(
        "/api/calendar/google/rsvp",
        json={"external_id": "evt-no-token", "response": "accepted"},
    )
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "缺少确认，未写入。"


def test_same_write_from_two_issues_is_one_open_row(plain_client: TestClient):
    title = "确认指纹幂等"
    body = _body(title)
    first_session = _session()
    second_session = _session()
    token, _session_id, _turn = _open(plain_client, body, session_id=first_session)
    again, _other, _second = _open(plain_client, body, session_id=second_session)
    assert again == token
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        assert _open_rows(_admin_id(), row.action, row.fingerprint) == 1
    finally:
        db.close()


def test_two_consumes_race_to_one_event(plain_client: TestClient):
    title = "确认指纹竞争"
    body = _body(title)
    token, _session_id, _turn = _open(plain_client, body)
    user_id = _admin_id()
    results: list[str] = []

    def once() -> None:
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            consume_schedule_claim(
                db,
                user_id,
                token,
                action="calendar.create",
                raw=body,
            )
            db.add(
                CalendarEvent(
                    user_id=user_id,
                    title=title,
                    start_at=datetime(2026, 11, 4, 10, 0),
                    end_at=datetime(2026, 11, 4, 11, 0),
                    status="active",
                )
            )
            db.commit()
            results.append("written")
        except ScheduleConfirmClosed:
            results.append("closed")
        finally:
            db.close()

    try:
        workers = [Thread(target=once), Thread(target=once)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert sorted(results) == ["closed", "written"]
        assert _count(title) == 1
    finally:
        _delete_title(title)
        done = SessionLocal()
        try:
            done.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.token == token).delete()
            done.commit()
        finally:
            done.close()
