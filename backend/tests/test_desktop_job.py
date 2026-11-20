from __future__ import annotations

import hashlib
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from fastapi.testclient import TestClient

from app.agents.harness import gateway
from app.agents.harness.desktop_channel import recognize_package
from app.agents.harness.surface import ServerSurface, surface_for_login
from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import _REGISTRY, ToolSpec, ensure_builtin_tools, get_tool, register
from app.core.db import SessionLocal, set_rls_context
from app.core.llm import ModelTurn, ToolCallSpec
from app.models.models import DesktopDevice, ScheduleConfirmClaim, Session as ChatSession, TaskRun, TaskStep, User
from app.services import task_run
from app.services.local_action import LocalAction, LocalClosed, commit_local, finish_local, propose_local
from app.services.loop_round import Hold, open_cards, save_turn
from app.tasks.job import run_job, set_executor
from desktop.executor import perform

PATH = "/tmp/fixture.txt"
LABEL = "studio"


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"desktop-job-{uuid4().hex}"
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
        set_rls_context(db, user_id)
        row = ChatSession(user_id=user_id, title="desktop-job")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def _device_seven(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(DesktopDevice, 7)
        if row is None:
            row = DesktopDevice(id=7, user_id=user_id, token_hash=digest, label=LABEL)
            db.add(row)
        else:
            row.user_id = user_id
            row.token_hash = digest
            row.label = LABEL
            row.revoked_at = None
        db.commit()
        stored = db.get(DesktopDevice, 7)
        assert stored is not None
        assert int(stored.id) == 7
        assert stored.label == LABEL
    finally:
        db.close()
    return token


def _action(path: str = PATH) -> LocalAction:
    return LocalAction("desktop.open", 7, path, ())


def _open_job(user_id: int, session_id: int, path: str = PATH) -> tuple[int, int, str]:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        turn = task_run.open_alongside(
            db,
            user_id=user_id,
            session_id=session_id,
            kind="job",
            goal="open a file",
        )
        row = db.get(TaskRun, turn.id)
        row.exec_token = "worker"
        row.exec_epoch = 1
        row.hold_until = datetime(2030, 1, 1)
        db.commit()
        hold = Hold(
            user_id=user_id,
            generation=turn.generation,
            exec_token="worker",
            exec_epoch=1,
            intent_version=1,
        )
        raw = {"device_id": 7, "local_target": path, "local_args": []}
        save_turn(
            db,
            hold=hold,
            turn=ModelTurn(
                "",
                "",
                (ToolCallSpec("call-open", "desktop.open", json.dumps(raw)),),
                "tool_calls",
            ),
        )
        step = db.query(TaskStep).filter(TaskStep.task_run_id == turn.id).one()
        tokens = open_cards(db, hold=hold, step_id=step.id)
        assert len(tokens) == 1
        return int(turn.id), int(turn.generation), tokens[0]
    finally:
        db.close()


def _load(task_id: int) -> TaskRun:
    db = SessionLocal()
    try:
        row = db.get(TaskRun, task_id)
        db.expunge(row)
        return row
    finally:
        db.close()


def _claim(token: str) -> ScheduleConfirmClaim:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        db.expunge(row)
        return row
    finally:
        db.close()


def _call_result(task_id: int) -> tuple[str, str | None]:
    from app.models.models import ToolCall

    db = SessionLocal()
    try:
        step_ids = [row.id for row in db.query(TaskStep).filter(TaskStep.task_run_id == task_id).all()]
        row = db.query(ToolCall).filter(ToolCall.task_step_id.in_(step_ids)).one()
        return row.state, row.result
    finally:
        db.close()


def test_a_job_opens_the_named_file_once_and_continues(plain_client: TestClient) -> None:
    assert plain_client is not None
    user_id = _user()
    session_id = _session(user_id)
    token = _device_seven(user_id)
    proof = recognize_package(token)
    assert proof is not None
    assert proof.device_id == 7
    task_id, generation, claim_token = _open_job(user_id, session_id)
    assert _load(task_id).status == "waiting_person"
    assert _load(task_id).generation == generation
    action = _action()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        license_row = commit_local(
            db,
            user_id=user_id,
            token=claim_token,
            action=action,
            proof=proof,
        )
    finally:
        db.close()
    assert license_row["kind"] == "armed"
    assert license_row["device_id"] == 7
    assert license_row["device_label"] == LABEL
    assert license_row["action"] == "desktop.open"
    assert license_row["local_target"] == PATH
    assert license_row["local_args"] == []
    opened: list[str] = []
    assert (
        perform(
            {**license_row, "kind": "unknown"},
            open_file=opened.append,
            run_program=lambda *_args: opened.append("run"),
            enrolled_device_id=7,
        )
        is False
    )
    assert (
        perform(
            license_row,
            open_file=opened.append,
            run_program=lambda *_args: opened.append("run"),
            enrolled_device_id=8,
        )
        is False
    )
    assert perform(
        license_row,
        open_file=opened.append,
        run_program=lambda *_args: opened.append("run"),
        enrolled_device_id=7,
    )
    assert opened == [PATH]
    again_db = SessionLocal()
    try:
        set_rls_context(again_db, user_id)
        again = commit_local(
            again_db,
            user_id=user_id,
            token=claim_token,
            action=action,
            proof=proof,
        )
    finally:
        again_db.close()
    assert again["kind"] == "unknown"
    assert again["request_id"] == license_row["request_id"]
    assert _claim(claim_token).remote_request_id == license_row["request_id"]
    assert (
        perform(
            again,
            open_file=opened.append,
            run_program=lambda *_args: opened.append("run"),
            enrolled_device_id=7,
        )
        is False
    )
    assert opened == [PATH]
    published: list[tuple] = []

    def capture(*args, **_kwargs):
        published.append(args)

    finisher = SessionLocal()
    try:
        set_rls_context(finisher, user_id)
        with patch("app.tasks.job.run_job.delay", capture):
            finish_local(
                finisher,
                user_id=user_id,
                token=claim_token,
                request_id=license_row["request_id"],
                action=action,
                proof=proof,
            )
            running = _load(task_id)
            assert running.status == "running"
            assert running.generation == generation
            assert len(published) == 1
            finish_local(
                finisher,
                user_id=user_id,
                token=claim_token,
                request_id=license_row["request_id"],
                action=action,
                proof=proof,
            )
    finally:
        finisher.close()
    assert len(published) == 1
    assert _load(task_id).generation == generation
    state, result = _call_result(task_id)
    assert state == "result"
    assert result == f"已写入：{license_row['request_id']}"
    try:
        set_executor(lambda *_args, **_kwargs: ModelTurn("opened", "", (), "stop"))
        run_job(*published[0])
    finally:
        set_executor(None)
    finished = _load(task_id)
    assert finished.generation == generation
    assert finished.status == "completed"


def test_a_changed_path_does_not_run(plain_client: TestClient) -> None:
    assert plain_client is not None
    user_id = _user()
    session_id = _session(user_id)
    token = _device_seven(user_id)
    proof = recognize_package(token)
    assert proof is not None
    _task_id, _generation, claim_token = _open_job(user_id, session_id)
    opened: list[str] = []
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        with pytest.raises(LocalClosed) as caught:
            commit_local(
                db,
                user_id=user_id,
                token=claim_token,
                action=_action(PATH + ".edited"),
                proof=proof,
            )
        assert caught.value.status == 409
        assert caught.value.detail == "参数已变，旧确认不能写入。"
    finally:
        db.close()
    assert opened == []
    claim = _claim(claim_token)
    assert claim.status == "open"
    assert claim.remote_request_id == ""


def test_a_probe_finish_does_not_continue_a_job(plain_client: TestClient) -> None:
    assert plain_client is not None
    user_id = _user()
    session_id = _session(user_id)
    token = _device_seven(user_id)
    proof = recognize_package(token)
    assert proof is not None
    action = _action("/tmp/probe-only.txt")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        offer = propose_local(db, user_id=user_id, session_id=session_id, action=action, proof=proof)
        license_row = commit_local(
            db,
            user_id=user_id,
            token=offer.token,
            action=action,
            proof=proof,
        )
    finally:
        db.close()
    assert license_row["kind"] == "armed"
    finisher = SessionLocal()
    try:
        set_rls_context(finisher, user_id)
        with (
            patch("app.services.task_run.continue_job") as continued,
            patch("app.tasks.job.run_job.delay") as delayed,
        ):
            finish_local(
                finisher,
                user_id=user_id,
                token=offer.token,
                request_id=license_row["request_id"],
                action=action,
                proof=proof,
            )
        continued.assert_not_called()
        delayed.assert_not_called()
    finally:
        finisher.close()
    db = SessionLocal()
    try:
        rows = db.query(TaskRun).filter(TaskRun.session_id == session_id).all()
        assert rows
        assert all(row.kind != "job" for row in rows)
        assert all(row.status == "waiting_person" for row in rows)
    finally:
        db.close()


def test_a_stopped_job_records_the_result_and_stays_stopped(plain_client: TestClient) -> None:
    assert plain_client is not None
    user_id = _user()
    session_id = _session(user_id)
    token = _device_seven(user_id)
    proof = recognize_package(token)
    assert proof is not None
    task_id, generation, claim_token = _open_job(user_id, session_id)
    action = _action()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        license_row = commit_local(
            db,
            user_id=user_id,
            token=claim_token,
            action=action,
            proof=proof,
        )
    finally:
        db.close()
    opened: list[str] = []
    assert perform(
        license_row,
        open_file=opened.append,
        run_program=lambda *_args: opened.append("run"),
        enrolled_device_id=7,
    )
    assert opened == [PATH]
    assert task_run.stop(user_id=user_id, generation=generation).status == "stopped"
    finisher = SessionLocal()
    try:
        set_rls_context(finisher, user_id)
        with (
            patch("app.services.task_run.continue_job") as continued,
            patch("app.tasks.job.run_job.delay") as delayed,
        ):
            finish_local(
                finisher,
                user_id=user_id,
                token=claim_token,
                request_id=license_row["request_id"],
                action=action,
                proof=proof,
            )
        continued.assert_not_called()
        delayed.assert_not_called()
    finally:
        finisher.close()
    assert _load(task_id).status == "stopped"
    _state, result = _call_result(task_id)
    assert license_row["request_id"] in (result or "")
    assert opened == [PATH]


def test_finish_does_not_continue_a_different_generation(plain_client: TestClient) -> None:
    assert plain_client is not None
    user_id = _user()
    session_id = _session(user_id)
    token = _device_seven(user_id)
    proof = recognize_package(token)
    assert proof is not None
    task_id, generation, claim_token = _open_job(user_id, session_id)
    other_db = SessionLocal()
    try:
        set_rls_context(other_db, user_id)
        other = task_run.open_alongside(
            other_db,
            user_id=user_id,
            session_id=session_id,
            kind="job",
            goal="other",
        )
        other_row = other_db.get(TaskRun, other.id)
        other_row.status = "waiting_person"
        claim = other_db.get(ScheduleConfirmClaim, claim_token)
        claim.generation_ids = json.dumps([task_id, int(other.id)])
        other_db.commit()
        other_id = int(other.id)
        other_generation = int(other.generation)
    finally:
        other_db.close()
    assert other_generation != generation
    action = _action()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        license_row = commit_local(db, user_id=user_id, token=claim_token, action=action, proof=proof)
    finally:
        db.close()
    finisher = SessionLocal()
    try:
        set_rls_context(finisher, user_id)
        with patch("app.tasks.job.run_job.delay"):
            finish_local(
                finisher,
                user_id=user_id,
                token=claim_token,
                request_id=license_row["request_id"],
                action=action,
                proof=proof,
            )
    finally:
        finisher.close()
    assert _load(task_id).status == "running"
    assert _load(task_id).generation == generation
    assert _load(other_id).status == "waiting_person"
    assert _load(other_id).generation == other_generation


def test_web_invoke_still_denies_a_local_tool(plain_client: TestClient, tmp_path) -> None:
    assert plain_client is not None
    calls = [0]

    def execute(_ctx: ToolContext, _args: dict) -> ToolResult:
        calls[0] += 1
        return ToolResult(ok=True)

    ensure_builtin_tools()
    register(ToolSpec(name="local_probe", risk="high", execute=execute, effect="read", local=True))
    try:
        ctx = ToolContext(db=None, message="hi", history=[], surface=surface_for_login())
        assert surface_for_login() is ServerSurface.WEB
        result = gateway.invoke("local_probe", ctx, {"local_target": PATH})
        assert result.denied is True
        assert result.deny_reason == "服务端执行面是 web，拒绝本地工具"
        assert calls[0] == 0
        target = tmp_path / "not-opened.txt"
        assert get_tool("desktop.open") is None
        denied = gateway.invoke("desktop.open", ctx, {"local_target": str(target), "device_id": 7})
        assert denied.denied is True
        assert "工具不在白名单" in denied.deny_reason
        assert not target.exists()
    finally:
        _REGISTRY.pop("local_probe", None)
