from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.harness.desktop_channel import PackageProof, recognize_package
from app.agents.harness.surface import ServerSurface, local_tool_denial
from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.models.models import User
from app.services.local_action import (
    LocalClosed,
    commit_local,
    finish_local,
    parse_local_action,
    propose_local,
)

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

_DENIAL = local_tool_denial(local=True, surface=ServerSurface.WEB) or ""


class LocalRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: int | None = None
    action: str = ""
    local_target: str = ""
    local_args: list[str] = Field(default_factory=list)
    confirm_token: str = ""
    request_id: str = ""


def _recognized(header: str | None) -> PackageProof:
    proof = recognize_package(header)
    if not isinstance(proof, PackageProof):
        raise HTTPException(status_code=403, detail=_DENIAL)
    return proof


def _header(request: Request) -> str | None:
    return request.headers.get("x-omni-package")


def _closed(exc: LocalClosed) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/propose")
def propose(
    body: LocalRequest,
    request: Request,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    proof = _recognized(_header(request))
    if body.session_id is None:
        raise HTTPException(status_code=422, detail="会话无效")
    try:
        action = parse_local_action(body.action, body.local_target, body.local_args)
        offer = propose_local(
            db,
            user_id=current_user.id,
            session_id=body.session_id,
            action=action,
            proof=proof,
        )
    except LocalClosed as exc:
        raise _closed(exc) from None
    return offer.as_dict()


@router.post("/commit")
def commit(
    body: LocalRequest,
    request: Request,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    proof = _recognized(_header(request))
    try:
        action = parse_local_action(body.action, body.local_target, body.local_args)
        return commit_local(
            db,
            user_id=current_user.id,
            token=body.confirm_token,
            action=action,
            proof=proof,
        )
    except LocalClosed as exc:
        raise _closed(exc) from None


@router.post("/finish")
def finish(
    body: LocalRequest,
    request: Request,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    proof = _recognized(_header(request))
    try:
        return finish_local(
            db,
            user_id=current_user.id,
            token=body.confirm_token,
            request_id=body.request_id,
            proof=proof,
        )
    except LocalClosed as exc:
        raise _closed(exc) from None
