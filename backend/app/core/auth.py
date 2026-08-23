"""Session cookie auth and password helpers."""

from __future__ import annotations

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.models import User

_SESSION = URLSafeTimedSerializer(settings.secret_key, salt="omni-session")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session_token(user_id: int) -> str:
    return _SESSION.dumps({"user_id": int(user_id)})


def load_session_token(token: str) -> int:
    data = _SESSION.loads(token, max_age=settings.session_max_age_seconds)
    return int(data["user_id"])


def session_cookie_params() -> dict:
    return {
        "key": settings.session_cookie_name,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.session_cookie_secure,
        "max_age": settings.session_max_age_seconds,
        "path": "/",
    }


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        user_id = load_session_token(token)
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")
    except BadSignature:
        raise HTTPException(status_code=401, detail="会话无效，请重新登录")

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不可用")
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.external_id or "",
        "name": user.name or "",
        "is_admin": bool(user.is_admin),
    }
