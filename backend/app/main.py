from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import audit, auth, calendar, chat, kb, memory, messages, sessions
from app.core.bootstrap import ensure_bootstrap_users
from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.health import readiness_checks
from app.core.request_id import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    configure_request_id_logging,
)
from app.core.secrets_check import check_insecure_secrets
from app.storage.factory import get_blob_store

configure_request_id_logging()

app = FastAPI(
    title=settings.app_name,
    openapi_tags=[
        {"name": "auth", "description": "登录与账号"},
        {"name": "chat", "description": "SSE 对话"},
        {"name": "sessions", "description": "会话"},
        {"name": "kb", "description": "知识库"},
        {"name": "calendar", "description": "日程"},
        {"name": "memory", "description": "长期记忆"},
        {"name": "audit", "description": "工具调用审计"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", REQUEST_ID_HEADER],
    expose_headers=[REQUEST_ID_HEADER],
)
app.add_middleware(RequestIdMiddleware)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(messages.router)
app.include_router(sessions.router)
app.include_router(kb.router)
app.include_router(calendar.router)
app.include_router(memory.router)
app.include_router(audit.router)


@app.on_event("startup")
def on_startup():
    check_insecure_secrets()
    init_db()
    get_blob_store()  # verify/create the MinIO/S3 upload bucket
    db = SessionLocal()
    try:
        ensure_bootstrap_users(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/ready")
def health_ready():
    checks, ok = readiness_checks()
    body = {"status": "ok" if ok else "not_ready", "app": settings.app_name, "checks": checks}
    if not ok:
        return JSONResponse(body, status_code=503)
    return body
