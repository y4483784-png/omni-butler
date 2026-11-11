"""Memory ledger: both statements stay visible, a stopped turn writes nothing, a failed planner stays pending, and one pulse claim is enough."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.db import SessionLocal, init_db
from app.models.models import (
    CalendarEvent,
    MemoryItem,
    MemoryLedger,
    Message,
    ScheduleConfirmClaim,
    Session as ChatSession,
    TaskRun,
    User,
)
from app.services import memory as memory_svc
from tests.conftest import login_as
from tests.test_isolation import _ensure_user


def _wipe(db, user_id: int) -> None:
    session_ids = [
        row.id for row in db.query(ChatSession.id).filter(ChatSession.user_id == user_id).all()
    ]
    if session_ids:
        db.query(Message).filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)
    db.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(TaskRun).filter(TaskRun.user_id == user_id).delete(synchronize_session=False)
    db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).delete(synchronize_session=False)
    if session_ids:
        db.query(ChatSession).filter(ChatSession.id.in_(session_ids)).delete(synchronize_session=False)
    db.query(MemoryLedger).filter(MemoryLedger.user_id == user_id).delete(synchronize_session=False)
    db.query(MemoryItem).filter(MemoryItem.user_id == user_id).delete(synchronize_session=False)
    db.commit()


def _open(user_id: int):
    init_db()
    db = SessionLocal()
    _wipe(db, user_id)
    return db


def test_two_stores_keep_before_and_after(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    db = _open(9801)
    try:
        first = "用户偏好简短回答"
        second = "用户偏好详细回答"
        assert memory_svc.store_memories(
            db, 9801, [{"kind": "preference", "key": "style", "content": first}]
        ) == 1
        assert memory_svc.store_memories(
            db, 9801, [{"kind": "preference", "key": "style", "content": second}]
        ) == 1
        item = next(row for row in memory_svc.panel_view(db, 9801) if row["key"] == "style")
        assert second in item["content"]
        changes = item["changes"]
        assert [row["id"] for row in changes] == sorted(row["id"] for row in changes)
        assert any(row["before"] == "" and first in row["after"] for row in changes)
        assert any(first in row["before"] and second in row["after"] for row in changes)
        assert len(changes) == 2
    finally:
        _wipe(db, 9801)
        db.close()


def test_stopped_turn_adds_no_ledger_and_skips_the_model(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", True)
    monkeypatch.setattr(memory_svc.settings, "llm_api_key", "test-key")
    calls = {"n": 0}

    def spy(*_args, **_kwargs):
        calls["n"] += 1
        return {"ops": [{"op": "ADD", "kind": "identity", "key": "name", "content": "用户希望被称为「不该写入」"}]}

    monkeypatch.setattr(memory_svc, "complete_json", spy)
    db = _open(9802)
    try:
        n = memory_svc.remember_from_turn(
            db,
            user_id=9802,
            user_message="叫我阿哲，回答请简洁一点",
            assistant_message="好",
            generation=4,
            status="stopped",
        )
        assert n == 0
        assert calls["n"] == 0
        assert db.query(MemoryLedger).filter(MemoryLedger.user_id == 9802).count() == 0
        assert memory_svc.list_memories(db, 9802) == []
        waiting = memory_svc.remember_from_turn(
            db,
            user_id=9802,
            user_message="叫我阿哲，回答请简洁一点",
            generation=4,
            status="waiting_person",
        )
        assert waiting == 0
        assert calls["n"] == 0
    finally:
        _wipe(db, 9802)
        db.close()


def test_planner_failure_stays_pending_and_does_not_publish(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", True)
    monkeypatch.setattr(memory_svc.settings, "llm_api_key", "test-key")
    calls = {"n": 0}

    def failed(*_args, **_kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr(memory_svc, "complete_json", failed)
    published = {"n": 0}

    def publish(*_args, **_kwargs):
        published["n"] += 1
        return {}

    monkeypatch.setattr("app.services.schedule_confirm.publish_schedule_claim", publish)
    db = _open(9803)
    try:
        db.add(
            MemoryItem(
                user_id=9803,
                kind="identity",
                key="name",
                content="用户希望被称为「小陈」",
            )
        )
        db.commit()
        n = memory_svc.remember_from_turn(
            db,
            user_id=9803,
            user_message="叫我阿哲",
            assistant_message="好",
            generation=9,
            status="completed",
        )
        assert n == 1
        assert calls["n"] == 1
        assert published["n"] == 0
        live = memory_svc.list_memories(db, 9803)
        assert len(live) == 1
        assert live[0].content == "用户希望被称为「小陈」"
        rows = db.query(MemoryLedger).filter(MemoryLedger.user_id == 9803).all()
        assert len(rows) == 1
        assert rows[0].pending == 1
        assert "阿哲" in rows[0].after
        assert rows[0].actor == "turn"
    finally:
        _wipe(db, 9803)
        db.close()


def test_person_write_keeps_a_pending_sibling(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    db = _open(9804)
    try:
        memory_svc.store_memories(
            db,
            9804,
            [{"kind": "identity", "key": "name", "content": "用户希望被称为「小陈」"}],
        )

        def planned(transcript, *, existing=None):
            return memory_svc.Extraction(
                memory_svc.PLANNER_OPS,
                ({"kind": "identity", "key": "name", "content": "用户希望被称为「阿哲」"},),
            )

        monkeypatch.setattr(memory_svc, "extract_for_ledger", planned)
        memory_svc.remember_from_turn(
            db,
            user_id=9804,
            user_message="叫我阿哲",
            generation=2,
            status="completed",
        )
        live = [row for row in memory_svc.list_memories(db, 9804) if row.key == "name"]
        assert live[0].content == "用户希望被称为「小陈」"
        pending = (
            db.query(MemoryLedger)
            .filter(MemoryLedger.user_id == 9804, MemoryLedger.pending == 1)
            .one()
        )
        memory_svc.store_memories(
            db,
            9804,
            [{"kind": "identity", "key": "name", "content": "用户希望被称为「陈工」"}],
        )
        db.refresh(pending)
        assert pending.pending == 1
        assert pending.decision is None
        assert "阿哲" in pending.after
        assert "陈工" in memory_svc.list_memories(db, 9804)[0].content
        restored = memory_svc.settle_pending(
            db, user_id=9804, ledger_id=pending.id, decision="restored"
        )
        assert restored is not None
        assert restored.pending == 0
        assert restored.decision == "restored"
        assert "阿哲" in memory_svc.list_memories(db, 9804)[0].content
        before = db.query(MemoryLedger).filter(MemoryLedger.user_id == 9804).count()
        again = memory_svc.settle_pending(
            db, user_id=9804, ledger_id=pending.id, decision="restored"
        )
        assert again is not None
        assert db.query(MemoryLedger).filter(MemoryLedger.user_id == 9804).count() == before
        missing = memory_svc.settle_pending(
            db, user_id=9805, ledger_id=pending.id, decision="discarded"
        )
        assert missing is None
    finally:
        _wipe(db, 9804)
        db.close()


def test_discard_leaves_the_live_value():
    db = _open(9806)
    try:
        db.add(
            MemoryItem(user_id=9806, kind="preference", key="style", content="用户偏好详细回答")
        )
        db.add(
            MemoryLedger(
                user_id=9806,
                kind="preference",
                key="style",
                source="turn",
                actor="turn",
                before="用户偏好详细回答",
                after="用户偏好简短回答",
                pending=1,
                source_ref="discard-1",
            )
        )
        db.commit()
        row = db.query(MemoryLedger).filter(MemoryLedger.user_id == 9806).one()
        settled = memory_svc.settle_pending(
            db, user_id=9806, ledger_id=row.id, decision="discarded"
        )
        assert settled is not None and settled.pending == 0 and settled.decision == "discarded"
        assert memory_svc.list_memories(db, 9806)[0].content == "用户偏好详细回答"
        before = db.query(MemoryLedger).filter(MemoryLedger.user_id == 9806).count()
        memory_svc.settle_pending(db, user_id=9806, ledger_id=row.id, decision="discarded")
        assert db.query(MemoryLedger).filter(MemoryLedger.user_id == 9806).count() == before
    finally:
        _wipe(db, 9806)
        db.close()


def test_push_pulse_twice_leaves_one_open_claim(monkeypatch):
    from app.tasks import pulse as pulse_mod

    calls = {"n": 0}
    real = pulse_mod.publish_schedule_claim

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pulse_mod, "publish_schedule_claim", wrapped)
    uid = 9811
    db = _open(uid)
    try:
        if db.get(User, uid) is None:
            db.add(User(id=uid, external_id=f"pulse-{uid}", name="pulse", is_active=1))
        db.add(ChatSession(user_id=uid, title="提醒"))
        db.commit()
        matter = pulse_mod.matter_for_schedule(
            user_id=uid,
            content="用户周五下午不排会",
            week=pulse_mod.week_monday(datetime(2026, 11, 10, tzinfo=timezone.utc)),
        )
        assert matter is not None
        start = datetime.fromisoformat(matter.start_at)
        end = datetime.fromisoformat(matter.end_at)
        assert start.weekday() == 4
        assert start.hour == 13 and start.minute == 0
        assert end.hour == 14 and (end - start).total_seconds() == 3600
        first = pulse_mod.push_pulse(matter)
        second = pulse_mod.push_pulse(matter)
        assert isinstance(first, pulse_mod.Shown)
        assert isinstance(second, pulse_mod.Suppressed)
        assert first.fingerprint == second.fingerprint
        assert calls["n"] == 1
        assert (
            db.query(ScheduleConfirmClaim)
            .filter(ScheduleConfirmClaim.user_id == uid, ScheduleConfirmClaim.status == "open")
            .count()
            == 1
        )
        messages = (
            db.query(Message)
            .join(ChatSession, ChatSession.id == Message.session_id)
            .filter(ChatSession.user_id == uid, Message.role == "assistant")
            .all()
        )
        assert len(messages) == 1
        card = json.loads(messages[0].schedule_card)
        assert card["phase"] == "draft"
        assert card["mode"] == "create"
        assert card["confirm_token"]
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == uid).count() == 0
    finally:
        _wipe(db, uid)
        db.query(User).filter(User.id == uid).delete()
        db.commit()
        db.close()


def test_repeated_generation_does_not_call_the_model_again(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", True)
    monkeypatch.setattr(memory_svc.settings, "llm_api_key", "test-key")
    calls = {"n": 0}

    def spy(*_args, **_kwargs):
        calls["n"] += 1
        return {
            "ops": [
                {"op": "ADD", "kind": "identity", "key": "name", "content": "用户希望被称为「阿哲」"}
            ]
        }

    monkeypatch.setattr(memory_svc, "complete_json", spy)
    db = _open(9807)
    try:
        first = memory_svc.remember_from_turn(
            db,
            user_id=9807,
            user_message="叫我阿哲",
            generation=7,
            status="completed",
        )
        second = memory_svc.remember_from_turn(
            db,
            user_id=9807,
            user_message="叫我阿哲",
            generation=7,
            status="completed",
        )
        assert first == 1
        assert second == 0
        assert calls["n"] == 1
        assert db.query(MemoryLedger).filter(MemoryLedger.user_id == 9807).count() == 1
    finally:
        _wipe(db, 9807)
        db.close()


def test_unknown_claim_suppresses_the_pulse(monkeypatch):
    from app.tasks import pulse as pulse_mod

    calls = {"n": 0}
    real = pulse_mod.publish_schedule_claim

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pulse_mod, "publish_schedule_claim", wrapped)
    uid = 9815
    db = _open(uid)
    try:
        if db.get(User, uid) is None:
            db.add(User(id=uid, external_id=f"pulse-{uid}", name="pulse", is_active=1))
        db.add(ChatSession(user_id=uid, title="未知确认"))
        db.commit()
        matter = pulse_mod.matter_for_schedule(
            user_id=uid,
            content="用户周五下午不排会",
            week=pulse_mod.week_monday(datetime(2026, 11, 10, tzinfo=timezone.utc)),
        )
        assert matter is not None
        first = pulse_mod.push_pulse(matter)
        assert isinstance(first, pulse_mod.Shown)
        claim = (
            db.query(ScheduleConfirmClaim)
            .filter(ScheduleConfirmClaim.user_id == uid, ScheduleConfirmClaim.status == "open")
            .one()
        )
        claim.status = "unknown"
        session_ids = [
            row.id for row in db.query(ChatSession.id).filter(ChatSession.user_id == uid).all()
        ]
        db.query(Message).filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.commit()
        second = pulse_mod.push_pulse(matter)
        assert isinstance(second, pulse_mod.Suppressed)
        assert calls["n"] == 1
        assert (
            db.query(Message)
            .join(ChatSession, ChatSession.id == Message.session_id)
            .filter(ChatSession.user_id == uid)
            .count()
            == 0
        )
        assert db.query(CalendarEvent).filter(CalendarEvent.user_id == uid).count() == 0
    finally:
        _wipe(db, uid)
        db.query(User).filter(User.id == uid).delete()
        db.commit()
        db.close()


def test_push_pulse_without_a_session_inserts_nothing():
    from app.tasks import pulse as pulse_mod

    uid = 9812
    db = _open(uid)
    try:
        matter = pulse_mod.matter_for_schedule(
            user_id=uid,
            content="用户周五下午不排会",
            week=pulse_mod.week_monday(datetime(2026, 11, 10, tzinfo=timezone.utc)),
        )
        assert matter is not None
        result = pulse_mod.push_pulse(matter)
        assert isinstance(result, pulse_mod.Suppressed)
        assert db.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.user_id == uid).count() == 0
        assert db.query(ChatSession).filter(ChatSession.user_id == uid).count() == 0
    finally:
        _wipe(db, uid)
        db.close()


def test_panel_http_shows_both_statements_and_hides_other_users(plain_client: TestClient, monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", True)
    monkeypatch.setattr(memory_svc.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(memory_svc, "complete_json", lambda *_a, **_k: None)
    created = plain_client.post(
        "/api/memory",
        json={"kind": "preference", "key": "ledger_note", "content": "用户偏好简短回答"},
    )
    assert created.status_code == 200, created.text
    updated = plain_client.post(
        "/api/memory",
        json={"kind": "preference", "key": "ledger_note", "content": "用户偏好详细回答"},
    )
    assert updated.status_code == 200, updated.text
    listed = plain_client.get("/api/memory")
    assert listed.status_code == 200
    item = next(row for row in listed.json() if row["key"] == "ledger_note")
    assert "详细" in item["content"]
    changes = item["changes"]
    assert any("简短" in row["after"] for row in changes)
    assert any("简短" in row["before"] and "详细" in row["after"] for row in changes)

    init_db()
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.external_id == "admin").one()
        admin_id = admin.id
        _wipe(db, 9813)
        _wipe(db, 9814)
        bob = _ensure_user(db, username="bob-ledger", password="bob-pass", user_id=9813)
        alice = _ensure_user(db, username="alice-ledger", password="alice-pass", user_id=9814)
        db.commit()
        n = memory_svc.remember_from_turn(
            db,
            user_id=alice.id,
            user_message="叫我阿哲",
            generation=15,
            status="completed",
        )
        assert n == 1
        pending = (
            db.query(MemoryLedger)
            .filter(MemoryLedger.user_id == alice.id, MemoryLedger.pending == 1)
            .one()
        )
        pending_id = pending.id
        bob_id = bob.id
        alice_id = alice.id
    finally:
        db.close()

    from app.main import app

    bob_client = TestClient(app)
    login_as(bob_client, "bob-ledger", "bob-pass")
    hidden = bob_client.get("/api/memory")
    assert hidden.status_code == 200
    assert all(row["key"] != "ledger_note" for row in hidden.json())
    assert hidden.json() == [] or all(
        not any(change.get("after") and "阿哲" in change["after"] for change in row.get("changes") or [])
        for row in hidden.json()
    )
    foreign = bob_client.post(
        f"/api/memory/ledger/{pending_id}/settle",
        json={"decision": "discarded"},
    )
    assert foreign.status_code == 404
    assert plain_client.post(
        f"/api/memory/ledger/{pending_id}/settle",
        json={"decision": "discarded"},
    ).status_code == 404

    alice_client = TestClient(app)
    login_as(alice_client, "alice-ledger", "alice-pass")
    bad = alice_client.post(f"/api/memory/ledger/{pending_id}/settle", json={"decision": "maybe"})
    assert bad.status_code == 400
    restored = alice_client.post(
        f"/api/memory/ledger/{pending_id}/settle",
        json={"decision": "restored"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["pending"] is False
    again = alice_client.post(
        f"/api/memory/ledger/{pending_id}/settle",
        json={"decision": "restored"},
    )
    assert again.status_code == 200
    db = SessionLocal()
    try:
        settle_rows = (
            db.query(MemoryLedger)
            .filter(
                MemoryLedger.user_id == alice_id,
                MemoryLedger.source == "settle",
                MemoryLedger.source_ref == str(pending_id),
            )
            .count()
        )
        assert settle_rows == 1
        _wipe(db, bob_id)
        _wipe(db, alice_id)
        db.query(MemoryItem).filter(
            MemoryItem.user_id == admin_id, MemoryItem.key == "ledger_note"
        ).delete(synchronize_session=False)
        db.query(MemoryLedger).filter(
            MemoryLedger.user_id == admin_id, MemoryLedger.key == "ledger_note"
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
