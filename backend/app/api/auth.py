"""Login / logout / user management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import (
    create_session_token,
    get_current_admin,
    get_current_user,
    hash_password,
    serialize_user,
    session_cookie_params,
    verify_password,
)
from app.core.db import get_db
from app.models.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


class CreateUserBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    name: str = ""


class ChangePasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(status_code=400, detail="请输入用户名和密码")

    user = db.query(User).filter(User.external_id == username).first()
    if not user or not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    token = create_session_token(user.id)
    response.set_cookie(value=token, **session_cookie_params())
    return serialize_user(user)


@router.post("/logout")
def logout(response: Response):
    params = session_cookie_params()
    response.delete_cookie(key=params["key"], path=params["path"])
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return serialize_user(user)


@router.post("/users")
def create_user(
    body: CreateUserBody,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    username = body.username.strip()
    if db.query(User).filter(User.external_id == username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")

    user = User(
        external_id=username,
        name=body.name.strip() or username,
        password_hash=hash_password(body.password),
        is_active=1,
        is_admin=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return serialize_user(user)


@router.post("/password")
def change_password(
    body: ChangePasswordBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Open WebUI-style: current password + new password."""
    if not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if body.password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    fresh = db.get(User, user.id)
    if fresh is None:
        raise HTTPException(status_code=401, detail="用户不可用")
    fresh.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
