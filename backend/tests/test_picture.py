from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from app.chat_images import (
    BODY_CAP_BYTES,
    INLINE_CAP_BYTES,
    ImageRejected,
    RequestBodyTooLarge,
    UnsupportedContent,
    decode_chat_image,
)
from app.core.llm import Role, RoleCall, ToolsRefused, _build_request, resolve, serialized_provider_body

PNG = b"\x89PNG\r\n\x1a\nrest"
JPEG = b"\xff\xd8\xff"
GIF = b"GIF89ahello"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"body"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_signatures_must_match_the_declared_type():
    assert decode_chat_image("image/png", _b64(PNG), max_upload_mb=20).data == PNG
    assert decode_chat_image("image/jpeg", _b64(JPEG), max_upload_mb=20).media_type == "image/jpeg"
    assert decode_chat_image("image/gif", _b64(GIF), max_upload_mb=20).media_type == "image/gif"
    assert decode_chat_image("image/webp", _b64(WEBP), max_upload_mb=20).media_type == "image/webp"
    with pytest.raises(ImageRejected) as mismatch:
        decode_chat_image("image/png", _b64(GIF), max_upload_mb=20)
    assert mismatch.value.code == "signature"
    with pytest.raises(ImageRejected) as bad64:
        decode_chat_image("image/png", "not base64!!!", max_upload_mb=20)
    assert bad64.value.code == "base64"
    with pytest.raises(ImageRejected) as media:
        decode_chat_image("image/bmp", _b64(PNG), max_upload_mb=20)
    assert media.value.code == "media_type"


def test_size_uses_max_upload_and_the_32mib_cap(monkeypatch):
    assert INLINE_CAP_BYTES == 32 * 1024 * 1024
    with pytest.raises(ImageRejected) as capped:
        decode_chat_image("image/png", _b64(PNG), max_upload_mb=0)
    assert capped.value.code == "size"
    monkeypatch.setattr("app.chat_images.INLINE_CAP_BYTES", 8)
    with pytest.raises(ImageRejected) as inline:
        decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    assert inline.value.code == "size"


def test_provider_body_measure_is_utf8_json_without_timeout():
    body = {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "你好"}],
        "stream": False,
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": "high",
        "timeout": 30,
    }
    raw = serialized_provider_body(body)
    assert raw.decode("utf-8") == (
        '{"model":"deepseek-flash","messages":[{"role":"user","content":"你好"}],'
        '"stream":false,"thinking":{"type":"enabled"},"reasoning_effort":"high"}'
    )
    assert "timeout" not in raw.decode("utf-8")
    assert BODY_CAP_BYTES == 48 * 1024 * 1024


def test_wire_allows_user_image_urls_and_refuses_other_shapes():
    from app.core.llm import complete_text

    image = decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    url = f"data:image/png;base64,{_b64(PNG)}"
    messages = [
        {"role": "system", "content": "stay"},
        {"role": "user", "content": "look", "web_search": {"search_query": "x"}},
    ]
    wired = _build_request(
        RoleCall(role=Role.CHAT, model="deepseek-flash", thinking=True),
        messages,
        kind="text",
        stream=False,
    )
    assert wired["messages"][0] == {"role": "system", "content": "stay"}
    assert "web_search" not in wired["messages"][1]
    from app.chat_images import attach_user_images

    attached = attach_user_images(messages, (image,))
    built = _build_request(
        RoleCall(role=Role.CHAT, model=resolve(Role.CHAT).model, thinking=True),
        attached,
        kind="text",
        stream=False,
    )
    content = built["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1] == {"type": "image_url", "image_url": {"url": url}}
    assert "temperature" not in built
    assert "max_tokens" not in built

    refused = [
        [{"role": "assistant", "content": [{"type": "text", "text": "no"}]}],
        [{"role": "user", "content": [{"type": "input_image", "image_url": {"url": url}}]}],
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]}],
        [{"role": "user", "content": "hi", "tool_calls": []}],
    ]
    for messages in refused[:3]:
        with pytest.raises(UnsupportedContent):
            _build_request(
                RoleCall(role=Role.CHAT, model="deepseek-flash", thinking=False),
                messages,
                kind="text",
                stream=False,
            )
    with pytest.raises(ToolsRefused):
        complete_text(refused[3], role=Role.CHAT)


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Msg:
    def __init__(self, content):
        self.content = content
        self.reasoning_content = None
        self.model_extra = None


class _Resp:
    def __init__(self, usage, finish="stop"):
        self.choices = [SimpleNamespace(message=_Msg("ok"), finish_reason=finish)]
        self.usage = usage


def _install_sync(monkeypatch, outputs):
    from app.core import llm as llm_mod

    calls: list[dict] = []
    queue = list(outputs)

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_sync_client", lambda: Client())
    return calls


def _rows(generation: int):
    from app.core.db import SessionLocal
    from app.models.models import LlmCall

    db = SessionLocal()
    try:
        return db.query(LlmCall).filter(LlmCall.generation == generation).all()
    finally:
        db.close()


def _clear(generation: int) -> None:
    from app.core.db import SessionLocal
    from app.models.models import LlmCall

    db = SessionLocal()
    try:
        db.query(LlmCall).filter(LlmCall.generation == generation).delete()
        db.commit()
    finally:
        db.close()


def test_complete_text_stores_null_usage_and_keeps_a_real_zero(monkeypatch):
    from app.core.generation_stop import bind, reset
    from app.core.llm import Spoken, complete_text

    calls = _install_sync(monkeypatch, [_Resp(None), _Resp(_Usage(0, None))])
    _clear(91001)
    _clear(91002)
    token = bind(user_id=1, generation=91001)
    try:
        missing = complete_text([{"role": "user", "content": "q"}], role=Role.CHAT)
    finally:
        reset(token)
    assert missing == Spoken("ok", "", None)
    stored = _rows(91001)
    assert len(stored) == 1
    assert stored[0].role == "chat"
    assert stored[0].model == resolve(Role.CHAT).model
    assert stored[0].prompt_tokens is None
    assert stored[0].completion_tokens is None
    assert stored[0].user_id == 1

    token = bind(user_id=1, generation=91002)
    try:
        zero = complete_text([{"role": "user", "content": "q"}], role=Role.CHAT)
    finally:
        reset(token)
    assert zero.prompt_tokens == 0
    stored = _rows(91002)
    assert len(stored) == 1
    assert stored[0].prompt_tokens == 0
    assert stored[0].completion_tokens is None
    assert calls


def test_failed_complete_text_does_not_store_unknown_usage(monkeypatch):
    from app.core.generation_stop import bind, reset
    from app.core.llm import complete_text

    class ConnectTimeout(Exception):
        pass

    _install_sync(monkeypatch, [ConnectTimeout("down")])
    _clear(91003)
    token = bind(user_id=1, generation=91003)
    try:
        spoken = complete_text([{"role": "user", "content": "q"}], role=Role.CHAT)
    finally:
        reset(token)
    assert spoken.content == ""
    assert _rows(91003) == []


def test_stream_stores_counts_and_a_drop_stays_null(monkeypatch):
    from app.core import llm as llm_mod
    from app.core.generation_stop import bind, reset
    from app.core.llm import AnswerDelta, stream_chat

    chunks: list = []

    class Completions:
        async def create(self, **kwargs):
            async def generate():
                for item in chunks:
                    yield item

            return generate()

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: Client())

    def chunk(text, usage):
        return SimpleNamespace(
            usage=usage,
            choices=[
                SimpleNamespace(
                    finish_reason="stop" if text is None else None,
                    delta=SimpleNamespace(content=text, reasoning_content=None, model_extra=None),
                )
            ]
            if text is not None
            else [],
        )

    chunks[:] = [chunk("hi", None), chunk(None, _Usage(4, 2))]
    _clear(91004)
    _clear(91005)
    token = bind(user_id=1, generation=91004)
    try:
        async def collect():
            return [item async for item in stream_chat([{"role": "user", "content": "q"}], role=Role.CHAT, usage_out={})]

        deltas = asyncio.run(collect())
    finally:
        reset(token)
    assert deltas == [AnswerDelta("hi")]
    stored = _rows(91004)
    assert len(stored) == 1
    assert stored[0].prompt_tokens == 4
    assert stored[0].completion_tokens == 2

    chunks[:] = [chunk("hi", None)]
    token = bind(user_id=1, generation=91005)
    try:
        async def dropped():
            gen = stream_chat([{"role": "user", "content": "q"}], role=Role.CHAT)
            async for _item in gen:
                break
            await gen.aclose()

        asyncio.run(dropped())
    finally:
        reset(token)
    stored = _rows(91005)
    assert len(stored) == 1
    assert stored[0].prompt_tokens is None
    assert stored[0].completion_tokens is None


def test_stream_infra_failure_does_not_store_a_usage_row(monkeypatch):
    from app.core import llm as llm_mod
    from app.core.generation_stop import bind, reset
    from app.core.llm import stream_chat

    class APIConnectionError(Exception):
        pass

    class Completions:
        async def create(self, **_kwargs):
            raise APIConnectionError("down")

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: Client())
    _clear(91006)
    token = bind(user_id=1, generation=91006)
    try:
        async def collect():
            return [item async for item in stream_chat([{"role": "user", "content": "q"}], role=Role.CHAT)]

        with pytest.raises(Exception):
            asyncio.run(collect())
    finally:
        reset(token)
    assert _rows(91006) == []


def test_oversized_provider_body_does_not_call_the_client(monkeypatch):
    from app.core import llm as llm_mod
    from app.core.llm import stream_chat

    calls: list[dict] = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("create was called")

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: Client())
    resolved = resolve(Role.CHAT)
    empty = _build_request(resolved, [{"role": "user", "content": ""}], kind="text", stream=True)
    empty["stream_options"] = {"include_usage": True}
    base = len(serialized_provider_body(empty))
    over = "x" * (BODY_CAP_BYTES - base + 1)

    async def collect():
        async for _item in stream_chat(
            [{"role": "user", "content": over}],
            role=Role.CHAT,
            usage_out={},
        ):
            pass

    with pytest.raises(RequestBodyTooLarge):
        asyncio.run(collect())
    assert calls == []


class _MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = False

    def put(self, key, data):
        raw = data if isinstance(data, (bytes, bytearray)) else data.read()
        self.objects[key] = bytes(raw)
        return key

    def exists(self, key) -> bool:
        return key in self.objects

    def delete(self, key) -> None:
        from app.core.db import SessionLocal
        from app.models.models import TaskImage, TaskRun

        db = SessionLocal()
        try:
            row = db.query(TaskImage).filter(TaskImage.storage_key == key).one()
            task = db.get(TaskRun, row.task_run_id)
            assert row.purge_state == "pending_delete"
            assert task is not None and task.status in {"completed", "stopped"}
        finally:
            db.close()
        if self.fail:
            raise OSError("delete failed")
        self.objects.pop(key, None)

    def materialize(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(self.objects[key])
        handle.close()
        return Path(handle.name)

    def release_materialized(self, path, key) -> None:
        Path(path).unlink(missing_ok=True)


def _session_for(user_id: int) -> int:
    from app.core.db import SessionLocal
    from app.models.models import Session as ChatSession
    from app.models.models import User

    db = SessionLocal()
    try:
        if db.get(User, user_id) is None:
            db.add(User(id=user_id, external_id=f"picture-{user_id}", name="picture", is_active=1))
            db.commit()
        row = ChatSession(user_id=user_id, title="picture")
        db.add(row)
        db.commit()
        return int(row.id)
    finally:
        db.close()


def test_job_image_round_trip_and_terminal_purge():
    from app.chat_images import decode_chat_image
    from app.core.db import SessionLocal
    from app.models.models import TaskImage
    from app.services import task_images, task_run
    from app.storage import factory as blob_factory

    image = decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    session_id = _session_for(1)
    turn = task_run.begin_turn(user_id=1, session_id=session_id)
    store = _MemoryStore()
    previous = blob_factory._store
    blob_factory._store = store
    try:
        db = SessionLocal()
        try:
            rows = task_images.bind_images(
                db,
                user_id=1,
                task_run_id=turn.id,
                images=(image,),
                store=store,
            )
            db.commit()
            assert len(rows) == 1
            assert rows[0].byte_length == len(PNG)
            assert rows[0].storage_key.startswith("task-image/")
            loaded = task_images.load_image_blocks(db, user_id=1, task_run_id=turn.id, store=store)
            assert loaded.kind == "ready"
            assert loaded.blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")
            hidden = task_images.load_image_blocks(db, user_id=2, task_run_id=turn.id, store=store)
            assert hidden.kind == "hidden"
            assert hidden.blocks == ()
        finally:
            db.close()

        db = SessionLocal()
        try:
            again = task_images.load_image_blocks(db, user_id=1, task_run_id=turn.id, store=store)
            assert again.kind == "ready"
        finally:
            db.close()

        store.fail = True
        stopped = task_run.stop(user_id=1, generation=turn.generation)
        assert stopped.status == "stopped"
        db = SessionLocal()
        try:
            pending = db.query(TaskImage).filter(TaskImage.task_run_id == turn.id).one()
            assert pending.purge_state == "pending_delete"
            assert store.objects
            hidden = task_images.load_image_blocks(db, user_id=1, task_run_id=turn.id, store=store)
            assert hidden.kind == "hidden"
        finally:
            db.close()
        store.fail = False
        assert task_images.purge_pending(user_id=1, store=store) == 1
        db = SessionLocal()
        try:
            assert db.query(TaskImage).filter(TaskImage.task_run_id == turn.id).count() == 0
        finally:
            db.close()
        assert store.objects == {}
    finally:
        blob_factory._store = previous


def test_missing_blob_is_unavailable_and_does_not_call_ocr(monkeypatch):
    from app.core.db import SessionLocal
    from app.models.models import TaskImage
    from app.services import task_images, task_run

    image = decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    session_id = _session_for(1)
    turn = task_run.begin_turn(user_id=1, session_id=session_id)
    store = _MemoryStore()
    db = SessionLocal()
    try:
        task_images.bind_images(db, user_id=1, task_run_id=turn.id, images=(image,), store=store)
        db.commit()
        store.objects.clear()
        called = []
        monkeypatch.setattr(
            "app.rag.vision_ocr.ocr_image_bytes",
            lambda *args, **kwargs: called.append(args),
        )
        loaded = task_images.load_image_blocks(db, user_id=1, task_run_id=turn.id, store=store)
        assert loaded.kind == "unavailable"
        assert loaded.blocks == ()
        assert called == []
        row = db.query(TaskImage).filter(TaskImage.task_run_id == turn.id).one()
        assert row.purge_state == "ready"
    finally:
        db.close()
        task_run.stop(user_id=1, generation=turn.generation)


def test_rolled_back_bind_does_not_delete_the_blob():
    from app.core.db import SessionLocal
    from app.models.models import TaskImage
    from app.services import task_images, task_run

    image = decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    session_id = _session_for(1)
    turn = task_run.begin_turn(user_id=1, session_id=session_id)
    store = _MemoryStore()
    db = SessionLocal()
    try:
        task_images.bind_images(db, user_id=1, task_run_id=turn.id, images=(image,), store=store)
        db.rollback()
        assert store.objects
        assert db.query(TaskImage).filter(TaskImage.task_run_id == turn.id).count() == 0
    finally:
        db.close()
        task_run.stop(user_id=1, generation=turn.generation)


def test_absent_pending_blob_still_drops_the_row():
    from app.core.db import SessionLocal
    from app.models.models import TaskImage
    from app.services import task_images, task_run
    from app.storage import factory as blob_factory

    image = decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    session_id = _session_for(1)
    turn = task_run.begin_turn(user_id=1, session_id=session_id)
    store = _MemoryStore()
    previous = blob_factory._store
    blob_factory._store = store
    db = SessionLocal()
    try:
        task_images.bind_images(db, user_id=1, task_run_id=turn.id, images=(image,), store=store)
        db.commit()
    finally:
        db.close()
    store.objects.clear()
    store.fail = False
    task_run.stop(user_id=1, generation=turn.generation)
    db = SessionLocal()
    try:
        assert db.query(TaskImage).filter(TaskImage.task_run_id == turn.id).count() == 0
    finally:
        db.close()
        blob_factory._store = previous


def test_chat_request_sends_png_to_the_chat_role_and_skips_ocr(auth_client, monkeypatch):
    from app.core import llm as llm_mod
    from app.core.db import SessionLocal
    from app.models.models import Document, Message, TaskImage
    from app.models.models import Session as ChatSession

    db = SessionLocal()
    try:
        before_images = db.query(TaskImage).count()
        session = ChatSession(user_id=1, title="picture-chat")
        db.add(session)
        db.commit()
        session_id = session.id
    finally:
        db.close()

    calls: list[dict] = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)

            async def generate():
                yield SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(content="seen", reasoning_content=None, model_extra=None),
                        )
                    ],
                )

            return generate()

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm_mod.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_mod, "_get_async_client", lambda: Client())
    ocr: list = []
    monkeypatch.setattr("app.rag.vision_ocr.ocr_image_bytes", lambda *args, **kwargs: ocr.append(args))
    workflow = {
        "intent": "chat",
        "forced": False,
        "citations": [],
        "thinking_steps": [],
        "llm_messages": [{"role": "user", "content": "what is this"}],
        "schedule_card": None,
        "artifact": None,
        "direct_answer": None,
        "answer_reasoning": None,
        "pending_calendar": None,
    }
    monkeypatch.setattr("app.api.chat._run_workflow_sync", lambda **kwargs: workflow)
    response = auth_client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "what is this",
            "images": [{"media_type": "image/png", "data_base64": _b64(PNG)}],
        },
    )
    assert response.status_code == 200
    assert "seen" in response.text
    assert ocr == []
    assert len(calls) == 1
    assert calls[0]["model"] == resolve(Role.CHAT).model
    content = calls[0]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "what is this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "tools" not in calls[0]
    db = SessionLocal()
    try:
        saved = db.query(Message).filter(Message.session_id == session_id, Message.role == "user").one()
        assert saved.content == "what is this"
        assert db.query(TaskImage).count() == before_images
        assert (
            db.query(Document)
            .filter(Document.stored_path.startswith("task-image/"))
            .count()
            == 0
        )
    finally:
        db.close()


def test_chat_rejects_a_mismatched_signature_and_regenerate_images(auth_client):
    from app.core.db import SessionLocal
    from app.models.models import Session as ChatSession

    db = SessionLocal()
    try:
        session = ChatSession(user_id=1, title="picture-reject")
        db.add(session)
        db.commit()
        session_id = session.id
    finally:
        db.close()
    bad = auth_client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "nope",
            "images": [{"media_type": "image/png", "data_base64": _b64(GIF)}],
        },
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "signature"
    regenerate = auth_client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "",
            "regenerate_message_id": 1,
            "images": [{"media_type": "image/png", "data_base64": _b64(PNG)}],
        },
    )
    assert regenerate.status_code == 400
    assert regenerate.json()["detail"] == "regenerate"


def test_grounded_chat_call_receives_the_image(monkeypatch):
    from app.agents.dispatch import TurnAssignment
    from app.agents.workflow import run_agent_workflow
    from app.core.db import SessionLocal
    from app.core.llm import Spoken

    seen: dict = {}

    def fake_complete(messages, *, role, **kwargs):
        seen["messages"] = messages
        seen["role"] = role
        return Spoken("draft", "", 3)

    def fake_ground(**kwargs):
        return Spoken("draft", "", 3), SimpleNamespace(passed=True, critique_failed=False), False

    monkeypatch.setattr("app.agents.workflow.should_ground_answer", lambda **kwargs: True)
    monkeypatch.setattr("app.agents.workflow.complete_text", fake_complete)
    monkeypatch.setattr("app.agents.workflow.ground_and_repair_answer", fake_ground)
    monkeypatch.setattr("app.agents.workflow.grounding_thinking_steps", lambda *args, **kwargs: [])
    image = decode_chat_image("image/png", _b64(PNG), max_upload_mb=20)
    assignment = TurnAssignment(
        next_tool=None,
        pending_tools=(),
        needs_freshness=False,
        needs_kb=False,
        needs_web=False,
        needs_calendar=False,
        needs_sandbox=False,
        forced=False,
        intent="chat",
    )
    db = SessionLocal()
    try:
        result = run_agent_workflow(
            db,
            assignment=assignment,
            message="what is this",
            history=[],
            user_id=1,
            images=(image,),
        )
    finally:
        db.close()
    assert result["direct_answer"] == "draft"
    assert seen["role"] is Role.CHAT
    content = seen["messages"][-1]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"


def test_picture_revision_upgrades_sqlite(tmp_path):
    database = tmp_path / "picture.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    env["DATABASE_MIGRATE_URL"] = ""
    env["REDIS_ENABLED"] = "false"
    backend = Path(__file__).resolve().parents[1]
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stderr
    from sqlalchemy import create_engine

    engine = create_engine(env["DATABASE_URL"])
    inspector = inspect(engine)
    llm_columns = {column["name"]: column for column in inspector.get_columns("llm_calls")}
    assert llm_columns["prompt_tokens"]["nullable"] is True
    assert llm_columns["completion_tokens"]["nullable"] is True
    assert llm_columns["user_id"]["nullable"] is True
    image_columns = {column["name"] for column in inspector.get_columns("task_images")}
    assert {"task_run_id", "media_type", "byte_length", "sha256", "storage_key", "purge_state"} <= image_columns
    down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0017_errand"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert down.returncode == 0, down.stderr
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert "llm_calls" not in names
    assert "task_images" not in names
    again = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode == 0, again.stderr
    engine.dispose()
