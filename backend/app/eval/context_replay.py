"""ContextManager session-replay evaluation (deterministic + optional live LLM)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from app.agents.context.assembler import ContextBundle, build_context, compose_answer_messages
from app.agents.context.budget import estimate_messages_tokens
from app.agents.context.summary import refresh_session_context
from app.agents.context.window import to_logical_turns
from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.eval.tenant import activate_eval_tenant
from app.models.models import Message
from app.models.models import Session as ChatSession

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "context_recall.jsonl"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "eval" / "context_cache.json"
DEFAULT_BASELINE = Path(__file__).resolve().parents[2] / "data" / "eval" / "context_eval_baseline.json"
DEFAULT_REPORT = Path(__file__).resolve().parents[2] / "reports" / "context_eval_latest.json"

ARMS = ("legacy", "window", "window_summary", "full", "oracle")
CTX_EVAL_USER_ID = 92003
CTX_EVAL_EXTERNAL_ID = "ctx_eval_92003"
_CITATION_RE = re.compile(r"\[(\d+)\]")
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?%?")


def _safe_div(n: float, d: float) -> float:
    return n / d if d else 0.0


def normalize_key(text: str) -> str:
    s = (text or "").strip().lower().replace(",", "").replace("，", "")
    s = s.replace("％", "%").replace(" ", "")
    if s.endswith("%"):
        try:
            return str(float(s[:-1]) / 100.0)
        except ValueError:
            return s
    # 八万 / 8万 rough fold
    s = s.replace("八万", "80000").replace("8万", "80000")
    s = s.replace("一百二十万", "1200000").replace("120万", "1200000")
    s = s.replace("八千", "8000")
    return s


def key_in_text(key: str, text: str) -> bool:
    if not key:
        return False
    blob = text or ""
    nk = normalize_key(key)
    nt = normalize_key(blob)
    if nk and nk in nt:
        return True
    # raw substring
    if key in blob:
        return True
    # numeric soft match
    try:
        kv = float(nk)
    except ValueError:
        return False
    for m in _NUM_RE.finditer(blob):
        raw = m.group(0)
        try:
            if raw.endswith("%"):
                vv = float(raw[:-1]) / 100.0
            else:
                vv = float(raw)
        except ValueError:
            continue
        if abs(vv - kv) <= max(1e-6, abs(kv) * 0.01):
            return True
    return False


def needle_presence(messages: list[dict], gold_keys: Sequence[str]) -> float:
    if not gold_keys:
        return 1.0
    blob = "\n".join(str(m.get("content") or "") for m in messages)
    hits = sum(1 for k in gold_keys if key_in_text(str(k), blob))
    return hits / len(gold_keys)


def entity_retention(summary: str, gold_keys: Sequence[str]) -> float | None:
    if not gold_keys or not (summary or "").strip():
        return None
    return needle_presence([{"content": summary}], gold_keys)


def pending_retention(working_block: str, pending: dict | None) -> float | None:
    if not pending:
        return None
    title = str(pending.get("title") or "")
    if not title:
        return None
    ok = title in (working_block or "")
    missing = pending.get("missing_fields") or []
    # Chinese labels may appear instead of field keys; require title at minimum.
    if missing and ok:
        # soft: title present is enough for retention of draft identity
        return 1.0
    return 1.0 if ok else 0.0


def check_invariants(messages: list[dict], *, message: str, has_system: bool) -> list[str]:
    errs: list[str] = []
    if not messages:
        return ["empty_messages"]
    if messages[-1].get("role") != "user" or messages[-1].get("content") != message:
        errs.append("latest_user_missing")
    if has_system and messages[0].get("role") != "system":
        errs.append("system_missing")
    body = [m for m in messages if m.get("role") in ("user", "assistant")]
    hist = body[:-1]
    turns = to_logical_turns(hist)
    for t in turns:
        if not t or t[0].get("role") != "user":
            errs.append("half_turn")
            break
    return errs


def token_f1(pred: str, gold: str) -> float:
    def toks(s: str) -> set[str]:
        s = normalize_key(s)
        if not s:
            return set()
        # char bigrams for CJK-ish text + split alnum
        parts = re.findall(r"[a-z0-9%.]+|[\u4e00-\u9fff]", s)
        return set(parts)

    p, g = toks(pred), toks(gold)
    if not g and not p:
        return 1.0
    if not g or not p:
        return 0.0
    inter = len(p & g)
    prec = _safe_div(inter, len(p))
    rec = _safe_div(inter, len(g))
    return _safe_div(2 * prec * rec, prec + rec)


JUDGE_PROMPTS = {
    "strict": (
        "你是判分器。给定问题、标准答案、模型回答。"
        "若回答包含标准答案或与其等价（含数字百分数等价），输出 "
        '{"correct":true,"reason":"..."}; 否则 {"correct":false,"reason":"..."}。'
        "只输出 JSON。"
    ),
    "update": (
        "你是判分器。这是知识更新题：标准答案是最新值。"
        "若回答包含最新正确答案即可判对，即使同时提到旧值。"
        '输出 {"correct":true/false,"reason":"..."}。只输出 JSON。'
    ),
    "temporal": (
        "你是判分器。这是时序题。若回答与标准答案等价，或天数/周数差一天，仍可判对。"
        '输出 {"correct":true/false,"reason":"..."}。只输出 JSON。'
    ),
    "abstain": (
        "你是判分器。标准行为是拒绝编造：回答应表明未提及/不知道/无法从对话得知。"
        "若模型编造了具体事实则判错。"
        '输出 {"correct":true/false,"reason":"..."}。只输出 JSON。'
    ),
    "rubric": (
        "你是判分器。标准答案是偏好要点。只要回答正确回忆并利用该偏好即可判对，不必覆盖所有措辞。"
        '输出 {"correct":true/false,"reason":"..."}。只输出 JSON。'
    ),
}


def score_live_answer(
    messages: list[dict],
    *,
    question: str,
    gold: str,
    judge_mode: str,
) -> tuple[str, int | None, float | None, float | None, bool, str]:
    """Return answer, prompt tokens, fact_recall, token_f1, infra_error, reason.

    A rate-limit or timeout is infra: fact_recall stays unset so an empty draft
    is not stored as a wrong answer. Invalid judge JSON still falls back inside
    ``judge_answer``; only transport failures raise.
    """
    from app.core.llm import LLMInfraError, Role, complete_json, complete_text

    try:
        spoken = complete_text(
            messages,
            role=Role.CHAT,
            raise_on_error=True,
        )
        answer = spoken.content
        actual = spoken.prompt_tokens
        ok, _reason = judge_answer(
            mode=judge_mode,
            question=question,
            gold=gold,
            hypothesis=answer or "",
            complete_json_fn=lambda msgs: complete_json(
                msgs, role=Role.JUDGE, raise_on_error=True
            ),
        )
        return answer or "", actual, (1.0 if ok else 0.0), token_f1(answer or "", gold), False, ""
    except LLMInfraError as exc:
        return "", None, None, None, True, str(exc)[:200]


def judge_answer(
    *,
    mode: str,
    question: str,
    gold: str,
    hypothesis: str,
    complete_json_fn: Callable[..., dict | None],
) -> tuple[bool, str]:
    system = JUDGE_PROMPTS.get(mode) or JUDGE_PROMPTS["strict"]
    parsed = complete_json_fn(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"问题：{question}\n标准答案：{gold}\n模型回答：{hypothesis}",
            },
        ]
    )
    if not isinstance(parsed, dict):
        # deterministic fallback
        if mode == "abstain":
            ok = any(k in (hypothesis or "") for k in ("未提及", "不知道", "没有", "无法"))
            return ok, "fallback_abstain"
        ok = needle_presence([{"content": hypothesis or ""}], [gold]) >= 0.34
        return ok, "fallback_keys"
    return bool(parsed.get("correct")), str(parsed.get("reason") or "")


def extractive_summarizer(prev_summary: str, overflow_msgs: list[dict], *, max_chars: int | None = None) -> str | None:
    budget = int(max_chars if max_chars is not None else settings.context_summary_max_chars)
    parts = []
    if (prev_summary or "").strip():
        parts.append(prev_summary.strip())
    for m in overflow_msgs:
        role = "用户" if m.get("role") == "user" else "助手"
        content = str(m.get("content") or "")[:120]
        parts.append(f"{role}：{content}")
    text = "；".join(parts)
    if len(text) > budget:
        text = text[:budget]
    return text or None


def overflow_needed_but_failed(session: ChatSession, before: str, upto_before: int) -> bool:
    """Heuristic: summary call counted as failure when coverage did not advance and text empty."""
    upto = int(getattr(session, "context_summary_upto_message_id", 0) or 0)
    text = str(getattr(session, "context_summary", "") or "")
    if upto <= upto_before and not text and before:
        return True
    return False


@contextmanager
def arm_settings(arm: str) -> Iterator[None]:
    """Temporarily override settings for an evaluation arm."""
    snaps: dict[str, Any] = {}

    def _set(name: str, value: Any) -> None:
        snaps[name] = getattr(settings, name)
        setattr(settings, name, value)

    try:
        if arm == "legacy":
            _set("context_manager_enabled", False)
        elif arm == "window":
            _set("context_manager_enabled", True)
            _set("context_summary_enabled", False)
            _set("context_working_state_max_chars", 0)
        elif arm == "window_summary":
            _set("context_manager_enabled", True)
            _set("context_summary_enabled", True)
            _set("context_working_state_max_chars", 0)
        elif arm == "full":
            _set("context_manager_enabled", True)
            _set("context_summary_enabled", True)
            # Tight budget so extractive CI actually hits compact / emergency.
            _set("context_max_tokens", min(int(settings.context_max_tokens), 4096))
            _set("max_context_turns", min(int(settings.max_context_turns), 4))
        elif arm == "oracle":
            _set("context_manager_enabled", True)
            _set("context_summary_enabled", True)
            _set("max_context_turns", 10000)
            _set("context_max_tokens", 10**7)
        else:
            raise ValueError(f"unknown arm: {arm}")
        yield
    finally:
        for k, v in snaps.items():
            setattr(settings, k, v)


@dataclass
class CaseResult:
    case_id: str
    arm: str
    case_type: str
    bucket: int
    depth: int
    needle_presence: float
    pending_retention: float | None
    entity_retention: float | None
    invariant_violations: list[str]
    est_tokens: int
    compose_ms: float
    compose_level: str
    fact_recall: float | None = None
    token_f1: float | None = None
    actual_tokens: int | None = None
    answer: str | None = None
    skipped: bool = False
    skip_reason: str = ""
    summary_calls: int = 0
    summary_failures: int = 0
    infra_error: bool = False
    infra_error_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "arm": self.arm,
            "type": self.case_type,
            "bucket": self.bucket,
            "depth": self.depth,
            "needle_presence": round(self.needle_presence, 4),
            "pending_retention": None if self.pending_retention is None else round(self.pending_retention, 4),
            "entity_retention": None if self.entity_retention is None else round(self.entity_retention, 4),
            "invariant_violations": self.invariant_violations,
            "est_tokens": self.est_tokens,
            "compose_ms": round(self.compose_ms, 3),
            "compose_level": self.compose_level,
            "fact_recall": None if self.fact_recall is None else round(self.fact_recall, 4),
            "token_f1": None if self.token_f1 is None else round(self.token_f1, 4),
            "actual_tokens": self.actual_tokens,
            "answer": self.answer,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "summary_calls": self.summary_calls,
            "summary_failures": self.summary_failures,
            "infra_error": self.infra_error,
            "infra_error_reason": self.infra_error_reason,
        }


@dataclass
class EvalReport:
    n: int
    split: str
    mode: str
    arms: dict[str, Any] = field(default_factory=dict)
    invariants: dict[str, Any] = field(default_factory=dict)
    efficiency: dict[str, Any] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    results: list[CaseResult] = field(default_factory=list)
    # Which model answered and which one judged. Self-grading is recorded rather
    # than hidden, so a reviewer can discount the live numbers accordingly.
    answer_model: str = ""
    judge_model: str = ""
    self_judged: bool = False
    n_infra_error: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "split": self.split,
            "mode": self.mode,
            "arms": self.arms,
            "invariants": self.invariants,
            "efficiency": self.efficiency,
            "failures": self.failures,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "answer_model": self.answer_model,
            "judge_model": self.judge_model,
            "self_judged": self.self_judged,
            "n_infra_error": self.n_infra_error,
        }

    def format_text(self) -> str:
        lines = [
            f"=== ContextManager Eval (n={self.n} split={self.split} mode={self.mode}) ===",
            f"  cache hits/misses  {self.cache_hits}/{self.cache_misses}",
            f"  invariant violations {self.invariants.get('violations', 0)}",
        ]
        for arm, block in self.arms.items():
            ov = block.get("overall") or {}
            lines.append(
                f"  [{arm}] needle={ov.get('needle_presence', 0):.2%} "
                f"fact={ov.get('fact_recall')} "
                f"tokens={ov.get('est_tokens_mean')} "
                f"pending={ov.get('pending_retention')}"
            )
        if self.failures:
            lines.append(f"Failures ({len(self.failures)}):")
            for f in self.failures[:12]:
                lines.append(f"  - {f.get('id')} [{f.get('arm')}] {f.get('metric')}={f.get('got')}")
        return "\n".join(lines)


def load_cases(path: Path | None = None, *, split: str = "smoke") -> list[dict[str, Any]]:
    p = path or DEFAULT_DATASET
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if split == "smoke":
        return [r for r in rows if r.get("split") == "smoke"]
    if split == "all":
        return rows
    # full = entire dataset
    return rows


def _load_cache(path: Path) -> dict[str, Any]:
    from app.eval.disk_cache import load_or_drop_entries

    return load_or_drop_entries(path)


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    from app.eval.disk_cache import save_entries

    save_entries(path, data)


def _answer_model_name() -> str:
    from app.core.llm import Role, resolve

    return resolve(Role.CHAT).model


def _judge_model_name() -> str:
    from app.core.llm import Role, resolve

    return resolve(Role.JUDGE).model


def _cache_key(arm: str, messages: list[dict], question: str) -> str:
    from app.core.llm import Role, cache_identity
    from app.core.prompts import answer_rules

    chat = cache_identity(Role.CHAT)
    judge = cache_identity(Role.JUDGE)
    blob = json.dumps(
        {
            "arm": arm,
            "messages": messages,
            "q": question,
            "model": chat.model,
            "chat_thinking": chat.thinking,
            "judge_model": judge.model,
            "judge_thinking": judge.thinking,
            "prompt_sha": hashlib.sha256(answer_rules().encode("utf-8")).hexdigest()[:12],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _ensure_db() -> None:
    # CLI may set sqlite memory URL before importing engines; still call init_db.
    init_db()


def _pool_from_evidence(evidence: list[dict] | None) -> tuple[str, Callable[[int], tuple[str, list[dict]]] | None]:
    if not evidence:
        return "", None

    def rebuild(n: int) -> tuple[str, list[dict]]:
        lines = ["【证据池】"]
        included: list[dict] = []
        used = 0
        for e in evidence:
            line = f"[{e.get('index')}] {e.get('filename')}: {e.get('snippet')}"
            if used + len(line) > n and included:
                break
            lines.append(line)
            included.append(e)
            used += len(line) + 1
        return "\n".join(lines), included

    text, _ = rebuild(12000)
    return text, rebuild


def replay_case(
    case: dict[str, Any],
    *,
    arm: str,
    summarizer: str = "extractive",
    live: bool = False,
    refresh_every: int = 1,
    cache: dict[str, Any] | None = None,
    refresh_cache: bool = False,
) -> CaseResult:
    history_script = list(case.get("history") or [])
    pending = case.get("pending_calendar")
    probe = case.get("probe") or {}
    question = str(probe.get("question") or "")
    gold_keys = list(probe.get("gold_keys") or [])
    gold_answer = str(probe.get("gold_answer") or "")
    judge_mode = str(probe.get("judge_mode") or "strict")
    evidence = case.get("evidence_pool")

    summary_calls = 0
    summary_failures = 0

    with arm_settings(arm):
        db = SessionLocal()
        try:
            activate_eval_tenant(
                db,
                CTX_EVAL_USER_ID,
                external_id=CTX_EVAL_EXTERNAL_ID,
                name="Context Eval Fixture",
            )
            session = ChatSession(user_id=CTX_EVAL_USER_ID, title=f"eval-{case.get('id')}-{arm}")
            db.add(session)
            db.commit()
            db.refresh(session)

            # teacher-forced replay
            built: list[dict] = []
            turn_i = 0
            for i in range(0, len(history_script), 2):
                user_msg = history_script[i]
                asst_msg = history_script[i + 1] if i + 1 < len(history_script) else None
                db.add(
                    Message(
                        session_id=session.id,
                        role="user",
                        content=str(user_msg.get("content") or ""),
                    )
                )
                db.commit()
                built.append({"role": "user", "content": str(user_msg.get("content") or "")})
                if asst_msg:
                    db.add(
                        Message(
                            session_id=session.id,
                            role="assistant",
                            content=str(asst_msg.get("content") or ""),
                        )
                    )
                    db.commit()
                    built.append({"role": "assistant", "content": str(asst_msg.get("content") or "")})

                turn_i += 1
                if refresh_every > 0 and turn_i % refresh_every == 0:
                    from app.agents.context import summary as sm

                    if summarizer == "extractive" or (not live and summarizer != "live"):

                        def _wrap(prev_summary, overflow_msgs, max_chars=None):
                            nonlocal summary_calls, summary_failures
                            summary_calls += 1
                            out = extractive_summarizer(prev_summary, overflow_msgs, max_chars=max_chars)
                            if out is None:
                                summary_failures += 1
                            return out

                        old = sm.summarize_incremental
                        sm.summarize_incremental = _wrap  # type: ignore[assignment]
                        try:
                            refresh_session_context(db, session_id=session.id, user_id=CTX_EVAL_USER_ID)
                        finally:
                            sm.summarize_incremental = old  # type: ignore[assignment]
                    else:
                        before = session.context_summary or ""
                        upto_before = int(session.context_summary_upto_message_id or 0)
                        refresh_session_context(db, session_id=session.id, user_id=CTX_EVAL_USER_ID)
                        db.refresh(session)
                        summary_calls += 1
                        if overflow_needed_but_failed(session, before, upto_before):
                            summary_failures += 1

            db.refresh(session)
            pool_text, rebuild = _pool_from_evidence(evidence)
            t0 = time.perf_counter()
            bundle = build_context(
                session=session,
                history=built,
                message=question,
                pending_calendar=pending if isinstance(pending, dict) else None,
                memory_block="",
            )
            messages, pool_used, included = compose_answer_messages(
                bundle=bundle,
                hist=bundle.answer_history,
                message=question,
                pool_text=pool_text,
                memory_block="",
                rebuild_pool=rebuild,
                has_evidence_system=bool(evidence),
            )
            compose_ms = (time.perf_counter() - t0) * 1000.0
            est = estimate_messages_tokens(messages)
            skipped = False
            skip_reason = ""
            if arm == "oracle" and est > 128000:
                skipped = True
                skip_reason = "oracle_est_tokens>128k"

            has_system = bool(bundle.summary_block or bundle.working_block or evidence)
            inv = check_invariants(messages, message=question, has_system=has_system)
            np = needle_presence(messages, gold_keys)
            pr = pending_retention(bundle.working_block, pending if isinstance(pending, dict) else None)
            er = entity_retention(session.context_summary or "", gold_keys)

            fact = None
            tf1 = None
            actual = None
            answer = None
            cache_obj = cache if cache is not None else {}

            infra_error = False
            infra_reason = ""
            if live and not skipped:
                ck = _cache_key(arm, messages, question)
                if not refresh_cache and ck in cache_obj:
                    cached = cache_obj[ck]
                    answer = cached.get("answer")
                    actual = cached.get("actual_tokens")
                    fact = cached.get("fact_recall")
                    tf1 = cached.get("token_f1")
                    infra_error = bool(cached.get("infra_error"))
                    infra_reason = str(cached.get("infra_error_reason") or "")
                    if infra_error:
                        fact = None
                        tf1 = None
                else:
                    answer, actual, fact, tf1, infra_error, infra_reason = score_live_answer(
                        messages,
                        question=question,
                        gold=gold_answer,
                        judge_mode=judge_mode,
                    )
                    # Do not cache an infra miss as fact_recall=0. A later run with
                    # a healthy model must be allowed to score the turn.
                    if not infra_error:
                        cache_obj[ck] = {
                            "answer": answer,
                            "actual_tokens": actual,
                            "fact_recall": fact,
                            "token_f1": tf1,
                        }

            return CaseResult(
                case_id=str(case.get("id")),
                arm=arm,
                case_type=str(case.get("type") or ""),
                bucket=int(case.get("bucket_turns") or 0),
                depth=int(case.get("depth_pct") or 0),
                needle_presence=np,
                pending_retention=pr,
                entity_retention=er,
                invariant_violations=inv,
                est_tokens=est,
                compose_ms=compose_ms,
                compose_level=str((bundle.stats or {}).get("compose_level") or (bundle.stats or {}).get("level") or "safe"),
                fact_recall=fact,
                token_f1=tf1,
                actual_tokens=actual,
                answer=answer,
                skipped=skipped,
                skip_reason=skip_reason,
                summary_calls=summary_calls,
                summary_failures=summary_failures,
                infra_error=infra_error,
                infra_error_reason=infra_reason,
            )
        finally:
            db.close()


def _agg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _compacted(level: str) -> float:
    """1 when compose actually dropped into compact/emergency.

    ``warn`` only shrinks the evidence pool. Counting it as compaction made the
    rate look healthy while the tight-budget path never discarded turns.
    """
    return 1.0 if level in ("compact", "emergency") else 0.0


def _slice_report(results: list[CaseResult]) -> dict[str, Any]:
    active = [r for r in results if not r.skipped]
    overall = {
        "needle_presence": round(_agg([r.needle_presence for r in active]), 4),
        "pending_retention": round(
            _agg([r.pending_retention for r in active if r.pending_retention is not None]), 4
        )
        if any(r.pending_retention is not None for r in active)
        else None,
        "entity_retention": round(
            _agg([r.entity_retention for r in active if r.entity_retention is not None]), 4
        )
        if any(r.entity_retention is not None for r in active)
        else None,
        "fact_recall": round(_agg([r.fact_recall for r in active if r.fact_recall is not None]), 4)
        if any(r.fact_recall is not None for r in active)
        else None,
        "token_f1": round(_agg([r.token_f1 for r in active if r.token_f1 is not None]), 4)
        if any(r.token_f1 is not None for r in active)
        else None,
        "est_tokens_mean": round(_agg([float(r.est_tokens) for r in active]), 2),
        "compose_ms_p50": round(statistics.median([r.compose_ms for r in active]), 3) if active else 0.0,
        "compaction_rate": round(_agg([_compacted(r.compose_level) for r in active]), 4),
        "summary_rate": round(_agg([1.0 if r.summary_calls > 0 else 0.0 for r in active]), 4),
        "n": len(active),
        "skipped": sum(1 for r in results if r.skipped),
    }

    def by(key: str) -> dict[str, Any]:
        groups: dict[str, list[CaseResult]] = {}
        for r in active:
            groups.setdefault(str(getattr(r, key)), []).append(r)
        out = {}
        for g, rows in groups.items():
            out[g] = {
                "needle_presence": round(_agg([x.needle_presence for x in rows]), 4),
                "fact_recall": round(_agg([x.fact_recall for x in rows if x.fact_recall is not None]), 4)
                if any(x.fact_recall is not None for x in rows)
                else None,
                "n": len(rows),
            }
        return out

    return {
        "overall": overall,
        "by_bucket": by("bucket"),
        "by_type": by("case_type"),
        "by_depth": by("depth"),
    }


def run_context_eval(
    cases: Sequence[dict[str, Any]],
    *,
    arms: Sequence[str] = ("legacy", "full"),
    summarizer: str = "extractive",
    live: bool = False,
    refresh_every: int = 1,
    split: str = "smoke",
    cache_path: Path | None = None,
    refresh_cache: bool = False,
) -> EvalReport:
    _ensure_db()
    cache_file = cache_path or DEFAULT_CACHE
    cache = _load_cache(cache_file)
    hits = misses = 0
    all_results: list[CaseResult] = []

    for case in cases:
        for arm in arms:
            before = len(cache)
            # crude hit tracking for live only
            cr = replay_case(
                case,
                arm=arm,
                summarizer="live" if live and summarizer != "extractive" else summarizer,
                live=live,
                refresh_every=refresh_every,
                cache=cache,
                refresh_cache=refresh_cache,
            )
            if live:
                # if cache grew, miss; else maybe hit — approximate via size
                if len(cache) > before:
                    misses += 1
                else:
                    hits += 1
            all_results.append(cr)

    if live:
        _save_cache(cache_file, cache)

    arm_blocks = {}
    for arm in arms:
        arm_blocks[arm] = _slice_report([r for r in all_results if r.arm == arm])

    violations = [r for r in all_results if r.invariant_violations]
    failures: list[dict[str, Any]] = []
    for r in all_results:
        if r.invariant_violations:
            failures.append(
                {
                    "id": r.case_id,
                    "arm": r.arm,
                    "type": r.case_type,
                    "bucket": r.bucket,
                    "metric": "invariant",
                    "expected": [],
                    "got": r.invariant_violations,
                    "probe": None,
                    "answer": r.answer,
                }
            )
        if r.pending_retention is not None and r.pending_retention < 1.0:
            failures.append(
                {
                    "id": r.case_id,
                    "arm": r.arm,
                    "type": r.case_type,
                    "bucket": r.bucket,
                    "metric": "pending_retention",
                    "expected": 1.0,
                    "got": r.pending_retention,
                    "probe": None,
                    "answer": r.answer,
                }
            )
        if r.infra_error:
            failures.append(
                {
                    "id": r.case_id,
                    "arm": r.arm,
                    "type": r.case_type,
                    "bucket": r.bucket,
                    "metric": "infra_error",
                    "expected": "",
                    "got": r.infra_error_reason,
                    "probe": None,
                    "answer": r.answer,
                }
            )
        if live and r.fact_recall is not None and r.fact_recall < 0.5:
            failures.append(
                {
                    "id": r.case_id,
                    "arm": r.arm,
                    "type": r.case_type,
                    "bucket": r.bucket,
                    "metric": "fact_recall",
                    "expected": 1.0,
                    "got": r.fact_recall,
                    "probe": None,
                    "answer": r.answer,
                }
            )

    ests = [float(r.est_tokens) for r in all_results if not r.skipped]
    actuals = [float(r.actual_tokens) for r in all_results if r.actual_tokens is not None]
    err = None
    if actuals and ests:
        paired = [
            abs(r.est_tokens - (r.actual_tokens or 0)) / max(1, r.actual_tokens or 1)
            for r in all_results
            if r.actual_tokens
        ]
        err = round(_agg(paired) * 100.0, 2) if paired else None

    report = EvalReport(
        n=len(cases),
        split=split,
        mode="live" if live else "extractive",
        answer_model=_answer_model_name() if live else "",
        judge_model=_judge_model_name() if live else "",
        self_judged=bool(live and _judge_model_name() == _answer_model_name()),
        n_infra_error=sum(1 for r in all_results if r.infra_error),
        arms=arm_blocks,
        invariants={"violations": len(violations), "cases": [r.case_id for r in violations[:50]]},
        efficiency={
            "est_tokens_mean": round(_agg(ests), 2),
            "actual_tokens_mean": round(_agg(actuals), 2) if actuals else None,
            "est_error_pct": err,
            "compose_ms_p50": round(statistics.median([r.compose_ms for r in all_results]), 3) if all_results else 0,
            "summary_calls": sum(r.summary_calls for r in all_results),
            "summary_rate": round(
                _safe_div(sum(1 for r in all_results if r.summary_calls > 0), max(1, len(all_results))),
                4,
            ),
            "compose_compression": round(
                _agg([_compacted(r.compose_level) for r in all_results if not r.skipped]),
                4,
            ),
            "summary_fail_rate": round(
                _safe_div(sum(r.summary_failures for r in all_results), max(1, sum(r.summary_calls for r in all_results))),
                4,
            ),
        },
        failures=failures,
        cache_hits=hits,
        cache_misses=misses,
        results=all_results,
    )
    return report


def gate_report(report: EvalReport, baseline: dict[str, Any] | None) -> list[str]:
    """Return list of gate failure messages (empty = pass)."""
    fails: list[str] = []
    if int(report.invariants.get("violations") or 0) != 0:
        fails.append(f"invariant_violations={report.invariants.get('violations')}")

    full = (report.arms.get("full") or {}).get("overall") or {}
    if "full" in report.arms:
        pr = full.get("pending_retention")
        if pr is not None and float(pr) < 1.0 - 1e-9:
            fails.append(f"pending_retention={pr} < 1.0")
    # needle_presence is diagnostic only (reported, not gated).

    if not baseline:
        return fails

    base_split = str(baseline.get("split") or "")
    report_split = str(report.split or "")
    if base_split and report_split and base_split != report_split:
        # Numbers from another split are not a regression signal.
        return fails

    def _base(arm: str, metric: str) -> float | None:
        try:
            return float((((baseline.get("arms") or {}).get(arm) or {}).get("overall") or {}).get(metric))
        except (TypeError, ValueError):
            return None

    if report.mode == "live":
        for metric, tol in (("fact_recall", 0.05),):
            cur = full.get(metric)
            base = _base("full", metric)
            if cur is None or base is None:
                continue
            if float(cur) + 1e-9 < float(base) - tol:
                fails.append(f"{metric} {cur} < baseline {base} - {tol}")

    # token mean should not rise >10%
    cur_tok = report.efficiency.get("actual_tokens_mean")
    base_tok = (baseline.get("efficiency") or {}).get("actual_tokens_mean")
    if cur_tok is not None and base_tok:
        if float(cur_tok) > float(base_tok) * 1.10 + 1e-9:
            fails.append(f"prompt_tokens_actual_mean {cur_tok} > baseline {base_tok} * 1.10")

    sfr = report.efficiency.get("summary_fail_rate")
    if sfr is not None and float(sfr) > 0.05:
        fails.append(f"summary_fail_rate {sfr} > 0.05")

    return fails
