from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.harness.desktop_channel import PackageProof
from app.agents.harness.surface import ServerSurface, local_tool_denial
from app.core.db import set_rls_context
from app.models.models import Message, ScheduleConfirmClaim, Session as ChatSession
from app.services.calendar import draft_expires_at
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

LOCAL_ACTIONS = ("desktop.open", "desktop.run")

_DENIAL = local_tool_denial(local=True, surface=ServerSurface.WEB) or ""
_SENTENCE = {
    "desktop.open": "请确认后才会打开。",
    "desktop.run": "请确认后才会运行。",
}


class LocalClosed(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class LocalAction:
    action: str
    local_target: str
    local_args: tuple[str, ...]

    def as_claim(self) -> dict:
        return {"local_target": self.local_target, "local_args": list(self.local_args)}


@dataclass(frozen=True)
class LocalOffer:
    token: str
    action: str
    local_target: str
    local_args: tuple[str, ...]
    expires_at: str

    def as_dict(self) -> dict:
        return {
            "confirm_token": self.token,
            "action": self.action,
            "local_target": self.local_target,
            "local_args": list(self.local_args),
            "expires_at": self.expires_at,
        }


def parse_local_action(action: object, local_target: object, local_args: object) -> LocalAction:
    name = str(action or "").strip()
    if name not in LOCAL_ACTIONS:
        raise LocalClosed(422, "本地动作无效")
    target = str(local_target or "").strip()
    if not target:
        raise LocalClosed(422, "缺少本地目标")
    args = _args(local_args)
    if name == "desktop.open":
        if args:
            raise LocalClosed(422, "打开文件不接受参数")
        return LocalAction(name, target, ())
    return LocalAction(name, target, args)


def propose_local(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    action: LocalAction,
    proof: PackageProof,
) -> LocalOffer:
    if not isinstance(proof, PackageProof):
        raise LocalClosed(403, _DENIAL)
    set_rls_context(db, user_id)
    _require_session(db, user_id, session_id)
    parts = write_parts(action.action, action.as_claim())
    task_ids = live_task_ids_for_session(db, user_id, session_id)
    if not task_ids:
        placeholder = _add_message(
            db,
            session_id,
            action,
            "",
            draft_expires_at(),
            "draft",
            proof.audit_word(),
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
            raise LocalClosed(404, "会话不存在") from exc
        task_ids = [turn.id]
    try:
        token, stored_expiry = issue_schedule_claim(
            db,
            user_id=user_id,
            session_id=session_id,
            action=action.action,
            raw=parts,
            task_run_ids=task_ids,
            expires_at=parse_card_expiry(draft_expires_at()),
        )
    except ScheduleConfirmClosed as exc:
        db.rollback()
        raise LocalClosed(409, CONFIRM_DETAIL.get(exc.reason, CONFIRM_DETAIL["closed"])) from exc
    claim = db.get(ScheduleConfirmClaim, token)
    phase = "draft"
    if claim is not None and claim.status == "written":
        phase = "confirmed"
    elif claim is not None and claim.status == "unknown":
        phase = "unknown"
    expires_at = _expiry_text(stored_expiry)
    _ensure_message(db, session_id, action, token, expires_at, phase, proof.audit_word())
    return LocalOffer(
        token=token,
        action=action.action,
        local_target=action.local_target,
        local_args=action.local_args,
        expires_at=expires_at,
    )


def commit_local(
    db: Session,
    *,
    user_id: int,
    token: str | None,
    action: LocalAction,
    proof: PackageProof,
) -> dict:
    if not isinstance(proof, PackageProof):
        raise LocalClosed(403, _DENIAL)
    set_rls_context(db, user_id)
    if token is None or str(token).strip() == "":
        raise LocalClosed(409, CONFIRM_DETAIL["missing"])
    key = str(token).strip()
    parts = write_parts(action.action, action.as_claim())
    fingerprint = write_fingerprint(parts)
    scope = write_scope(user_id, parts)
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is not None and claim.user_id == user_id and claim.status in {"written", "unknown"}:
        if claim.action != action.action or claim.fingerprint != fingerprint or claim.scope != scope:
            db.rollback()
            raise LocalClosed(409, CONFIRM_DETAIL["mismatch"])
        kind = "finished" if claim.status == "written" else "unknown"
        request_id = (claim.remote_request_id or "").strip()
        db.rollback()
        return _license(kind, request_id, action)
    try:
        consume_schedule_claim(db, user_id, key, action=action.action, raw=parts)
    except ScheduleConfirmClosed as exc:
        raise LocalClosed(409, CONFIRM_DETAIL.get(exc.reason, CONFIRM_DETAIL["closed"])) from exc
    request_id = str(uuid4())
    arm_remote_claim(db, key, request_id)
    set_rls_context(db, user_id)
    stored = db.get(ScheduleConfirmClaim, key)
    armed = "" if stored is None else (stored.remote_request_id or "").strip()
    if armed != request_id:
        raise LocalClosed(409, CONFIRM_DETAIL["unknown"])
    return _license("armed", armed, action)


def finish_local(
    db: Session,
    *,
    user_id: int,
    token: str | None,
    request_id: str | None,
    proof: PackageProof,
) -> dict:
    if not isinstance(proof, PackageProof):
        raise LocalClosed(403, _DENIAL)
    set_rls_context(db, user_id)
    if token is None or str(token).strip() == "":
        raise LocalClosed(409, CONFIRM_DETAIL["missing"])
    key = str(token).strip()
    marker = str(request_id or "").strip()
    claim = (
        db.query(ScheduleConfirmClaim)
        .filter(ScheduleConfirmClaim.token == key)
        .with_for_update()
        .one_or_none()
    )
    if claim is None or claim.user_id != user_id:
        db.rollback()
        raise LocalClosed(409, CONFIRM_DETAIL["closed"])
    # A calendar or mail claim must not be closed by this finish.
    if claim.action not in LOCAL_ACTIONS:
        db.rollback()
        raise LocalClosed(409, CONFIRM_DETAIL["mismatch"])
    if not marker or (claim.remote_request_id or "").strip() != marker:
        db.rollback()
        raise LocalClosed(409, CONFIRM_DETAIL["mismatch"])
    finish_confirm(db, key)
    return {"kind": "finished", "request_id": marker}


def _args(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise LocalClosed(422, "参数必须是列表")
    return tuple(str(item).strip() for item in value)


def _license(kind: str, request_id: str, action: LocalAction) -> dict:
    return {
        "kind": kind,
        "request_id": request_id,
        "action": action.action,
        "local_target": action.local_target,
        "local_args": list(action.local_args),
    }


def _require_session(db: Session, user_id: int, session_id: int) -> None:
    owned = (
        db.query(ChatSession.id)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .one_or_none()
    )
    if owned is None:
        raise LocalClosed(404, "会话不存在")


def _card(action: LocalAction, token: str, expires_at: str, phase: str, surface: str) -> dict:
    return {
        "mode": "desktop",
        "phase": phase,
        "confirm_token": token,
        "expires_at": expires_at,
        "action": action.action,
        "local_target": action.local_target,
        "local_args": list(action.local_args),
        "surface": surface,
    }


def _add_message(
    db: Session,
    session_id: int,
    action: LocalAction,
    token: str,
    expires_at: str,
    phase: str,
    surface: str,
) -> Message:
    row = Message(
        session_id=session_id,
        role="assistant",
        content=_SENTENCE[action.action],
        schedule_card=json.dumps(_card(action, token, expires_at, phase, surface), ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def _ensure_message(
    db: Session,
    session_id: int,
    action: LocalAction,
    token: str,
    expires_at: str,
    phase: str,
    surface: str,
) -> None:
    card = _card(action, token, expires_at, phase, surface)
    encoded = json.dumps(card, ensure_ascii=False)
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.role == "assistant")
        .all()
    )
    for row in rows:
        parsed = _load_card(row.schedule_card)
        if not _same_action(parsed, action):
            continue
        current = str(parsed.get("confirm_token") or "")
        if current == token:
            if parsed.get("phase") != phase or parsed.get("surface") != surface:
                parsed["phase"] = phase
                parsed["expires_at"] = expires_at
                parsed["surface"] = surface
                row.schedule_card = json.dumps(parsed, ensure_ascii=False)
                db.commit()
            return
        if not current or _claim_cancelled(db, current):
            row.schedule_card = encoded
            db.commit()
            return
    _add_message(db, session_id, action, token, expires_at, phase, surface)
    db.commit()


def _load_card(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _same_action(card: dict, action: LocalAction) -> bool:
    if card.get("mode") != "desktop":
        return False
    stored = card.get("local_args") or []
    if not isinstance(stored, list):
        return False
    return (
        card.get("action") == action.action
        and card.get("local_target") == action.local_target
        and stored == list(action.local_args)
    )


def _claim_cancelled(db: Session, token: str) -> bool:
    claim = db.get(ScheduleConfirmClaim, token)
    return claim is not None and claim.status == "cancelled"


def _expiry_text(value: datetime | None) -> str:
    if value is None:
        return draft_expires_at()
    stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return stamp.isoformat()
