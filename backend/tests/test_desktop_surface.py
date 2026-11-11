from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.harness import gateway
from app.agents.harness.desktop_channel import PackageProof, recognize_package
from app.agents.harness.surface import ServerSurface, surface_for_login
from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import _REGISTRY, ToolSpec, ensure_builtin_tools, get_tool, register
from app.core.auth import hash_password
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.models import Message, ScheduleConfirmClaim, TaskRun, User
from app.services import task_run
from app.services.local_action import LOCAL_ACTIONS, LocalAction, LocalClosed, propose_local
from app.services.schedule_confirm import canonical_action, write_parts, write_scope
from app.tasks.celery_app import celery_app
from tests.test_schedule_confirm_claims import _admin_id, _session

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from desktop.confirm_local import confirm_local
from desktop.executor import open_with_xdg, perform, run_argv

DENIAL = "服务端执行面是 web，拒绝本地工具"
TOKEN = "ev-package-token"
PROBE = "/tmp/ev-desktop-probe.txt"
BACKEND = REPO / "backend"


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"X-Omni-Package": token}


def _propose(
    client: TestClient,
    session_id: int,
    *,
    action: str = "desktop.open",
    local_target: str = PROBE,
    local_args: list[str] | None = None,
    extra: dict | None = None,
    token: str = TOKEN,
) -> dict:
    payload = {
        "session_id": session_id,
        "action": action,
        "local_target": local_target,
        "local_args": [] if local_args is None else local_args,
        "desktop": "desktop",
        "surface": "web",
    }
    if extra:
        payload.update(extra)
    saved = client.post("/api/desktop/propose", json=payload, headers=_headers(token))
    assert saved.status_code == 200, saved.text
    return saved.json()


class _Transport:
    def __init__(self, client: TestClient, token: str = TOKEN) -> None:
        self.client = client
        self.token = token

    def post(self, path: str, body: dict) -> dict:
        saved = self.client.post(path, json=body, headers=_headers(self.token))
        assert saved.status_code == 200, saved.text
        return saved.json()


def _claim(token: str) -> tuple[str, str, str]:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        return row.status, row.remote_request_id or "", row.scope
    finally:
        db.close()


def _claim_count() -> int:
    db = SessionLocal()
    try:
        return db.query(ScheduleConfirmClaim).count()
    finally:
        db.close()


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


def _stop_live(session_id: int) -> bool:
    stopped = False
    for generation, status in _generations(session_id):
        if status not in {"completed", "stopped"}:
            task_run.stop(user_id=_admin_id(), generation=generation)
            stopped = True
    return stopped


def _desktop_message(session_id: int) -> tuple[str, dict]:
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
        if isinstance(card, dict) and card.get("mode") == "desktop":
            return row.content, card
    raise AssertionError("missing desktop card")


def _other_client() -> TestClient:
    name = f"desktop-other-{uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(
            User(
                external_id=name,
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

    client = TestClient(app)
    logged = client.post("/api/auth/login", json={"username": name, "password": "other-pass"})
    assert logged.status_code == 200, logged.text
    return client


def _open_spy():
    calls: list[str] = []

    def open_file(path: str) -> None:
        calls.append(path)

    def run_program(program: str, args: tuple[str, ...]) -> None:
        raise AssertionError((program, args))

    return calls, open_file, run_program


def test_package_word_is_not_a_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(TypeError):
        PackageProof("desktop")
    monkeypatch.setattr(settings, "desktop_package_token", "")
    assert recognize_package(TOKEN) is None
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    assert recognize_package(None) is None
    assert recognize_package("desktop") is None
    assert recognize_package(" web ") is None
    assert recognize_package("other-secret") is None
    proof = recognize_package("  " + TOKEN + "  ")
    assert isinstance(proof, PackageProof)
    assert proof.audit_word() == "desktop"
    monkeypatch.setattr(settings, "desktop_package_token", "desktop")
    assert recognize_package("desktop") is None


def test_web_session_denies_local_probe(plain_client: TestClient) -> None:
    assert plain_client is not None
    calls = [0]

    def execute(_ctx: ToolContext, _args: dict) -> ToolResult:
        calls[0] += 1
        return ToolResult(ok=True)

    ensure_builtin_tools()
    register(ToolSpec(name="local_probe", risk="high", execute=execute, effect="read", local=True))
    try:
        with pytest.raises(ValueError):
            ServerSurface("desktop")
        surface = surface_for_login()
        assert surface is ServerSurface.WEB
        ctx = ToolContext(db=None, message="hi", history=[], surface=surface)
        result = gateway.invoke("local_probe", ctx, {"local_target": PROBE})
        assert result.denied is True
        assert result.deny_reason == DENIAL
        assert calls[0] == 0
    finally:
        _REGISTRY.pop("local_probe", None)


def test_unregistered_desktop_open_stays_on_the_whitelist(plain_client: TestClient, tmp_path: Path) -> None:
    assert plain_client is not None
    target = tmp_path / "not-opened.txt"
    ensure_builtin_tools()
    assert get_tool("desktop.open") is None
    ctx = ToolContext(db=None, message="hi", history=[], surface=surface_for_login())
    result = gateway.invoke("desktop.open", ctx, {"local_target": str(target)})
    assert result.denied is True
    assert "工具不在白名单" in result.deny_reason
    assert "拒绝本地工具" not in result.deny_reason
    assert not target.exists()


def test_string_proof_writes_no_claim(plain_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert plain_client is not None
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    before = _claim_count()
    db = SessionLocal()
    try:
        with pytest.raises(LocalClosed) as caught:
            propose_local(
                db,
                user_id=_admin_id(),
                session_id=session_id,
                action=LocalAction("desktop.open", PROBE + ".string", ()),
                proof="desktop",
            )
        assert caught.value.status == 403
        assert caught.value.detail == DENIAL
        db.rollback()
    finally:
        db.close()
    assert _claim_count() == before


def test_spoofed_desktop_header_does_not_arm(plain_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    target = PROBE + ".spoof"
    before = _claim_count()
    missing = plain_client.post(
        "/api/desktop/propose",
        json={"session_id": session_id, "action": "desktop.open", "local_target": target, "local_args": []},
    )
    assert missing.status_code == 403
    assert missing.json()["detail"] == DENIAL
    assert _claim_count() == before
    offered = _propose(plain_client, session_id, local_target=target)
    token = offered["confirm_token"]
    status, remote, scope = _claim(token)
    assert status == "open"
    assert remote == ""
    assert scope == f"desktop:user:{_admin_id()}"
    opens: list[str] = []
    spoof = plain_client.post(
        "/api/desktop/commit",
        json={
            "desktop": "desktop",
            "surface": "desktop",
            "session_id": session_id,
            "confirm_token": token,
            "action": "desktop.open",
            "local_target": target,
            "local_args": [],
        },
        headers={"X-Omni-Package": "desktop"},
    )
    assert spoof.status_code == 403
    assert spoof.json()["detail"] == DENIAL
    if isinstance(spoof.json(), dict) and spoof.json().get("kind") == "armed":
        perform(
            spoof.json(),
            open_file=lambda path: opens.append(path),
            run_program=lambda *_args: opens.append("run"),
        )
    assert opens == []
    status, remote, _scope = _claim(token)
    assert status == "open"
    assert remote == ""


def test_recognized_package_opens_once(plain_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    offered = _propose(plain_client, session_id)
    content, card = _desktop_message(session_id)
    assert content == "请确认后才会打开。"
    assert card["mode"] == "desktop"
    assert card["surface"] == "desktop"
    assert card["local_target"] == PROBE
    calls, open_file, run_program = _open_spy()
    transport = _Transport(plain_client)
    assert confirm_local(transport, offered, open_file=open_file, run_program=run_program) is True
    assert calls == [PROBE]
    audit = (BACKEND / "data" / "harness" / "harness-audit.jsonl").read_text(encoding="utf-8")
    assert '"server_surface": "desktop"' in audit
    assert '"tool": "desktop.open"' in audit
    status, remote, _scope = _claim(offered["confirm_token"])
    assert status == "written"
    assert remote
    assert confirm_local(transport, offered, open_file=open_file, run_program=run_program) is False
    assert calls == [PROBE]
    status, remote_again, _scope = _claim(offered["confirm_token"])
    assert status == "written"
    assert remote_again == remote


def test_other_user_cannot_open_and_owner_still_can(
    plain_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    target = PROBE + ".owner"
    offered = _propose(plain_client, session_id, local_target=target)
    other = _other_client()
    saved = other.post(
        "/api/desktop/commit",
        json={
            "confirm_token": offered["confirm_token"],
            "action": "desktop.open",
            "local_target": target,
            "local_args": [],
        },
        headers=_headers(),
    )
    assert saved.status_code == 409
    assert saved.json()["detail"] == "这张确认已经失效，未写入。"
    calls, open_file, run_program = _open_spy()
    assert calls == []
    assert confirm_local(_Transport(plain_client), offered, open_file=open_file, run_program=run_program) is True
    assert calls == [target]


def test_stopped_generation_does_not_open(plain_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    target = PROBE + ".stopped"
    offered = _propose(plain_client, session_id, local_target=target)
    assert _stop_live(session_id)
    saved = plain_client.post(
        "/api/desktop/commit",
        json={
            "confirm_token": offered["confirm_token"],
            "action": "desktop.open",
            "local_target": target,
            "local_args": [],
        },
        headers=_headers(),
    )
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "这张确认已经失效，未写入。"
    calls: list[str] = []
    if saved.status_code == 200:
        perform(saved.json(), open_file=lambda path: calls.append(path), run_program=lambda *_args: calls.append("run"))
    assert calls == []
    status, remote, _scope = _claim(offered["confirm_token"])
    assert remote == ""
    assert status != "unknown"


def test_stop_after_open_does_not_close(plain_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    target = PROBE + ".finished"
    offered = _propose(plain_client, session_id, local_target=target)
    calls, open_file, run_program = _open_spy()
    closes: list[str] = []

    def run_program_unused(program: str, args: tuple[str, ...]) -> None:
        closes.append(program)
        run_program(program, args)

    assert confirm_local(
        _Transport(plain_client),
        offered,
        open_file=open_file,
        run_program=run_program_unused,
    )
    assert calls == [target]
    assert closes == []
    assert _stop_live(session_id)
    assert calls == [target]
    assert closes == []
    status, _remote, _scope = _claim(offered["confirm_token"])
    assert status == "written"


def test_run_starts_the_program_once(plain_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "desktop_package_token", TOKEN)
    session_id = _session()
    offered = _propose(
        plain_client,
        session_id,
        action="desktop.run",
        local_target="/usr/bin/true",
        local_args=[],
    )
    content, _card = _desktop_message(session_id)
    assert content == "请确认后才会运行。"
    opens: list[str] = []
    runs: list[tuple[str, tuple[str, ...]]] = []
    assert confirm_local(
        _Transport(plain_client),
        offered,
        open_file=lambda path: opens.append(path),
        run_program=lambda program, args: runs.append((program, tuple(args))),
    )
    assert opens == []
    assert runs == [("/usr/bin/true", ())]
    assert confirm_local(
        _Transport(plain_client),
        offered,
        open_file=lambda path: opens.append(path),
        run_program=lambda program, args: runs.append((program, tuple(args))),
    ) is False
    assert runs == [("/usr/bin/true", ())]


def test_a_shell_string_is_not_an_argument_list() -> None:
    with pytest.raises(ValueError):
        write_parts("desktop.run", {"local_target": "/bin/true", "local_args": "echo hi"})


def test_desktop_scope_wins_over_merchant() -> None:
    assert write_scope(7, {"local_target": PROBE, "local_args": [], "merchant_key": "shop", "mail_to": ["a@b.c"]}) == (
        "desktop:user:7"
    )
    for name in LOCAL_ACTIONS:
        assert canonical_action(name) == name


def test_actions_match_the_package_list() -> None:
    text = (REPO / "desktop" / "actions.js").read_text(encoding="utf-8")
    found = tuple(re.findall(r'"(desktop\.[a-z]+)"', text))
    assert found == LOCAL_ACTIONS


def test_web_sources_do_not_carry_the_package_buttons() -> None:
    banned = ("打开测试文件", "desktop/executor", "shell.openPath")
    for path in (REPO / "frontend" / "src").rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, path
    window = (REPO / "frontend" / "src" / "components" / "ChatWindow.tsx").read_text(encoding="utf-8")
    assert 'mode !== "desktop"' in window
    for path in (REPO / "desktop").rglob("*"):
        if path.suffix not in {".py", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "khoj" not in text.lower(), path
        assert "shell=True" not in text, path
        assert "shell.openPath" not in text, path
    assert "xdg-open" not in inspect_source(perform)
    assert "xdg-open" in inspect_source(open_with_xdg)
    assert "shell=False" in inspect_source(run_argv)
    package = json.loads((REPO / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert "electron" in package["dependencies"]
    assert "frontend" not in json.dumps(package)
    main = (REPO / "desktop" / "main.js").read_text(encoding="utf-8")
    preload = (REPO / "desktop" / "preload.js").read_text(encoding="utf-8")
    assert "http://127.0.0.1:5173" in main
    assert "nodeIntegration: false" in main
    assert "contextIsolation: true" in main
    assert "DESKTOP_PACKAGE_TOKEN" in main
    assert "shell: false" in main
    assert "desktop-propose" in main
    assert "desktop-confirm" in main
    assert '"propose"' in main
    assert "打开测试文件" in preload
    assert "运行测试程序" in preload
    assert "desktop-propose" in preload
    assert "desktop-confirm" in preload
    assert "DESKTOP_PACKAGE_TOKEN" not in preload


def test_compose_and_beat_stay_put() -> None:
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    block = text.split("services:", 1)[1].split("\nvolumes:", 1)[0]
    names = re.findall(r"^  ([a-z0-9-]+):", block, re.M)
    assert names == [
        "postgres",
        "qdrant",
        "redis",
        "minio",
        "sandbox",
        "sandbox-runner",
        "eval",
        "errand-compose",
        "api",
        "worker",
        "beat",
        "nginx",
    ]
    assert set(celery_app.conf.beat_schedule) == {
        "recover-stale-document-ingests",
        "memory-schedule-pulse",
        "watch-task-runs",
    }


def test_desktop_dev_refuses_an_empty_token() -> None:
    script = REPO / "scripts" / "desktop-dev.sh"
    text = script.read_text(encoding="utf-8")
    assert "npm run dev" in text
    assert "electron" in text
    assert "docker" not in text.lower()
    env = os.environ.copy()
    env["DESKTOP_PACKAGE_TOKEN"] = ""
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "DESKTOP_PACKAGE_TOKEN" in result.stderr


def inspect_source(fn: object) -> str:
    import inspect

    return inspect.getsource(fn)
