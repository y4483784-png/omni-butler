import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from app.agents.dispatch import assign_turn
from app.agents.router import RouterError
from app.agents.harness.surface import surface_for_login
from app.agents.workflow import run_agent_workflow
from app.agents.context import build_context, invalidate_summary_if_needed, refresh_session_context
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import SessionLocal, set_rls_context
from app.core.deps import get_db_scoped
from app.core.generation_stop import GenerationStopped, bind, is_stopped, reset
from app.chat_images import ChatImage, ImageRejected, RequestBodyTooLarge, decode_chat_images
from app.core.llm import AnswerDelta, LLMInfraError, ReasoningDelta, Role, stream_chat
from app.core.moderation import INPUT_BLOCKED_MESSAGE, RedactBuffer, contains_sensitive, redact_text
from app.core.messages import llm_user_error
from app.core.ownership import require_owned_session
from app.core.redis import sliding_window_allow_async
from app.models.models import Document, Message, Session as ChatSession, User
from app.services import task_run
from app.services.artifact import infer_workspace_artifact, prepare_artifact_for_storage
from app.services.message_format import model_turn
from app.services.session import auto_name, generate_session_title
from app.services.data_analysis import has_tabular_docs
from app.services.memory import load_memory_prompt, remember_from_turn
from app.services.schedule_confirm import (
    ScheduleConfirmClosed,
    cancel_open_schedule_claims,
    followup_needs_claim,
    phase_for_stored_card,
    publish_followup_claims,
    publish_schedule_claim,
    schedule_card_needs_claim,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_type: str
    data_base64: str


class ChatRequest(BaseModel):
    session_id: int
    message: str = ""
    use_kb: bool = False
    document_ids: list[int] = Field(default_factory=list)
    regenerate_message_id: int | None = None
    images: list[ChatImageIn] = Field(default_factory=list)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _persist_assistant_only(
    db: DbSession,
    session: ChatSession,
    session_id: int,
    assistant_content: str,
    *,
    reasoning: str | None = None,
    citations_payload: list | None = None,
    schedule_card: dict | None = None,
    artifact_stored: dict | None = None,
    pending_calendar_json: str = "",
    job_generation: int | None = None,
) -> None:
    session.pending_calendar = pending_calendar_json
    session.updated_at = datetime.now(timezone.utc)
    _save_assistant_turn(
        db,
        session_id,
        assistant_content,
        reasoning=reasoning,
        citations_payload=citations_payload,
        schedule_card=schedule_card,
        artifact_stored=artifact_stored,
        job_generation=job_generation,
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
    cancel_open_schedule_claims(db, req.session_id)
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
    history = [model_turn(m) for m in remaining]
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
    void_drafts: bool = True,
    skip_message_id: int | None = None,
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
        history = []
        for row in db.query(Message).filter(Message.session_id == req.session_id).order_by(Message.id).all():
            if skip_message_id is not None and row.id == skip_message_id:
                continue
            history.append(model_turn(row))

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
    # Legacy slot-fill state is not a workflow input anymore. Clear it on the next turn.
    if session.pending_calendar:
        session.pending_calendar = ""
        db.commit()
    if void_drafts and history_override is None:
        from app.services.calendar import void_draft_schedule_cards

        void_draft_schedule_cards(db, session.id)
    pending_calendar = None

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


def _commit_user_turn(db: DbSession, session: ChatSession, message: str) -> Message:
    row = Message(session_id=session.id, role="user", content=message)
    db.add(row)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def _card_event_body(schedule_card: dict) -> str:
    token = str(schedule_card.get("confirm_token") or "").strip()
    if token:
        return token
    for action in schedule_card.get("actions") or []:
        if isinstance(action, dict):
            owned = str(action.get("confirm_token") or "").strip()
            if owned:
                return owned
    return "card"


def _save_assistant_turn(
    db: DbSession,
    session_id: int,
    content: str,
    *,
    reasoning: str | None = None,
    citations_payload: list | None = None,
    schedule_card: dict | None = None,
    artifact_stored: dict | None = None,
    job_generation: int | None = None,
) -> None:
    stored_reasoning = reasoning.strip() if isinstance(reasoning, str) else ""
    if not content and not stored_reasoning:
        return
    schedule_card = phase_for_stored_card(db, schedule_card)
    db.add(
        Message(
            session_id=session_id,
            role="assistant",
            content=content or "",
            reasoning=stored_reasoning or None,
            citations=json.dumps(citations_payload or [], ensure_ascii=False) if citations_payload else "",
            schedule_card=json.dumps(schedule_card, ensure_ascii=False) if schedule_card else "",
            artifact=json.dumps(artifact_stored, ensure_ascii=False) if artifact_stored else "",
        )
    )
    owner = db.get(ChatSession, session_id)
    if job_generation is not None and owner is not None and owner.user_id is not None:
        task_run.touch_progress(
            db,
            user_id=int(owner.user_id),
            generation=int(job_generation),
        )
    if schedule_card and owner is not None and owner.user_id is not None:
        from app.services.resume import publish_event

        publish_event(
            db,
            user_id=int(owner.user_id),
            session_id=int(session_id),
            kind="message",
            body=_card_event_body(schedule_card),
        )
    db.commit()


def _persist_turn(
    db: DbSession,
    session: ChatSession,
    session_id: int,
    user_message: str,
    assistant_content: str,
    *,
    reasoning: str | None = None,
    citations_payload: list | None = None,
    schedule_card: dict | None = None,
    artifact_stored: dict | None = None,
    pending_calendar_json: str = "",
    job_generation: int | None = None,
) -> None:
    session.pending_calendar = pending_calendar_json
    _commit_user_turn(db, session, user_message)
    _save_assistant_turn(
        db,
        session_id,
        assistant_content,
        reasoning=reasoning,
        citations_payload=citations_payload,
        schedule_card=schedule_card,
        artifact_stored=artifact_stored,
        job_generation=job_generation,
    )


def _run_workflow_sync(**kwargs) -> dict:
    db = SessionLocal()
    try:
        uid = int(kwargs.get("user_id") or 0)
        if uid:
            set_rls_context(db, uid)
        assignment = assign_turn(
            kwargs["message"],
            kwargs["history"],
            context=kwargs.get("context"),
            use_kb=kwargs.get("use_kb", False),
            has_kb_docs=kwargs.get("has_kb_docs", False),
            has_tabular_docs=kwargs.get("has_tabular_docs", False),
            document_ids=kwargs.get("document_ids"),
        )
        return run_agent_workflow(db, assignment=assignment, **kwargs)
    finally:
        db.close()


def _classify_talk(db: DbSession, *, user_id: int, session_id: int, message: str):
    from app.agents.dispatch import OpenJob, classify_message

    set_rls_context(db, user_id)
    listed = task_run.open_jobs(db, user_id=user_id, session_id=session_id)
    return classify_message(
        message,
        [
            OpenJob(
                generation=row.generation,
                kind=row.kind,
                status=row.status,
                goal=row.goal,
                web_constraint=row.web_constraint,
            )
            for row in listed
        ],
    )


def _bind_job_images(
    db: DbSession,
    *,
    user_id: int,
    task_run_id: int | None,
    images: tuple[ChatImage, ...],
) -> None:
    if not images or task_run_id is None:
        return
    from app.services.task_images import bind_images
    from app.storage.factory import get_blob_store

    bind_images(
        db,
        user_id=int(user_id),
        task_run_id=int(task_run_id),
        images=images,
        store=get_blob_store(),
    )


def _apply_talk(
    db: DbSession,
    *,
    user_id: int,
    session_id: int,
    relation,
    message: str,
    message_id: int | None,
    images: tuple[ChatImage, ...] = (),
):
    from app.agents.dispatch import TalkReply

    if relation.relation == "new":
        from app.services.resume import dispatch_task, enqueue_job

        task_id = enqueue_job(
            db,
            user_id=user_id,
            session_id=session_id,
            goal=message,
            web=relation.web,
            message_id=message_id,
            dispatch=False,
        )
        _bind_job_images(db, user_id=user_id, task_run_id=task_id, images=images)
        dispatch_task(db, task_id)
        return relation, None
    if relation.relation in {"supplement", "redirect"} and relation.target_generation is not None:
        applied = task_run.apply_intent(
            db,
            user_id=user_id,
            generation=relation.target_generation,
            text=message,
            web=relation.web,
            web_constraint_message_id=message_id,
        )
        if not applied.applied:
            return TalkReply(relation="ask", target_generation=None, web="allow", ask=relation.ask or ""), None
        _bind_job_images(db, user_id=user_id, task_run_id=applied.task_run_id, images=images)
        return relation, applied.task_run_id
    if relation.relation == "cancel" and relation.target_generation is not None:
        try:
            task_run.stop(user_id=user_id, generation=relation.target_generation)
        except task_run.MissingTurn:
            return TalkReply(relation="ask", target_generation=None, web="allow", ask=""), None
        return relation, None
    return relation, None


async def _run_workflow_with_heartbeat(**kwargs) -> dict:
    """Run workflow in a worker thread with its own DB session."""
    return await asyncio.to_thread(_run_workflow_sync, **kwargs)


def _remember_turn_isolated(
    user_id: int,
    user_message: str,
    assistant_content: str,
    generation: int | None = None,
    status: str | None = None,
) -> None:
    """Extract memories on a worker thread with a dedicated DB session."""
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        remember_from_turn(
            db,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_content,
            generation=generation,
            status=status,
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

    if is_regenerate and req.images:
        return JSONResponse({"detail": "regenerate"}, status_code=400)

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
        ctx = _prepare_session_context(db, req, user_id=uid, load_kb_flags=False, void_drafts=False)
        session, history, new_title, uid, _, _, pending_calendar = ctx

    if session is None:
        return StreamingResponse(
            iter([_sse({"type": "error", "content": "会话不存在"})]),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    if contains_sensitive(user_message):
        return JSONResponse({"detail": INPUT_BLOCKED_MESSAGE}, status_code=400)

    try:
        images: tuple[ChatImage, ...] = decode_chat_images(
            req.images,
            max_upload_mb=settings.max_upload_mb,
        )
    except ImageRejected as exc:
        return JSONResponse({"detail": exc.code}, status_code=400)

    saved_user_id: int | None = None
    talk = None
    if not is_regenerate:
        talk = _classify_talk(db, user_id=uid, session_id=session.id, message=user_message)
        saved_user_id = _commit_user_turn(db, session, user_message).id

    turn = task_run.begin_turn(user_id=uid, session_id=session.id)
    keep_job_id = None
    if not is_regenerate and talk is not None:
        talk, keep_job_id = _apply_talk(
            db,
            user_id=uid,
            session_id=session.id,
            relation=talk,
            message=user_message,
            message_id=saved_user_id,
            images=images,
        )
        from app.services.calendar import void_draft_schedule_cards

        void_draft_schedule_cards(db, session.id, keep_task_run_id=keep_job_id)
    talk_ask = talk.ask if talk is not None and talk.relation == "ask" else ""

    def _finish_persist(
        assistant_content: str,
        *,
        reasoning: str | None = None,
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
                reasoning=reasoning,
                citations_payload=citations_payload,
                schedule_card=schedule_card,
                artifact_stored=artifact_stored,
                pending_calendar_json=pending_calendar_json,
                job_generation=turn.generation,
            )
        elif saved_user_id is not None:
            session.pending_calendar = pending_calendar_json
            _save_assistant_turn(
                db,
                req.session_id,
                assistant_content,
                reasoning=reasoning,
                citations_payload=citations_payload,
                schedule_card=schedule_card,
                artifact_stored=artifact_stored,
                job_generation=turn.generation,
            )
        else:
            _persist_turn(
                db,
                session,
                req.session_id,
                user_message,
                assistant_content,
                reasoning=reasoning,
                citations_payload=citations_payload,
                schedule_card=schedule_card,
                artifact_stored=artifact_stored,
                pending_calendar_json=pending_calendar_json,
                job_generation=turn.generation,
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
            db,
            req,
            user_id=current_user.id,
            load_kb_flags=True,
            void_drafts=False,
            skip_message_id=saved_user_id,
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
        stop_token = bind(user_id=uid, generation=turn.generation)
        try:
            acc: list[str] = []
            reasoning_parts: list[str] = []
            ttft_logged = False
            wf: dict | None = None
            wf_task: asyncio.Task | None = None
            extra_artifact: dict | None = None
            artifact_stored: dict | None = None
            stream_usage: dict = {}
            rejected_body = False
            try:
                yield _sse({"type": "ack", "path": "agent", "keep_task_run_id": keep_job_id})
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
                        task_id=turn.id,
                        generation=turn.generation,
                        surface=surface_for_login(),
                        images=images,
                        talk_ask=talk_ask,
                    )
                )
                while not wf_task.done():
                    if await request.is_disconnected():
                        task_run.stop(user_id=uid, generation=turn.generation)
                        wf_task.cancel()
                        return
                    done, _ = await asyncio.wait({wf_task}, timeout=2.0)
                    if not done:
                        yield ": ping\n\n"
                wf = await wf_task
                if isinstance(wf.get("context_stats"), dict):
                    for k in ("compose_level", "compose_dropped_turns", "compose_tokens"):
                        if k in wf["context_stats"]:
                            ctx_stats[k] = wf["context_stats"][k]
                intent = wf["intent"]
                forced = wf["forced"]
                citations_payload = wf["citations"]
                thinking_steps = wf.get("thinking_steps") or []
                llm_messages = wf["llm_messages"]
                schedule_card = wf.get("schedule_card")
                if isinstance(schedule_card, dict):
                    schedule_card = {
                        **schedule_card,
                        "task_generation": turn.generation,
                        "task_kind": turn.kind,
                    }
                    wf["schedule_card"] = schedule_card
                artifact = wf.get("artifact")
                artifact_stored = prepare_artifact_for_storage(artifact)
                direct_answer = redact_text(wf.get("direct_answer") or "")
                direct_reasoning = redact_text(wf.get("answer_reasoning") or "")
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
                    needs_person = isinstance(schedule_card, dict) and (
                        schedule_card_needs_claim(schedule_card) or followup_needs_claim(schedule_card)
                    )
                    if needs_person:
                        try:
                            task_run.wait_for_person(user_id=uid, generation=turn.generation)
                        except task_run.LateResult:
                            schedule_card = None
                            wf["schedule_card"] = None
                    if schedule_card and isinstance(schedule_card, dict) and followup_needs_claim(schedule_card):
                        try:
                            schedule_card = publish_followup_claims(
                                uid, session.id, schedule_card, task_run_id=turn.id
                            )
                        except ScheduleConfirmClosed:
                            schedule_card = None
                        wf["schedule_card"] = schedule_card
                    elif schedule_card:
                        try:
                            schedule_card = publish_schedule_claim(
                                uid, session.id, schedule_card, task_run_id=turn.id
                            )
                        except ScheduleConfirmClosed:
                            schedule_card = None
                        wf["schedule_card"] = schedule_card
                    if schedule_card:
                        yield _sse({"type": "schedule_card", "card": schedule_card})
                if artifact_stored:
                    yield _sse({"type": "artifact", "artifact": artifact_stored})
                if is_stopped() or await request.is_disconnected():
                    task_run.stop(user_id=uid, generation=turn.generation)
                elif direct_answer or direct_reasoning:
                    if direct_reasoning:
                        reasoning_parts.append(direct_reasoning)
                        yield _sse({"type": "reasoning", "content": direct_reasoning})
                    if direct_answer:
                        if not ttft_logged:
                            ttft_ms = int((time.perf_counter() - t0) * 1000)
                            logger.info(
                                "chat_ttft path=agent_direct ttft_ms=%d intent=%s "
                                "ctx_tokens=%d ctx_level=%s ctx_dropped=%d "
                                "ctx_compose_level=%s ctx_compose_dropped=%d regenerate=%s",
                                ttft_ms,
                                intent,
                                int(ctx_stats.get("tokens") or 0),
                                ctx_stats.get("level") or "safe",
                                int(ctx_stats.get("dropped_turns") or 0),
                                ctx_stats.get("compose_level") or "-",
                                int(ctx_stats.get("compose_dropped_turns") or 0),
                                is_regenerate,
                            )
                            yield _sse({"type": "ttft", "ms": ttft_ms, "path": "agent"})
                            ttft_logged = True
                        acc.append(direct_answer)
                        yield _sse({"type": "token", "content": direct_answer})
                elif llm_messages:
                    answer_buf = RedactBuffer()
                    reason_buf = RedactBuffer()
                    def _note_ttft() -> str | None:
                        nonlocal ttft_logged
                        if ttft_logged:
                            return None
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                        logger.info(
                            "chat_ttft path=agent_stream ttft_ms=%d intent=%s history_len=%d "
                            "ctx_tokens=%d ctx_level=%s ctx_dropped=%d "
                            "ctx_compose_level=%s ctx_compose_dropped=%d regenerate=%s",
                            ttft_ms,
                            intent,
                            len(workflow_history),
                            int(ctx_stats.get("tokens") or 0),
                            ctx_stats.get("level") or "safe",
                            int(ctx_stats.get("dropped_turns") or 0),
                            ctx_stats.get("compose_level") or "-",
                            int(ctx_stats.get("compose_dropped_turns") or 0),
                            is_regenerate,
                        )
                        ttft_logged = True
                        return _sse({"type": "ttft", "ms": ttft_ms, "path": "agent"})
                    try:
                        leave_stream = False
                        stream = stream_chat(
                            llm_messages,
                            role=Role.CHAT,
                            usage_out=stream_usage,
                            images=images,
                        )
                        try:
                            async for delta in stream:
                                if is_stopped() or await request.is_disconnected():
                                    task_run.stop(user_id=uid, generation=turn.generation)
                                    leave_stream = True
                                    break
                                if isinstance(delta, ReasoningDelta):
                                    for piece in reason_buf.push(delta.text):
                                        if is_stopped() or await request.is_disconnected():
                                            task_run.stop(user_id=uid, generation=turn.generation)
                                            leave_stream = True
                                            break
                                        reasoning_parts.append(piece)
                                        yield _sse({"type": "reasoning", "content": piece})
                                elif isinstance(delta, AnswerDelta):
                                    for piece in answer_buf.push(delta.text):
                                        if is_stopped() or await request.is_disconnected():
                                            task_run.stop(user_id=uid, generation=turn.generation)
                                            leave_stream = True
                                            break
                                        marker = _note_ttft()
                                        if marker:
                                            yield marker
                                        acc.append(piece)
                                        yield _sse({"type": "token", "content": piece})
                                if leave_stream:
                                    break
                            if is_stopped():
                                leave_stream = True
                            if not leave_stream:
                                for piece in reason_buf.finish():
                                    reasoning_parts.append(piece)
                                    yield _sse({"type": "reasoning", "content": piece})
                                for piece in answer_buf.finish():
                                    marker = _note_ttft()
                                    if marker:
                                        yield marker
                                    acc.append(piece)
                                    yield _sse({"type": "token", "content": piece})
                        finally:
                            await stream.aclose()
                    except LLMInfraError as exc:
                        if is_stopped() or await request.is_disconnected():
                            task_run.stop(user_id=uid, generation=turn.generation)
                        else:
                            for piece in reason_buf.finish():
                                reasoning_parts.append(piece)
                                yield _sse({"type": "reasoning", "content": piece})
                            err_content = llm_user_error(exc)
                            acc.clear()
                            acc.append(err_content)
                            yield _sse({"type": "error", "content": err_content})
                if not artifact_stored:
                    inferred = infer_workspace_artifact(redact_text("".join(acc)))
                    if inferred and inferred.get("kind") != "image":
                        extra_artifact = prepare_artifact_for_storage(inferred)
                        if extra_artifact and extra_artifact.get("kind") != "image":
                            yield _sse({"type": "artifact", "artifact": extra_artifact})
            except GenerationStopped:
                pass
            except RequestBodyTooLarge:
                rejected_body = True
                yield _sse({"type": "error", "content": "body_limit"})
            except asyncio.CancelledError:
                task_run.stop(user_id=uid, generation=turn.generation)
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
                if rejected_body:
                    try:
                        task_run.stop(user_id=uid, generation=turn.generation)
                    except task_run.LateResult:
                        pass
                    if saved_user_id is not None:
                        set_rls_context(db, uid)
                        saved = db.get(Message, saved_user_id)
                        if saved is not None:
                            db.delete(saved)
                            db.commit()
                    yield _sse({"type": "done"})
                else:
                    assistant_content = redact_text("".join(acc))
                    assistant_reasoning = "".join(reasoning_parts)
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
                            reasoning=assistant_reasoning,
                            citations_payload=wf.get("citations"),
                            schedule_card=wf.get("schedule_card"),
                            pending_calendar_json=pending_json,
                            artifact_stored=persist_artifact,
                        )
                        persisted = True
                    elif acc:
                        set_rls_context(db, uid)
                        _finish_persist(
                            assistant_content,
                            reasoning=assistant_reasoning,
                            artifact_stored=extra_artifact,
                        )
                        persisted = True
                    elif not is_regenerate and saved_user_id is None:
                        set_rls_context(db, uid)
                        _commit_user_turn(db, session, user_message)
                    if persisted and not is_stopped():
                        try:
                            finished = task_run.finish(user_id=uid, generation=turn.generation)
                        except task_run.LateResult as exc:
                            logger.warning(
                                "late task finish user_id=%s generation=%s status=%s",
                                uid,
                                turn.generation,
                                exc.turn.status,
                            )
                            finished = exc.turn
                        if finished.status == "completed":
                            try:
                                await asyncio.to_thread(
                                    _remember_turn_isolated,
                                    uid,
                                    user_message,
                                    assistant_content,
                                    finished.generation,
                                    finished.status,
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
                        try:
                            est = int(ctx_stats.get("tokens") or 0)
                            actual = stream_usage.get("prompt_tokens")
                            err_pct = None
                            if actual is not None and int(actual) > 0:
                                err_pct = round(abs(est - int(actual)) / float(actual) * 100.0, 2)
                            logger.info(
                                "chat_ctx session_id=%s ctx_est_tokens=%d ctx_actual_tokens=%s "
                                "ctx_err_pct=%s ctx_level=%s ctx_compose_level=%s "
                                "summary_status=%s",
                                session.id,
                                est,
                                actual if actual is not None else "-",
                                err_pct if err_pct is not None else "-",
                                ctx_stats.get("level") or "safe",
                                ctx_stats.get("compose_level") or "-",
                                "deferred",
                            )
                        except Exception:
                            logger.exception("chat_ctx log failed")
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
        finally:
            reset(stop_token)

    return StreamingResponse(agent_event_gen(), media_type="text/event-stream", headers=SSE_HEADERS)


class AcknowledgeBody(BaseModel):
    generation: int
    call_id: str
    control: str = ""


@router.post("/acknowledge")
def acknowledge_unknown(
    body: AcknowledgeBody,
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db_scoped),
):
    from app.services.call_owner import acknowledge
    from app.services.call_policy import ACK_CONTROL

    set_rls_context(db, int(user.id))
    accepted = acknowledge(
        db,
        user_id=int(user.id),
        generation=int(body.generation),
        call_id=body.call_id,
        submitted_control=body.control == ACK_CONTROL,
    )
    return {"accepted": bool(accepted)}
