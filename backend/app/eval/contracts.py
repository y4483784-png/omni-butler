"""Schemas and quality contracts for the frozen evaluation datasets.

The generators are intentionally deterministic.  These contracts validate
quality (shape, balance, uniqueness, and gold references) rather than merely
asserting a headline row count.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_ROOT / "data" / "eval"
RAG_CORPUS_ROOT = DATA_ROOT / "corpus" / "rag"

Intent = Literal["chat", "rag", "web_search", "calendar", "data_analysis"]
Difficulty = Literal["easy", "medium", "hard"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagCase(StrictModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=2)
    gold_doc_keys: list[str] = Field(min_length=1)
    tags: list[str]
    difficulty: Difficulty


class RoutingGold(StrictModel):
    intent: Intent
    tools: list[Literal["kb", "web", "calendar", "sandbox"]]


class RoutingTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class RoutingCase(StrictModel):
    id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    history: list[RoutingTurn]
    context: dict[str, Any]
    gold: RoutingGold
    tags: list[str]


class YZCase(StrictModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    gold_doc_keys: list[str]
    gold_facts: list[str]
    ground_truth: str = Field(min_length=1)
    expect_unanswerable: bool
    gold: RoutingGold
    tags: list[str]


TaskType = Literal["fact_qa", "compute", "refuse", "chat", "calendar", "multi_tool", "no_tool"]

# Fixture keys the agent suite may scope a task to (see ``YZ_FIXTURES``).
AgentDocKey = Literal["office_policy_txt", "product_guide_md", "attendance_csv"]


class AgentContext(StrictModel):
    """Availability switches handed to ``run_agent_workflow`` for one task.

    These used to be hard-coded in ``agent_e2e.run_task`` (always True / None),
    which made the availability clamps, document scoping and the pending-calendar
    short circuit untestable.
    """

    has_kb_docs: bool
    has_tabular_docs: bool
    # Fixture keys, resolved to document ids after ingest; empty = no scoping.
    document_keys: list[AgentDocKey] = Field(default_factory=list)
    # Legacy field. Production no longer short-circuits routing on this value.
    pending_calendar: dict[str, Any] | None = None


class AgentCase(StrictModel):
    """End-to-end task: run the real agent loop, judge completion by ``task_type``."""

    id: str = Field(min_length=1)
    query: str = Field(min_length=2)
    history: list[RoutingTurn]
    task_type: TaskType
    # None = routing not scored for this task (e.g. refusals on tabular data)
    route_gold: RoutingGold | None
    gold_doc_keys: list[str]
    answer_spec: dict[str, Any] | None
    context: AgentContext
    tags: list[str]


class GroundingCase(StrictModel):
    id: str = Field(min_length=1)
    source: Literal["kb", "web", "sandbox"]
    query: str = Field(min_length=2)
    gold_doc_keys: list[str]
    contexts: list[str]
    needs_sandbox: bool
    expect_unanswerable: bool
    tags: list[str]


class TabularCase(StrictModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=2)
    split: Literal["smoke", "full"]
    difficulty: Difficulty
    tags: list[str]
    concepts: list[str]
    fixture: str = Field(min_length=1)
    fixtures: list[str] = Field(min_length=1)
    tolerance: float = Field(ge=0)
    expect_uncomputable: bool
    missing_tokens: list[str]
    accept_percent: bool
    require_derive: bool
    gold: Any | None
    gold_method: str | None
    prior_ir: dict[str, Any] | None
    gold_plan: dict[str, Any]


class ContextTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    is_needle: bool


class ContextProbe(StrictModel):
    question: str = Field(min_length=2)
    gold_answer: str
    gold_keys: list[str] = Field(min_length=1)
    judge_mode: Literal["strict", "update", "temporal", "rubric", "abstain"]
    expect_abstain: bool


class ContextCase(StrictModel):
    id: str = Field(min_length=1)
    split: Literal["smoke", "full"]
    type: Literal[
        "user_fact",
        "assistant_fact",
        "knowledge_update",
        "multi_hop",
        "temporal",
        "pending_task",
        "preference",
        "abstention",
    ]
    bucket_turns: Literal[12, 30, 60, 120]
    depth_pct: Literal[0, 25, 50, 75]
    seed: int
    history: list[ContextTurn]
    pending_calendar: dict[str, Any] | None
    evidence_pool: list[dict[str, Any]] | None
    probe: ContextProbe
    tags: list[str]


_CALENDAR_CATEGORIES = (
    "confirm_before_write",
    "meeting_block",
    "id_mutate",
    "free_busy",
    "notes_description",
    "wall_clock",
    "create_meeting",
    "create_block",
    "list_schedule",
    "conflict",
    "attendees",
    "error_paths",
)
CalendarCategory = Literal[
    "confirm_before_write",
    "meeting_block",
    "id_mutate",
    "free_busy",
    "notes_description",
    "wall_clock",
    "create_meeting",
    "create_block",
    "list_schedule",
    "conflict",
    "attendees",
    "error_paths",
]


class CalendarSeed(StrictModel):
    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    start_at: str
    end_at: str
    description: str
    location: str
    all_day: bool
    participants: list[str]
    external_id: str
    local: bool


class CalendarTurn(StrictModel):
    utterance: str = Field(min_length=1)


class CalendarFields(StrictModel):
    title: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_minutes: int | None = None
    participants: list[str] | None = None
    location: str | None = None
    description: str | None = None
    kind: str | None = None
    external_id: str | None = None


class CalendarMatch(CalendarFields):
    attendees: list[str] | None = None


class CalendarSpan(StrictModel):
    start_at: str
    end_at: str


class CalendarCardExpect(StrictModel):
    type: Literal[
        "meeting_create",
        "block_create",
        "meeting_update",
        "block_update",
        "cancel",
        "list",
        "none",
    ] | None = None
    phase: Literal["draft", "info", "absent"] | None = None
    fields: CalendarFields | None = None
    suggested_slots: Literal["absent", "present_ok", "non_overlapping"] | None = None
    must_not_overlap: list[CalendarSpan] | None = None
    must_list_titles: list[str] | None = None


class CalendarGoogleExpect(StrictModel):
    op: Literal["none", "create", "update", "delete"] | None = None
    external_id: str | None = None
    match: CalendarMatch | None = None
    unchanged: list[str] | None = None


class CalendarExpect(StrictModel):
    confirm: Literal["do_not_confirm", "confirm_create", "confirm_update", "confirm_cancel"]
    before_confirm_google: Literal["none"] | None = None
    card: CalendarCardExpect | None = None
    google: CalendarGoogleExpect | None = None
    http_status: int | None = None
    copy_must: list[str] | None = None
    copy_must_not: list[str] | None = None
    local_row: Literal["unchanged", "inserted", "updated", "absent"] | None = None


class CalendarCase(StrictModel):
    id: str = Field(min_length=1)
    split: Literal["smoke", "full"]
    category: CalendarCategory
    tags: list[str] = Field(min_length=1)
    time_complete: bool
    now: str
    zone: str = Field(min_length=1)
    connection: Literal["connected", "disconnected"]
    fault: Literal["none", "blackhole", "revoke", "insert_500"]
    seeds: list[CalendarSeed]
    turns: list[CalendarTurn] = Field(min_length=1, max_length=3)
    expect: CalendarExpect


RowCheck = Callable[[list[dict[str, Any]], list[str]], None]

# Tool precedence mirrors ``router.intent_from_decision``: sandbox dominates kb, which
# dominates calendar, which dominates web. Kept here (not imported) so the dataset
# contract cannot drift silently with a product change.
_INTENT_BY_TOOL = (
    ("sandbox", "data_analysis"),
    ("kb", "rag"),
    ("calendar", "calendar"),
    ("web", "web_search"),
)


def derived_intent(tools: list[str]) -> str:
    """Intent implied by a tool set; the only self-consistent label for a row."""
    present = set(tools or [])
    for tool, intent in _INTENT_BY_TOOL:
        if tool in present:
            return intent
    return "chat"


@dataclass(frozen=True)
class DatasetContract:
    name: str
    filename: str
    model: type[StrictModel]
    min_rows: int
    max_rows: int
    text_field: str | None
    check: RowCheck

    @property
    def path(self) -> Path:
        return DATA_ROOT / self.filename


def normalize_text(value: str) -> str:
    """Normalize user-visible text for duplicate detection."""
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _ratio(
    errors: list[str],
    label: str,
    count: int,
    total: int,
    low: float,
    high: float,
) -> None:
    value = count / total if total else 0.0
    if not low <= value <= high:
        errors.append(
            f"{label}: {count}/{total}={value:.1%}, expected {low:.1%}..{high:.1%}"
        )


def _rag_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    categories = Counter()
    doc_counts: Counter[str] = Counter()
    allowed_categories = {"single_doc", "multi_doc", "hard_negative"}
    for row in rows:
        cats = allowed_categories.intersection(row["tags"])
        if len(cats) != 1:
            errors.append(f"{row['id']}: expected exactly one RAG category tag, got {sorted(cats)}")
        else:
            categories.update(cats)
        if "multi_doc" in cats and len(row["gold_doc_keys"]) < 2:
            errors.append(f"{row['id']}: multi_doc requires at least two gold documents")
        if "multi_doc" not in cats and len(row["gold_doc_keys"]) != 1:
            errors.append(f"{row['id']}: non-multi case requires exactly one gold document")
        doc_counts.update(row["gold_doc_keys"])

    _ratio(errors, "rag.single_doc", categories["single_doc"], len(rows), 0.70, 0.80)
    _ratio(errors, "rag.multi_doc", categories["multi_doc"], len(rows), 0.12, 0.18)
    _ratio(errors, "rag.hard_negative", categories["hard_negative"], len(rows), 0.08, 0.12)
    long_queries = sum(1 for row in rows if len(row["query"]) >= 15)
    short_queries = sum(1 for row in rows if len(row["query"]) <= 5)
    _ratio(errors, "rag.query_length>=15", long_queries, len(rows), 0.35, 1.0)
    _ratio(errors, "rag.query_length<=5", short_queries, len(rows), 0.0, 0.02)

    # Colloquial phrasing: the dataset must not slide back into extractive rewrites of
    # corpus sentences. Measured when this guard was added: 166/400 = 41.5% carry a
    # question mark or a spoken opener, median length 12 characters.
    spoken_markers = ("？", "?", "帮我", "请问", "麻烦")
    spoken = sum(1 for row in rows if any(m in row["query"] for m in spoken_markers))
    _ratio(errors, "rag.spoken_phrasing", spoken, len(rows), 0.35, 1.0)
    lengths = sorted(len(row["query"]) for row in rows)
    median_len = lengths[len(lengths) // 2] if lengths else 0
    if median_len < 12:
        errors.append(
            f"rag.median_query_length: {median_len}, expected >= 12 "
            "(queries are drifting back to extractive fragments)"
        )

    corpus_keys = {
        p.stem for p in RAG_CORPUS_ROOT.glob("*.md") if p.name.casefold() != "readme.md"
    }
    if not corpus_keys:
        errors.append(f"rag corpus is empty: {RAG_CORPUS_ROOT}")
    elif len(corpus_keys) != 16:
        errors.append(f"rag corpus has {len(corpus_keys)} documents, expected 16")
    unknown = sorted(set(doc_counts) - corpus_keys)
    if unknown:
        errors.append(f"rag gold references unknown corpus keys: {unknown}")
    if doc_counts and max(doc_counts.values()) > min(doc_counts.values()) * 2.5:
        errors.append(f"rag document distribution too skewed: {dict(doc_counts.most_common())}")


def _yz_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    categories = ("policy", "product", "attendance", "multi_doc", "unanswerable", "chat")
    counts: Counter[str] = Counter()
    valid_doc_keys = {"office_policy_txt", "product_guide_md", "attendance_csv"}
    expected_ranges = {
        "policy": (0.29, 0.36),
        "product": (0.22, 0.28),
        "attendance": (0.22, 0.28),
        "multi_doc": (0.06, 0.09),
        "unanswerable": (0.04, 0.06),
        "chat": (0.04, 0.06),
    }
    for row in rows:
        cats = [c for c in categories if c in row["tags"]]
        if len(cats) != 1:
            errors.append(f"{row['id']}: expected exactly one YZ category tag, got {cats}")
        else:
            counts[cats[0]] += 1
        unknown = set(row["gold_doc_keys"]) - valid_doc_keys
        if unknown:
            errors.append(f"{row['id']}: unknown YZ gold keys: {sorted(unknown)}")
        is_chat = row["gold"]["intent"] == "chat"
        if is_chat and (row["gold_doc_keys"] or row["gold_facts"]):
            errors.append(f"{row['id']}: chat case must not carry retrieval/fact gold")
        if row["expect_unanswerable"] and row["gold_facts"]:
            errors.append(f"{row['id']}: unanswerable case must not carry gold_facts")
        # Citation scoring skips unanswerable rows; gold_doc_keys on refuse is a
        # leftover (agent suite forbids it). Warn via yz_label_conflicts, not fail.
        if not is_chat and not row["expect_unanswerable"] and not row["gold_facts"]:
            errors.append(f"{row['id']}: answerable YZ case requires gold_facts")
    for category, (low, high) in expected_ranges.items():
        _ratio(errors, f"yz.{category}", counts[category], len(rows), low, high)
    long_queries = sum(1 for row in rows if len(row["query"]) >= 15)
    _ratio(errors, "yz.query_length>=15", long_queries, len(rows), 0.35, 1.0)


def _routing_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    intents = Counter(row["gold"]["intent"] for row in rows)
    # Bounds follow the *label convention* (intent derived from the tool set), not the
    # earlier hand-labelled mix: the 15 kb+calendar rows now read as rag, which moves
    # the rag/calendar split. Re-measured after that fix: rag 0.3095, calendar 0.1762.
    bounds = {
        "chat": (0.18, 0.22),
        "rag": (0.27, 0.34),
        "web_search": (0.13, 0.17),
        "calendar": (0.15, 0.20),
        "data_analysis": (0.14, 0.18),
    }
    for intent, (low, high) in bounds.items():
        _ratio(errors, f"routing.{intent}", intents[intent], len(rows), low, high)
    # Intent is a projection of the tool set (router.intent_from_decision); a row that
    # contradicts that projection is unanswerable by construction.
    for row in rows:
        want = derived_intent(row["gold"]["tools"])
        if row["gold"]["intent"] != want:
            errors.append(
                f"{row['id']}: intent {row['gold']['intent']} contradicts tools "
                f"{row['gold']['tools']} (derived {want})"
            )
    multi = sum(1 for row in rows if len(row["gold"]["tools"]) > 1)
    history = sum(1 for row in rows if row["history"])
    pending = sum(1 for row in rows if row["context"].get("pending_calendar"))
    _ratio(errors, "routing.multi_tool", multi, len(rows), 0.10, 0.15)
    _ratio(errors, "routing.multiturn", history, len(rows), 0.20, 0.35)
    if pending != 0:
        errors.append(f"routing.pending_calendar: {pending} rows still carry a draft; expected 0")
    # Real multi-turn: histories must be distinct, not one placeholder reused 50 times.
    hist_keys = Counter(
        json.dumps(row["history"], ensure_ascii=False, sort_keys=True) for row in rows if row["history"]
    )
    distinct = len(hist_keys)
    if history and distinct < 0.4 * history:
        errors.append(f"routing.history: only {distinct} distinct histories across {history} multiturn rows")
    anaphora = sum(
        1 for row in rows if row["history"] and "multi_tool" not in row["tags"]
    )
    if anaphora < 20:
        errors.append(f"routing.anaphora: {anaphora} single-tool multiturn rows, expected >=20")


_STEM_PREFIX = re.compile(r"^(请问|麻烦(帮我)?(看下|查下)?|制度里|帮我查|顺便问下|那|再)[，,]?")
_STEM_SUFFIX = re.compile(r"(怎么规定|是多少|怎么计|多少|吗|呢)[？?]?$")


def query_stem(text: str) -> str:
    """Strip politeness prefixes/suffixes so template farms collapse to one stem."""
    t = normalize_text(text)
    for _ in range(3):
        t2 = _STEM_PREFIX.sub("", t)
        t2 = _STEM_SUFFIX.sub("", t2)
        if t2 == t:
            break
        t = t2
    return t


def _agent_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    types = Counter(row["task_type"] for row in rows)
    for tt in ("fact_qa", "compute", "refuse", "chat", "calendar", "multi_tool"):
        if types[tt] == 0:
            errors.append(f"agent: no {tt} tasks")
    multi = sum(1 for row in rows if row["history"])
    _ratio(errors, "agent.multiturn", multi, len(rows), 0.25, 0.60)
    short = sum(1 for row in rows if len(row["query"]) <= 8)
    _ratio(errors, "agent.short_query<=8", short, len(rows), 0.0, 0.20)
    stems = Counter(query_stem(row["query"]) for row in rows if not row["history"])
    farms = {s: n for s, n in stems.items() if n > 2}
    if farms:
        errors.append(f"agent: template farm (>2 phrasings of one stem): {farms}")
    for row in rows:
        tt = row["task_type"]
        if tt in ("fact_qa", "compute", "multi_tool") and not row["answer_spec"]:
            errors.append(f"{row['id']}: {tt} requires answer_spec")
        if tt == "refuse" and (row["gold_doc_keys"] or row["answer_spec"]):
            errors.append(f"{row['id']}: refuse must not carry gold_doc_keys/answer_spec")
        if tt in ("chat", "calendar") and row["gold_doc_keys"]:
            errors.append(f"{row['id']}: {tt} must not carry gold_doc_keys")
        if tt in ("fact_qa", "compute", "multi_tool") and not row["gold_doc_keys"]:
            errors.append(f"{row['id']}: {tt} requires gold_doc_keys")
        unknown = set(row["gold_doc_keys"]) - {"office_policy_txt", "product_guide_md", "attendance_csv"}
        if unknown:
            errors.append(f"{row['id']}: unknown gold keys {sorted(unknown)}")
        tools = (row["route_gold"] or {}).get("tools") or []
        if "aggregate" in row["tags"] and "sandbox" not in tools:
            errors.append(f"{row['id']}: attendance aggregate must route to sandbox")
        if "lookup" in row["tags"] and tools != ["kb"]:
            errors.append(f"{row['id']}: attendance lookup must route to kb only")

        # Availability switches must not contradict the gold route: a task that
        # declares "no tabular docs" can never be scored as "sandbox expected".
        ctx = row["context"]
        if not ctx["has_tabular_docs"] and "sandbox" in tools:
            errors.append(f"{row['id']}: sandbox gold but has_tabular_docs=false")
        if not ctx["has_kb_docs"] and "kb" in tools:
            errors.append(f"{row['id']}: kb gold but has_kb_docs=false")
        if "doc_scope" in row["tags"] and not ctx["document_keys"]:
            errors.append(f"{row['id']}: doc_scope requires document_keys")
        if ctx["document_keys"] and tools != ["kb"]:
            errors.append(f"{row['id']}: document scoping always routes to kb only")
        if tt == "no_tool":
            # Availability clamp: routing is still scored, but as "no tool at all".
            if tools:
                errors.append(f"{row['id']}: no_tool must expect an empty tool set")
            if row["gold_doc_keys"]:
                errors.append(f"{row['id']}: no_tool must not carry gold_doc_keys")
            if not row["answer_spec"]:
                errors.append(f"{row['id']}: no_tool requires answer_spec")
    for tag, low in (("availability_gate", 6), ("doc_scope", 4)):
        have = sum(1 for row in rows if tag in row["tags"])
        if have < low:
            errors.append(f"agent: only {have} {tag} tasks, expected >= {low}")


def cross_dataset_checks() -> list[str]:
    """Label conventions must agree across routing / agent / yz datasets."""
    errors: list[str] = []
    errors.extend(suite_contract_alignment_errors())
    routing, _ = load_jsonl(DATA_ROOT / "office_tool_routing.jsonl")
    for row in routing:
        tools = set((row.get("gold") or {}).get("tools") or [])
        ctx = row.get("context") or {}
        if "sandbox" in tools and not ctx.get("has_tabular_docs"):
            errors.append(f"routing {row.get('id')}: sandbox gold without has_tabular_docs")
        if "kb" in tools and not (ctx.get("has_kb_docs") or ctx.get("forced_kb") or ctx.get("use_kb")):
            errors.append(f"routing {row.get('id')}: kb gold without has_kb_docs")
    agent, _ = load_jsonl(DATA_ROOT / "agent_tasks.jsonl")
    for row in agent:
        if row.get("task_type") == "refuse" and row.get("gold_doc_keys"):
            errors.append(f"agent {row.get('id')}: refuse must not carry gold_doc_keys")
        tools = set(((row.get("route_gold") or {}).get("tools") or []))
        if "aggregate" in (row.get("tags") or []) and "sandbox" not in tools:
            errors.append(f"agent {row.get('id')}: aggregate tag without sandbox route")
        ctx = row.get("context") or {}
        if "sandbox" in tools and not ctx.get("has_tabular_docs"):
            errors.append(f"agent {row.get('id')}: sandbox gold without has_tabular_docs")
        if "kb" in tools and not ctx.get("has_kb_docs"):
            errors.append(f"agent {row.get('id')}: kb gold without has_kb_docs")
        gold_intent = (row.get("route_gold") or {}).get("intent")
        if gold_intent is not None and gold_intent != derived_intent(sorted(tools)):
            errors.append(
                f"agent {row.get('id')}: route_gold.intent {gold_intent} contradicts "
                f"tools {sorted(tools)} (derived {derived_intent(sorted(tools))})"
            )
    return errors


def yz_label_conflicts() -> list[str]:
    """YZ rows whose gold routing contradicts the routing-suite convention (aggregates -> sandbox)."""
    rows, _ = load_jsonl(DATA_ROOT / "yz_fullchain.jsonl")
    bad = []
    for row in rows:
        tags = set(row.get("tags") or [])
        tools = (row.get("gold") or {}).get("tools") or []
        if tags & {"department_aggregate", "global_aggregate"} and "sandbox" not in tools:
            bad.append(row["id"])
    return bad


def _grounding_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    sources = Counter(row["source"] for row in rows)
    for source in ("kb", "web", "sandbox"):
        _ratio(errors, f"grounding.{source}", sources[source], len(rows), 0.30, 0.37)
    first = Counter(row["source"] for row in rows[:24])
    if first != {"kb": 8, "web": 8, "sandbox": 8}:
        errors.append(f"grounding smoke prefix must be 8/source, got {dict(first)}")
    for row in rows:
        if row["source"] == "kb" and not row["gold_doc_keys"]:
            errors.append(f"{row['id']}: kb grounding case requires gold_doc_keys")
        if row["source"] != "kb" and not row["contexts"]:
            errors.append(f"{row['id']}: frozen {row['source']} case requires contexts")
        if row["source"] == "sandbox" and not row["needs_sandbox"]:
            errors.append(f"{row['id']}: sandbox case requires needs_sandbox=true")


def _tabular_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    smoke = sum(1 for row in rows if row["split"] == "smoke")
    if smoke != 30:
        errors.append(f"tabular smoke: {smoke}, expected 30")
    join_ok = sum(1 for row in rows if "join_ok" in row["tags"])
    if join_ok < 12:
        errors.append(f"tabular join_ok: {join_ok}, expected at least 12")

    # Guard the smoke slice's composition. It is the only table slice that runs under
    # CI-like conditions, so a rebalance that quietly turns it into "30 easy filter
    # cases" would let the plan gate keep passing while covering less and less.
    smoke_rows = [row for row in rows if row["split"] == "smoke"]
    if sum(1 for row in smoke_rows if "rate" in row["tags"]) < 1:
        errors.append("tabular smoke must cover at least one rate case")
    if sum(1 for row in smoke_rows if row["expect_uncomputable"]) < 1:
        errors.append("tabular smoke must cover at least one refusal case")
    smoke_dirty = sum(1 for row in smoke_rows if "dirty" in row["tags"])
    if smoke_dirty < 2:
        errors.append(f"tabular smoke dirty cases: {smoke_dirty}, expected >= 2")
    # Medium-difficulty cases are all "filter by a column value" and the heuristic
    # planner cannot read column values out of a question (measured 5/94), so keeping
    # them out of the smoke slice is deliberate — see the eval README section
    # "冒烟档为什么只放简单题". This note is the reason; do not "fix" it by adding them.
    if sum(1 for row in smoke_rows if row["difficulty"] == "medium"):
        errors.append(
            "tabular smoke must not contain medium-difficulty cases: the plan smoke "
            "gate scores the heuristic arm, which cannot plan value filters"
        )
    if sum(1 for row in smoke_rows if "join_ok" in row["tags"]):
        errors.append("tabular smoke must not contain join_ok cases (heuristic join arm)")

    fixture_names = {p.name for p in (DATA_ROOT / "fixtures").glob("*.csv")}
    for row in rows:
        unknown = set(row["fixtures"]) - fixture_names
        if unknown:
            errors.append(f"{row['id']}: missing fixture files: {sorted(unknown)}")
        if row["fixture"] not in row["fixtures"]:
            errors.append(f"{row['id']}: fixture must be included in fixtures")
        if row["expect_uncomputable"] and row["gold"] is not None:
            errors.append(f"{row['id']}: uncomputable case must have gold=null")
        if not row["expect_uncomputable"] and row["gold"] is None:
            errors.append(f"{row['id']}: computable case requires gold")


def _context_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    combos = Counter(
        (row["type"], row["bucket_turns"], row["depth_pct"], row["seed"]) for row in rows
    )
    if len(combos) != len(rows) or any(n != 1 for n in combos.values()):
        errors.append("context factorial keys are not unique")
    types = {row["type"] for row in rows}
    buckets = {row["bucket_turns"] for row in rows}
    depths = {row["depth_pct"] for row in rows}
    seeds = {row["seed"] for row in rows}
    if (len(types), buckets, depths, len(seeds)) != (
        8,
        {12, 30, 60, 120},
        {0, 25, 50, 75},
        3,
    ):
        errors.append(
            "context factorial coverage must be 8 types x 4 buckets x 4 depths x 3 seeds"
        )
    smoke = [row for row in rows if row["split"] == "smoke"]
    if len(smoke) != 32:
        errors.append(f"context smoke: {len(smoke)}, expected 32")
    for row in rows:
        if len(row["history"]) != row["bucket_turns"] * 2:
            errors.append(
                f"{row['id']}: history has {len(row['history'])} messages, "
                f"expected {row['bucket_turns'] * 2}"
            )


_CALENDAR_QUOTAS = {
    "confirm_before_write": (20, 4),
    "meeting_block": (18, 3),
    "id_mutate": (22, 4),
    "free_busy": (20, 4),
    "notes_description": (16, 3),
    "wall_clock": (4, 2),
    "create_meeting": (10, 2),
    "create_block": (10, 2),
    "list_schedule": (8, 2),
    "conflict": (8, 1),
    "attendees": (6, 1),
    "error_paths": (8, 2),
}
_CALENDAR_GATED = frozenset(
    {
        "confirm_before_write",
        "meeting_block",
        "id_mutate",
        "free_busy",
        "notes_description",
        "wall_clock",
        "error_paths",
    }
)
_CALENDAR_EXTRA_TAGS = frozenset({"past_clock", "disconnected", "multi_turn", "all_day"})
_WALL_CLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00$")
_BLOCK_TYPES = frozenset({"block_create", "block_update"})


def _wall_ok(value: str) -> bool:
    return bool(_WALL_CLOCK.match(value or "")) and "+" not in value and not value.endswith("Z")


def _calendar_check(rows: list[dict[str, Any]], errors: list[str]) -> None:
    if len(rows) != 150:
        errors.append(f"calendar rows: {len(rows)}, expected 150")
    want_ids = {f"CAL-{index:03d}" for index in range(1, 151)}
    have_ids = {row["id"] for row in rows}
    if have_ids != want_ids:
        missing = sorted(want_ids - have_ids)[:8]
        extra = sorted(have_ids - want_ids)[:8]
        errors.append(f"calendar ids must be CAL-001..CAL-150 missing={missing} extra={extra}")
    counts: Counter[str] = Counter(row["category"] for row in rows)
    smoke_counts: Counter[str] = Counter(row["category"] for row in rows if row["split"] == "smoke")
    if sum(smoke_counts.values()) != 30:
        errors.append(f"calendar smoke: {sum(smoke_counts.values())}, expected 30")
    for category, (full, smoke) in _CALENDAR_QUOTAS.items():
        if counts[category] != full:
            errors.append(f"calendar.{category}: {counts[category]}, expected {full}")
        if smoke_counts[category] != smoke:
            errors.append(f"calendar.{category} smoke: {smoke_counts[category]}, expected {smoke}")
    for category in _CALENDAR_GATED:
        if smoke_counts[category] < 1:
            errors.append(f"calendar smoke must cover gated category {category}")
    seen: dict[tuple, str] = {}
    past_clock = 0
    for row in rows:
        tags = list(row["tags"])
        if tags.count(row["category"]) != 1:
            errors.append(f"{row['id']}: category tag {row['category']} must appear once")
        other_categories = [tag for tag in tags if tag in _CALENDAR_QUOTAS and tag != row["category"]]
        if other_categories:
            errors.append(f"{row['id']}: tags include another category {other_categories}")
        unknown = [tag for tag in tags if tag not in _CALENDAR_QUOTAS and tag not in _CALENDAR_EXTRA_TAGS]
        if unknown:
            errors.append(f"{row['id']}: tags outside the calendar vocab {unknown}")
        if row["connection"] == "disconnected" and "disconnected" not in tags:
            errors.append(f"{row['id']}: disconnected rows must carry the disconnected tag")
        if len(row["turns"]) > 1 and "multi_turn" not in tags:
            errors.append(f"{row['id']}: multi-turn rows must carry multi_turn")
        signature = (
            "\n".join(turn["utterance"] for turn in row["turns"]),
            row["connection"],
            row["fault"],
            tuple(seed["key"] for seed in row["seeds"]),
        )
        if signature in seen:
            errors.append(f"{row['id']}: duplicates scenario {seen[signature]}")
        else:
            seen[signature] = row["id"]
        if not _wall_ok(row["now"]):
            errors.append(f"{row['id']}: now must be an offset-less wall clock")
        keys = [seed["key"] for seed in row["seeds"]]
        if len(keys) != len(set(keys)):
            errors.append(f"{row['id']}: duplicate seed keys")
        for seed in row["seeds"]:
            if not _wall_ok(seed["start_at"]) or not _wall_ok(seed["end_at"]):
                errors.append(f"{row['id']}: seed {seed['key']} times must be offset-less")
            if row["connection"] == "disconnected" and not seed["local"]:
                errors.append(f"{row['id']}: disconnected seed {seed['key']} must be local")
        expect = row["expect"]
        card = expect.get("card") or {}
        fields = card.get("fields") or {}
        if row["time_complete"]:
            if not _wall_ok(str(fields.get("start_at") or "")) or not _wall_ok(str(fields.get("end_at") or "")):
                errors.append(f"{row['id']}: time_complete requires offset-less card start_at and end_at")
        elif fields.get("start_at") or fields.get("end_at"):
            errors.append(f"{row['id']}: time_complete=false must not carry card start_at/end_at")
        if expect.get("confirm") == "do_not_confirm" and expect.get("http_status") is not None:
            errors.append(f"{row['id']}: do_not_confirm must not set http_status")
        google = expect.get("google") or {}
        external = google.get("external_id")
        if external is not None and external not in {"absent", "new"} and not str(external).startswith("seed:"):
            errors.append(f"{row['id']}: google.external_id must be absent, new, or seed:<key>")
        if isinstance(external, str) and external.startswith("seed:"):
            key = external.split(":", 1)[1]
            if key not in keys:
                errors.append(f"{row['id']}: google.external_id references unknown seed {key}")
        category = row["category"]
        if category == "confirm_before_write":
            if expect.get("before_confirm_google") != "none" or expect.get("confirm") != "do_not_confirm":
                errors.append(f"{row['id']}: confirm_before_write must not confirm and must keep google quiet")
        elif category == "meeting_block":
            kind = card.get("type")
            if kind not in {
                "meeting_create",
                "block_create",
                "meeting_update",
                "block_update",
                "cancel",
            }:
                errors.append(f"{row['id']}: meeting_block card.type must be a meeting, block, or cancel")
            if kind in _BLOCK_TYPES and (
                "participants" not in fields or "location" not in fields
            ):
                errors.append(f"{row['id']}: block cards must check participants and location")
        elif category == "id_mutate":
            if not external and expect.get("http_status") != 409:
                errors.append(f"{row['id']}: id_mutate needs google.external_id or http 409")
        elif category == "free_busy":
            read_failure = bool(expect.get("copy_must"))
            listed = bool(card.get("must_list_titles"))
            if not listed and not read_failure:
                errors.append(f"{row['id']}: free_busy needs titles or a read-failure sentence")
            if not any(
                (
                    card.get("suggested_slots"),
                    card.get("must_not_overlap"),
                    expect.get("copy_must"),
                )
            ):
                errors.append(f"{row['id']}: free_busy needs a slot, overlap, or copy check")
        elif category == "notes_description":
            if "description" not in fields and "description" not in (google.get("match") or {}):
                errors.append(f"{row['id']}: notes_description must check description")
        elif category == "wall_clock":
            if not row["time_complete"] or "start_at" not in fields or "end_at" not in fields:
                errors.append(f"{row['id']}: wall_clock must lock start_at and end_at")
            if "past_clock" in tags:
                past_clock += 1
                if str(fields.get("start_at") or "") >= row["now"]:
                    errors.append(f"{row['id']}: past_clock gold must be earlier than now")
        if category in {"create_meeting", "create_block"} and not row["time_complete"]:
            errors.append(f"{row['id']}: {category} must be time_complete")
    if counts["wall_clock"] and past_clock < 1:
        errors.append("calendar wall_clock must include at least one past_clock row")


CONTRACTS: dict[str, DatasetContract] = {
    "rag": DatasetContract(
        "rag", "rag_retrieval.jsonl", RagCase, 380, 420, "query", _rag_check
    ),
    # "yz" is retired as a suite (replaced by "agent"); its dataset file stays on disk
    # as a historical reference, so it is deliberately not registered here.
    "agent": DatasetContract(
        "agent", "agent_tasks.jsonl", AgentCase, 140, 260, None, _agent_check
    ),
    "routing": DatasetContract(
        "routing",
        "office_tool_routing.jsonl",
        RoutingCase,
        380,
        420,
        "message",
        _routing_check,
    ),
    "grounding": DatasetContract(
        "grounding",
        "grounding_faithfulness.jsonl",
        GroundingCase,
        110,
        130,
        "query",
        _grounding_check,
    ),
    "tabular": DatasetContract(
        "tabular",
        "office_tabular.jsonl",
        TabularCase,
        180,
        220,
        "query",
        _tabular_check,
    ),
    "context": DatasetContract(
        "context",
        "context_recall.jsonl",
        ContextCase,
        384,
        384,
        None,
        _context_check,
    ),
    "calendar": DatasetContract(
        "calendar",
        "calendar_behavior.jsonl",
        CalendarCase,
        150,
        150,
        None,
        _calendar_check,
    ),
}

# Frozen vocabularies make misspelled slice names fail fast instead of silently
# creating a one-row metric bucket. Add a tag here as part of the same review
# that adds it to a seed/generator.
TAG_VOCAB: dict[str, frozenset[str]] = {
    "rag": frozenset(
        {
            "single_doc", "multi_doc", "hard_negative", "policy", "travel",
            "finance", "hr", "benefits", "it", "security", "retention",
            "legal", "procurement", "product", "tech", "tabular", "office",
            "meeting", "expense", "calendar", "travel_expense_procedure",
            "account_security", "meeting_minutes", "procurement_policy",
            "data_retention", "employee_benefits",
        }
    ),
    "yz": frozenset(
        {
            "policy", "product", "attendance", "multi_doc", "unanswerable",
            "chat", "access", "agenda", "analysis", "approval",
            "attendance_average", "attendance_gap", "attendance_rate",
            "attendance_total", "break", "calendar", "citation", "confusion",
            "cross_document", "csv_and_policy", "csv_policy_reasoning", "data",
            "data_analysis", "department", "department_aggregate",
            "department_count", "device", "document", "employee_summary",
            "exact", "expected_total", "faq", "format", "formats", "friday",
            "full_attendance", "getting_started", "global_aggregate",
            "headcount", "hours", "howto", "howto_and_policy", "incident",
            "kb_mode", "late", "late_and_leave", "late_average", "late_max",
            "late_threshold", "late_total", "latex", "leave_average",
            "leave_threshold", "leave_total", "limit", "limits", "md_and_txt",
            "meeting", "minutes", "no_late", "noise", "overtime",
            "overtime_average", "overtime_max", "overtime_threshold",
            "overtime_total", "policy_over_product", "refusal", "render",
            "sandbox_ui", "security", "troubleshoot", "workspace",
        }
    ),
    "agent": frozenset(
        {
            "fact", "hours", "break", "late", "bonus", "document", "approval",
            "security", "meeting", "device", "access", "incident", "upload",
            "limits", "formats", "kb_mode", "sandbox_ui", "calendar",
            "troubleshoot", "render", "getting_started", "data_analysis",
            "multiturn", "noisy", "attendance", "aggregate", "headcount",
            "late_total", "lookup", "multi_tool", "refuse", "kb", "table", "chat",
            "availability_gate", "slot_fill", "doc_scope",
        }
    ),
    "routing": frozenset(
        {
            "attendance", "calendar", "chat", "finance", "forced_ui", "hr",
            "it", "kb", "legal", "meeting", "multi_tool", "multiturn",
            "office", "procurement", "product", "retention", "sales",
            "calendar_cancel", "calendar_list", "calendar_suggest", "calendar_update",
            "sandbox", "security", "single_tool", "slot_fill", "tabular",
            "travel", "web",
        }
    ),
    "grounding": frozenset(
        {
            "attend", "attendance", "benefit", "document", "expense",
            "inventory", "kb", "meeting", "orders", "policy", "product",
            "project", "sales", "sandbox", "security", "service", "training",
            "travel", "unanswerable", "web",
        }
    ),
    "tabular": frozenset(
        {
            "count", "count_distinct", "date", "derive", "dirty", "filter",
            "followup", "group", "join_ok", "join_refuse", "max", "mean",
            "median", "min", "missing", "rate", "sum",
        }
    ),
    "context": frozenset(
        {
            "user_fact", "assistant_fact", "knowledge_update", "multi_hop",
            "temporal", "pending_task", "preference", "abstention",
            "b12", "b30", "b60", "b120", "d0", "d25", "d50", "d75",
        }
    ),
    "calendar": frozenset(
        {
            "confirm_before_write",
            "meeting_block",
            "id_mutate",
            "free_busy",
            "notes_description",
            "wall_clock",
            "create_meeting",
            "create_block",
            "list_schedule",
            "conflict",
            "attendees",
            "error_paths",
            "past_clock",
            "disconnected",
            "multi_turn",
            "all_day",
        }
    ),
}


@dataclass
class ValidationResult:
    contract: DatasetContract
    rows: list[dict[str, Any]]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.is_file():
        return rows, [f"missing dataset: {path}"]
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_no}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"line {line_no}: expected JSON object")
            continue
        rows.append(value)
    return rows, errors


def validate_dataset(name: str) -> ValidationResult:
    contract = CONTRACTS[name]
    raw_rows, errors = load_jsonl(contract.path)
    rows: list[dict[str, Any]] = []
    expected_keys = set(contract.model.model_fields)
    for line_no, raw in enumerate(raw_rows, start=1):
        try:
            model = contract.model.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"row {line_no}: {exc}")
            continue
        if set(raw) != expected_keys:
            missing = sorted(expected_keys - set(raw))
            extra = sorted(set(raw) - expected_keys)
            errors.append(f"{raw.get('id', line_no)}: schema drift missing={missing} extra={extra}")
        rows.append(model.model_dump(mode="python"))

    if not contract.min_rows <= len(rows) <= contract.max_rows:
        errors.append(
            f"row count {len(rows)} outside {contract.min_rows}..{contract.max_rows}"
        )
    ids = [row["id"] for row in rows]
    duplicates = sorted(key for key, n in Counter(ids).items() if n > 1)
    if duplicates:
        errors.append(f"duplicate ids: {duplicates[:20]}")
    if contract.text_field:
        by_text: dict[str, list[str]] = {}
        for row in rows:
            key = normalize_text(str(row[contract.text_field]))
            by_text.setdefault(key, []).append(row["id"])
        text_dups = [ids for ids in by_text.values() if len(ids) > 1]
        if text_dups:
            errors.append(f"normalized duplicate texts: {text_dups[:20]}")
    unknown_tags = sorted(
        {
            tag
            for row in rows
            for tag in row.get("tags", [])
            if tag not in TAG_VOCAB[name]
        }
    )
    if unknown_tags:
        errors.append(f"unknown tags: {unknown_tags}")
    contract.check(rows, errors)
    return ValidationResult(contract, rows, errors)


def eval_suite_ids() -> set[str]:
    """Dataset-bearing eval suites from the SUITES registry (excludes pytest)."""
    from app.eval.suites import SUITES

    return {s.id for s in SUITES.values() if s.kind == "eval"}


def suite_contract_alignment_errors() -> list[str]:
    """Every eval suite must have a dataset contract, and vice versa."""
    expected = eval_suite_ids()
    have = set(CONTRACTS)
    errors: list[str] = []
    missing = sorted(expected - have)
    extra = sorted(have - expected)
    if missing:
        errors.append(f"SUITES without contracts: {missing}")
    if extra:
        errors.append(f"contracts without SUITES: {extra}")
    return errors


def validate_all() -> list[ValidationResult]:
    return [validate_dataset(name) for name in CONTRACTS]


def render_dataset_card(results: list[ValidationResult]) -> str:
    lines = [
        "# Evaluation Dataset Card",
        "",
        "Generated by `python scripts/lint_eval_datasets.py --card`.",
        "",
        "L2 主指标套件是 `agent`。`yz` 套件已下线（由 `agent` 取代），其题集文件 "
        "`yz_fullchain.jsonl` 保留作历史对照，因此不再出现在下表。",
        "",
        "| suite | rows | status | file |",
        "|---|---:|---|---|",
    ]
    for result in results:
        status = "ok" if result.ok else f"{len(result.errors)} error(s)"
        lines.append(
            f"| {result.contract.name} | {len(result.rows)} | {status} | "
            f"`{result.contract.filename}` |"
        )
    lines.extend(["", "## Distributions", ""])
    for result in results:
        rows = result.rows
        lines.append(f"### {result.contract.name}")
        if result.contract.name == "routing":
            values = Counter(row["gold"]["intent"] for row in rows)
        elif result.contract.name == "grounding":
            values = Counter(row["source"] for row in rows)
        elif result.contract.name == "context":
            values = Counter(row["type"] for row in rows)
        elif result.contract.name == "calendar":
            values = Counter(row["category"] for row in rows)
        else:
            values = Counter(tag for row in rows for tag in row.get("tags", []))
        lines.append("")
        lines.append(", ".join(f"`{key}`={value}" for key, value in values.most_common()))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
