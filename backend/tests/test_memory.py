"""H3 long-term memory: extract → store (upsert) → inject."""

from __future__ import annotations

from app.core.db import SessionLocal, init_db
from app.models.models import MemoryItem
from app.services import memory as memory_svc


def test_extract_memories_heuristic_identity_and_style():
    items = memory_svc.extract_memories_heuristic("我叫小陈，请用简短回答。")
    kinds = {(i["kind"], i["key"]) for i in items}
    assert ("identity", "name") in kinds
    assert ("preference", "style") in kinds
    name = next(i for i in items if i["key"] == "name")
    assert "小陈" in name["content"]


def test_heuristic_skips_ephemeral_turns():
    """Ordinary tasks/questions must not become memories (PRD 3.4 durable facts only)."""
    for text in (
        "帮我总结这份周报",
        "今天的新闻有哪些",
        "我是来问报销流程的",
        "我在看这个表格",
        "根据文档说明一下架构",
        "帮我定明早十点的周会",
        "请用表格把这份数据画出来",
    ):
        assert memory_svc.extract_memories_heuristic(text) == [], text
        assert memory_svc.is_durable_candidate(text) is False, text


def test_heuristic_keeps_prd_profile_facts():
    items = memory_svc.extract_memories_heuristic(
        "我是产品经理，我喜欢用表格看数据，周五下午不排会，我的直属领导是王总"
    )
    keys = {(i["kind"], i["key"]) for i in items}
    assert ("identity", "role") in keys
    assert ("preference", "format") in keys
    assert ("preference", "schedule") in keys
    assert ("entity", "boss") in keys
    org = memory_svc.extract_memories_heuristic("我在Acme公司做产品。")
    assert any(i["kind"] == "entity" and i["key"] == "org" for i in org)


def test_llm_empty_ops_does_not_fall_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", True)
    monkeypatch.setattr(memory_svc.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(memory_svc, "complete_json", lambda *_a, **_k: {"ops": []})
    # Would match heuristic, but the manager said nothing to store.
    assert memory_svc.extract_memories("我叫小陈，请用简短回答。") == []


def test_llm_invented_keys_are_dropped(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", True)
    monkeypatch.setattr(memory_svc.settings, "llm_api_key", "test-key")

    def fake_json(_msgs, **_k):
        return {
            "ops": [
                {"op": "ADD", "kind": "entity", "key": "weekly_report_q1", "content": "用户在问周报"},
                {"op": "ADD", "kind": "identity", "key": "name", "content": "用户希望被称为「小陈」"},
            ]
        }

    monkeypatch.setattr(memory_svc, "complete_json", fake_json)
    items = memory_svc.extract_memories("我叫小陈")
    assert len(items) == 1
    assert items[0]["key"] == "name"


def test_duplicate_fact_not_written_again(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", False)
    items = memory_svc.extract_memories(
        "叫我阿哲",
        existing=[{"kind": "identity", "key": "name", "content": "用户希望被称为「阿哲」"}],
    )
    assert items == []


def test_remember_ephemeral_turn_writes_nothing(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", False)
    init_db()
    db = SessionLocal()
    try:
        db.query(MemoryItem).filter(MemoryItem.user_id == 9003).delete()
        db.commit()
        n = memory_svc.remember_from_turn(
            db,
            user_id=9003,
            user_message="帮我总结附件里的周报，再画张图",
            assistant_message="这是本周工作的详细总结……",
        )
        assert n == 0
        assert memory_svc.list_memories(db, 9003) == []
    finally:
        db.query(MemoryItem).filter(MemoryItem.user_id == 9003).delete()
        db.commit()
        db.close()


def test_store_upsert_and_inject(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", False)
    monkeypatch.setattr(memory_svc.settings, "memory_max_chars", 800)

    init_db()
    db = SessionLocal()
    try:
        db.query(MemoryItem).filter(MemoryItem.user_id == 9001).delete()
        db.commit()

        n = memory_svc.store_memories(
            db,
            9001,
            [
                {"kind": "identity", "key": "name", "content": "用户希望被称为「小陈」"},
                {"kind": "preference", "key": "style", "content": "用户偏好简短回答"},
            ],
        )
        assert n == 2

        n2 = memory_svc.store_memories(
            db,
            9001,
            [{"kind": "identity", "key": "name", "content": "用户希望被称为「陈工」"}],
        )
        assert n2 == 1
        rows = memory_svc.list_memories(db, 9001)
        name_rows = [r for r in rows if r.kind == "identity" and r.key == "name"]
        assert len(name_rows) == 1
        assert "陈工" in name_rows[0].content

        block = memory_svc.load_memory_prompt(db, 9001)
        assert "【长期记忆】" in block
        assert "陈工" in block

        msgs = [{"role": "user", "content": "你好"}]
        injected = memory_svc.inject_system_memory(msgs, block)
        assert injected[0]["role"] == "system"
        assert "长期记忆" in injected[0]["content"]
        assert injected[1]["role"] == "user"
    finally:
        db.query(MemoryItem).filter(MemoryItem.user_id == 9001).delete()
        db.commit()
        db.close()


def test_format_memory_block_respects_budget(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_max_chars", 80)

    class _Row:
        kind = "preference"
        key = "style"
        content = "用户偏好非常非常非常非常非常长的说明文字用于测试截断预算"

    block = memory_svc.format_memory_block([_Row()], max_chars=80)
    # Header alone may fit; oversize line should be skipped → empty or header-only filtered to ""
    assert block == "" or len(block) <= 80


def test_remember_from_turn_heuristic(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_svc.settings, "memory_use_llm", False)

    init_db()
    db = SessionLocal()
    try:
        db.query(MemoryItem).filter(MemoryItem.user_id == 9002).delete()
        db.commit()
        n = memory_svc.remember_from_turn(
            db,
            user_id=9002,
            user_message="叫我阿哲，回答请简洁一点",
            assistant_message="好的，阿哲。",
        )
        assert n >= 1
        block = memory_svc.load_memory_prompt(db, 9002)
        assert "阿哲" in block or "简洁" in block or "简短" in block
    finally:
        db.query(MemoryItem).filter(MemoryItem.user_id == 9002).delete()
        db.commit()
        db.close()


def test_memory_disabled_skips(monkeypatch):
    monkeypatch.setattr(memory_svc.settings, "memory_enabled", False)
    assert memory_svc.extract_memories("我叫小陈") == []
    init_db()
    db = SessionLocal()
    try:
        assert memory_svc.load_memory_prompt(db, 1) == ""
        assert (
            memory_svc.remember_from_turn(
                db, user_id=1, user_message="我叫小陈", assistant_message="好"
            )
            == 0
        )
    finally:
        db.close()


def test_remember_turn_isolated_uses_own_session(monkeypatch):
    from app.api import chat as chat_mod

    seen: dict = {}

    def fake_remember(db, **kwargs):
        seen["closed_before"] = db.is_active
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(chat_mod, "remember_from_turn", fake_remember)
    chat_mod._remember_turn_isolated(42, "用户说", "助手答")
    assert seen["kwargs"]["user_id"] == 42
    assert seen["kwargs"]["user_message"] == "用户说"
    assert seen["kwargs"]["assistant_message"] == "助手答"
