from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.models import ErrandRecord, ErrandVault, MerchantConnector, Message, ScheduleConfirmClaim, TaskRun, User
from app.services import task_run
from app.services.errand_order import ErrandOrder, ErrandOrderError, Secret
from app.services.schedule_confirm import ScheduleConfirmClosed, canonical_action, write_parts, write_scope
from tests.test_schedule_confirm_claims import _admin_id, _session

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
_WALLS = (
    "请使用App",
    "打开App",
    "仅支持App",
    "Download the app",
    "Open in the app",
)


class _ShopState:
    def __init__(self) -> None:
        self.base = ""
        self.places: list[dict] = []
        self.lookups: list[dict] = []
        self.tasks: list[dict] = []
        self.place_mode = "placed"
        self.place_body: dict = {"outcome": "placed", "order_id": "ord-1"}
        self.lookup_body: dict = {"told": ""}
        self.compose_body: dict = {
            "outcome": "app_wall",
            "order_id": "",
            "told": "请使用App",
            "clicks_after_wall": 0,
        }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/health":
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = self.path.split("?", 1)[0]
        state: _ShopState = self.server.state
        item = {"key": self.headers.get("Idempotency-Key"), "body": raw}
        if path == "/place":
            state.places.append(item)
            if state.place_mode == "timeout":
                self._hold()
                return
            self._send(200, state.place_body)
            return
        if path == "/lookup":
            state.lookups.append(item)
            self._send(200, state.lookup_body)
            return
        if path == "/tasks":
            state.tasks.append(item)
            self._send(200, state.compose_body)
            return
        self._send(404, {"detail": "not found"})

    def _hold(self) -> None:
        self.connection.settimeout(2)
        try:
            while True:
                chunk = self.connection.recv(64)
                if not chunk:
                    return
        except OSError:
            return

    def _send(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args) -> None:
        return


@pytest.fixture
def shop(monkeypatch):
    state = _ShopState()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.state = state
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    state.base = f"http://{host}:{port}"
    _ready(state.base)
    monkeypatch.setattr(settings, "errand_compose_url", state.base)
    monkeypatch.setattr(settings, "errand_compose_token", "")
    monkeypatch.setattr(settings, "errand_vault_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "errand_http_timeout", 5.0)
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_connector_commit_places_once(plain_client: TestClient, shop: _ShopState, monkeypatch):
    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("note_external_success")

    monkeypatch.setattr("app.services.event_inbox.note_external_success", boom)
    merchant = _merchant()
    order = _order(merchant)
    secret = "pay-secret-9"
    _connector(merchant, shop.base)
    stored = _vault(plain_client, merchant, secret)
    assert stored.status_code == 200, stored.text
    assert stored.json() == {"stored": True}
    assert secret not in stored.text
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    assert offered.status_code == 200, offered.text
    assert shop.places == []
    assert shop.tasks == []
    token = offered.json()["confirm_token"]
    content, card = _card(session_id)
    assert content == "请确认后才会下单。"
    assert card["mode"] == "errand"
    assert card["phase"] == "draft"
    assert secret not in json.dumps(card, ensure_ascii=False)
    saved = _commit(plain_client, token, order)
    assert saved.status_code == 200, saved.text
    assert saved.json()["order_id"] == "ord-1"
    assert saved.json()["status"] == "written"
    assert secret not in saved.text
    assert len(shop.places) == 1
    assert shop.tasks == []
    assert calls["n"] == 0
    status, remote = _claim(token)
    assert status == "written"
    assert shop.places[0]["key"] == remote
    assert secret.encode("utf-8") in shop.places[0]["body"]
    rows = _records(_admin_id(), merchant)
    assert rows == [(remote, "ord-1")]


def test_empty_token_writes_nothing(plain_client: TestClient, shop: _ShopState):
    merchant = _merchant()
    pan = "4111111111114242"
    password = "pw-planted-secret"
    order = _order(merchant)
    saved = plain_client.post(
        "/api/errand/commit",
        json={**order, "confirm_token": "", "pan": pan, "password": password},
    )
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "缺少确认，未写入。"
    assert pan not in saved.text
    assert password not in saved.text
    assert shop.places == []
    assert shop.tasks == []
    assert _vault_count(_admin_id(), merchant) == 0


def test_app_wall_stops_without_a_shop_call(plain_client: TestClient, shop: _ShopState):
    merchant = _merchant()
    order = _order(merchant)
    stored = _vault(plain_client, merchant, "pay-secret-9")
    assert stored.status_code == 200, stored.text
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    assert offered.status_code == 200, offered.text
    assert shop.places == []
    assert shop.tasks == []
    saved = _commit(plain_client, offered.json()["confirm_token"], order)
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "这家店只能在 App 里下单，已停止。"
    assert len(shop.tasks) == 1
    assert json.loads(shop.tasks[0]["body"])["kind"] == "place"
    assert shop.places == []
    assert _records(_admin_id(), merchant) == []


def test_timeout_does_not_place_again(plain_client: TestClient, shop: _ShopState, monkeypatch):
    def calendar_lookup(self, request_id):
        raise AssertionError("lookup_inserted_event")

    def settle(*_args, **_kwargs):
        raise AssertionError("settle_unknown")

    monkeypatch.setattr(
        "app.services.calendar_google.GoogleCalendarProvider.lookup_inserted_event",
        calendar_lookup,
    )
    monkeypatch.setattr("app.agents.workflow.settle_unknown", settle)
    monkeypatch.setattr(settings, "errand_http_timeout", 0.3)
    merchant = _merchant()
    order = _order(merchant)
    _connector(merchant, shop.base)
    stored = _vault(plain_client, merchant, "pay-secret-9")
    assert stored.status_code == 200, stored.text
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    token = offered.json()["confirm_token"]
    shop.place_mode = "timeout"
    shop.lookup_body = {"told": "已下单", "order_id": "someone-else"}
    first = _commit(plain_client, token, order)
    assert first.status_code == 409, first.text
    assert first.json()["detail"] == "结果未知，未再次写入。请先核对日历。"
    assert len(shop.places) == 1
    assert len(shop.lookups) >= 1
    assert _records(_admin_id(), merchant) == []
    status, remote = _claim(token)
    assert status == "unknown"
    assert remote
    second = _commit(plain_client, token, order)
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "结果未知，未再次写入。请先核对日历。"
    assert len(shop.places) == 1
    assert _records(_admin_id(), merchant) == []
    shop.lookup_body = {"told": "已下单", "order_id": "ord-lookup-1", "idempotency_key": remote}
    found = _commit(plain_client, token, order)
    assert found.status_code == 200, found.text
    assert found.json()["order_id"] == "ord-lookup-1"
    assert len(shop.places) == 1
    assert _records(_admin_id(), merchant) == [(remote, "ord-lookup-1")]


def test_write_parts_match_claim_parts(plain_client: TestClient):
    raw = _order(_merchant())
    parts = write_parts("errand.place", raw)
    assert parts == ErrandOrder.parse(raw).claim_parts()
    assert write_parts("errand.place", parts) == parts
    assert write_scope(4, parts) == f"errand:user:4:merchant:{parts['merchant_key']}"
    assert write_scope(4, {**parts, "mail_to": ["a@b.c"]}) == f"errand:user:4:merchant:{parts['merchant_key']}"
    assert write_scope(4, {"mail_to": ["a@b.c"], "subject": "s", "body": "b"}) == "mail:user:4"
    assert canonical_action("errand.place") == "errand.place"
    with pytest.raises(ScheduleConfirmClosed):
        canonical_action("errand.cancel")
    for key in ("password", "pan", "cvv", "cookie", "secret"):
        with pytest.raises(ErrandOrderError):
            ErrandOrder.parse({**raw, key: "hidden"})
    secret = Secret("4242424242424242")
    assert str(secret) == ""
    assert "4242" not in repr(secret)


def test_other_user_cannot_commit(plain_client: TestClient, shop: _ShopState):
    merchant = _merchant()
    order = _order(merchant)
    _connector(merchant, shop.base)
    stored = _vault(plain_client, merchant, "pay-secret-9")
    assert stored.status_code == 200, stored.text
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    token = offered.json()["confirm_token"]
    other = _other_client()
    stolen = _commit(other, token, order)
    assert stolen.status_code == 409, stolen.text
    assert shop.places == []
    saved = _commit(plain_client, token, order)
    assert saved.status_code == 200, saved.text
    assert len(shop.places) == 1
    again = _commit(other, token, order)
    assert again.status_code == 409, again.text
    assert len(shop.places) == 1


def test_missing_pay_secret_does_not_charge(plain_client: TestClient, shop: _ShopState):
    merchant = _merchant()
    order = _order(merchant)
    _connector(merchant, shop.base)
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    assert offered.status_code == 200, offered.text
    saved = _commit(plain_client, offered.json()["confirm_token"], order)
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "没有可用的支付凭证，未支付。"
    assert shop.places == []
    assert shop.tasks == []
    status, remote = _claim(offered.json()["confirm_token"])
    assert status == "open"
    assert remote == ""


def test_browser_opens_only_a_public_host(plain_client: TestClient, shop: _ShopState):
    from app.services.errands import public_start_url

    assert public_start_url("shop.example") == "https://shop.example/"
    for blocked in ("127.0.0.1", "10.0.0.8", "localhost", "shop.local", "metadata.google.internal", "not a host"):
        assert public_start_url(blocked) == ""
    merchant = f"shop-{uuid4().hex[:8]}.example"
    assert public_start_url(merchant) == f"https://{merchant}/"
    order = _order(merchant)
    stored = _vault(plain_client, merchant, "pay-secret-9")
    assert stored.status_code == 200, stored.text
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    saved = _commit(plain_client, offered.json()["confirm_token"], order)
    assert saved.status_code == 409, saved.text
    assert len(shop.tasks) == 1
    body = json.loads(shop.tasks[0]["body"])
    assert body["url"] == f"https://{merchant}/"
    assert body["kind"] == "place"
    assert "pay-secret-9" not in saved.text


def test_stopped_generation_does_not_place(plain_client: TestClient, shop: _ShopState):
    merchant = _merchant()
    order = _order(merchant)
    _connector(merchant, shop.base)
    stored = _vault(plain_client, merchant, "pay-secret-9")
    assert stored.status_code == 200, stored.text
    session_id = _session()
    offered = _propose(plain_client, session_id, order)
    assert offered.status_code == 200, offered.text
    stopped = False
    for generation, status in _generations(session_id):
        if status not in {"completed", "stopped"}:
            task_run.stop(user_id=_admin_id(), generation=generation)
            stopped = True
    assert stopped
    saved = _commit(plain_client, offered.json()["confirm_token"], order)
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"] == "这张确认已经失效，未写入。"
    assert shop.places == []
    assert shop.tasks == []


def test_api_does_not_ship_a_browser(plain_client: TestClient):
    req = (BACKEND / "requirements.txt").read_text(encoding="utf-8").lower()
    for name in ("playwright", "selenium", "puppeteer", "drission"):
        assert name not in req
    compose_req = (REPO / "services" / "errand-compose" / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "playwright" in compose_req
    for name in ("selenium", "puppeteer", "drission"):
        assert name not in compose_req
    for path in (BACKEND / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "playwright" not in text.lower(), path
        if "agents" in path.parts:
            assert "errand_vault" not in text, path
            assert "decrypt_token" not in text, path
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    start = text.index("\n  errand-compose:\n")
    end = text.index("\n  api:\n", start)
    block = text[start:end]
    assert "omni-butler-errand-compose:local" in block
    assert "8010" in block
    assert "network_mode" not in block
    assert "DATABASE_URL" not in block
    assert "\n    ports:" not in block
    assert "ERRAND_COMPOSE_URL: http://errand-compose:8010" in text
    assert text.count("ERRAND_COMPOSE_URL:") == 1
    service = (BACKEND / "app" / "services" / "errands.py").read_text(encoding="utf-8")
    assert "note_external_success" not in service
    assert "settle_unknown" not in service
    assert "lookup_inserted_event" not in service


def test_compose_decides_without_a_browser(plain_client: TestClient):
    path = REPO / "services" / "errand-compose" / "decide.py"
    source = path.read_text(encoding="utf-8")
    assert "playwright" not in source.lower()
    module = _load(path, "errand_compose_decide")
    for phrase in _WALLS:
        result = module.decide(phrase, pay_clicked=False, clicks_after_wall=4)
        assert result["outcome"] == "app_wall"
        assert result["clicks_after_wall"] == 0
    paid = module.decide("请使用App", pay_clicked=True, clicks_after_wall=2)
    assert paid["outcome"] == "unknown"
    assert paid["clicks_after_wall"] == 2
    bare = module.decide("已下单 其他单", armed="armed-1")
    assert bare["outcome"] != "already"
    named = module.decide("已下单 armed-1", armed="armed-1", order_id="ord-9")
    assert named["outcome"] == "already"
    assert named["order_id"] == "ord-9"
    assert named["told"] == "已下单"
    assert "armed-1" not in named["told"]
    page = module.decide("<html>4111111111114242 已下单</html>", armed="armed-1")
    assert "4111" not in json.dumps(page)
    assert module.allowed_start("https://shop.example/")
    assert not module.allowed_start("http://shop.example/")
    assert not module.allowed_start("https://127.0.0.1/")
    assert not module.allowed_start("https://10.1.2.3/")
    assert not module.allowed_start("https://localhost/")
    assert not module.allowed_start("https://user:pw@shop.example/")
    driver = (REPO / "services" / "errand-compose" / "driver.py").read_text(encoding="utf-8")
    assert "playwright" in driver.lower()
    assert "The plan did not choose this library." in driver


def _ready(base: str) -> None:
    for _ in range(50):
        try:
            response = httpx.get(f"{base}/health", timeout=0.2, trust_env=False)
        except httpx.HTTPError:
            continue
        if response.status_code == 200:
            return
    raise RuntimeError("fake shop did not start")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _merchant() -> str:
    return f"shop-{uuid4().hex[:8]}"


def _order(merchant: str) -> dict:
    return {
        "merchant_key": merchant,
        "lines": [{"sku": "milk", "qty": 1, "query": "牛奶"}],
        "ship_to": {"name": "李", "phone": "13800000000", "region": "上海", "address": "南京路 1 号"},
        "pay_method": "支付宝",
        "amount_cap": 5000,
    }


def _propose(client: TestClient, session_id: int, order: dict):
    return client.post("/api/errand/proposal", json={"session_id": session_id, **order})


def _commit(client: TestClient, token: str, order: dict):
    return client.post("/api/errand/commit", json={"confirm_token": token, **order})


def _vault(client: TestClient, merchant: str, secret: str):
    return client.post(
        "/api/errand/vault",
        json={"merchant_key": merchant, "kind": "pay", "secret": secret},
    )


def _connector(merchant: str, base: str) -> None:
    db = SessionLocal()
    try:
        db.add(
            MerchantConnector(
                merchant_key=merchant,
                place_url=f"{base}/place",
                lookup_url=f"{base}/lookup",
                needs_login=0,
            )
        )
        db.commit()
    finally:
        db.close()


def _claim(token: str) -> tuple[str, str]:
    db = SessionLocal()
    try:
        row = db.get(ScheduleConfirmClaim, token)
        assert row is not None
        return row.status, row.remote_request_id or ""
    finally:
        db.close()


def _records(user_id: int, merchant: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ErrandRecord)
            .filter(ErrandRecord.user_id == user_id, ErrandRecord.merchant_key == merchant)
            .all()
        )
        return [(row.idempotency_key, row.order_id) for row in rows]
    finally:
        db.close()


def _vault_count(user_id: int, merchant: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ErrandVault)
            .filter(ErrandVault.user_id == user_id, ErrandVault.merchant_key == merchant)
            .count()
        )
    finally:
        db.close()


def _card(session_id: int) -> tuple[str, dict]:
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
        if isinstance(card, dict) and card.get("mode") == "errand":
            return row.content, card
    raise AssertionError("missing errand card")


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


def _other_client() -> TestClient:
    name = f"errand-other-{uuid4().hex[:8]}"
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
