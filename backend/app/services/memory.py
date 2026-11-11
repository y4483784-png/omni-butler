"""
Long-term memory service (Phase 4 / Harness H3).

Industry pattern (LangMem profile manager + Mem0 ADD/UPDATE/NONE):
  - Semantic *profile* facts only — not episodic chat logs
  - Compare new facts against the current user profile before writing
  - Canonical keys so each topic has at most one row (no per-turn keys)

PRD 3.4: identity / preference / important entities. One-off tasks stay out.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.llm import Role, complete_json
from app.models.models import SOURCE_ACTOR, MemoryItem, MemoryLedger

logger = logging.getLogger(__name__)

PLANNER_OPS = "planner_ops"
PLANNER_FAILED = "planner_failed"
HEURISTIC_ONLY = "heuristic_only"

_VALID_KINDS = frozenset({"identity", "preference", "entity"})

# Closed key set ≈ LangMem UserProfile fields. Auto-extract may not invent keys.
_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "identity": frozenset({"name", "role"}),
    "preference": frozenset({"style", "language", "format", "schedule"}),
    "entity": frozenset({"org", "boss", "project", "room", "team"}),
}
_KEY_ALIASES = {
    "姓名": "name",
    "名字": "name",
    "称呼": "name",
    "nickname": "name",
    "职位": "role",
    "岗位": "role",
    "title": "role",
    "job": "role",
    "风格": "style",
    "语气": "style",
    "brevity": "style",
    "格式": "format",
    "表格": "format",
    "语言": "language",
    "lang": "language",
    "排期": "schedule",
    "日历": "schedule",
    "calendar": "schedule",
    "公司": "org",
    "单位": "org",
    "组织": "org",
    "employer": "org",
    "领导": "boss",
    "上司": "boss",
    "老板": "boss",
    "manager": "boss",
    "项目": "project",
    "会议室": "room",
    "房间": "room",
}

_EXTRACT_SYSTEM = """你是办公助手的长期记忆管家（对标 LangMem profile / Mem0）。
只把【跨会话仍成立】的用户画像写入记忆。默认不记。

只输出 JSON（禁止 Markdown）：
{"ops":[{"op":"ADD|UPDATE|NONE","id":null,"kind":"identity|preference|entity","key":"白名单键","content":"一句中文事实"}]}

key 白名单（禁止自造）：
- identity: name（称呼/姓名）, role（岗位）
- preference: style（回答风格）, language, format（表格/列表等）, schedule（如周五下午不排会）
- entity: org（公司/部门）, boss（直属领导）, project, room, team

硬性规则：
1. 只采【用户亲口陈述】的稳定事实；助手回复、文档内容、搜索结果、日程草稿都不是记忆。
2. 提问、一次性任务、本轮要办的事、会议纪要、报销/周报等事务 → 不记。
3. 已有记忆里已有相同事实 → NONE；用户明确更正同一 key → UPDATE 并带上 id。
4. 没有合格事实 → {"ops":[]}。每轮最多 2 条。
5. 不要记密码、证件号、验证码。

负例（必须空）：「帮我总结这份文档」「今天天气」「我是来问报销流程的」「我在看这个表」。
正例：「我叫小陈，我是产品经理」→ name + role；「我喜欢用表格看数据，周五下午不排会」→ format + schedule；「我的直属领导是王总」→ boss。"""

# Durable-cue gate: skip LLM/heuristic unless the user turn looks like a profile statement.
_DURABLE_CUE = re.compile(
    r"("
    r"我叫|叫我|请叫我|称呼我|"
    r"我是(?:一名|一个)?.{0,12}(?:经理|工程师|架构师|设计师|分析师|运营|总监|主管|专员|助理|顾问)|"
    r"就职于|任职于|我在.{0,24}(?:公司|集团|部门|团队)|"
    r"我喜欢|我偏好|我习惯|偏好|"
    r"请用(?:简洁|简短|详细|中文|英文)|"
    r"回答请|回复请|以后都|每次都|"
    r"周五.{0,8}不排|不排会|"
    r"直属领导|我的领导|我老板|领导是|老板是|"
    r"请记住|记住我|"
    r"我负责.{0,16}项目"
    r")",
    re.IGNORECASE,
)

_NAME_RE = re.compile(
    r"(?:我叫|叫我|请叫我|称呼我)(?:为|做)?\s*[「\"']?([^\s，。！？,.!?'\"」]{1,12})[」\"']?"
)
_ROLE_RE = re.compile(
    r"我是(?:一名|一个)?\s*"
    r"([^\s，。！？]{0,8}(?:产品经理|项目经理|工程师|架构师|设计师|分析师|运营|总监|主管|专员|助理|顾问|测试|研发))"
)
_BRIEF_RE = re.compile(
    r"请用?(简短|简洁|精炼)|用(简短|简洁|精炼)(的)?(回答|回复)|(回答|回复).{0,4}(简短|简洁|精炼)|少说点|短一点"
)
_DETAIL_RE = re.compile(
    r"请用?(详细|详尽)|用(详细|详尽)(的)?(回答|回复)|(回答|回复).{0,4}(详细|详尽)|多说点|展开说"
)
_FORMAT_RE = re.compile(r"(喜欢用|偏好用|习惯用|以后用|每次都用)\s*(表格|图表|列表)")
_SCHEDULE_RE = re.compile(r"(周五|星期[五]|周[五]).{0,8}不排(会|会议|日程)?")
_ORG_RE = re.compile(
    r"(?:就职于|任职于)\s*([^\s，。！？,.!?]{2,40})"
    r"|(?:我在)\s*([^\s，。！？,.!?]{2,40})(?:公司|集团|部门|团队)"
)
_BOSS_RE = re.compile(r"(?:直属领导|我的领导|我老板|领导是|老板是)\s*[是为]?\s*([^\s，。！？,.!?]{1,12})")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_durable_candidate(text: str) -> bool:
    """Cheap gate: skip turns that look like tasks/questions, not profile facts."""
    raw = (text or "").strip()
    if len(raw) < 3 or len(raw) > 2000:
        return False
    return bool(_DURABLE_CUE.search(raw))


def _canon_key(kind: str, key: str) -> str:
    k = (key or "").strip().lower()
    k = _KEY_ALIASES.get(k, k)
    k = re.sub(r"[^\w\-]+", "_", k, flags=re.UNICODE).strip("_")
    allowed = _ALLOWED_KEYS.get(kind, frozenset())
    return k if k in allowed else ""


def _normalize_item(raw: dict[str, Any], *, strict_keys: bool = False) -> dict[str, str] | None:
    kind = str(raw.get("kind") or "").strip().lower()
    key = str(raw.get("key") or "").strip().lower()[:64]
    content = str(raw.get("content") or "").strip()[:240]
    if kind not in _VALID_KINDS or not key or not content:
        return None
    if strict_keys:
        key = _canon_key(kind, key)
        if not key:
            return None
    else:
        key = re.sub(r"[^\w\-]+", "_", key, flags=re.UNICODE).strip("_") or kind
    return {"kind": kind, "key": key, "content": content}


def _content_similar(a: str, b: str) -> bool:
    x, y = (a or "").strip(), (b or "").strip()
    if not x or not y:
        return False
    if x == y or x in y or y in x:
        return True
    return SequenceMatcher(None, x, y).ratio() >= 0.86


def extract_memories_heuristic(transcript: str) -> list[dict[str, str]]:
    """Rule-based extraction for durable profile facts only (works without LLM)."""
    text = transcript or ""
    if not is_durable_candidate(text):
        return []
    out: list[dict[str, str]] = []
    m = _NAME_RE.search(text)
    if m:
        out.append({"kind": "identity", "key": "name", "content": f"用户希望被称为「{m.group(1).strip()}」"})
    m = _ROLE_RE.search(text)
    if m:
        out.append({"kind": "identity", "key": "role", "content": f"用户的岗位是{m.group(1).strip()}"})
    if _BRIEF_RE.search(text):
        out.append({"kind": "preference", "key": "style", "content": "用户偏好简短、精炼的回答"})
    if _DETAIL_RE.search(text):
        out.append({"kind": "preference", "key": "style", "content": "用户偏好详细、展开的回答"})
    if _FORMAT_RE.search(text):
        out.append({"kind": "preference", "key": "format", "content": "用户喜欢用表格或图表看数据"})
    if _SCHEDULE_RE.search(text):
        out.append({"kind": "preference", "key": "schedule", "content": "用户周五下午不排会"})
    m = _ORG_RE.search(text)
    if m:
        org = (m.group(1) or m.group(2) or "").strip()
        if org:
            out.append({"kind": "entity", "key": "org", "content": f"用户相关组织/单位：{org}"})
    m = _BOSS_RE.search(text)
    if m:
        out.append({"kind": "entity", "key": "boss", "content": f"用户的直属领导是{m.group(1).strip()}"})
    dedup: dict[tuple[str, str], dict[str, str]] = {}
    for item in out:
        dedup[(item["kind"], item["key"])] = item
    return list(dedup.values())


def _items_from_llm_payload(parsed: dict[str, Any]) -> list[dict[str, str]]:
    raw_ops = parsed.get("ops")
    if not isinstance(raw_ops, list):
        raw_ops = parsed.get("items") or []
    items: list[dict[str, str]] = []
    if not isinstance(raw_ops, list):
        return items
    for raw in raw_ops[:2]:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "ADD").strip().upper()
        if op in {"NONE", "NOOP", "SKIP"}:
            continue
        if op == "DELETE":
            continue
        item = _normalize_item(raw, strict_keys=True)
        if item:
            items.append(item)
    return items


@dataclass(frozen=True)
class Extraction:
    origin: str
    items: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class PersonWrite:
    user_id: int
    source: str
    source_ref: str
    kind: str
    key: str
    after: str
    remove: bool = False
    memory_id: int | None = None


@dataclass(frozen=True)
class TurnWrite:
    user_id: int
    generation: int | None
    kind: str
    key: str
    after: str
    origin: str


def is_low_confidence(origin: str) -> bool:
    return origin == PLANNER_FAILED


def _classify_origin(parsed: object, *, llm_attempted: bool) -> str:
    if not llm_attempted:
        return HEURISTIC_ONLY
    if isinstance(parsed, dict):
        return PLANNER_OPS
    return PLANNER_FAILED


def decide_apply(
    *,
    low_confidence: bool,
    latest_applied_actor: str | None,
    incoming: str,
) -> bool:
    """True when this statement becomes the live profile value."""
    if low_confidence:
        return False
    if incoming == "turn" and latest_applied_actor == "person":
        return False
    return True


def extract_for_ledger(
    transcript: str,
    *,
    existing: list[MemoryItem] | list[dict[str, str]] | None = None,
) -> Extraction:
    """Extract durable facts and record why the planner was or was not used."""
    text = (transcript or "").strip()
    if not text or not settings.memory_enabled or not is_durable_candidate(text):
        return Extraction(HEURISTIC_ONLY, ())

    existing_rows = existing or []
    llm_attempted = bool(settings.memory_use_llm and settings.llm_api_key)
    if llm_attempted:
        existing_brief = []
        for row in existing_rows[:40]:
            if isinstance(row, MemoryItem):
                existing_brief.append(
                    {"id": row.id, "kind": row.kind, "key": row.key, "content": (row.content or "")[:120]}
                )
            elif isinstance(row, dict):
                existing_brief.append(
                    {
                        "id": row.get("id"),
                        "kind": row.get("kind"),
                        "key": row.get("key"),
                        "content": str(row.get("content") or "")[:120],
                    }
                )
        parsed = complete_json(
            [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": "已有记忆：\n"
                    + (str(existing_brief) if existing_brief else "[]")
                    + "\n\n本轮用户发言：\n"
                    + text[:1500],
                },
            ],
            role=Role.PLANNER,
        )
        origin = _classify_origin(parsed, llm_attempted=True)
        # None means the planner call failed. An empty ops dict is a real answer.
        if origin == PLANNER_OPS and isinstance(parsed, dict):
            items = _dedupe_against_existing(_items_from_llm_payload(parsed), existing_rows)
            return Extraction(origin, tuple(items))
        items = _dedupe_against_existing(extract_memories_heuristic(text), existing_rows)
        return Extraction(PLANNER_FAILED, tuple(items))

    items = _dedupe_against_existing(extract_memories_heuristic(text), existing_rows)
    return Extraction(HEURISTIC_ONLY, tuple(items))


def extract_memories(
    transcript: str,
    *,
    existing: list[MemoryItem] | list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Extract durable profile facts. Empty is the correct result for ordinary turns."""
    return list(extract_for_ledger(transcript, existing=existing).items)


def _existing_pairs(existing: list) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for row in existing or []:
        if isinstance(row, MemoryItem):
            pairs.append((row.kind or "", row.key or "", row.content or ""))
        elif isinstance(row, dict):
            pairs.append((str(row.get("kind") or ""), str(row.get("key") or ""), str(row.get("content") or "")))
    return pairs


def _dedupe_against_existing(items: list[dict[str, str]], existing: list) -> list[dict[str, str]]:
    """Drop facts already stored (Mem0 NONE). Same kind+key still upserts with new wording."""
    known = _existing_pairs(existing)
    out: list[dict[str, str]] = []
    for item in items:
        same_key = next((c for k, ky, c in known if k == item["kind"] and ky == item["key"]), None)
        if same_key is not None and _content_similar(same_key, item["content"]):
            continue
        if any(_content_similar(c, item["content"]) for _k, _ky, c in known):
            continue
        out.append(item)
    return out


def _locked_live(
    db: Session,
    user_id: int,
    kind: str,
    key: str,
    memory_id: int | None,
) -> MemoryItem | None:
    if memory_id is not None:
        row = (
            db.query(MemoryItem)
            .filter(MemoryItem.id == memory_id, MemoryItem.user_id == user_id)
            .with_for_update()
            .one_or_none()
        )
        if row is not None:
            return row
    return (
        db.query(MemoryItem)
        .filter(MemoryItem.user_id == user_id, MemoryItem.kind == kind, MemoryItem.key == key)
        .with_for_update()
        .one_or_none()
    )


def _latest_applied(db: Session, user_id: int, kind: str, key: str) -> MemoryLedger | None:
    return (
        db.query(MemoryLedger)
        .filter(
            MemoryLedger.user_id == user_id,
            MemoryLedger.kind == kind,
            MemoryLedger.key == key,
            MemoryLedger.pending == 0,
            MemoryLedger.decision.is_(None),
        )
        .order_by(MemoryLedger.id.desc())
        .with_for_update()
        .first()
    )


def _project_live(
    db: Session,
    *,
    user_id: int,
    kind: str,
    key: str,
    content: str,
    remove: bool,
    memory_id: int | None,
) -> MemoryItem | None:
    row: MemoryItem | None = None
    if memory_id is not None:
        row = (
            db.query(MemoryItem)
            .filter(MemoryItem.id == memory_id, MemoryItem.user_id == user_id)
            .one_or_none()
        )
    if row is None:
        row = (
            db.query(MemoryItem)
            .filter(MemoryItem.user_id == user_id, MemoryItem.kind == kind, MemoryItem.key == key)
            .one_or_none()
        )
    if remove:
        if row is not None:
            db.delete(row)
        return None
    now = _utcnow()
    if row is None:
        row = MemoryItem(
            user_id=user_id,
            kind=kind,
            key=key,
            content=content,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        return row
    row.kind = kind
    row.key = key
    row.content = content
    row.updated_at = now
    return row


def _commit_change(db: Session, write: PersonWrite | TurnWrite) -> MemoryLedger | None:
    if isinstance(write, TurnWrite):
        source = "turn"
        source_ref = str(int(write.generation)) if write.generation is not None else uuid4().hex
        remove = False
        memory_id = None
        origin = write.origin
        user_id = int(write.user_id)
        kind = write.kind
        key = write.key
        after = write.after
    else:
        source = write.source
        source_ref = write.source_ref
        remove = write.remove
        memory_id = write.memory_id
        origin = HEURISTIC_ONLY
        user_id = int(write.user_id)
        kind = write.kind
        key = write.key
        after = "" if remove else write.after
    if user_id < 1 or source not in SOURCE_ACTOR:
        return None
    live = _locked_live(db, user_id, kind, key, memory_id)
    before = (live.content or "") if live is not None else ""
    if (
        not remove
        and live is not None
        and (live.kind or "") == kind
        and (live.key or "") == key
        and before == after
    ):
        return None
    latest = _latest_applied(db, user_id, kind, key)
    apply = decide_apply(
        low_confidence=is_low_confidence(origin),
        latest_applied_actor=None if latest is None else latest.actor,
        incoming=SOURCE_ACTOR[source],
    )
    now = _utcnow()
    row = MemoryLedger(
        user_id=user_id,
        kind=kind,
        key=key,
        source=source,
        actor=SOURCE_ACTOR[source],
        before=before,
        after=after,
        pending=0 if apply else 1,
        decision=None,
        source_ref=source_ref,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    if apply:
        _project_live(
            db,
            user_id=user_id,
            kind=kind,
            key=key,
            content=after,
            remove=remove,
            memory_id=memory_id,
        )
    db.flush()
    return row


def _turn_recorded(db: Session, user_id: int, generation: int) -> bool:
    found = (
        db.query(MemoryLedger.id)
        .filter(
            MemoryLedger.user_id == user_id,
            MemoryLedger.source == "turn",
            MemoryLedger.source_ref == str(int(generation)),
        )
        .first()
    )
    return found is not None


def store_memories(
    db: Session,
    user_id: int,
    items: list[dict[str, str]],
    *,
    auto: bool = False,
) -> int:
    """Upsert memories for a user. Returns number of rows written/updated."""
    if not settings.memory_enabled or not items:
        return 0
    n = 0
    uid = int(user_id)
    if uid < 1:
        return 0
    existing = list_memories(db, uid, limit=max(40, int(settings.memory_max_items)))
    existing_keys = {(r.kind or "", r.key or "") for r in existing}
    cap = int(settings.memory_max_items)
    for raw in items:
        item = _normalize_item(raw, strict_keys=auto) if isinstance(raw, dict) and "kind" in raw else None
        if not item:
            continue
        key_pair = (item["kind"], item["key"])
        if auto and key_pair not in existing_keys and len(existing_keys) >= cap:
            continue
        wrote = _commit_change(
            db,
            PersonWrite(
                user_id=uid,
                source="store",
                source_ref="",
                kind=item["kind"],
                key=item["key"],
                after=item["content"],
            ),
        )
        if wrote is None:
            continue
        existing_keys.add(key_pair)
        n += 1
    if n:
        db.commit()
    return n


def list_memories(db: Session, user_id: int, *, limit: int = 40) -> list[MemoryItem]:
    uid = int(user_id or 1)
    return (
        db.query(MemoryItem)
        .filter(MemoryItem.user_id == uid)
        .order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
        .limit(limit)
        .all()
    )


def format_memory_block(rows: list[MemoryItem], *, max_chars: int | None = None) -> str:
    """Build a short system-prompt block; empty if nothing to inject."""
    budget = int(max_chars if max_chars is not None else settings.memory_max_chars)
    if budget <= 0 or not rows:
        return ""
    lines = ["【长期记忆】以下是跨会话偏好，回答时参考，勿逐条复述："]
    used = len(lines[0])
    kind_label = {"identity": "身份", "preference": "偏好", "entity": "实体"}
    for row in rows:
        label = kind_label.get(row.kind or "", row.kind or "记忆")
        line = f"- {label}/{row.key}：{(row.content or '').strip()}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def load_memory_prompt(db: Session, user_id: int) -> str:
    """Best-effort: memory injection must never break answer generation."""
    if not settings.memory_enabled:
        return ""
    try:
        return format_memory_block(list_memories(db, user_id))
    except Exception as exc:
        logger.warning("memory load skipped: %s", exc)
        return ""


def inject_system_memory(messages: list[dict], memory_block: str) -> list[dict]:
    """Prepend or merge memory into the first system message."""
    block = (memory_block or "").strip()
    if not block or not messages:
        return messages
    out = [dict(m) for m in messages]
    if out[0].get("role") == "system":
        out[0]["content"] = block + "\n\n" + str(out[0].get("content") or "")
    else:
        out.insert(0, {"role": "system", "content": block})
    return out


def remember_from_turn(
    db: Session,
    *,
    user_id: int,
    user_message: str,
    assistant_message: str = "",
    generation: int | None = None,
    status: str | None = None,
) -> int:
    """Extract + store durable profile facts from the user turn (best-effort).

    Assistant text is ignored: Mem0/ChatGPT-style memory comes from the user, not
    the model's recap of this session.
    """
    if not settings.memory_enabled:
        return 0
    if generation is not None and status != "completed":
        return 0
    uid = int(user_id)
    if uid < 1:
        return 0
    if generation is not None and _turn_recorded(db, uid, generation):
        return 0
    user_text = (user_message or "").strip()
    if not is_durable_candidate(user_text):
        return 0
    try:
        existing = list_memories(db, uid, limit=max(40, int(settings.memory_max_items)))
        found = extract_for_ledger(user_text, existing=existing)
        existing_keys = {(r.kind or "", r.key or "") for r in existing}
        cap = int(settings.memory_max_items)
        n = 0
        for raw in found.items:
            item = _normalize_item(raw, strict_keys=True)
            if not item:
                continue
            key_pair = (item["kind"], item["key"])
            if key_pair not in existing_keys and len(existing_keys) >= cap:
                continue
            wrote = _commit_change(
                db,
                TurnWrite(
                    user_id=uid,
                    generation=generation,
                    kind=item["kind"],
                    key=item["key"],
                    after=item["content"],
                    origin=found.origin,
                ),
            )
            if wrote is None:
                continue
            existing_keys.add(key_pair)
            n += 1
        if n:
            db.commit()
        return n
    except Exception:
        logger.exception("remember_from_turn failed")
        db.rollback()
        return 0


def serialize_memory(row: MemoryItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind or "preference",
        "key": row.key or "",
        "content": row.content or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def create_memory(
    db: Session,
    user_id: int,
    *,
    kind: str,
    key: str,
    content: str,
) -> MemoryItem:
    item = _normalize_item({"kind": kind, "key": key, "content": content})
    if not item:
        raise ValueError("记忆条目无效：请填写类型、键名和内容")
    uid = int(user_id)
    _commit_change(
        db,
        PersonWrite(
            user_id=uid,
            source="panel",
            source_ref="",
            kind=item["kind"],
            key=item["key"],
            after=item["content"],
        ),
    )
    db.commit()
    row = (
        db.query(MemoryItem)
        .filter(MemoryItem.user_id == uid, MemoryItem.kind == item["kind"], MemoryItem.key == item["key"])
        .one()
    )
    return row


def update_memory(
    db: Session,
    row: MemoryItem,
    *,
    kind: str | None = None,
    key: str | None = None,
    content: str | None = None,
) -> MemoryItem:
    next_kind = kind if kind is not None else (row.kind or "")
    next_key = key if key is not None else (row.key or "")
    next_content = content if content is not None else (row.content or "")
    item = _normalize_item({"kind": next_kind, "key": next_key, "content": next_content})
    if not item:
        raise ValueError("记忆条目无效：请填写类型、键名和内容")

    clash = (
        db.query(MemoryItem)
        .filter(
            MemoryItem.user_id == row.user_id,
            MemoryItem.kind == item["kind"],
            MemoryItem.key == item["key"],
            MemoryItem.id != row.id,
        )
        .one_or_none()
    )
    if clash is not None:
        raise ValueError("已存在相同类型和键名的记忆")

    _commit_change(
        db,
        PersonWrite(
            user_id=int(row.user_id),
            source="panel",
            source_ref="",
            kind=item["kind"],
            key=item["key"],
            after=item["content"],
            memory_id=row.id,
        ),
    )
    db.commit()
    db.refresh(row)
    return row


def delete_memory(db: Session, row: MemoryItem) -> None:
    _commit_change(
        db,
        PersonWrite(
            user_id=int(row.user_id),
            source="panel",
            source_ref="",
            kind=row.kind or "",
            key=row.key or "",
            after="",
            remove=True,
            memory_id=row.id,
        ),
    )
    db.commit()


def serialize_change(row: MemoryLedger) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind or "",
        "key": row.key or "",
        "source": row.source or "",
        "actor": row.actor or "",
        "before": row.before or "",
        "after": row.after or "",
        "pending": bool(row.pending),
        "decision": row.decision,
    }


def panel_view(db: Session, user_id: int) -> list[dict[str, Any]]:
    uid = int(user_id or 0)
    if uid < 1:
        return []
    memories = list_memories(db, uid, limit=100)
    changes = (
        db.query(MemoryLedger)
        .filter(MemoryLedger.user_id == uid)
        .order_by(MemoryLedger.id.asc())
        .all()
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for change in changes:
        grouped.setdefault((change.kind or "", change.key or ""), []).append(serialize_change(change))
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for mem in memories:
        pair = (mem.kind or "", mem.key or "")
        seen.add(pair)
        payload = serialize_memory(mem)
        payload["changes"] = grouped.get(pair, [])
        items.append(payload)
    for pair, rows in grouped.items():
        if pair in seen:
            continue
        items.append(
            {
                "id": None,
                "kind": pair[0],
                "key": pair[1],
                "content": "",
                "created_at": "",
                "updated_at": "",
                "changes": rows,
            }
        )
    return items


def settle_pending(
    db: Session,
    *,
    user_id: int,
    ledger_id: int,
    decision: str,
) -> MemoryLedger | None:
    uid = int(user_id or 0)
    if uid < 1 or decision not in {"restored", "discarded"}:
        return None
    row = (
        db.query(MemoryLedger)
        .filter(MemoryLedger.id == int(ledger_id), MemoryLedger.user_id == uid)
        .one_or_none()
    )
    if row is None:
        return None
    if row.pending != 1:
        return row
    if decision == "discarded":
        row.pending = 0
        row.decision = "discarded"
        row.updated_at = _utcnow()
        db.commit()
        return row
    try:
        _commit_change(
            db,
            PersonWrite(
                user_id=uid,
                source="settle",
                source_ref=str(row.id),
                kind=row.kind or "",
                key=row.key or "",
                after=row.after or "",
            ),
        )
        row.pending = 0
        row.decision = "restored"
        row.updated_at = _utcnow()
        db.commit()
    except IntegrityError:
        db.rollback()
        return (
            db.query(MemoryLedger)
            .filter(MemoryLedger.id == int(ledger_id), MemoryLedger.user_id == uid)
            .one_or_none()
        )
    return row
