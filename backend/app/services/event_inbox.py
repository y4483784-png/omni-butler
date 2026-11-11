"""Event inbox. Receive a delivery, then route it. Do not perform the write."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_rls_context
from app.models.models import (
    EVENT_RECEIPT_ROUTES,
    EVENT_RECEIPT_STATES,
    EventReceipt,
    Message,
    ScheduleConfirmClaim,
    Session as ChatSession,
    TaskRun,
)
from app.services.calendar import draft_expires_at
from app.services.schedule_confirm import (
    ScheduleConfirmClosed,
    _bind_open_claim,
    _stamp_claim_phase,
    canonical_action,
    parse_card_expiry,
    write_fingerprint,
    write_parts,
    write_scope,
)
from app.services.task_run import UnfinishedWriteKey, UnownedSession, open_alongside

_SOURCES = frozenset({"internal", "mail"})
_NOISE_KINDS = frozenset(
    {"bounce", "unsubscribe", "noreply", "delivery_status", "auto_reply"}
)
_DECISIONS = frozenset({"act", "ignore"})
_ENTER = frozenset({"running", "waiting_resource"})
_CARD_MODE = {
    "calendar.create": "create",
    "calendar.update": "update",
    "calendar.delete": "cancel",
    "calendar.choose": "choose",
}
_MAX_BODY = 8000
_MAX_ID = 200


class EventRejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class ReceiptView:
    id: int
    source: str
    delivery_id: str
    state: str
    route: str
    generation: int | None
    task_run_id: int | None
    claim_token: str | None
    open_claims: int
    created: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "delivery_id": self.delivery_id,
            "state": self.state,
            "route": self.route,
            "generation": self.generation,
            "task_run_id": self.task_run_id,
            "claim_token": self.claim_token,
            "open_claims": self.open_claims,
            "created": self.created,
        }


def submit_event(
    db: Session,
    *,
    user_id: int,
    source: str,
    delivery_id: str,
    session_id: int,
    body: str,
    model_decision: str,
    generation: int | None = None,
    automated: bool = False,
    kind: str = "",
    action: str = "calendar.create",
    card: dict | None = None,
) -> ReceiptView:
    set_rls_context(db, user_id)
    source_name = _source(source)
    delivery = _delivery(delivery_id)
    text = _body(body)
    decision = _decision(model_decision)
    try:
        existing = _existing(db, user_id, source_name, delivery)
        if existing is not None:
            return _view(db, existing, created=False)
        _require_session(db, user_id, session_id)
        fingerprint = _fingerprint(text, action, card)
        if _noise(text, automated, kind):
            saved = _insert(
                db,
                user_id=user_id,
                session_id=session_id,
                source=source_name,
                delivery_id=delivery,
                fingerprint=fingerprint,
                state="received",
                route="noise",
                body=text,
            )
        elif decision == "ignore":
            saved = _insert(
                db,
                user_id=user_id,
                session_id=session_id,
                source=source_name,
                delivery_id=delivery,
                fingerprint=fingerprint,
                state="received",
                route="held",
                body=text,
            )
        else:
            saved = _route_act(
                db,
                user_id=user_id,
                session_id=session_id,
                source=source_name,
                delivery_id=delivery,
                fingerprint=fingerprint,
                body=text,
                generation=generation,
                action=action,
                card=card,
            )
        view = _view(db, saved, created=True)
        db.commit()
        return view
    except EventRejected:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        set_rls_context(db, user_id)
        found = _existing(db, user_id, source_name, delivery)
        if found is None:
            raise
        return _view(db, found, created=False)


def note_external_success(
    db: Session,
    *,
    user_id: int,
    source: str,
    delivery_id: str,
    remote_request_id: str,
) -> ReceiptView:
    """Mark success only when this receipt's claim was written and the remote id matches."""
    set_rls_context(db, user_id)
    marker = str(remote_request_id or "").strip()
    if not marker:
        raise EventRejected(409, "没有对方接受的写入编号")
    source_name = _source(source)
    delivery = _delivery(delivery_id)
    try:
        receipt = _existing(db, user_id, source_name, delivery)
        if receipt is None:
            raise EventRejected(404, "回执不存在")
        if receipt.state == "external_success":
            if receipt.external_request_id == marker:
                return _view(db, receipt, created=False)
            raise EventRejected(409, "回执已经记下另一次外部成功")
        claim = _written_claim(db, user_id, receipt.claim_token, marker)
        if claim is None:
            raise EventRejected(409, "对方还没有接受这次写入")
        receipt.state = "external_success"
        receipt.external_request_id = marker
        db.flush()
        view = _view(db, receipt, created=False)
        db.commit()
        return view
    except EventRejected:
        db.rollback()
        raise


def _route_act(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    source: str,
    delivery_id: str,
    fingerprint: str,
    body: str,
    generation: int | None,
    action: str,
    card: dict | None,
) -> EventReceipt:
    target = _target(db, user_id, session_id, generation)
    if target is None:
        return _open_task(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            body=body,
        )
    if target.status == "stopped":
        return _insert(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            state="received",
            route="stopped",
            body=body,
            generation=target.generation,
            task_run_id=target.id,
        )
    if target.status == "completed":
        return _open_task(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            body=body,
        )
    if target.status == "waiting_person":
        return _while_waiting(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            body=body,
            target=target,
            action=action,
            card=card,
        )
    if target.status in _ENTER:
        return _insert(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            state="received",
            route="passthrough",
            body=body,
            generation=target.generation,
            task_run_id=target.id,
        )
    raise EventRejected(409, "任务状态不能接收事件")


def _while_waiting(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    source: str,
    delivery_id: str,
    fingerprint: str,
    body: str,
    target: TaskRun,
    action: str,
    card: dict | None,
) -> EventReceipt:
    locked = (
        db.query(TaskRun)
        .filter(TaskRun.id == target.id, TaskRun.user_id == user_id)
        .with_for_update()
        .one()
    )
    if locked.status != "waiting_person":
        raise EventRejected(409, "任务已经不在等人确认")
    open_claims = _open_claims(db, user_id, session_id)
    matched = next((row for row in open_claims if row.fingerprint == fingerprint), None)
    if matched is not None:
        return _insert(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            state="received",
            route="kept",
            body=body,
            generation=locked.generation,
            task_run_id=locked.id,
            claim_token=matched.token,
        )
    already = (
        db.query(EventReceipt.id)
        .filter(
            EventReceipt.user_id == user_id,
            EventReceipt.generation == locked.generation,
            EventReceipt.route == "reissued",
        )
        .with_for_update()
        .first()
    )
    if already is not None or card is None:
        return _insert(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            state="received",
            route="queued",
            body=body,
            generation=locked.generation,
            task_run_id=locked.id,
        )
    token = _reissue(db, user_id, session_id, locked, action, card)
    return _insert(
        db,
        user_id=user_id,
        session_id=session_id,
        source=source,
        delivery_id=delivery_id,
        fingerprint=fingerprint,
        state="received",
        route="reissued",
        body=body,
        generation=locked.generation,
        task_run_id=locked.id,
        claim_token=token,
    )


def _reissue(
    db: Session,
    user_id: int,
    session_id: int,
    target: TaskRun,
    action: str,
    card: dict,
) -> str:
    try:
        action_name = canonical_action(action)
    except ScheduleConfirmClosed as exc:
        raise EventRejected(422, "这张卡片的动作不能签发") from exc
    mode = _CARD_MODE.get(action_name)
    if mode is None:
        raise EventRejected(422, "这张卡片的动作不能签发")
    raw = _card_raw(card)
    parts = write_parts(action_name, raw)
    for claim in _open_claims(db, user_id, session_id):
        claim.status = "cancelled"
        _stamp_claim_phase(db, session_id, claim.token, "cancelled")
    db.flush()
    expires_at = draft_expires_at()
    try:
        token, _expiry = _bind_open_claim(
            db,
            key=UnfinishedWriteKey(
                user_id=user_id,
                action=action_name,
                fingerprint=write_fingerprint(parts),
            ),
            scope=write_scope(user_id, parts),
            session_id=session_id,
            task_run_ids=[target.id],
            expires_at=parse_card_expiry(expires_at),
        )
    except ScheduleConfirmClosed as exc:
        raise EventRejected(409, "旧确认不能换成一张新卡") from exc
    stored = {
        "mode": mode,
        "phase": "draft",
        "title": raw["title"],
        "start_at": raw["start_at"],
        "end_at": raw["end_at"],
        "participants": raw["participants"],
        "location": raw["location"],
        "description": raw["description"],
        "meet": raw["meet"],
        "confirm_token": token,
        "expires_at": expires_at,
    }
    db.add(
        Message(
            session_id=session_id,
            role="assistant",
            content="请确认后才会写入。",
            schedule_card=json.dumps(stored, ensure_ascii=False),
        )
    )
    db.flush()
    return token


def _open_task(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    source: str,
    delivery_id: str,
    fingerprint: str,
    body: str,
) -> EventReceipt:
    if source == "mail":
        return _insert(
            db,
            user_id=user_id,
            session_id=session_id,
            source=source,
            delivery_id=delivery_id,
            fingerprint=fingerprint,
            state="received",
            route="filed",
            body=body,
        )
    try:
        turn = open_alongside(db, user_id=user_id, session_id=session_id)
    except UnownedSession as exc:
        raise EventRejected(404, "会话不存在") from exc
    return _insert(
        db,
        user_id=user_id,
        session_id=session_id,
        source=source,
        delivery_id=delivery_id,
        fingerprint=fingerprint,
        state="dispatched",
        route="opened",
        body=body,
        generation=turn.generation,
        task_run_id=turn.id,
    )


def _target(db: Session, user_id: int, session_id: int, generation: int | None) -> TaskRun | None:
    if generation is not None:
        row = (
            db.query(TaskRun)
            .filter(
                TaskRun.user_id == user_id,
                TaskRun.session_id == session_id,
                TaskRun.generation == generation,
            )
            .one_or_none()
        )
        if row is None:
            raise EventRejected(404, "任务不存在")
        return row
    rows = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id, TaskRun.session_id == session_id)
        .order_by(TaskRun.generation.desc())
        .all()
    )
    waiting = next((row for row in rows if row.status == "waiting_person"), None)
    if waiting is not None:
        return waiting
    return next((row for row in rows if row.status in _ENTER), None)


def _insert(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    source: str,
    delivery_id: str,
    fingerprint: str,
    state: str,
    route: str,
    body: str,
    generation: int | None = None,
    task_run_id: int | None = None,
    claim_token: str = "",
) -> EventReceipt:
    if state not in EVENT_RECEIPT_STATES or route not in EVENT_RECEIPT_ROUTES:
        raise EventRejected(500, "回执状态无效")
    if state == "external_success":
        raise EventRejected(409, "收到事件还不是外部成功")
    row = EventReceipt(
        user_id=user_id,
        session_id=session_id,
        source=source,
        delivery_id=delivery_id,
        fingerprint=fingerprint,
        state=state,
        route=route,
        generation=generation,
        task_run_id=task_run_id,
        claim_token=claim_token,
        external_request_id="",
        body=body,
    )
    db.add(row)
    db.flush()
    return row


def _existing(db: Session, user_id: int, source: str, delivery_id: str) -> EventReceipt | None:
    return (
        db.query(EventReceipt)
        .filter(
            EventReceipt.user_id == user_id,
            EventReceipt.source == source,
            EventReceipt.delivery_id == delivery_id,
        )
        .one_or_none()
    )


def _open_claims(db: Session, user_id: int, session_id: int) -> list[ScheduleConfirmClaim]:
    return (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == user_id,
            ScheduleConfirmClaim.session_id == session_id,
            ScheduleConfirmClaim.status == "open",
        )
        .with_for_update()
        .all()
    )


def _open_claim_count(db: Session, user_id: int, session_id: int) -> int:
    return (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == user_id,
            ScheduleConfirmClaim.session_id == session_id,
            ScheduleConfirmClaim.status == "open",
        )
        .count()
    )


def _written_claim(
    db: Session,
    user_id: int,
    token: str,
    remote_request_id: str,
) -> ScheduleConfirmClaim | None:
    key = str(token or "").strip()
    if not key:
        return None
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id or claim.status != "written":
        return None
    if (claim.remote_request_id or "").strip() != remote_request_id:
        return None
    return claim


def _view(db: Session, row: EventReceipt, *, created: bool) -> ReceiptView:
    token = (row.claim_token or "").strip()
    return ReceiptView(
        id=row.id,
        source=row.source,
        delivery_id=row.delivery_id,
        state=row.state,
        route=row.route,
        generation=row.generation,
        task_run_id=row.task_run_id,
        claim_token=token or None,
        open_claims=_open_claim_count(db, row.user_id, row.session_id),
        created=created,
    )


def _require_session(db: Session, user_id: int, session_id: int) -> None:
    owned = (
        db.query(ChatSession.id)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .one_or_none()
    )
    if owned is None:
        raise EventRejected(404, "会话不存在")


def _source(value: str) -> str:
    name = str(value or "").strip().casefold()
    if name not in _SOURCES:
        raise EventRejected(422, "这个来源不接入")
    if len(name) > 64:
        raise EventRejected(422, "来源太长")
    return name


def _delivery(value: str) -> str:
    delivery = str(value or "").strip()
    if not delivery or len(delivery) > _MAX_ID:
        raise EventRejected(422, "投递编号无效")
    return delivery


def _body(value: str) -> str:
    text = str(value or "")
    if len(text) > _MAX_BODY:
        raise EventRejected(422, "事件正文太长")
    return text


def _decision(value: str) -> str:
    decision = str(value or "").strip().casefold()
    if decision not in _DECISIONS:
        raise EventRejected(422, "模型还没有决定要不要动")
    return decision


def _noise(body: str, automated: bool, kind: str) -> bool:
    if automated:
        return True
    if str(kind or "").strip().casefold() in _NOISE_KINDS:
        return True
    return not body.strip()


def _fingerprint(body: str, action: str, card: dict | None) -> str:
    if card is None:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
    try:
        action_name = canonical_action(action)
    except ScheduleConfirmClosed as exc:
        raise EventRejected(422, "这张卡片的动作不能签发") from exc
    return write_fingerprint(write_parts(action_name, _card_raw(card)))


def _card_raw(card: dict) -> dict:
    return {
        "title": card.get("title") or "",
        "start_at": card.get("start_at") or "",
        "end_at": card.get("end_at") or "",
        "participants": card.get("participants") or [],
        "location": card.get("location") or "",
        "description": card.get("description") or "",
        "meet": bool(card.get("meet")),
        "event_id": card.get("event_id"),
        "external_id": card.get("external_id") or "",
        "response": card.get("response") or "",
    }
