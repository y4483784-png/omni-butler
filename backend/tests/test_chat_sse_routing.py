"""SSE routing tests: unified agent path + RouterError surfacing (no fast path)."""

from __future__ import annotations

import importlib.util
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.agents.router import RouterError
from app.core.db import SessionLocal
from app.models.models import Document
from app.models.models import Session as ChatSession

# app startup provisions the MinIO/S3 bucket; without boto3 the TestClient cannot boot.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("boto3") is None,
    reason="requires boto3 for app startup blob store",
)


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for part in body.split("\n\n"):
        for line in part.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def _fake_stream(*_args, **_kwargs):
    for ch in "你好":
        yield ch


def _seed_session_with_kb() -> int:
    db = SessionLocal()
    try:
        sess = ChatSession(user_id=1, title="测试")
        db.add(sess)
        db.add(Document(user_id=1, filename="notes.md", status="ready"))
        db.commit()
        db.refresh(sess)
        return sess.id
    finally:
        db.close()


def test_agent_path_yields_early_progress(auth_client: TestClient):
    sid = _seed_session_with_kb()
    wf = {
        "intent": "rag",
        "forced": False,
        "citations": [],
        "thinking_steps": ["知识库检索"],
        "llm_messages": [{"role": "user", "content": "根据文档总结架构"}],
        "schedule_card": None,
        "artifact": None,
        "direct_answer": "摘要",
        "pending_calendar": None,
    }

    with (
        patch("app.api.chat._run_workflow_sync", return_value=wf),
        patch("app.api.chat.stream_chat", side_effect=_fake_stream),
    ):
        r = auth_client.post(
            "/api/chat",
            json={"session_id": sid, "message": "根据文档总结架构"},
        )

    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "ack"
    assert events[0]["path"] == "agent"
    assert any(e.get("type") == "status" and e.get("phase") == "planning" for e in events)
    intent_evt = next(e for e in events if e["type"] == "intent")
    assert intent_evt["intent"] == "rag"
    assert "token" in types
    assert types[-1] == "done"


def test_plain_chat_still_goes_through_agent_path(auth_client: TestClient):
    """Fast path is removed: greetings also traverse the workflow."""
    sid = _seed_session_with_kb()
    wf = {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [{"role": "user", "content": "你好呀"}],
        "schedule_card": None,
        "artifact": None,
        "direct_answer": None,
        "pending_calendar": None,
    }

    with (
        patch("app.api.chat._run_workflow_sync", return_value=wf),
        patch("app.api.chat.stream_chat", side_effect=_fake_stream),
    ):
        r = auth_client.post("/api/chat", json={"session_id": sid, "message": "你好呀"})

    events = _parse_sse(r.text)
    assert events[0]["type"] == "ack"
    assert events[0]["path"] == "agent"
    assert all(e.get("path") != "fast" for e in events)


def test_router_error_surfaces_as_sse_error(auth_client: TestClient):
    sid = _seed_session_with_kb()

    def boom(**_kwargs):
        raise RouterError("工具规划失败：schema 约束输出失败（2 次）")

    with (
        patch("app.api.chat._run_workflow_sync", side_effect=boom),
        patch("app.api.chat.stream_chat", side_effect=_fake_stream),
    ):
        r = auth_client.post("/api/chat", json={"session_id": sid, "message": "公司几点上班"})

    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert "error" in types
    err = next(e for e in events if e["type"] == "error")
    assert "工具规划失败" in err["content"]
    # No fabricated chat answer
    assert "token" not in types
    assert types[-1] == "done"


def test_chat_rejects_sensitive_input(auth_client: TestClient, monkeypatch):
    from app.core import moderation as mod

    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", True)
    monkeypatch.setattr(mod.settings, "sensitive_use_builtin", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "forbiddenxyz")
    sid = _seed_session_with_kb()
    r = auth_client.post("/api/chat", json={"session_id": sid, "message": "tell me forbiddenxyz"})
    assert r.status_code == 400
    assert "违规" in r.json()["detail"]


def test_chat_redacts_direct_answer(auth_client: TestClient, monkeypatch):
    from app.core import moderation as mod

    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", True)
    monkeypatch.setattr(mod.settings, "sensitive_use_builtin", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "forbiddenxyz")
    sid = _seed_session_with_kb()
    wf = {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [{"role": "user", "content": "hello"}],
        "schedule_card": None,
        "artifact": None,
        "direct_answer": "leak forbiddenxyz here",
        "pending_calendar": None,
    }
    with (
        patch("app.api.chat._run_workflow_sync", return_value=wf),
        patch("app.api.chat.stream_chat", side_effect=_fake_stream),
    ):
        r = auth_client.post("/api/chat", json={"session_id": sid, "message": "hello"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    tokens = "".join(e.get("content") or "" for e in events if e.get("type") == "token")
    assert "forbiddenxyz" not in tokens
    assert "***" in tokens


def test_chat_timeout_uses_prd_copy(auth_client: TestClient):
    sid = _seed_session_with_kb()

    def boom(**_kwargs):
        raise TimeoutError("deadline exceeded")

    with (
        patch("app.api.chat._run_workflow_sync", side_effect=boom),
        patch("app.api.chat.stream_chat", side_effect=_fake_stream),
    ):
        r = auth_client.post("/api/chat", json={"session_id": sid, "message": "你好"})

    events = _parse_sse(r.text)
    err = next(e for e in events if e["type"] == "error")
    assert err["content"] == "当前网络或大模型服务响应超时，请稍后重试。"


def test_first_turn_replaces_title_after_reply(auth_client: TestClient):
    db = SessionLocal()
    try:
        sess = ChatSession(user_id=1, title="新会话")
        db.add(sess)
        db.commit()
        db.refresh(sess)
        sid = sess.id
    finally:
        db.close()

    wf = {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [{"role": "user", "content": "帮我定明早周会"}],
        "schedule_card": None,
        "artifact": None,
        "direct_answer": "已记下",
        "pending_calendar": None,
    }
    with (
        patch("app.api.chat._run_workflow_sync", return_value=wf),
        patch("app.api.chat.stream_chat", side_effect=_fake_stream),
        patch("app.api.chat.generate_session_title", return_value="周会安排"),
    ):
        r = auth_client.post("/api/chat", json={"session_id": sid, "message": "帮我定明早周会"})

    events = _parse_sse(r.text)
    titles = [e["content"] for e in events if e.get("type") == "session_title"]
    assert titles[-1] == "周会安排"
    db = SessionLocal()
    try:
        row = db.get(ChatSession, sid)
        assert row is not None
        assert row.title == "周会安排"
    finally:
        db.close()


if __name__ == "__main__":
    test_agent_path_yields_early_progress()
    test_plain_chat_still_goes_through_agent_path()
    test_router_error_surfaces_as_sse_error()
    print("ok")
