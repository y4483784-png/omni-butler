"""
Tool routing / intent evaluation for Omni-Butler (strict LLM router).

Uses production ``plan_tools`` (same entry as ``workflow.plan_node``). Optional
disk cache avoids re-hitting the API on every eval run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from app.agents.router import (
    RouterError,
    intent_from_decision,
    plan_tools,
    router_failure_is_infra,
)
from app.eval.metrics import (
    confusion_matrix,
    multilabel_report,
    recall_at_k,
)

TOOL_LABELS = ("kb", "web", "calendar", "sandbox")
# Gold labels plus the sentinel ``error`` class so router crashes cannot hide in macro-F1.
INTENT_LABELS = ("chat", "rag", "web_search", "calendar", "data_analysis", "error")

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "office_tool_routing.jsonl"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "eval" / "router_cache.json"


class CacheMissError(RuntimeError):
    """Smoke replay requested but the cache has no entry for the current router fingerprint."""


@dataclass
class EvalCase:
    id: str
    message: str
    history: list[dict]
    context: dict[str, Any]
    gold: dict[str, Any]
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvalCase":
        return cls(
            id=str(raw["id"]),
            message=str(raw.get("message") or ""),
            history=list(raw.get("history") or []),
            context=dict(raw.get("context") or {}),
            gold=dict(raw.get("gold") or {}),
            tags=list(raw.get("tags") or []),
        )


@dataclass
class CaseResult:
    case_id: str
    message: str
    gold_intent: str
    pred_intent: str
    gold_tools: list[str]
    pred_tools: list[str]
    intent_ok: bool
    tools_exact_ok: bool
    tags: list[str]
    confidence: str | None = None
    error: str | None = None
    infra_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "message": self.message,
            "gold_intent": self.gold_intent,
            "pred_intent": self.pred_intent,
            "gold_tools": self.gold_tools,
            "pred_tools": self.pred_tools,
            "intent_ok": self.intent_ok,
            "tools_exact_ok": self.tools_exact_ok,
            "confidence": self.confidence,
            "error": self.error,
            "infra_error": self.infra_error,
            "tags": self.tags,
        }


@dataclass
class EvalReport:
    n: int
    intent_accuracy: float
    intent_macro_f1: float
    intent_confusion: dict[str, dict[str, int]]
    tools: dict[str, Any]
    trajectory_recall_at_k: dict[str, float]
    failures: list[CaseResult] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    intent_derived_accuracy: float = 0.0
    n_error: int = 0
    n_infra_error: int = 0
    infra_error_rate: float = 0.0
    n_scored: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_scored": self.n_scored,
            "n_error": self.n_error,
            "n_infra_error": self.n_infra_error,
            "infra_error_rate": round(self.infra_error_rate, 4),
            "intent": {
                "derived_accuracy": round(self.intent_derived_accuracy, 4),
                "accuracy": round(self.intent_accuracy, 4),
                "macro_f1": round(self.intent_macro_f1, 4),
                "confusion_matrix": self.intent_confusion,
            },
            "tools": self.tools,
            "trajectory_recall_at_k": {
                k: round(v, 4) for k, v in self.trajectory_recall_at_k.items()
            },
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "failures": [f.to_dict() for f in self.failures],
        }

    def format_text(self) -> str:
        lines = [
            f"=== Omni-Butler Tool Routing Eval (n={self.n}, scored={self.n_scored}) ===",
            f"  cache hits/misses  {self.cache_hits}/{self.cache_misses}",
            f"  infra errors       {self.n_infra_error} ({self.infra_error_rate:.2%})",
            "",
            "Intent (single-label)",
            f"  accuracy     {self.intent_accuracy:.2%}",
            f"  macro-F1     {self.intent_macro_f1:.2%}",
            "",
            "Tool set (multi-label, ordered queue as set)",
            f"  exact-match  {self.tools.get('exact_match_accuracy', 0):.2%}",
            f"  micro-F1     {self.tools.get('micro', {}).get('f1', 0):.2%}",
            f"  macro-F1     {self.tools.get('macro', {}).get('f1', 0):.2%}",
            f"  hamming loss {self.tools.get('hamming_loss', 0):.4f} (lower better)",
        ]
        for k, v in self.trajectory_recall_at_k.items():
            lines.append(f"  recall@{k}     {v:.2%}")
        if self.failures:
            lines.extend(["", f"Failures ({len(self.failures)}):"])
            for f in self.failures[:15]:
                err = f" err={f.error}" if f.error else ""
                lines.append(
                    f"  - {f.case_id}: intent {f.pred_intent}!={f.gold_intent} "
                    f"tools {f.pred_tools}!={f.gold_tools}{err}"
                )
            if len(self.failures) > 15:
                lines.append(f"  ... +{len(self.failures) - 15} more")
        return "\n".join(lines)


def load_cases(path: str | Path | None = None) -> list[EvalCase]:
    p = Path(path) if path else DEFAULT_DATASET
    cases: list[EvalCase] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(EvalCase.from_dict(json.loads(line)))
    return cases


def _ctx(case: EvalCase) -> dict[str, Any]:
    c = case.context
    return {
        "has_kb_docs": bool(c.get("has_kb_docs", False)),
        "has_tabular_docs": bool(c.get("has_tabular_docs", False)),
        "forced_kb": bool(c.get("forced_kb", False)),
        "use_kb": bool(c.get("use_kb", False)),
        "pending_calendar": c.get("pending_calendar"),
        "document_ids": c.get("document_ids"),
    }


def router_fingerprint() -> dict[str, Any]:
    """Model + prompt identity: a changed router must never hit a stale cache entry."""
    from app.agents import router as router_mod
    from app.core.llm import resolved_router_model

    return {
        "model": resolved_router_model(),
        "prompt_sha": router_mod.router_prompt_sha(),
        "cache_version": getattr(router_mod, "_ROUTER_CACHE_VERSION", 0),
    }


def _cache_key(case: EvalCase) -> str:
    ctx = _ctx(case)
    payload = {
        "fp": router_fingerprint(),
        "message": case.message,
        "history": case.history,
        "has_kb_docs": ctx["has_kb_docs"] or ctx["forced_kb"] or ctx["use_kb"],
        "has_tabular_docs": ctx["has_tabular_docs"],
        "forced_kb": ctx["forced_kb"] or ctx["use_kb"],
        "document_ids": ctx.get("document_ids"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def predict_case(
    case: EvalCase,
    *,
    cache: dict[str, Any] | None = None,
    refresh_cache: bool = False,
    require_cache: bool = False,
) -> CaseResult:
    """Single ``plan_tools()`` call produces both intent and tools (production path)."""
    ctx = _ctx(case)
    forced = ctx["forced_kb"] or ctx["use_kb"]
    has_kb = ctx["has_kb_docs"] or forced
    gold_intent = str(case.gold.get("intent") or "chat")
    gold_tools = list(case.gold.get("tools") or [])

    key = _cache_key(case)
    cached = None if refresh_cache or cache is None else cache.get(key)

    error: str | None = None
    confidence: str | None = None
    # Mirror workflow.plan_node: an open calendar slot-fill short-circuits the LLM router.
    pending_cal = ctx.get("pending_calendar")
    try:
        if pending_cal:
            decision_dict = {
                "needs_kb": False,
                "needs_web": False,
                "needs_calendar": True,
                "needs_sandbox": False,
                "confidence": "high",
            }
            # Short circuit, not a cache lookup: counting it as a hit inflated the
            # replay diagnostics by 24 rows.
            cache_used = False
        elif isinstance(cached, dict) and "needs_kb" in cached:
            decision_dict = cached
            cache_used = True
        else:
            if require_cache:
                raise CacheMissError(
                    f"{case.id}: router cache miss/stale (model or prompt changed); "
                    "run `routing --profile full` to refresh"
                )
            cache_used = False
            from app.agents.context.assembler import build_context

            bundle = build_context(history=case.history, pending_calendar=pending_cal)
            planned = plan_tools(
                case.message,
                bundle.router_history,
                has_kb_docs=has_kb,
                forced_kb=forced,
                has_tabular_docs=ctx["has_tabular_docs"],
                document_ids=ctx.get("document_ids"),
                context_line=bundle.context_line,
            )
            decision_dict = {
                "needs_kb": bool(planned.get("needs_kb")),
                "needs_web": bool(planned.get("needs_web")),
                "needs_calendar": bool(planned.get("needs_calendar")),
                "needs_sandbox": bool(planned.get("needs_sandbox")),
                "confidence": planned.get("confidence") or "",
            }
            if cache is not None:
                cache[key] = decision_dict

        pred_intent = intent_from_decision(decision_dict)
        pending: list[str] = []
        if decision_dict.get("needs_kb"):
            pending.append("kb")
        if decision_dict.get("needs_web"):
            pending.append("web")
        if decision_dict.get("needs_calendar"):
            pending.append("calendar")
        if decision_dict.get("needs_sandbox"):
            pending.append("sandbox")
        pred_tools = pending
        confidence = str(decision_dict.get("confidence") or "") or None
        # annotate for caller stats
        case._cache_hit = cache_used  # type: ignore[attr-defined]
    except RouterError as exc:
        # Schema misses use the sentinel ``error`` label so they cannot inflate
        # accuracy. Rate-limit / timeout are infra: they leave the denominator.
        infra = router_failure_is_infra(exc)
        pred_intent = "error"
        pred_tools = []
        error = str(exc)
        case._cache_hit = False  # type: ignore[attr-defined]
        return CaseResult(
            case_id=case.id,
            message=case.message,
            gold_intent=gold_intent,
            pred_intent=pred_intent,
            gold_tools=gold_tools,
            pred_tools=pred_tools,
            intent_ok=False,
            tools_exact_ok=False,
            tags=case.tags,
            confidence=confidence,
            error=error,
            infra_error=infra,
        )

    return CaseResult(
        case_id=case.id,
        message=case.message,
        gold_intent=gold_intent,
        pred_intent=pred_intent,
        gold_tools=gold_tools,
        pred_tools=pred_tools,
        intent_ok=pred_intent == gold_intent,
        tools_exact_ok=set(gold_tools) == set(pred_tools),
        tags=case.tags,
        confidence=confidence,
        error=error,
    )


def _intent_macro_f1_from_confusion(mat: dict[str, dict[str, int]], labels: Sequence[str]) -> float:
    f1s: list[float] = []
    for lab in labels:
        tp = mat.get(lab, {}).get(lab, 0)
        fp = sum(mat.get(r, {}).get(lab, 0) for r in mat if r != lab)
        # every non-diagonal prediction counts, including the sentinel "error" class
        fn = sum(v for c, v in mat.get(lab, {}).items() if c != lab)
        from app.eval.metrics import f1_score, precision_score, recall_score

        p = precision_score(tp, fp)
        r = recall_score(tp, fn)
        f1s.append(f1_score(p, r))
    return sum(f1s) / len(labels) if labels else 0.0


def run_tool_routing_eval(
    cases: list[EvalCase] | None = None,
    *,
    dataset_path: str | Path | None = None,
    predictor: Callable[[EvalCase], CaseResult] | None = None,
    cache_path: str | Path | None = None,
    refresh_cache: bool = False,
    require_cache: bool = False,
) -> EvalReport:
    """Evaluate routing via production ``plan_tools`` (+ optional disk cache)."""
    items = cases if cases is not None else load_cases(dataset_path)
    cpath = Path(cache_path) if cache_path else DEFAULT_CACHE
    cache = {} if refresh_cache else _load_cache(cpath)
    hits = misses = 0

    def _predict(c: EvalCase) -> CaseResult:
        nonlocal hits, misses
        r = predict_case(c, cache=cache, refresh_cache=refresh_cache, require_cache=require_cache)
        if getattr(c, "_cache_hit", False):
            hits += 1
        else:
            misses += 1
        return r

    predict = predictor or _predict
    results = [predict(c) for c in items]
    if predictor is None:
        _save_cache(cpath, cache)

    # Infra (rate-limit / timeout) is not a routing miss. Schema failures stay
    # in the denominator as the sentinel ``error`` intent.
    scored_rows = [r for r in results if not r.infra_error]
    n_infra = sum(1 for r in results if r.infra_error)
    if not results:
        intent_acc = 1.0
        intent_derived_acc = 1.0
    elif not scored_rows:
        # Every row was transport failure. Do not report a vacuous 1.0.
        intent_acc = 0.0
        intent_derived_acc = 0.0
    else:
        intent_acc = sum(r.intent_ok for r in scored_rows) / len(scored_rows)
        derived_ok = sum(
            1
            for r in scored_rows
            if r.pred_intent == intent_from_decision(
                {f"needs_{t}": True for t in r.gold_tools}
            )
        )
        intent_derived_acc = derived_ok / len(scored_rows)

    gold_intents = [r.gold_intent for r in scored_rows]
    pred_intents = [r.pred_intent for r in scored_rows]
    intent_cm = confusion_matrix(gold_intents, pred_intents, labels=INTENT_LABELS)
    intent_macro_f1 = _intent_macro_f1_from_confusion(intent_cm, INTENT_LABELS)

    gold_sets = [set(r.gold_tools) for r in scored_rows]
    pred_sets = [set(r.pred_tools) for r in scored_rows]
    tools_rep = multilabel_report(gold_sets, pred_sets, labels=TOOL_LABELS)
    tools_dict = tools_rep.to_dict()
    if results and not scored_rows:
        tools_dict["exact_match_accuracy"] = 0.0

    # Diagnostic only: queue order is fixed by code and empty-gold rows would score 1.0.
    traj = {}
    traj_rows = [r for r in scored_rows if r.gold_tools]
    for k in (1, 2, 3, 4):
        traj[f"k={k}"] = (
            sum(recall_at_k(r.gold_tools, r.pred_tools, k) for r in traj_rows) / len(traj_rows)
            if traj_rows
            else 1.0
        )

    failures = [r for r in results if not r.intent_ok or not r.tools_exact_ok or r.error]

    return EvalReport(
        n=len(results),
        intent_accuracy=intent_acc,
        intent_macro_f1=intent_macro_f1,
        intent_confusion=intent_cm,
        tools=tools_dict,
        trajectory_recall_at_k=traj,
        failures=failures,
        cache_hits=hits,
        cache_misses=misses,
        intent_derived_accuracy=intent_derived_acc,
        n_error=sum(1 for r in results if r.error and not r.infra_error),
        n_infra_error=n_infra,
        infra_error_rate=(n_infra / len(results)) if results else 0.0,
        n_scored=len(scored_rows),
    )
