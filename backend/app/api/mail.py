from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.models.models import User
from app.services.mail_entry import MailClosed, OutboundMail, commit_send, propose_send

router = APIRouter(prefix="/api/mail", tags=["mail"])


class MailProposal(BaseModel):
    session_id: int
    to: list[str]
    subject: str = ""
    body: str = ""


class MailSend(BaseModel):
    confirm_token: str = ""
    to: list[str]
    subject: str = ""
    body: str = ""


def _draft(to: list[str], subject: str, body: str) -> OutboundMail:
    addresses = tuple(str(item).strip() for item in to if str(item).strip())
    if not addresses or any("@" not in item for item in addresses):
        raise HTTPException(status_code=422, detail="收件人无效")
    if not subject.strip() or not body.strip():
        raise HTTPException(status_code=422, detail="邮件缺少主题或正文")
    return OutboundMail(mail_to=addresses, subject=subject.strip(), body=body.strip())


@router.post("/proposal")
def propose(
    body: MailProposal,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    draft = _draft(body.to, body.subject, body.body)
    try:
        offer = propose_send(
            db,
            user_id=current_user.id,
            session_id=body.session_id,
            draft=draft,
        )
    except MailClosed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return offer.as_dict()


@router.post("/send")
def send(
    body: MailSend,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    draft = _draft(body.to, body.subject, body.body)
    try:
        view = commit_send(db, user_id=current_user.id, token=body.confirm_token, draft=draft)
    except MailClosed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return view.as_dict()
