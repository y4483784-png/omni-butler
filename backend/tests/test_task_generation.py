from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import inspect

from app.agents.harness.constitution import load_constitution
from app.core.db import SessionLocal, engine
from app.models.models import Session as ChatSession
from app.models.models import User
from app.services import task_run


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"task-run-{uuid4().hex}"
        user = User(external_id=external_id, name=external_id, is_active=1, password_hash="")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="task run")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _pair() -> tuple[int, int]:
    user_id = _user()
    return user_id, _session(user_id)


def test_finish_reads_back_completed():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    finished = task_run.finish(user_id=user_id, generation=turn.generation)
    assert finished.status == "completed"
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "completed"


def test_later_begin_leaves_the_finished_turn_completed():
    user_id, session_id = _pair()
    first = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.finish(user_id=user_id, generation=first.generation)
    second = task_run.begin_turn(user_id=user_id, session_id=session_id)
    assert task_run.get(user_id=user_id, generation=first.generation).status == "completed"
    assert second.generation > first.generation


def test_second_begin_stops_the_open_turn():
    user_id, session_id = _pair()
    first = task_run.begin_turn(user_id=user_id, session_id=session_id)
    second = task_run.begin_turn(user_id=user_id, session_id=session_id)
    assert task_run.get(user_id=user_id, generation=first.generation).status == "stopped"
    assert second.generation > first.generation
    assert first.id != second.id


def test_finish_after_wait_for_person_stays_waiting_person():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    waiting = task_run.wait_for_person(user_id=user_id, generation=turn.generation)
    assert waiting.status == "waiting_person"
    finished = task_run.finish(user_id=user_id, generation=turn.generation)
    assert finished.status == "waiting_person"
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "waiting_person"


def test_new_begin_stops_a_resource_wait():
    user_id, session_id = _pair()
    first = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.wait_for_resource(user_id=user_id, generation=first.generation)
    task_run.begin_turn(user_id=user_id, session_id=session_id)
    assert task_run.get(user_id=user_id, generation=first.generation).status == "stopped"


def test_finish_after_stop_raises_and_stays_stopped():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.stop(user_id=user_id, generation=turn.generation)
    with pytest.raises(task_run.LateResult) as caught:
        task_run.finish(user_id=user_id, generation=turn.generation)
    assert caught.value.turn.status == "stopped"
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "stopped"


def test_wait_for_person_after_stop_raises_and_stays_stopped():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.stop(user_id=user_id, generation=turn.generation)
    with pytest.raises(task_run.LateResult) as caught:
        task_run.wait_for_person(user_id=user_id, generation=turn.generation)
    assert caught.value.turn.status == "stopped"
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "stopped"


def test_stop_after_finish_stays_completed():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    task_run.finish(user_id=user_id, generation=turn.generation)
    stopped = task_run.stop(user_id=user_id, generation=turn.generation)
    assert stopped.status == "completed"
    assert task_run.get(user_id=user_id, generation=turn.generation).status == "completed"


def test_other_user_cannot_read_the_turn():
    user_id, session_id = _pair()
    turn = task_run.begin_turn(user_id=user_id, session_id=session_id)
    other_id = _user()
    assert task_run.get(user_id=other_id, generation=turn.generation) is None
    assert all(row.id != turn.id for row in task_run.list_for_user(user_id=other_id))


def test_schema_omits_reason_action_and_fingerprint():
    columns = {column["name"] for column in inspect(engine).get_columns("task_runs")}
    assert "reason" not in columns
    assert "action" not in columns
    assert "fingerprint" not in columns


def test_turn_unique_key_is_user_and_generation():
    inspector = inspect(engine)
    column_sets: list[tuple[str, ...]] = []
    for item in inspector.get_unique_constraints("task_runs"):
        column_sets.append(tuple(item["column_names"]))
    for item in inspector.get_indexes("task_runs"):
        if item["unique"]:
            column_sets.append(tuple(item["column_names"]))
    for columns in column_sets:
        assert "action" not in columns
        assert "fingerprint" not in columns
    assert ("user_id", "generation") in column_sets


def test_allowed_tools_match_the_charter_and_survive_finish_and_stop():
    charter = tuple(load_constitution()["allowed_tools"])
    user_id, session_id = _pair()
    first = task_run.begin_turn(user_id=user_id, session_id=session_id)
    second = task_run.begin_turn(user_id=user_id, session_id=session_id)
    assert first.allowed_tools == charter
    assert second.allowed_tools == charter
    finished = task_run.finish(user_id=user_id, generation=second.generation)
    stopped = task_run.stop(user_id=user_id, generation=first.generation)
    assert finished.allowed_tools == charter
    assert stopped.allowed_tools == charter
    assert task_run.get(user_id=user_id, generation=first.generation).allowed_tools == charter
    assert task_run.get(user_id=user_id, generation=second.generation).allowed_tools == charter


def test_other_users_session_inserts_nothing():
    owner_id, session_id = _pair()
    other_id = _user()
    owner_before = task_run.list_for_user(user_id=owner_id)
    other_before = task_run.list_for_user(user_id=other_id)
    with pytest.raises(task_run.UnownedSession):
        task_run.begin_turn(user_id=other_id, session_id=session_id)
    with pytest.raises(task_run.UnownedSession):
        task_run.begin_turn(user_id=owner_id, session_id=-1)
    assert task_run.list_for_user(user_id=owner_id) == owner_before
    assert task_run.list_for_user(user_id=other_id) == other_before


def test_unfinished_write_key_omits_generation():
    assert set(task_run.UnfinishedWriteKey.__dataclass_fields__) == {
        "user_id",
        "action",
        "fingerprint",
    }


def test_chat_answer_finishes_completed(auth_client):
    from unittest.mock import patch

    from app.core.db import SessionLocal
    from app.models.models import Session as ChatSession
    from app.models.models import User

    db = SessionLocal()
    try:
        admin_id = db.query(User).filter(User.external_id == "admin").one().id
        sess = ChatSession(user_id=admin_id, title="task chat")
        db.add(sess)
        db.commit()
        db.refresh(sess)
        session_id = sess.id
    finally:
        db.close()
    wf = {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [{"role": "user", "content": "hello"}],
        "schedule_card": None,
        "artifact": None,
        "direct_answer": "done",
        "pending_calendar": None,
    }
    with patch("app.api.chat._run_workflow_sync", return_value=wf):
        response = auth_client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "hello"},
        )
    assert response.status_code == 200
    rows = [
        row
        for row in task_run.list_for_user(user_id=admin_id)
        if row.session_id == session_id
    ]
    assert len(rows) == 1
    assert rows[0].status == "completed"


def test_chat_confirm_card_stays_waiting_person(auth_client):
    from unittest.mock import patch

    from app.core.db import SessionLocal
    from app.models.models import Session as ChatSession
    from app.models.models import User

    db = SessionLocal()
    try:
        admin_id = db.query(User).filter(User.external_id == "admin").one().id
        sess = ChatSession(user_id=admin_id, title="task card")
        db.add(sess)
        db.commit()
        db.refresh(sess)
        session_id = sess.id
    finally:
        db.close()
    wf = {
        "intent": "calendar",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [],
        "schedule_card": {"phase": "draft", "mode": "create", "action": "create", "title": "stand-up"},
        "artifact": None,
        "direct_answer": "请确认",
        "pending_calendar": None,
    }
    with patch("app.api.chat._run_workflow_sync", return_value=wf):
        response = auth_client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "约个站会"},
        )
    assert response.status_code == 200
    assert '"type": "schedule_card"' in response.text or '"type":"schedule_card"' in response.text
    rows = [
        row
        for row in task_run.list_for_user(user_id=admin_id)
        if row.session_id == session_id
    ]
    assert len(rows) == 1
    assert rows[0].status == "waiting_person"


def test_begin_turn_stops_only_that_session():
    user_id = _user()
    home = _session(user_id)
    other = _session(user_id)
    first = task_run.begin_turn(user_id=user_id, session_id=home)
    second = task_run.begin_turn(user_id=user_id, session_id=other)
    assert task_run.get(user_id=user_id, generation=first.generation).status == "running"
    assert second.generation == first.generation + 1
    third = task_run.begin_turn(user_id=user_id, session_id=home)
    assert task_run.get(user_id=user_id, generation=first.generation).status == "stopped"
    assert task_run.get(user_id=user_id, generation=second.generation).status == "running"
    assert third.generation == second.generation + 1
