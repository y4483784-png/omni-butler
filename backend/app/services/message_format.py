"""Serialize Message rows for API responses."""

from __future__ import annotations

import json

from app.models.models import Message


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


def model_turn(m: Message) -> dict[str, str]:
    return {"role": m.role, "content": m.content or ""}
