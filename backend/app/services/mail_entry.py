"""Direct delivery. An authenticated caller names the session and the delivery id.

The server does not poll or subscribe. The plan did not lock that choice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_rls_context
from app.models.models import MailAcceptance, Message, ScheduleConfirmClaim
from app.services.calendar import draft_expires_at
from app.services.event_inbox import (
    EventRejected,
    ReceiptView,
    _bind_mail_claim,
    _existing,
    _file_unrouted,
    _require_session,
    note_external_success,
    submit_event,
)
from app.services.schedule_confirm import (
    CONFIRM_DETAIL,
    ScheduleConfirmClosed,
    arm_remote_claim,
    consume_schedule_claim,
    finish_confirm,
    issue_schedule_claim,
    live_task_ids_for_session,
    parse_card_expiry,
    write_fingerprint,
    write_parts,
    write_scope,
)
from app.services.task_run import UnownedSession, insert_card_action


@dataclass(frozen=True)
class CalendarCard:
    title: str = ""
    start_at: str = ""
    end_at: str = ""
    participants: tuple[dict[str, str], ...] = ()
    location: str = ""
    description: str = ""
    meet: bool = False
    event_id: str | None = None
    external_id: str = ""
    response: str = ""

    def as_inbox(self) -> dict:
        return {
            "title": self.title,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "participants": list(self.participants),
            "location": self.location,
            "description": self.description,
            "meet": self.meet,
            "event_id": self.event_id,
            "external_id": self.external_id,
            "response": self.response,
        }


@dataclass(frozen=True)
class InboundMail:
    delivery_id: str
    body: str
    automated: bool = False
    kind: str = ""
    generation: int | None = None
    action: str = "calendar.create"
    card: CalendarCard | None = None


def receive(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    mail: InboundMail,
) -> ReceiptView:
    delivery = str(mail.delivery_id or "").strip()
    if delivery.startswith("out:"):
        raise EventRejected(422, "投递编号无效")
    return submit_event(
        db,
        user_id=user_id,
        source="mail",
        delivery_id=delivery,
        session_id=session_id,
        body=mail.body,
        model_decision="act",
        generation=mail.generation,
        automated=mail.automated,
        kind=mail.kind,
        action=str(mail.action or "calendar.create"),
        card=None if mail.card is None else mail.card.as_inbox(),
    )


class MailClosed(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class OutboundMail:
    mail_to: tuple[str, ...]
    subject: str
    body: str

    def as_claim(self) -> dict:
        return {"mail_to": list(self.mail_to), "subject": self.subject, "body": self.body}


@dataclass(frozen=True)
class SendOffer:
    receipt_id: int
    state: str
    route: str
    token: str
    expires_at: str
    delivery_id: str

    def as_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "state": self.state,
            "route": self.route,
            "token": self.token,
            "expires_at": self.expires_at,
            "delivery_id": self.delivery_id,
            "confirm_token": self.token,
        }


def propose_send(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    draft: OutboundMail,
) -> SendOffer:
    set_rls_context(db, user_id)
    try:
        _require_session(db, user_id, session_id)
    except EventRejected as exc:
        raise MailClosed(exc.status, exc.detail) from exc
    parts = write_parts("mail.send", draft.as_claim())
    fingerprint = write_fingerprint(parts)
    delivery_id = f"out:{fingerprint}"
    claim = _active_mail_claim(db, user_id, fingerprint)
    if claim is None:
        task_ids = live_task_ids_for_session(db, user_id, session_id)
        if not task_ids:
            placeholder = _add_send_message(
                db,
                session_id,
                _send_card(parts, "", draft_expires_at(), "draft"),
            )
            try:
                turn = insert_card_action(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    message_id=placeholder.id,
                )
            except UnownedSession as exc:
                db.rollback()
                raise MailClosed(404, "会话不存在") from exc
            task_ids = [turn.id]
        try:
            token, stored_expiry = issue_schedule_claim(
                db,
                user_id=user_id,
                session_id=session_id,
                action="mail.send",
                raw=parts,
                task_run_ids=task_ids,
                expires_at=parse_card_expiry(draft_expires_at()),
            )
        except ScheduleConfirmClosed as exc:
            db.rollback()
            raise MailClosed(409, CONFIRM_DETAIL.get(exc.reason, CONFIRM_DETAIL["closed"])) from exc
        claim = db.get(ScheduleConfirmClaim, token)
    else:
        token = claim.token
        stored_expiry = claim.expires_at
    phase = "confirmed" if claim is not None and claim.status == "written" else "draft"
    try:
        view = _file_unrouted(
            db,
            user_id=user_id,
            session_id=session_id,
            delivery_id=delivery_id,
            body=str(parts["body"]),
            fingerprint=fingerprint,
        )
        if (view.claim_token or "") != token:
            view = _bind_mail_claim(
                db,
                user_id=user_id,
                delivery_id=delivery_id,
                claim_token=token,
            )
    except EventRejected as exc:
        raise MailClosed(exc.status, exc.detail) from exc
    expires_at = _expiry_text(stored_expiry)
    _ensure_send_message(db, session_id, parts, token, expires_at, phase)
    return SendOffer(
        receipt_id=view.id,
        state=view.state,
        route=view.route,
        token=token,
        expires_at=expires_at,
        delivery_id=delivery_id,
    )


def commit_send(
    db: Session,
    *,
    user_id: int,
    token: str,
    draft: OutboundMail,
) -> ReceiptView:
    set_rls_context(db, user_id)
    if token is None or str(token).strip() == "":
        raise MailClosed(409, CONFIRM_DETAIL["missing"])
    key = str(token).strip()
    parts = write_parts("mail.send", draft.as_claim())
    fingerprint = write_fingerprint(parts)
    delivery_id = f"out:{fingerprint}"
    scope = write_scope(user_id, parts)
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id:
        db.rollback()
        raise MailClosed(409, CONFIRM_DETAIL["closed"])
    if claim.action != "mail.send" or claim.fingerprint != fingerprint or claim.scope != scope:
        db.rollback()
        raise MailClosed(409, CONFIRM_DETAIL["mismatch"])
    receipt = _existing(db, user_id, "mail", delivery_id)
    if receipt is None or (receipt.claim_token or "").strip() != key:
        db.rollback()
        raise MailClosed(409, CONFIRM_DETAIL["missing"])
    remote = (claim.remote_request_id or "").strip()
    if claim.status in {"unknown", "written"}:
        if not remote:
            db.rollback()
            raise MailClosed(409, CONFIRM_DETAIL["unknown"])
        _accept(db, user_id=user_id, delivery_id=delivery_id, remote_request_id=remote, parts=parts)
        finish_confirm(db, key)
        return _seal(db, user_id=user_id, delivery_id=delivery_id, remote_request_id=remote)
    try:
        consume_schedule_claim(db, user_id, key, action="mail.send", raw=parts)
    except ScheduleConfirmClosed as exc:
        raise MailClosed(409, CONFIRM_DETAIL.get(exc.reason, CONFIRM_DETAIL["closed"])) from exc
    remote = str(uuid4())
    arm_remote_claim(db, key, remote)
    _accept(db, user_id=user_id, delivery_id=delivery_id, remote_request_id=remote, parts=parts)
    finish_confirm(db, key)
    return _seal(db, user_id=user_id, delivery_id=delivery_id, remote_request_id=remote)


def _active_mail_claim(db: Session, user_id: int, fingerprint: str) -> ScheduleConfirmClaim | None:
    rows = (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == user_id,
            ScheduleConfirmClaim.action == "mail.send",
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


def _send_card(parts: dict, token: str, expires_at: str, phase: str) -> dict:
    return {
        "mode": "send",
        "phase": phase,
        "confirm_token": token,
        "expires_at": expires_at,
        "mail": {
            "to": [{"email": address, "name": ""} for address in parts["mail_to"]],
            "subject": parts["subject"],
            "body": parts["body"],
        },
    }


def _add_send_message(db: Session, session_id: int, card: dict) -> Message:
    row = Message(
        session_id=session_id,
        role="assistant",
        content="请确认后才会发送。",
        schedule_card=json.dumps(card, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def _ensure_send_message(
    db: Session,
    session_id: int,
    parts: dict,
    token: str,
    expires_at: str,
    phase: str,
) -> None:
    card = _send_card(parts, token, expires_at, phase)
    encoded = json.dumps(card, ensure_ascii=False)
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.role == "assistant")
        .all()
    )
    for row in rows:
        parsed = _load_card(row.schedule_card)
        if parsed.get("mode") != "send" or not _same_mail(parsed.get("mail"), parts):
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
    _add_send_message(db, session_id, card)
    db.commit()


def _load_card(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _same_mail(mail: object, parts: dict) -> bool:
    if not isinstance(mail, dict):
        return False
    people = mail.get("to") or []
    emails = []
    if isinstance(people, list):
        for item in people:
            if isinstance(item, dict):
                emails.append(str(item.get("email") or "").strip())
            else:
                emails.append(str(item or "").strip())
    emails = [item for item in emails if item]
    emails.sort(key=str.casefold)
    return (
        emails == list(parts["mail_to"])
        and str(mail.get("subject") or "") == parts["subject"]
        and str(mail.get("body") or "") == parts["body"]
    )


def _claim_cancelled(db: Session, token: str) -> bool:
    claim = db.get(ScheduleConfirmClaim, token)
    return claim is not None and claim.status == "cancelled"


def _expiry_text(value: datetime | None) -> str:
    if value is None:
        return draft_expires_at()
    stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


def _accept(
    db: Session,
    *,
    user_id: int,
    delivery_id: str,
    remote_request_id: str,
    parts: dict,
) -> None:
    set_rls_context(db, user_id)
    if _same_acceptance(db, user_id, remote_request_id, delivery_id) is not None:
        return
    db.add(
        MailAcceptance(
            user_id=user_id,
            remote_request_id=remote_request_id,
            delivery_id=delivery_id,
            recipients=json.dumps(list(parts["mail_to"]), ensure_ascii=False),
            subject=str(parts["subject"]),
            body=str(parts["body"]),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_rls_context(db, user_id)
        if _same_acceptance(db, user_id, remote_request_id, delivery_id) is None:
            raise


def _same_acceptance(
    db: Session,
    user_id: int,
    remote_request_id: str,
    delivery_id: str,
) -> MailAcceptance | None:
    by_delivery = (
        db.query(MailAcceptance)
        .filter(MailAcceptance.user_id == user_id, MailAcceptance.delivery_id == delivery_id)
        .one_or_none()
    )
    by_remote = (
        db.query(MailAcceptance)
        .filter(
            MailAcceptance.user_id == user_id,
            MailAcceptance.remote_request_id == remote_request_id,
        )
        .one_or_none()
    )
    if by_remote is not None and (by_delivery is None or by_remote.id != by_delivery.id):
        raise MailClosed(409, "这次确认已经记下另一封邮件")
    return by_delivery


def _seal(db: Session, *, user_id: int, delivery_id: str, remote_request_id: str) -> ReceiptView:
    try:
        return note_external_success(
            db,
            user_id=user_id,
            source="mail",
            delivery_id=delivery_id,
            remote_request_id=remote_request_id,
        )
    except EventRejected as exc:
        raise MailClosed(exc.status, exc.detail) from exc
