from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db_scoped
from app.models.models import User
from app.services.errand_order import ErrandOrderError
from app.services.errand_vault import VaultError, store_secret
from app.services.errands import ErrandClosed, commit_errand, propose_errand

router = APIRouter(prefix="/api/errand", tags=["errand"])


@router.post("/proposal")
def propose(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    try:
        session_id = int(body["session_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="会话无效") from None
    try:
        offer = propose_errand(db, user_id=current_user.id, session_id=session_id, raw=body)
    except ErrandOrderError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    except ErrandClosed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return offer.as_dict()


@router.post("/commit")
def commit(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    try:
        result = commit_errand(
            db,
            user_id=current_user.id,
            token=body.get("confirm_token"),
            raw=body,
        )
    except ErrandOrderError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    except ErrandClosed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return result


@router.post("/vault")
def vault(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    try:
        store_secret(
            db,
            user_id=current_user.id,
            merchant_key=str(body.get("merchant_key") or ""),
            kind=str(body.get("kind") or ""),
            secret=str(body.get("secret") or ""),
        )
    except VaultError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    return {"stored": True}
