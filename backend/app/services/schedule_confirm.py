from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import exists, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, set_rls_context
from app.models.models import TASK_RUN_STATUSES, Message, ScheduleConfirmClaim, Session as ChatSession, TaskRun
from app.services import task_run
from app.services.calendar import draft_expires_at
from app.services.calendar_provider import invitee_wire, parse_invitees
from app.services.task_run import UnfinishedWriteKey

_CLAIM_MODES = frozenset({"create", "update", "cancel", "choose"})
_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)
_LIVE = frozenset(status for status in TASK_RUN_STATUSES if status not in {"completed", "stopped"})
_ACTIONS = {
    "create": "calendar.create",
    "calendar.create": "calendar.create",
    "update": "calendar.update",
    "calendar.update": "calendar.update",
    "cancel": "calendar.delete",
    "delete": "calendar.delete",
    "calendar.delete": "calendar.delete",
    "rsvp": "calendar.rsvp",
    "calendar.rsvp": "calendar.rsvp",
    "choose": "calendar.choose",
    "calendar.choose": "calendar.choose",
    "mail.send": "mail.send",
}

CONFIRM_DETAIL = {
    "missing": "缺少确认，未写入。",
    "mismatch": "参数已变，旧确认不能写入。",
    "closed": "这张确认已经失效，未写入。",
    "unknown": "结果未知，未再次写入。请先核对日历。",
    "no-generation": "没有进行中的任务，未签发确认。",
    "source": "这张卡片不能发起这次确认。",
}


class ScheduleConfirmClosed(Exception):
    def __init__(self, reason: str = "closed") -> None:
        self.reason = reason
        super().__init__(reason)


def schedule_card_needs_claim(card: dict) -> bool:
    phase = str(card.get("phase") or "")
    mode = str(card.get("mode") or "")
    return phase in {"", "draft"} and mode in _CLAIM_MODES


def followup_needs_claim(card: dict) -> bool:
    if str(card.get("mode") or "") != "followup":
        return False
    actions = card.get("actions") or []
    return any(isinstance(item, dict) and item.get("start_at") for item in actions)


def canonical_action(value: object) -> str:
    action = _ACTIONS.get(str(value or "").strip())
    if action is None:
        raise ScheduleConfirmClosed("closed")
    return action


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


def write_parts(action: str, raw: dict | None) -> dict:
    body = raw or {}
    if action == "mail.send":
        return {
            "mail_to": _mail_recipients(body.get("mail_to")),
            "subject": _text(body.get("subject")),
            "body": _text(body.get("body")),
        }
    title = _text(body.get("title"))
    start_at = _time(body.get("start_at"))
    end_at = _time(body.get("end_at"))
    participants = _people(body.get("participants"))
    location = _text(body.get("location"))
    description = _text(body.get("description"))
    meet = bool(body.get("meet"))
    event_id = _event_id(body.get("event_id"))
    external_id = _text(body.get("external_id"))
    response = _text(body.get("response"))
    if action == "calendar.rsvp":
        return {"external_id": external_id, "response": response}
    if action == "calendar.delete" and not external_id:
        return {"event_id": event_id}
    if action == "calendar.delete":
        return {
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "participants": participants,
            "external_id": external_id,
        }
    if action == "calendar.create":
        return {
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "participants": participants,
            "location": location,
            "description": description,
            "meet": meet,
        }
    parts = {
        "title": title,
        "start_at": start_at,
        "end_at": end_at,
        "participants": participants,
        "location": location,
        "description": description,
        "meet": meet,
    }
    if external_id:
        parts["external_id"] = external_id
    if event_id:
        parts["event_id"] = event_id
    return parts


def write_fingerprint(parts: dict) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_scope(user_id: int, parts: dict) -> str:
    if "mail_to" in parts:
        return f"mail:user:{user_id}"
    external_id = parts.get("external_id") or ""
    event_id = parts.get("event_id") or ""
    if external_id:
        return f"calendar:user:{user_id}:external:{external_id}"
    if event_id:
        return f"calendar:user:{user_id}:event:{event_id}"
    return f"calendar:user:{user_id}"


def followup_create_raw(action: dict) -> dict:
    evidence = action.get("evidence")
    evidence_text = "" if evidence is None else str(evidence)
    return {
        "title": f"跟进：{action.get('text') or ''}",
        "start_at": action.get("start_at") or "",
        "end_at": action.get("end_at") or "",
        "participants": [],
        "location": "",
        "description": f"follow_action: {action.get('id') or ''}\n{evidence_text}",
        "meet": False,
    }


def issue_schedule_claim(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    action: str,
    raw: dict,
    task_run_ids: list[int],
    expires_at: datetime | None,
) -> tuple[str, datetime | None]:
    action = canonical_action(action)
    parts = write_parts(action, raw)
    fingerprint = write_fingerprint(parts)
    scope = write_scope(user_id, parts)
    ids = _owned_live_ids(db, user_id, task_run_ids)
    if not ids:
        raise ScheduleConfirmClosed("no-generation")
    key = UnfinishedWriteKey(user_id=user_id, action=action, fingerprint=fingerprint)
    for _ in range(2):
        try:
            token, expiry = _bind_open_claim(
                db,
                key=key,
                scope=scope,
                session_id=session_id,
                task_run_ids=ids,
                expires_at=expires_at,
            )
            db.commit()
            return token, expiry
        except IntegrityError:
            db.rollback()
    raise ScheduleConfirmClosed("closed")


def continue_from_card(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    message_id: int,
    action: str,
    raw: dict,
) -> tuple[str, datetime | None]:
    action_name = canonical_action(action)
    message = _source_message(db, user_id, session_id, message_id)
    if message is None:
        raise ScheduleConfirmClosed("source")
    card = _stored_card(message)
    if card is None:
        raise ScheduleConfirmClosed("source")
    if str(card.get("mode") or "") not in {"list", "suggest"}:
        return _reissue_linked(db, user_id, session_id, card, action, raw)
    if not _completed_read(db, user_id, card):
        raise ScheduleConfirmClosed("source")
    offered = _selection_offered(card, action_name, raw)
    edited = (
        action_name == "calendar.create"
        and _waiting_card_action(db, user_id, session_id, message.id) is not None
    )
    if not offered and not edited:
        raise ScheduleConfirmClosed("source")
    parts = write_parts(action_name, raw)
    key = UnfinishedWriteKey(
        user_id=user_id,
        action=action_name,
        fingerprint=write_fingerprint(parts),
    )
    scope = write_scope(user_id, parts)
    expires_at = parse_card_expiry(draft_expires_at())
    for _ in range(2):
        try:
            turn = task_run.insert_card_action(
                db,
                user_id=user_id,
                session_id=session_id,
                message_id=message.id,
            )
            token, expiry = _bind_open_claim(
                db,
                key=key,
                scope=scope,
                session_id=session_id,
                task_run_ids=[turn.id],
                expires_at=expires_at,
            )
            db.commit()
            return token, expiry
        except IntegrityError:
            db.rollback()
    raise ScheduleConfirmClosed("closed")


def publish_schedule_claim(
    user_id: int,
    session_id: int,
    card: dict,
    *,
    task_run_id: int | None = None,
) -> dict:
    if not isinstance(card, dict) or not schedule_card_needs_claim(card):
        return card
    if task_run_id is None:
        raise ScheduleConfirmClosed("no-generation")
    expires = parse_card_expiry(card.get("expires_at")) or parse_card_expiry(draft_expires_at())
    isolated = SessionLocal()
    try:
        set_rls_context(isolated, user_id)
        token, _expiry = issue_schedule_claim(
            isolated,
            user_id=user_id,
            session_id=session_id,
            action=_card_action(card),
            raw=_card_raw(card),
            task_run_ids=[task_run_id],
            expires_at=expires,
        )
    finally:
        isolated.close()
    return {**card, "confirm_token": token}


def publish_followup_claims(
    user_id: int,
    session_id: int,
    card: dict,
    *,
    task_run_id: int,
) -> dict:
    actions = []
    expires = parse_card_expiry(card.get("expires_at")) or parse_card_expiry(draft_expires_at())
    isolated = SessionLocal()
    try:
        set_rls_context(isolated, user_id)
        for item in card.get("actions") or []:
            if not isinstance(item, dict) or not item.get("start_at"):
                actions.append(item)
                continue
            token, _expiry = issue_schedule_claim(
                isolated,
                user_id=user_id,
                session_id=session_id,
                action="calendar.create",
                raw=followup_create_raw(item),
                task_run_ids=[task_run_id],
                expires_at=expires,
            )
            actions.append({**item, "confirm_token": token})
    finally:
        isolated.close()
    return {**card, "actions": actions}


def consume_schedule_claim(
    db: Session,
    user_id: int,
    token: str | None,
    *,
    action: str,
    raw: dict,
) -> None:
    if token is None or str(token).strip() == "":
        raise ScheduleConfirmClosed("missing")
    action = canonical_action(action)
    parts = write_parts(action, raw)
    fingerprint = write_fingerprint(parts)
    scope = write_scope(user_id, parts)
    key = str(token).strip()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id:
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    if claim.status == "unknown":
        db.rollback()
        raise ScheduleConfirmClosed("unknown")
    if claim.status != "open" or _expired(claim.expires_at, now):
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    if claim.action != action or claim.fingerprint != fingerprint or claim.scope != scope:
        db.rollback()
        raise ScheduleConfirmClosed("mismatch")
    associated = _load_ids(claim.generation_ids)
    rows = _locked_runs(db, user_id, associated)
    live_ids = [row.id for row in rows if row.status in _LIVE]
    if not live_ids:
        claim.status = "cancelled"
        db.commit()
        raise ScheduleConfirmClosed("closed")
    result = db.execute(
        update(ScheduleConfirmClaim)
        .where(ScheduleConfirmClaim.token == key)
        .where(ScheduleConfirmClaim.user_id == user_id)
        .where(ScheduleConfirmClaim.status == "open")
        .where(ScheduleConfirmClaim.action == action)
        .where(ScheduleConfirmClaim.fingerprint == fingerprint)
        .where(ScheduleConfirmClaim.scope == scope)
        .where(or_(ScheduleConfirmClaim.expires_at.is_(None), ScheduleConfirmClaim.expires_at > now))
        .where(
            exists().where(
                TaskRun.user_id == user_id,
                TaskRun.id.in_(live_ids),
                TaskRun.status.in_(tuple(_LIVE)),
            )
        )
        .values(status="unknown")
    )
    if result.rowcount != 1:
        db.rollback()
        raise ScheduleConfirmClosed("closed")
    claim.status = "unknown"
    db.info["confirm_token"] = key


def arm_remote_claim(db: Session, token: str, request_id: str) -> None:
    """Remember the external key and commit it before the request is sent.

    A later rollback cannot reopen this claim. An empty key means the remote
    call has not been armed, so a failed refresh can still roll the claim back.
    """
    key = str(token or "").strip()
    marker = str(request_id or "").strip()
    if not key or not marker:
        return
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.status not in {"unknown", "open"}:
        return
    if (claim.remote_request_id or "").strip():
        return
    claim.status = "unknown"
    claim.remote_request_id = marker
    _stamp_claim_phase(db, claim.session_id, claim.token, "unknown")
    db.commit()


def reopen_unsent_claim(db: Session, token: str) -> None:
    """Return a claim to open when the remote call was never armed."""
    claim = _claim_for_update(db, token)
    if claim is None or claim.status != "unknown":
        return
    if (claim.remote_request_id or "").strip():
        return
    claim.status = "open"
    _stamp_claim_phase(db, claim.session_id, claim.token, "draft")
    db.commit()


def reopen_known_rejection(db: Session, token: str) -> None:
    """A definite rejection did not create the event. The same claim may try again."""
    claim = _claim_for_update(db, token)
    if claim is None or claim.status != "unknown":
        return
    claim.status = "open"
    claim.remote_request_id = ""
    _stamp_claim_phase(db, claim.session_id, claim.token, "draft")
    db.commit()


def finish_confirm(db: Session, token: str | None) -> None:
    """Mark a successful write. Repeating this leaves the claim written."""
    claim = _claim_for_update(db, token)
    if claim is None or claim.status not in {"unknown", "written"}:
        return
    claim.status = "written"
    _stamp_claim_phase(db, claim.session_id, claim.token, "confirmed")
    db.commit()


def _claim_for_update(db: Session, token: str | None) -> ScheduleConfirmClaim | None:
    key = str(token or "").strip()
    if not key:
        return None
    return (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )


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
    if claim.status == "unknown" and card.get("phase") != "unknown":
        return {**card, "phase": "unknown"}
    if claim.status == "written" and card.get("phase") != "confirmed":
        return {**card, "phase": "confirmed"}
    if claim.status == "cancelled" and card.get("phase") == "draft":
        return {**card, "phase": "cancelled"}
    return card


def live_task_ids_for_session(db: Session, user_id: int, session_id: int) -> list[int]:
    rows = (
        db.query(TaskRun.id)
        .filter(
            TaskRun.user_id == user_id,
            TaskRun.session_id == session_id,
            TaskRun.status.in_(tuple(_LIVE)),
        )
        .all()
    )
    return [row[0] for row in rows]


def _reissue_linked(
    db: Session,
    user_id: int,
    session_id: int,
    card: dict,
    action: str,
    raw: dict,
) -> tuple[str, datetime | None]:
    token = str(card.get("confirm_token") or "").strip()
    if not token:
        raise ScheduleConfirmClosed("source")
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == token)
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id or claim.session_id != session_id or claim.status != "open":
        raise ScheduleConfirmClosed("source")
    ids = _owned_live_ids(db, user_id, _load_ids(claim.generation_ids))
    if not ids:
        raise ScheduleConfirmClosed("source")
    return issue_schedule_claim(
        db,
        user_id=user_id,
        session_id=session_id,
        action=action,
        raw=raw,
        task_run_ids=ids,
        expires_at=parse_card_expiry(draft_expires_at()),
    )


def _source_message(db: Session, user_id: int, session_id: int, message_id: int) -> Message | None:
    return (
        db.query(Message)
        .join(ChatSession, ChatSession.id == Message.session_id)
        .filter(
            Message.id == int(message_id),
            Message.session_id == session_id,
            Message.role == "assistant",
            ChatSession.user_id == user_id,
        )
        .one_or_none()
    )


def _stored_card(message: Message) -> dict | None:
    try:
        card = json.loads(message.schedule_card or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(card, dict):
        return None
    return card


def _completed_read(db: Session, user_id: int, card: dict) -> bool:
    if str(card.get("mode") or "") not in {"list", "suggest"}:
        return False
    expires = parse_card_expiry(card.get("expires_at"))
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    if expires is not None and expires <= now:
        return False
    raw = card.get("task_generation")
    if raw is None or raw == "":
        return True
    try:
        generation = int(raw)
    except (TypeError, ValueError):
        return False
    row = (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id, TaskRun.generation == generation)
        .one_or_none()
    )
    return row is not None and row.status == "completed"


def _waiting_card_action(db: Session, user_id: int, session_id: int, message_id: int) -> TaskRun | None:
    return (
        db.query(TaskRun)
        .filter(
            TaskRun.user_id == user_id,
            TaskRun.session_id == session_id,
            TaskRun.allowed_tools == task_run.card_action_marker(message_id),
            TaskRun.status == "waiting_person",
        )
        .one_or_none()
    )


def _selection_offered(card: dict, action: str, raw: dict) -> bool:
    body = raw or {}
    if action == "calendar.create":
        start_at = _time(body.get("start_at"))
        end_at = _time(body.get("end_at"))
        if not start_at or not end_at:
            return False
        for slot in card.get("suggested_slots") or []:
            if not isinstance(slot, dict):
                continue
            if _time(slot.get("start_at")) == start_at and _time(slot.get("end_at")) == end_at:
                return True
        return False
    if action == "calendar.rsvp":
        external_id = _text(body.get("external_id"))
        response = _text(body.get("response"))
        if not external_id or response not in {"accepted", "declined", "tentative"}:
            return False
        if str(card.get("mode") or "") != "list":
            return False
        for item in card.get("items") or []:
            if isinstance(item, dict) and _text(item.get("external_id")) == external_id:
                return True
        return False
    return False


def _card_action(card: dict) -> str:
    mode = str(card.get("mode") or "")
    if mode == "cancel":
        return "calendar.delete"
    if mode == "update":
        return "calendar.update"
    if mode == "choose":
        return "calendar.choose"
    return "calendar.create"


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


def _bind_open_claim(
    db: Session,
    *,
    key: UnfinishedWriteKey,
    scope: str,
    session_id: int,
    task_run_ids: list[int],
    expires_at: datetime | None,
) -> tuple[str, datetime | None]:
    unknown = (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == key.user_id,
            ScheduleConfirmClaim.action == key.action,
            ScheduleConfirmClaim.fingerprint == key.fingerprint,
            ScheduleConfirmClaim.status == "unknown",
        )
        .with_for_update()
        .one_or_none()
    )
    if unknown is not None:
        raise ScheduleConfirmClosed("unknown")
    existing = (
        db.query(ScheduleConfirmClaim)
        .filter(
            ScheduleConfirmClaim.user_id == key.user_id,
            ScheduleConfirmClaim.action == key.action,
            ScheduleConfirmClaim.fingerprint == key.fingerprint,
            ScheduleConfirmClaim.status == "open",
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None and _still_authorized(db, key.user_id, existing):
        _attach(existing, task_run_ids)
        db.flush()
        return existing.token, existing.expires_at
    if existing is not None:
        existing.status = "cancelled"
        db.flush()
    token = str(uuid4())
    row = ScheduleConfirmClaim(
        token=token,
        user_id=key.user_id,
        session_id=session_id,
        status="open",
        expires_at=expires_at,
        action=key.action,
        fingerprint=key.fingerprint,
        scope=scope,
        generation_ids=_dump_ids(task_run_ids),
    )
    db.add(row)
    db.flush()
    return token, expires_at


def _still_authorized(db: Session, user_id: int, claim: ScheduleConfirmClaim) -> bool:
    rows = _locked_runs(db, user_id, _load_ids(claim.generation_ids))
    return any(row.status in _LIVE for row in rows)


def _owned_live_ids(db: Session, user_id: int, task_run_ids: list[int]) -> list[int]:
    return [row.id for row in _locked_runs(db, user_id, task_run_ids) if row.status in _LIVE]


def _locked_runs(db: Session, user_id: int, task_run_ids: list[int]) -> list[TaskRun]:
    if not task_run_ids:
        return []
    return (
        db.query(TaskRun)
        .filter(TaskRun.user_id == user_id, TaskRun.id.in_(task_run_ids))
        .with_for_update()
        .all()
    )


def _attach(claim: ScheduleConfirmClaim, task_run_ids: list[int]) -> None:
    claim.generation_ids = _dump_ids([*_load_ids(claim.generation_ids), *task_run_ids])


def _load_ids(raw: str | None) -> list[int]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    ids: list[int] = []
    for item in parsed:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _dump_ids(ids: list[int]) -> str:
    return json.dumps(sorted(set(ids)), separators=(",", ":"))


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _time(value: object) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    stamp = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0).isoformat(timespec="seconds")


def _event_id(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value).strip()


def _mail_recipients(value: object) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    found: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = _text(item.get("email"))
        else:
            text = _text(item)
        if text:
            found.append(text)
    found.sort(key=str.casefold)
    return found


def _people(value: object) -> list[dict[str, str]]:
    rows = [invitee_wire(item) for item in parse_invitees(value)]
    rows.sort(key=lambda item: (item["email"].casefold(), item["name"]))
    return rows


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
        if not isinstance(card, dict):
            continue
        changed = False
        if card.get("confirm_token") == token and card.get("phase") != phase:
            card["phase"] = phase
            changed = True
        actions = card.get("actions")
        if isinstance(actions, list):
            for item in actions:
                if (
                    isinstance(item, dict)
                    and item.get("confirm_token") == token
                    and item.get("phase") != phase
                ):
                    item["phase"] = phase
                    changed = True
        if not changed:
            continue
        row.schedule_card = json.dumps(card, ensure_ascii=False)
