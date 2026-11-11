"""Direct delivery. An authenticated caller names the session and the delivery id.

The server does not poll or subscribe. The plan did not lock that choice.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.event_inbox import EventRejected, ReceiptView, submit_event


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
