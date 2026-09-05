import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.agents.router import RouterError
from app.agents.workflow import run_agent_workflow
from app.agents.context import build_context, invalidate_summary_if_needed, refresh_session_context
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import SessionLocal, set_rls_context
from app.core.deps import get_db_scoped
from app.core.llm import stream_chat
from app.core.moderation import INPUT_BLOCKED_MESSAGE, contains_sensitive, redact_text, redact_token_stream
from app.core.messages import llm_user_error
from app.core.ownership import require_owned_session
from app.core.redis import sliding_window_allow_async
from app.models.models import Document, Message, Session as ChatSession, User
from app.services.artifact import infer_workspace_artifact, prepare_artifact_for_storage
from app.services.session import auto_name, generate_session_title
from app.services.data_analysis import has_tabular_docs
from app.services.memory import load_memory_prompt, remember_from_turn

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatRequest(BaseModel):
    session_id: int
    message: str = ""
    use_kb: bool = False
    document_ids: list[int] = Field(default_factory=list)
    regenerate_message_id: int | None = None


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _persist_assistant_only(
    db: DbSession,
    session: ChatSession,
    session_id: int,
    assistant_content: str,
    *,
    citations_payload: list | None = None,
    schedule_card: dict | None = None,
    artifact_stored: dict | None = None,
    pending_calendar_json: str = "",
) -> None:
    session.pending_calendar = pending_calendar_json
    session.updated_at = datetime.now(timezone.utc)
    _save_assistant_turn(
        db,
        session_id,
        assistant_content,
        citations_payload=citations_payload,
        schedule_card=schedule_card,
        artifact_stored=artifact_stored,
    )


def _apply_regenerate(
    db: DbSession,
    req: ChatRequest,
    *,
    user_id: int,
) -> tuple[ChatSession, list[dict], str] | None:
    """Delete target assistant + later turns; return session, history before user turn, user text."""
    try:
        session = require_owned_session(db, req.session_id, user_id)
    except Exception:
        return None

    target = db.get(Message, req.regenerate_message_id)
    if not target or target.session_id != req.session_id or target.role != "assistant":
        return None

    prior_user = (
        db.query(Message)
        .filter(
            Message.session_id == req.session_id,
            Message.id < target.id,
            Message.role == "user",
        )
        .order_by(Message.id.desc())
        .first()
    )
    if not prior_user:
        return None

    user_content = prior_user.content
    db.query(Message).filter(
        Message.session_id == req.session_id,
        Message.id >= target.id,
    ).delete(synchronize_session=False)
    db.commit()

    remaining = (
        db.query(Message)
        .filter(Message.session_id == req.session_id)
        .order_by(Message.id)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in remaining]
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    max_remaining_id = remaining[-1].id if remaining else 0
    if invalidate_summary_if_needed(session, max_remaining_id):
        db.commit()

    return session, history, user_content


def _prepare_session_context(
    db: DbSession,
    req: ChatRequest,
    *,
    user_id: int,
    load_kb_flags: bool = True,
    user_message: str | None = None,
    history_override: list[dict] | None = None,
) -> tuple[ChatSession | None, list[dict], str | None, int, bool, bool, dict | None]:
    """Load session, history, KB flags. Returns session or None."""
    try:
        session = require_owned_session(db, req.session_id, user_id)
    except Exception:
        return None, [], None, user_id, False, False, None

    msg_text = user_message if user_message is not None else req.message

    if history_override is not None:
        history = history_override
    else:
        history = [
            {"role": m.role, "content": m.content}
            for m in db.query(Message).filter(Message.session_id == req.session_id).order_by(Message.id).all()
        ]

    new_title: str | None = None
    if (
        not req.regenerate_message_id
        and session.title == "新会话"
        and not any(m["role"] == "user" for m in history)
    ):
        new_title = auto_name(session.id, msg_text)
        session.title = new_title
        db.commit()

    uid = session.user_id or user_id
    pending_calendar = json.loads(session.pending_calendar) if session.pending_calendar else None

    has_kb_docs = False
    tabular = False
    if load_kb_flags:
        ready_q = db.query(Document).filter(Document.user_id == uid, Document.status == "ready")
        if req.document_ids:
            ready_q = ready_q.filter(Document.id.in_(req.document_ids))
        has_kb_docs = ready_q.count() > 0
        tabular = has_tabular_docs(db, user_id=uid, document_ids=req.document_ids or None)

    return session, history, new_title, uid, bool(has_kb_docs), bool(tabular), pending_calendar


def _replace_session_title(session_id: int, expected: str, title: str, user_id: int) -> bool:
    """Write title on a fresh DB session so SSE teardown cannot roll it back.

    Returns True if the row still had ``expected`` (fallback / user hasn't renamed).
    """
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = db.get(ChatSession, session_id)
        if row is None or row.title != expected:
            return False
        row.title = title
        db.commit()
        return True
    except Exception:
        logger.exception("replace session title failed")
        db.rollback()
        return False
    finally:
        db.close()


def _commit_user_turn(db: DbSession, session: ChatSession, message: str) -> None:
    db.add(Message(session_id=session.id, role="user", content=message))
    session.updated_at = datetime.now(timezone.utc)
    db.commit()


def _save_assistant_turn(
    db: DbSession,
    session_id: int,
    content: str,
    *,
    citations_payload: list | None = None,
    schedule_card: dict | None = None,
    artifact_stored: dict | None = None,
) -> None:
    if not content:
        return
    db.add(
        Message(
            session_id=session_id,
            role="assistant",
            content=content,
            citations=json.dumps(citations_payload or [], ensure_ascii=False) if citations_payload else "",
            schedule_card=json.dumps(schedule_card, ensure_ascii=False) if schedule_card else "",
            artifact=json.dumps(artifact_stored, ensure_ascii=False) if artifact_stored else "",
        )
    )
    db.commit()


def _persist_turn(
    db: DbSession,
    session: ChatSession,
    session_id: int,
    user_message: str,
    assistant_content: str,
    *,
    citations_payload: list | None = None,
    schedule_card: dict | None = None,
    artifact_stored: dict | None = None,
    pending_calendar_json: str = "",
) -> None:
    session.pending_calendar = pending_calendar_json
    _commit_user_turn(db, session, user_message)
    _save_assistant_turn(
        db,
        session_id,
        assistant_content,
        citations_payload=citations_payload,
        schedule_card=schedule_card,
        artifact_stored=artifact_stored,
    )


def _run_workflow_sync(**kwargs) -> dict:
    db = SessionLocal()
    try:
        uid = int(kwargs.get("user_id") or 0)
        if uid:
            set_rls_context(db, uid)
        return run_agent_workflow(db, **kwargs)
    finally:
        db.close()


async def _run_workflow_with_heartbeat(**kwargs) -> dict:
    """Run workflow in a worker thread with its own DB session."""
    return await asyncio.to_thread(_run_workflow_sync, **kwargs)


def _remember_turn_isolated(user_id: int, user_message: str, assistant_content: str) -> None:
    """Extract memories on a worker thread with a dedicated DB session."""
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        remember_from_turn(
            db,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_content,
        )
    except Exception:
        logger.exception("memory extract failed")
    finally:
        db.close()


def _refresh_context_isolated(
    user_id: int,
    session_id: int,
    *,
    citations: list | None = None,
    document_ids: list[int] | None = None,
    analysis_summary: dict | None = None,
    artifact: dict | None = None,
) -> None:
    """Refresh session summary + working_state on a worker thread."""
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        refresh_session_context(
            db,
            session_id=session_id,
            user_id=user_id,
            citations=citations,
            document_ids=document_ids,
            analysis_summary=analysis_summary,
            artifact=artifact,
        )
    except Exception:
        logger.exception("context refresh failed")
    finally:
        db.close()


@router.post("")
async def chat(
    req: ChatRequest,
    request: Request,
    db: DbSession = Depends(get_db_scoped),
    current_user: User = Depends(get_current_user),
):
    t0 = time.perf_counter()
    is_regenerate = req.regenerate_message_id is not None
    user_message = req.message
    uid = current_user.id

    try:
        rate_session = require_owned_session(db, req.session_id, uid)
    except Exception:
        return StreamingResponse(
            iter([_sse({"type": "error", "content": "会话不存在"})]),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    allowed, _, retry_after = await sliding_window_allow_async(
        f"chat:user:{rate_session.user_id or uid}",
        limit=settings.chat_rate_limit,
        window_seconds=settings.chat_rate_window_seconds,
    )
    if not allowed:
        return StreamingResponse(
            iter([_sse({"type": "error", "content": "请求过于频繁，请稍后再试"})]),
            status_code=429,
            media_type="text/event-stream",
            headers={**SSE_HEADERS, "Retry-After": str(max(1, retry_after))},
        )

    if is_regenerate:
        reg = _apply_regenerate(db, req, user_id=uid)
        if reg is None:
            return StreamingResponse(
                iter([_sse({"type": "error", "content": "无法重新生成：消息不存在或格式无效"})]),
                media_type="text/event-stream",
                headers=SSE_HEADERS,
            )
        session, history, user_message = reg
        ctx = _prepare_session_context(
            db,
            req,
            user_id=uid,
            load_kb_flags=False,
            user_message=user_message,
            history_override=history,
        )
        _, history, new_title, uid, _, _, pending_calendar = ctx
    else:
        ctx = _prepare_session_context(db, req, user_id=uid, load_kb_flags=False)
        session, history, new_title, uid, _, _, pending_calendar = ctx

    if session is None:
        return StreamingResponse(
            iter([_sse({"type": "error", "content": "会话不存在"})]),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    if contains_sensitive(user_message):
        return JSONResponse({"detail": INPUT_BLOCKED_MESSAGE}, status_code=400)

    def _finish_persist(
        assistant_content: str,
        *,
        citations_payload: list | None = None,
        schedule_card: dict | None = None,
        artifact_stored: dict | None = None,
        pending_calendar_json: str = "",
    ) -> None:
        if is_regenerate:
            _persist_assistant_only(
                db,
                session,
                req.session_id,
                assistant_content,
                citations_payload=citations_payload,
                schedule_card=schedule_card,
                artifact_stored=artifact_stored,
                pending_calendar_json=pending_calendar_json,
            )
        else:
            _persist_turn(
                db,
                session,
                req.session_id,
                user_message,
                assistant_content,
                citations_payload=citations_payload,
                schedule_card=schedule_card,
                artifact_stored=artifact_stored,
                pending_calendar_json=pending_calendar_json,
            )

    # Always run agent workflow (no fast-path skip). Reload KB flags.
    if is_regenerate:
        _, history, _, uid, has_kb_docs, tabular, pending_calendar = _prepare_session_context(
            db,
            req,
            user_id=current_user.id,
            load_kb_flags=True,
            user_message=user_message,
            history_override=history,
        )
    else:
        _, history, _, uid, has_kb_docs, tabular, pending_calendar = _prepare_session_context(
            db, req, user_id=current_user.id, load_kb_flags=True
        )

    memory_block = load_memory_prompt(db, uid)
    ctx_bundle = build_context(
        session=session,
        history=history,
        message=user_message,
        pending_calendar=pending_calendar,
        memory_block=memory_block,
    )
    ctx_state = ctx_bundle.to_state()
    ctx_stats = dict(ctx_bundle.stats or {})
    workflow_history = list(ctx_bundle.answer_history)

    async def agent_event_gen():
        acc: list[str] = []
        ttft_logged = False
        wf: dict | None = None
        wf_task: asyncio.Task | None = None
        extra_artifact: dict | None = None
        artifact_stored: dict | None = None
        try:
            yield _sse({"type": "ack", "path": "agent"})
            if new_title:
                yield _sse({"type": "session_title", "content": new_title})
            yield _sse({"type": "status", "phase": "planning"})

            wf_task = asyncio.create_task(
                _run_workflow_with_heartbeat(
                    message=user_message,
                    history=workflow_history,
                    use_kb=req.use_kb,
                    has_kb_docs=has_kb_docs,
                    has_tabular_docs=tabular,
                    document_ids=req.document_ids or None,
                    user_id=uid,
                    pending_calendar=pending_calendar,
                    context=ctx_state,
                )
            )
            while not wf_task.done():
                if await request.is_disconnected():
                    wf_task.cancel()
                    try:
                        await wf_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    return
                done, _ = await asyncio.wait({wf_task}, timeout=2.0)
                if not done:
                    yield ": ping\n\n"
            wf = await wf_task

            intent = wf["intent"]
            forced = wf["forced"]
            citations_payload = wf["citations"]
            thinking_steps = wf.get("thinking_steps") or []
            llm_messages = wf["llm_messages"]
            schedule_card = wf.get("schedule_card")
            artifact = wf.get("artifact")
            artifact_stored = prepare_artifact_for_storage(artifact)
            direct_answer = redact_text(wf.get("direct_answer") or "")
            pending_json = (
                json.dumps(wf.get("pending_calendar"), ensure_ascii=False) if wf.get("pending_calendar") else ""
            )
            session.pending_calendar = pending_json

            yield _sse({"type": "intent", "intent": intent, "forced": forced})
            if thinking_steps:
                yield _sse({"type": "thinking", "steps": thinking_steps})
            if citations_payload:
                yield _sse({"type": "citations", "citations": citations_payload})
            if schedule_card:
                yield _sse({"type": "schedule_card", "card": schedule_card})
            if artifact_stored:
                yield _sse({"type": "artifact", "artifact": artifact_stored})

            if direct_answer:
                if not ttft_logged:
                    ttft_ms = int((time.perf_counter() - t0) * 1000)
                    logger.info(
                        "chat_ttft path=agent_direct ttft_ms=%d intent=%s "
                        "ctx_tokens=%d ctx_level=%s ctx_dropped=%d regenerate=%s",
                        ttft_ms,
                        intent,
                        int(ctx_stats.get("tokens") or 0),
                        ctx_stats.get("level") or "safe",
                        int(ctx_stats.get("dropped_turns") or 0),
                        is_regenerate,
                    )
                    yield _sse({"type": "ttft", "ms": ttft_ms, "path": "agent"})
                    ttft_logged = True
                acc.append(direct_answer)
                yield _sse({"type": "token", "content": direct_answer})
            else:
                async for delta in redact_token_stream(stream_chat(llm_messages)):
                    if not ttft_logged:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                        logger.info(
                            "chat_ttft path=agent_stream ttft_ms=%d intent=%s history_len=%d "
                            "ctx_tokens=%d ctx_level=%s ctx_dropped=%d regenerate=%s",
                            ttft_ms,
                            intent,
                            len(workflow_history),
                            int(ctx_stats.get("tokens") or 0),
                            ctx_stats.get("level") or "safe",
                            int(ctx_stats.get("dropped_turns") or 0),
                            is_regenerate,
                        )
                        yield _sse({"type": "ttft", "ms": ttft_ms, "path": "agent"})
                        ttft_logged = True
                    acc.append(delta)
                    yield _sse({"type": "token", "content": delta})
                    if await request.is_disconnected():
                        break
            if not artifact_stored:
                inferred = infer_workspace_artifact(redact_text("".join(acc)))
                if inferred and inferred.get("kind") != "image":
                    extra_artifact = prepare_artifact_for_storage(inferred)
                    if extra_artifact and extra_artifact.get("kind") != "image":
                        yield _sse({"type": "artifact", "artifact": extra_artifact})
        except asyncio.CancelledError:
            if wf_task is not None and not wf_task.done():
                wf_task.cancel()
                try:
                    await wf_task
                except (asyncio.CancelledError, Exception):
                    pass
            raise
        except RouterError as e:
            err_content = str(e) if str(e).startswith("工具规划失败") else f"工具规划失败：{e}"
            logger.warning("router failed: %s", e)
            if not acc:
                acc.append(err_content)
            yield _sse({"type": "error", "content": err_content})
        except Exception as e:
            err_content = llm_user_error(e)
            if not acc:
                acc.append(err_content)
            yield _sse({"type": "error", "content": err_content})
        finally:
            assistant_content = redact_text("".join(acc))
            persisted = False
            if wf is not None:
                persist_artifact = prepare_artifact_for_storage(wf.get("artifact")) or extra_artifact
                pending_json = (
                    json.dumps(wf.get("pending_calendar"), ensure_ascii=False)
                    if wf.get("pending_calendar")
                    else ""
                )
                set_rls_context(db, uid)
                _finish_persist(
                    assistant_content,
                    citations_payload=wf.get("citations"),
                    schedule_card=wf.get("schedule_card"),
                    pending_calendar_json=pending_json,
                    artifact_stored=persist_artifact,
                )
                persisted = True
            elif acc:
                set_rls_context(db, uid)
                _finish_persist(assistant_content, artifact_stored=extra_artifact)
                persisted = True
            elif not is_regenerate:
                set_rls_context(db, uid)
                _commit_user_turn(db, session, user_message)
            if persisted:
                try:
                    await asyncio.to_thread(
                        _remember_turn_isolated,
                        uid,
                        user_message,
                        assistant_content,
                    )
                except Exception:
                    logger.exception("memory extract thread failed")
                try:
                    await asyncio.to_thread(
                        _refresh_context_isolated,
                        uid,
                        session.id,
                        citations=wf.get("citations") if wf else None,
                        document_ids=req.document_ids or None,
                        analysis_summary=wf.get("analysis_summary") if wf else None,
                        artifact=(
                            prepare_artifact_for_storage(wf.get("artifact"))
                            if wf
                            else extra_artifact
                        ),
                    )
                except Exception:
                    logger.exception("context refresh thread failed")
                if new_title and not is_regenerate:
                    try:
                        llm_title = await asyncio.to_thread(
                            generate_session_title,
                            user_message,
                            assistant_content,
                        )
                        if llm_title and llm_title != new_title:
                            replaced = await asyncio.to_thread(
                                _replace_session_title,
                                session.id,
                                new_title,
                                llm_title,
                                uid,
                            )
                            if replaced:
                                session.title = llm_title
                                yield _sse({"type": "session_title", "content": llm_title})
                    except Exception:
                        logger.exception("session title llm failed")
            yield _sse({"type": "done"})

    return StreamingResponse(agent_event_gen(), media_type="text/event-stream", headers=SSE_HEADERS)
