"""Serialize Message rows for API responses."""

from __future__ import annotations

import json

from sqlalchemy.orm import object_session

from app.models.models import Message, ScheduleConfirmClaim, TaskRun


def serialize_message(m: Message) -> dict:
    citations: list = []
    schedule_card = None
    artifact = None
    if m.citations:
        try:
            citations = json.loads(m.citations)
        except json.JSONDecodeError:
            citations = []
    if m.schedule_card:
        try:
            schedule_card = json.loads(m.schedule_card)
        except json.JSONDecodeError:
            schedule_card = None
        if isinstance(schedule_card, dict):
            schedule_card = _with_job_kind(m, schedule_card)
    if getattr(m, "artifact", None):
        try:
            artifact = json.loads(m.artifact)
        except json.JSONDecodeError:
            artifact = None
    fb = (getattr(m, "feedback", None) or "").strip()
    reasoning = getattr(m, "reasoning", None)
    if not isinstance(reasoning, str) or not reasoning.strip():
        reasoning = None
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "reasoning": reasoning,
        "citations": citations,
        "scheduleCard": schedule_card,
        "artifact": artifact,
        "feedback": fb if fb in ("up", "down") else None,
    }


def _card_tokens(card: dict) -> list[str]:
    tokens: list[str] = []
    raw = card.get("confirm_token")
    if raw:
        tokens.append(str(raw))
    for item in card.get("actions") or []:
        if isinstance(item, dict) and item.get("confirm_token"):
            tokens.append(str(item["confirm_token"]))
    return tokens


def _linked_ids(claims: list[ScheduleConfirmClaim]) -> list[int]:
    found: list[int] = []
    for claim in claims:
        try:
            parsed = json.loads(claim.generation_ids or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            try:
                found.append(int(item))
            except (TypeError, ValueError):
                continue
    return found


def _with_job_kind(message: Message, card: dict) -> dict:
    if card.get("task_kind") == "job":
        return card
    session = object_session(message)
    tokens = _card_tokens(card)
    if session is None or not tokens:
        return card
    claims = session.query(ScheduleConfirmClaim).filter(ScheduleConfirmClaim.token.in_(tokens)).all()
    ids = _linked_ids(claims)
    if not ids:
        return card
    job = session.query(TaskRun.id).filter(TaskRun.id.in_(ids), TaskRun.kind == "job").first()
    if job is None:
        return card
    return {**card, "task_kind": "job"}


def model_turn(m: Message) -> dict[str, str]:
    return {"role": m.role, "content": m.content or ""}
