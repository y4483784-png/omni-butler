"""FastAPI dependencies combining auth + scoped DB session."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import SessionLocal, set_rls_context
from app.models.models import User


def get_db_scoped(current_user: User = Depends(get_current_user)) -> Session:
    db = SessionLocal()
    try:
        set_rls_context(db, current_user.id)
        yield db
    finally:
        db.close()
