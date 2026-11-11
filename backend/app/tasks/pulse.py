"""Weekly schedule reminder behind the existing confirm claim."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.core.db import SessionLocal, maintenance_session, set_rls_context
from app.models.models import MemoryItem, Message, ScheduleConfirmClaim, Session as ChatSession
from app.services import task_run
from app.services.calendar import draft_expires_at
from app.services.schedule_confirm import (
    ScheduleConfirmClosed,
    publish_schedule_claim,
    schedule_card_needs_claim,
    write_fingerprint,
    write_parts,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_ACTION = "calendar.create"


@dataclass(frozen=True)
class PulseMatter:
    user_id: int
    title: str
    start_at: str
    end_at: str


@dataclass(frozen=True)
class Shown:
    fingerprint: str


@dataclass(frozen=True)
class Suppressed:
    fingerprint: str


def week_monday(now: datetime) -> date:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        day = now.date()
    else:
        day = now.astimezone(timezone.utc).date()
    return day - timedelta(days=day.weekday())


def matter_for_schedule(*, user_id: int, content: str, week: date) -> PulseMatter | None:
    title = (content or "").strip()
    if int(user_id) < 1 or not title:
        return None
    monday = week - timedelta(days=week.weekday())
    friday = monday + timedelta(days=4)
    start = datetime(friday.year, friday.month, friday.day, 13, 0, tzinfo=timezone.utc)
    end = datetime(friday.year, friday.month, friday.day, 14, 0, tzinfo=timezone.utc)
    return PulseMatter(
        user_id=int(user_id),
        title=title,
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )


def _draft_card(matter: PulseMatter) -> dict:
    return {
        "mode": "create",
        "phase": "draft",
        "action": _ACTION,
        "title": matter.title,
        "start_at": matter.start_at,
        "end_at": matter.end_at,
        "participants": [],
        "location": "",
        "description": "",
        "meet": False,
        "expires_at": draft_expires_at(),
    }


def _parse_card(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        card = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(card, dict):
        return None
    return card


def _fingerprint_of(card: dict) -> str | None:
    if str(card.get("mode") or "") != "create":
        return None
    try:
        parts = write_parts(_ACTION, card)
    except ScheduleConfirmClosed:
        return None
    return write_fingerprint(parts)


def _token(message: Message) -> str:
    card = _parse_card(message.schedule_card or "")
    if card is None:
        return ""
    return str(card.get("confirm_token") or "").strip()


def _latest_session(db, user_id: int) -> ChatSession | None:
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .first()
    )


def _matching_messages(db, user_id: int, fingerprint: str) -> list[Message]:
    rows = (
        db.query(Message)
        .join(ChatSession, ChatSession.id == Message.session_id)
        .filter(
            ChatSession.user_id == user_id,
            Message.role == "assistant",
            Message.schedule_card.isnot(None),
            Message.schedule_card != "",
        )
        .order_by(Message.id.asc())
        .all()
    )
    matched: list[Message] = []
    for row in rows:
        card = _parse_card(row.schedule_card or "")
        if card is not None and _fingerprint_of(card) == fingerprint:
            matched.append(row)
    return matched


def _claim(db, user_id: int, fingerprint: str, status: str) -> ScheduleConfirmClaim | None:
    return (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == user_id,
            ScheduleConfirmClaim.action == _ACTION,
            ScheduleConfirmClaim.fingerprint == fingerprint,
            ScheduleConfirmClaim.status == status,
        )
        .one_or_none()
    )


def _insert_message(db, session_id: int, title: str, card: dict) -> Message:
    row = Message(
        session_id=session_id,
        role="assistant",
        content=f"请确认日程「{title}」。未确认不会写入。",
        schedule_card=json.dumps(card, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def _stamp(message: Message, token: str) -> None:
    card = _parse_card(message.schedule_card or "") or {}
    card["confirm_token"] = token
    message.schedule_card = json.dumps(card, ensure_ascii=False)


def push_pulse(matter: PulseMatter) -> Shown | Suppressed:
    user_id = int(matter.user_id)
    card = _draft_card(matter)
    if user_id < 1 or not matter.title.strip() or not schedule_card_needs_claim(card):
        return Suppressed("")
    fingerprint = _fingerprint_of(card) or ""
    if not fingerprint:
        return Suppressed("")
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        session = _latest_session(db, user_id)
        if session is None:
            return Suppressed(fingerprint)
        if _claim(db, user_id, fingerprint, "unknown") is not None:
            return Suppressed(fingerprint)
        messages = _matching_messages(db, user_id, fingerprint)
        open_claim = _claim(db, user_id, fingerprint, "open")
        if open_claim is not None:
            # A reused claim still comes back as a card, so the second push stops here.
            if any(_token(row) == open_claim.token for row in messages):
                return Suppressed(fingerprint)
            target = messages[0] if messages else _insert_message(db, session.id, matter.title, card)
            _stamp(target, open_claim.token)
            db.commit()
            return Shown(fingerprint)
        if any(_token(row) for row in messages):
            return Suppressed(fingerprint)
        target = next((row for row in messages if not _token(row)), None)
        if target is None:
            target = _insert_message(db, session.id, matter.title, card)
        turn = task_run.insert_card_action(
            db,
            user_id=user_id,
            session_id=session.id,
            message_id=target.id,
        )
        db.commit()
        try:
            published = publish_schedule_claim(
                user_id,
                session.id,
                card,
                task_run_id=turn.id,
            )
        except ScheduleConfirmClosed:
            return Suppressed(fingerprint)
        token = str((published or {}).get("confirm_token") or "").strip()
        if not token:
            return Suppressed(fingerprint)
        _stamp(target, token)
        db.commit()
        return Shown(fingerprint)
    finally:
        db.close()


@celery_app.task(name="app.tasks.pulse.run_pulse", queue="maintenance")
def run_pulse() -> None:
    owner = maintenance_session()
    try:
        user_ids = [
            row[0]
            for row in owner.query(MemoryItem.user_id)
            .filter(MemoryItem.kind == "preference", MemoryItem.key == "schedule")
            .distinct()
            .all()
        ]
    finally:
        owner.close()
    week = week_monday(datetime.now(timezone.utc))
    for user_id in user_ids:
        db = SessionLocal()
        try:
            set_rls_context(db, int(user_id))
            row = (
                db.query(MemoryItem)
                .filter(
                    MemoryItem.user_id == int(user_id),
                    MemoryItem.kind == "preference",
                    MemoryItem.key == "schedule",
                )
                .one_or_none()
            )
            content = "" if row is None else (row.content or "")
        finally:
            db.close()
        matter = matter_for_schedule(user_id=int(user_id), content=content, week=week)
        if matter is None:
            continue
        try:
            push_pulse(matter)
        except Exception:
            logger.exception("pulse skipped user_id=%s", user_id)
