from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.harness.desktop_channel import PackageProof, recognize_package
from app.agents.harness.surface import ServerSurface, local_tool_denial
from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.models.models import User
from app.services import desktop_devices
from app.services.local_action import (
    LocalClosed,
    commit_local,
    finish_local,
    parse_local_action,
    propose_local,
)

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

_DENIAL = local_tool_denial(local=True, surface=ServerSurface.WEB) or ""


class DeviceEnroll(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str

    @field_validator("label")
    @classmethod
    def label_has_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("设备名不能为空")
        return value


class DeviceEnrolled(BaseModel):
    id: int
    token: str
    label: str


class DeviceListItem(BaseModel):
    id: int
    label: str
    created_at: datetime


class DeviceRevoked(BaseModel):
    id: int


class LocalRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: int | None = None
    action: str = ""
    device_id: int | None = None
    local_target: str = ""
    local_args: list[str] = Field(default_factory=list)
    confirm_token: str = ""
    request_id: str = ""


def _recognized(header: str | None, user_id: int) -> PackageProof:
    proof = recognize_package(header)
    if not isinstance(proof, PackageProof) or proof.user_id != user_id:
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
    proof = _recognized(_header(request), current_user.id)
    if body.session_id is None:
        raise HTTPException(status_code=422, detail="会话无效")
    try:
        action = parse_local_action(body.action, body.local_target, body.local_args, body.device_id)
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
    proof = _recognized(_header(request), current_user.id)
    try:
        action = parse_local_action(body.action, body.local_target, body.local_args, body.device_id)
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
    proof = _recognized(_header(request), current_user.id)
    try:
        action = parse_local_action(body.action, body.local_target, body.local_args, body.device_id)
        return finish_local(
            db,
            user_id=current_user.id,
            token=body.confirm_token,
            request_id=body.request_id,
            action=action,
            proof=proof,
        )
    except LocalClosed as exc:
        raise _closed(exc) from None


@router.post("/devices", response_model=DeviceEnrolled)
def enroll_device(
    body: DeviceEnroll,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    try:
        device_id, token, label = desktop_devices.enroll(
            db, user_id=current_user.id, label=body.label
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="设备已存在") from None
    return {"id": device_id, "token": token, "label": label}


@router.get("/devices", response_model=list[DeviceListItem])
def list_devices(
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    return desktop_devices.list_devices(db, user_id=current_user.id)


@router.post("/devices/{device_id}/revoke", response_model=DeviceRevoked)
def revoke_device(
    device_id: int,
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    revoked = desktop_devices.revoke(db, user_id=current_user.id, device_id=device_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {"id": revoked}
