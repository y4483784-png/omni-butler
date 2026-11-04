from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, set_rls_context
from app.models.models import Message, ScheduleConfirmClaim
from app.services.calendar import draft_expires_at

_CLAIM_MODES = frozenset({"create", "update", "cancel", "choose"})
_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)


class ScheduleConfirmClosed(Exception):
    pass


def schedule_card_needs_claim(card: dict) -> bool:
    phase = str(card.get("phase") or "")
    mode = str(card.get("mode") or "")
    return phase in {"", "draft"} and mode in _CLAIM_MODES


def parse_card_expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    stamp = value.strip()
    if not stamp:
        return None
    if _OFFSET.search(stamp) is None:
        stamp = f"{stamp}Z"
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def open_schedule_claim(db: Session, user_id: int, session_id: int, expires_at: datetime | None) -> str:
    token = str(uuid4())
    db.add(
        ScheduleConfirmClaim(
            token=token,
            user_id=user_id,
            session_id=session_id,
            status="open",
            expires_at=expires_at,
        )
    )
    db.commit()
    return token


def publish_schedule_claim(user_id: int, session_id: int, card: dict) -> dict:
    if not isinstance(card, dict) or not schedule_card_needs_claim(card):
        return card
    expires = parse_card_expiry(card.get("expires_at")) or parse_card_expiry(draft_expires_at())
    isolated = SessionLocal()
    try:
        set_rls_context(isolated, user_id)
        token = open_schedule_claim(isolated, user_id, session_id, expires)
    finally:
        isolated.close()
    return {**card, "confirm_token": token}


def _stamp_claim_phase(db: Session, session_id: int, token: str, phase: str) -> None:
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.role == "assistant")
        .all()
    )
    for row in rows:
        raw = row.schedule_card or ""
        if not raw:
            continue
        try:
            card = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(card, dict) or card.get("confirm_token") != token:
            continue
        if card.get("phase") == phase:
            continue
        card["phase"] = phase
        row.schedule_card = json.dumps(card, ensure_ascii=False)


def consume_schedule_claim(db: Session, user_id: int, token: str | None) -> None:
    if token is None or str(token).strip() == "":
        return
    key = str(token).strip()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    result = db.execute(
        update(ScheduleConfirmClaim)
        .where(ScheduleConfirmClaim.token == key)
        .where(ScheduleConfirmClaim.user_id == user_id)
        .where(ScheduleConfirmClaim.status == "open")
        .where(or_(ScheduleConfirmClaim.expires_at.is_(None), ScheduleConfirmClaim.expires_at > now))
        .values(status="written")
    )
    if result.rowcount != 1:
        db.rollback()
        raise ScheduleConfirmClosed()
    claim = db.get(ScheduleConfirmClaim, key)
    if claim is not None:
        _stamp_claim_phase(db, claim.session_id, claim.token, "confirmed")


def release_schedule_claim(db: Session, user_id: int, token: str) -> None:
    key = str(token).strip()
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id:
        db.rollback()
        raise KeyError(key)
    if claim.status == "cancelled":
        db.rollback()
        return
    if claim.status != "open":
        db.rollback()
        raise ScheduleConfirmClosed()
    claim.status = "cancelled"
    _stamp_claim_phase(db, claim.session_id, claim.token, "cancelled")
    db.commit()


def cancel_open_schedule_claims(db: Session, session_id: int) -> int:
    result = db.execute(
        update(ScheduleConfirmClaim)
        .where(ScheduleConfirmClaim.session_id == session_id)
        .where(ScheduleConfirmClaim.status == "open")
        .values(status="cancelled")
    )
    return int(result.rowcount or 0)


def phase_for_stored_card(db: Session, card: dict | None) -> dict | None:
    if not isinstance(card, dict):
        return card
    token = card.get("confirm_token")
    if not token:
        return card
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == str(token))
        .with_for_update()
        .one_or_none()
    )
    if claim is None:
        return card
    if claim.status == "written" and card.get("phase") != "confirmed":
        return {**card, "phase": "confirmed"}
    if claim.status == "cancelled" and card.get("phase") == "draft":
        return {**card, "phase": "cancelled"}
    return card
