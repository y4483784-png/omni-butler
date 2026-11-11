from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.harness.effects import decide_replay
from app.core.config import settings
from app.core.db import set_rls_context
from app.models.models import ErrandRecord, MerchantConnector, Message, ScheduleConfirmClaim
from app.services.calendar import draft_expires_at
from app.services.errand_order import ErrandOrder, Secret
from app.services.errand_vault import VaultError, read_secret
from app.services.schedule_confirm import (
    CONFIRM_DETAIL,
    ScheduleConfirmClosed,
    arm_remote_claim,
    consume_schedule_claim,
    finish_confirm,
    issue_schedule_claim,
    live_task_ids_for_session,
    parse_card_expiry,
    reopen_known_rejection,
    write_fingerprint,
)
from app.services.task_run import UnownedSession, insert_card_action

logger = logging.getLogger(__name__)

_SEALED = frozenset({"password", "pan", "cvv", "cookie", "secret", "pay", "login"})
_APP_WALL = "这家店只能在 App 里下单，已停止。"
_NO_PAY = "没有可用的支付凭证，未支付。"
_NO_LOGIN = "没有可用的登录凭证，未下单。"
_NO_CHANNEL = "没有可用的下单通道，未下单。"
_OVER_CAP = "价格超过上限，未支付。"
_REJECTED = "这家店拒绝了这笔订单，未支付。"
_CARD_TEXT = "请确认后才会下单。"


class ErrandClosed(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


class RemoteDown(Exception):
    pass


@dataclass(frozen=True)
class ShopReply:
    kind: str
    order_id: str = ""
    told: str = ""
    clicks_after_wall: int = 0
    price: int | None = None
    named: bool = False


@dataclass(frozen=True)
class ErrandOffer:
    token: str
    expires_at: str
    phase: str

    def as_dict(self) -> dict:
        return {
            "confirm_token": self.token,
            "token": self.token,
            "expires_at": self.expires_at,
            "phase": self.phase,
        }


def propose_errand(db: Session, *, user_id: int, session_id: int, raw: dict) -> ErrandOffer:
    set_rls_context(db, user_id)
    _require_session(db, user_id, session_id)
    order = ErrandOrder.parse(raw)
    parts = order.claim_parts()
    fingerprint = write_fingerprint(parts)
    claim = _active_claim(db, user_id, fingerprint)
    if claim is None:
        task_ids = live_task_ids_for_session(db, user_id, session_id)
        if not task_ids:
            placeholder = _add_message(db, session_id, _card(parts, "", draft_expires_at(), "draft"))
            try:
                turn = insert_card_action(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    message_id=placeholder.id,
                )
            except UnownedSession as exc:
                db.rollback()
                raise ErrandClosed(404, "会话不存在") from exc
            task_ids = [turn.id]
        try:
            token, stored_expiry = issue_schedule_claim(
                db,
                user_id=user_id,
                session_id=session_id,
                action="errand.place",
                raw=parts,
                task_run_ids=task_ids,
                expires_at=parse_card_expiry(draft_expires_at()),
            )
        except ScheduleConfirmClosed as exc:
            db.rollback()
            raise _closed(exc) from exc
        claim = db.get(ScheduleConfirmClaim, token)
    else:
        token = claim.token
        stored_expiry = claim.expires_at
    phase = "confirmed" if claim is not None and claim.status == "written" else "draft"
    if claim is not None and claim.status == "unknown":
        phase = "unknown"
    expires_at = _expiry_text(stored_expiry)
    _ensure_message(db, session_id, parts, token, expires_at, phase)
    return ErrandOffer(token=token, expires_at=expires_at, phase=phase)


def commit_errand(db: Session, *, user_id: int, token: object, raw: dict) -> dict:
    set_rls_context(db, user_id)
    if token is None or str(token).strip() == "":
        try:
            consume_schedule_claim(db, user_id, "", action="errand.place", raw={})
        except ScheduleConfirmClosed as exc:
            raise _closed(exc) from None
        raise ErrandClosed(409, CONFIRM_DETAIL["missing"])
    order = ErrandOrder.parse(raw)
    parts = order.claim_parts()
    key = str(token).strip()
    try:
        consume_schedule_claim(db, user_id, key, action="errand.place", raw=parts)
    except ScheduleConfirmClosed as exc:
        if exc.reason == "unknown":
            return _reconcile(db, user_id, key, order)
        raise _closed(exc) from None
    pay = _optional_secret(db, user_id, order.merchant_key, "pay")
    if pay is None:
        db.rollback()
        raise ErrandClosed(409, _NO_PAY)
    connector = _connector(db, order.merchant_key)
    if not _channel_ready(connector):
        db.rollback()
        raise ErrandClosed(409, _NO_CHANNEL)
    login = _optional_secret(db, user_id, order.merchant_key, "login")
    if connector is not None and int(connector.needs_login or 0) and login is None:
        db.rollback()
        raise ErrandClosed(409, _NO_LOGIN)
    armed = str(uuid4())
    arm_remote_claim(db, key, armed)
    set_rls_context(db, user_id)
    claim = _load_claim(db, key)
    stored = "" if claim is None else str(claim.remote_request_id or "").strip()
    if not stored:
        raise ErrandClosed(409, CONFIRM_DETAIL["unknown"])
    try:
        reply = _place(connector, order, stored, pay, login)
    except RemoteDown:
        return _reconcile(db, user_id, key, order)
    return _apply_reply(db, user_id, key, order, stored, reply)


def drop_sealed(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if str(key).strip().casefold() not in _SEALED}


def post_json(url: str, *, headers: dict[str, str], payload: dict, timeout: float) -> dict:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(url, headers=headers, json=payload, follow_redirects=False)
    except (httpx.TransportError, OSError):
        logger.info("errand transport failed")
        raise RemoteDown() from None
    try:
        body = response.json()
    except ValueError:
        body = {"told": response.text[:500]}
    if not isinstance(body, dict):
        body = {"told": ""}
    safe = drop_sealed(body)
    logger.info("errand http status=%s outcome=%s", response.status_code, safe.get("outcome"))
    if response.status_code >= 500:
        raise RemoteDown()
    if response.status_code >= 400 and "outcome" not in body:
        body = {**body, "outcome": "rejected"}
    return body


def _apply_reply(
    db: Session,
    user_id: int,
    token: str,
    order: ErrandOrder,
    armed: str,
    reply: ShopReply,
) -> dict:
    if reply.price is not None and reply.price > order.amount_cap:
        reopen_known_rejection(db, token)
        raise ErrandClosed(409, _OVER_CAP)
    if reply.kind == "app_wall" and reply.clicks_after_wall <= 0:
        reopen_known_rejection(db, token)
        raise ErrandClosed(409, _APP_WALL)
    if reply.kind == "need_secret" and reply.clicks_after_wall <= 0:
        reopen_known_rejection(db, token)
        raise ErrandClosed(409, _NO_LOGIN)
    if reply.kind == "rejected" and reply.clicks_after_wall <= 0:
        reopen_known_rejection(db, token)
        raise ErrandClosed(409, _REJECTED)
    if reply.order_id and (reply.kind == "placed" or (reply.kind == "already" and reply.named)):
        return _save(db, user_id, token, order, armed, reply.order_id)
    return _reconcile(db, user_id, token, order)


def _reconcile(db: Session, user_id: int, token: str, order: ErrandOrder) -> dict:
    set_rls_context(db, user_id)
    claim = _load_claim(db, token)
    armed = ""
    if claim is not None and claim.user_id == user_id:
        armed = str(claim.remote_request_id or "").strip()
    choice = decide_replay(
        "non_replayable_write",
        attempt=1,
        external_id=armed,
        prior_external_id=armed,
    )
    if choice != "reconcile" or not armed:
        raise ErrandClosed(409, CONFIRM_DETAIL["unknown"])
    existing = _record(db, user_id, armed)
    if existing is not None:
        finish_confirm(db, token)
        return _ok(existing.order_id, armed)
    try:
        found = _lookup(db, order, armed)
    except RemoteDown:
        found = None
    if found is not None and found.named:
        return _save(db, user_id, token, order, armed, found.order_id)
    raise ErrandClosed(409, CONFIRM_DETAIL["unknown"])


def _save(db: Session, user_id: int, token: str, order: ErrandOrder, armed: str, order_id: str) -> dict:
    _insert_record(db, user_id, armed, order.merchant_key, order_id)
    finish_confirm(db, token)
    return _ok(order_id, armed)


def _ok(order_id: str, armed: str) -> dict:
    return {"order_id": order_id, "status": "written", "idempotency_key": armed}


def _place(
    connector: MerchantConnector | None,
    order: ErrandOrder,
    armed: str,
    pay: Secret,
    login: Secret | None,
) -> ShopReply:
    if connector is not None:
        body = post_json(
            connector.place_url,
            headers=_headers(armed, compose=False),
            payload=_connector_payload(order, pay),
            timeout=_timeout(),
        )
        return interpret(body, armed)
    body = post_json(
        _tasks_url(),
        headers=_headers(armed, compose=True),
        payload=_compose_payload(order, armed, pay, login, "place"),
        timeout=_timeout(),
    )
    return interpret(body, armed)


def _lookup(db: Session, order: ErrandOrder, armed: str) -> ShopReply | None:
    connector = _connector(db, order.merchant_key)
    headers = _headers(armed, compose=connector is None)
    if connector is not None:
        lookup_url = str(connector.lookup_url or "").strip()
        if not lookup_url:
            return None
        body = post_json(
            lookup_url,
            headers=headers,
            payload={"idempotency_key": armed},
            timeout=_timeout(),
        )
        return interpret(body, armed)
    if not (settings.errand_compose_url or "").strip():
        return None
    body = post_json(
        _tasks_url(),
        headers=headers,
        payload={"kind": "lookup", "idempotency_key": armed, "merchant_key": order.merchant_key},
        timeout=_timeout(),
    )
    return interpret(body, armed)


def _connector_payload(order: ErrandOrder, pay: Secret) -> dict:
    payload = order.claim_parts()
    payload["pay"] = pay.reveal()
    return payload


def _compose_payload(
    order: ErrandOrder,
    armed: str,
    pay: Secret | None,
    login: Secret | None,
    kind: str,
) -> dict:
    payload = {"kind": kind, "idempotency_key": armed, "merchant_key": order.merchant_key}
    start = public_start_url(order.merchant_key)
    if start:
        payload["url"] = start
    if kind != "place":
        return payload
    payload.update(order.claim_parts())
    if pay is not None:
        payload["pay"] = pay.reveal()
    if login is not None:
        payload["login"] = login.reveal()
    return payload


def _headers(armed: str, *, compose: bool) -> dict[str, str]:
    headers = {"Idempotency-Key": armed}
    if not compose:
        return headers
    token = (settings.errand_compose_token or "").strip()
    if token:
        headers["token"] = token
    return headers


def interpret(body: dict, armed: str) -> ShopReply:
    clicks_raw = body.get("clicks_after_wall", 0)
    try:
        clicks = int(clicks_raw)
    except (TypeError, ValueError):
        clicks = 0
    price_raw = body.get("price")
    price = price_raw if isinstance(price_raw, int) and not isinstance(price_raw, bool) else None
    told = body.get("told") if isinstance(body.get("told"), str) else ""
    order_id = body.get("order_id") if isinstance(body.get("order_id"), str) else ""
    outcome = body.get("outcome") if isinstance(body.get("outcome"), str) else ""
    echoed = body.get("idempotency_key")
    echoed_key = echoed.strip() if isinstance(echoed, str) else ""
    text = json.dumps(drop_sealed(body), ensure_ascii=False)
    named = bool(armed) and armed in text and "已下单" in text
    if echoed_key and echoed_key != armed:
        named = False
    if outcome == "app_wall":
        return ShopReply("app_wall", order_id=order_id, told=told, clicks_after_wall=clicks, price=price)
    if outcome == "placed" and not (echoed_key and echoed_key != armed):
        return ShopReply("placed", order_id=order_id, told=told, clicks_after_wall=clicks, price=price, named=True)
    if outcome == "already":
        if named:
            return ShopReply("already", order_id=order_id, told=told, clicks_after_wall=clicks, price=price, named=True)
        return ShopReply("unknown", order_id=order_id, told=told, clicks_after_wall=clicks, price=price)
    if named:
        return ShopReply("already", order_id=order_id, told=told, clicks_after_wall=clicks, price=price, named=True)
    if outcome in {"rejected", "need_secret"}:
        return ShopReply(outcome, told=told, clicks_after_wall=clicks, price=price)
    return ShopReply("unknown", order_id=order_id, told=told, clicks_after_wall=clicks, price=price)


def _channel_ready(connector: MerchantConnector | None) -> bool:
    if connector is not None:
        return bool(str(connector.place_url or "").strip())
    return bool((settings.errand_compose_url or "").strip())


def public_start_url(merchant_key: str) -> str:
    host = merchant_key.strip().lower().rstrip(".")
    if not host or "/" in host or "\\" in host or "@" in host or " " in host:
        return ""
    url = f"https://{host}/"
    parsed = urlparse(url)
    if parsed.hostname != host or parsed.scheme != "https":
        return ""
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal")):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2 or not all(_host_label(label) for label in labels):
        return ""
    return url


def _host_label(label: str) -> bool:
    if not label or label.startswith("-") or label.endswith("-"):
        return False
    return all(ch.isalnum() or ch == "-" for ch in label)


def _tasks_url() -> str:
    return f"{(settings.errand_compose_url or '').strip().rstrip('/')}/tasks"


def _timeout() -> float:
    try:
        return float(settings.errand_http_timeout)
    except (TypeError, ValueError):
        return 10.0


def _optional_secret(db: Session, user_id: int, merchant_key: str, kind: str) -> Secret | None:
    try:
        return read_secret(db, user_id=user_id, merchant_key=merchant_key, kind=kind)
    except VaultError:
        return None


def _connector(db: Session, merchant_key: str) -> MerchantConnector | None:
    return (
        db.query(MerchantConnector)
        .filter(MerchantConnector.merchant_key == merchant_key)
        .one_or_none()
    )


def _record(db: Session, user_id: int, armed: str) -> ErrandRecord | None:
    return (
        db.query(ErrandRecord)
        .filter(ErrandRecord.user_id == user_id, ErrandRecord.idempotency_key == armed)
        .one_or_none()
    )


def _insert_record(db: Session, user_id: int, armed: str, merchant_key: str, order_id: str) -> None:
    set_rls_context(db, user_id)
    if _record(db, user_id, armed) is not None:
        return
    db.add(
        ErrandRecord(
            user_id=user_id,
            idempotency_key=armed,
            merchant_key=merchant_key,
            order_id=order_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_rls_context(db, user_id)
        if _record(db, user_id, armed) is None:
            raise


def _load_claim(db: Session, token: str) -> ScheduleConfirmClaim | None:
    key = str(token or "").strip()
    if not key:
        return None
    return db.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.token == key).one_or_none()


def _active_claim(db: Session, user_id: int, fingerprint: str) -> ScheduleConfirmClaim | None:
    rows = (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == user_id,
            ScheduleConfirmClaim.action == "errand.place",
            ScheduleConfirmClaim.fingerprint == fingerprint,
            ScheduleConfirmClaim.status.in_(("open", "unknown", "written")),
        )
        .all()
    )
    for status in ("open", "unknown", "written"):
        found = next((row for row in rows if row.status == status), None)
        if found is not None:
            return found
    return None


def _require_session(db: Session, user_id: int, session_id: int) -> None:
    from app.models.models import Session as ChatSession

    row = (
        db.query(ChatSession.id)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise ErrandClosed(404, "会话不存在")


def _card(parts: dict, token: str, expires_at: str, phase: str) -> dict:
    return {
        "mode": "errand",
        "phase": phase,
        "confirm_token": token,
        "expires_at": expires_at,
        "errand": parts,
    }


def _add_message(db: Session, session_id: int, card: dict) -> Message:
    row = Message(
        session_id=session_id,
        role="assistant",
        content=_CARD_TEXT,
        schedule_card=json.dumps(card, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def _ensure_message(
    db: Session,
    session_id: int,
    parts: dict,
    token: str,
    expires_at: str,
    phase: str,
) -> None:
    card = _card(parts, token, expires_at, phase)
    encoded = json.dumps(card, ensure_ascii=False)
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.role == "assistant")
        .all()
    )
    for row in rows:
        parsed = _load_card(row.schedule_card)
        if parsed.get("mode") != "errand" or parsed.get("errand") != parts:
            continue
        current = str(parsed.get("confirm_token") or "")
        if current == token:
            if parsed.get("phase") != phase:
                parsed["phase"] = phase
                parsed["expires_at"] = expires_at
                row.schedule_card = json.dumps(parsed, ensure_ascii=False)
                db.commit()
            return
        if not current or _claim_cancelled(db, current):
            row.schedule_card = encoded
            db.commit()
            return
    _add_message(db, session_id, card)
    db.commit()


def _load_card(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _claim_cancelled(db: Session, token: str) -> bool:
    claim = db.get(ScheduleConfirmClaim, token)
    return claim is not None and claim.status == "cancelled"


def _expiry_text(value: datetime | None) -> str:
    if value is None:
        return draft_expires_at()
    stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


def _closed(exc: ScheduleConfirmClosed) -> ErrandClosed:
    return ErrandClosed(409, CONFIRM_DETAIL.get(exc.reason, CONFIRM_DETAIL["closed"]))
