"""End-to-end agent eval: drives the *real* ``run_agent_workflow`` graph.

Unlike the retired YZ suite (retrieve -> complete_text side path) every task goes
through plan -> retrieve/sandbox/calendar -> reflect -> answer (+ grounding), so
routing, evidence and answer quality are measured on the production path.

Three layers are scored separately and a failing task is attributed to the first
layer that broke (routing -> evidence -> answer):

* routing  : executed tool set == ``route_gold.tools`` (when the task scores routing)
* evidence : gold documents cited / sandbox actually executed / calendar tool used
* answer   : ``judges.judge_completion`` for the task's ``task_type``

Web is stubbed (deterministic, no network); sandbox, MinIO, Qdrant, DB are real.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents.harness import gateway
from app.agents.harness.types import ToolResult
from app.core.config import settings
from app.core.db import SessionLocal, init_db, set_rls_context
from app.core.llm import Role, Spoken, complete_text
from app.eval.judges import Verdict, judge_completion
from app.eval.stats import bootstrap_ci
from app.eval.yz_fullchain import (
    YZ_EVAL_USER_ID,
    YZ_FIXTURES,
    clear_yz_corpus,
    seed_yz_corpus,
)

logger = logging.getLogger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "agent_tasks.jsonl"

# Task types that actually exercise retrieval / computation; chat, calendar and
# refuse are close to free passes and are reported separately.
CORE_TASK_TYPES = ("fact_qa", "compute", "multi_tool", "no_tool")

_FILENAME_TO_KEY = {name: key for key, name in YZ_FIXTURES.items()}


@dataclass
class TaskResult:
    task_id: str
    query: str
    task_type: str
    multiturn: bool
    answer: str = ""
    executed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    cited_doc_keys: list[str] = field(default_factory=list)
    gold_tools: list[str] | None = None
    routing_ok: bool | None = None
    evidence_ok: bool | None = None
    completion_ok: bool = False
    verdict: dict[str, Any] = field(default_factory=dict)
    failed_layer: str | None = None  # routing | evidence | answer | workflow_error | infra_error
    latency_s: float = 0.0
    prompt_tokens: int | None = None
    error: str = ""
    thinking_tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "query": self.query,
            "task_type": self.task_type,
            "multiturn": self.multiturn,
            "answer": self.answer[:600],
            "executed_tools": self.executed_tools,
            "denied_tools": self.denied_tools,
            "cited_doc_keys": self.cited_doc_keys,
            "gold_tools": self.gold_tools,
            "routing_ok": self.routing_ok,
            "evidence_ok": self.evidence_ok,
            "completion_ok": self.completion_ok,
            "verdict": self.verdict,
            "failed_layer": self.failed_layer,
            "latency_s": round(self.latency_s, 2),
            "prompt_tokens": self.prompt_tokens,
            "error": self.error,
            "thinking_tail": self.thinking_tail,
        }


def load_tasks(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_DATASET
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def stratified_sample(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Round-robin over (task_type, multiturn) strata so a smoke slice covers every kind of task."""
    if limit >= len(items):
        return list(items)
    strata: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for it in items:
        strata.setdefault((it["task_type"], bool(it.get("history"))), []).append(it)
    out: list[dict[str, Any]] = []
    queues = [list(v) for _, v in sorted(strata.items(), key=lambda kv: (kv[0][0], kv[0][1]))]
    while len(out) < limit and any(queues):
        for q in queues:
            if q and len(out) < limit:
                out.append(q.pop(0))
    return out


@contextmanager
def trace_gateway(trace: list[dict[str, Any]]):
    """Record every governed tool call; replace ``web`` with a deterministic stub."""
    original = gateway.invoke

    def _wrapped(tool_name, ctx, args=None):
        tool = (tool_name or "").strip()
        if tool == "web":
            result = ToolResult(
                ok=True,
                evidence=[
                    {
                        "index": 0,
                        "source_type": "web",
                        "tool": "web",
                        "filename": "eval-web-stub",
                        "title": "评测联网桩",
                        "snippet": "评测环境未联网，无可用搜索结果。",
                        "content": "评测环境未联网，无可用搜索结果。",
                        "url": "https://eval.example/web-stub",
                    }
                ],
                thinking_steps=["联网搜索（评测桩）"],
            )
        else:
            result = original(tool_name, ctx, args)
        trace.append(
            {
                "tool": tool,
                "denied": bool(getattr(result, "denied", False)),
                "ok": bool(getattr(result, "ok", True)),
                "has_summary": getattr(result, "analysis_summary", None) is not None,
                "uncomputable": bool(getattr(result, "analysis_uncomputable", False)),
                "schedule_card": getattr(result, "schedule_card", None) is not None,
            }
        )
        return result

    gateway.invoke = _wrapped  # type: ignore[assignment]
    try:
        yield
    finally:
        gateway.invoke = original  # type: ignore[assignment]


def _cited_doc_keys(citations: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for c in citations or []:
        key = _FILENAME_TO_KEY.get(str(c.get("filename") or ""))
        if key and key not in keys:
            keys.append(key)
    return keys


def _task_context(task: dict[str, Any]) -> dict[str, Any]:
    """Availability switches for one task (mirrors ``contracts.AgentContext``)."""
    ctx = task.get("context") or {}
    return {
        "has_kb_docs": bool(ctx.get("has_kb_docs", True)),
        "has_tabular_docs": bool(ctx.get("has_tabular_docs", True)),
        "document_keys": list(ctx.get("document_keys") or []),
        "pending_calendar": ctx.get("pending_calendar"),
    }


def _resolve_document_ids(keys: list[str], key_to_id: dict[str, int]) -> list[int] | None:
    ids = [key_to_id[k] for k in keys if k in key_to_id]
    return ids or None


def workflow_kwargs(task: dict[str, Any], document_ids: list[int] | None) -> dict[str, Any]:
    """``run_agent_workflow`` keyword arguments for one task.

    Split out so the availability switches, pending-calendar short circuit and
    document scoping can be unit-tested without a database or a live model.
    """
    ctx = _task_context(task)
    return {
        "message": task["query"],
        "history": list(task.get("history") or []),
        "use_kb": False,
        "has_kb_docs": ctx["has_kb_docs"],
        "has_tabular_docs": ctx["has_tabular_docs"],
        "document_ids": document_ids,
        "user_id": YZ_EVAL_USER_ID,
        "pending_calendar": ctx["pending_calendar"],
    }


def _infra_from(exc: BaseException) -> BaseException | None:
    """Walk cause/context so a graph wrapper does not hide a model timeout.

    Router rate-limit / timeout is ``RouterInfraError`` (an ``LLMInfraError``).
    A bare provider timeout with the same type name is infra too, so it leaves
    ``core_completion_rate`` instead of counting as ``workflow_error``.
    """
    from app.core.llm import is_llm_infra_exception

    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if is_llm_infra_exception(cur):
            return cur
        cur = cur.__cause__ or cur.__context__
    return None


def run_task(
    db,
    task: dict[str, Any],
    *,
    retries: int = 4,
    key_to_id: dict[str, int] | None = None,
) -> TaskResult:
    from app.agents.workflow import run_agent_workflow

    ctx = _task_context(task)
    res = TaskResult(
        task_id=task["id"],
        query=task["query"],
        task_type=task["task_type"],
        multiturn=bool(task.get("history")),
    )
    route_gold = task.get("route_gold")
    res.gold_tools = sorted(route_gold["tools"]) if route_gold else None
    document_ids = _resolve_document_ids(ctx["document_keys"], key_to_id or {})
    if ctx["document_keys"] and document_ids is None:
        res.failed_layer = "workflow_error"
        res.error = f"unknown document keys: {ctx['document_keys']}"
        return res
    trace: list[dict[str, Any]] = []
    t0 = time.monotonic()
    try:
        with trace_gateway(trace):
            out = run_agent_workflow(db, **workflow_kwargs(task, document_ids))
        answer = out.get("direct_answer") or ""
        if not answer and out.get("llm_messages"):
            # With usage, so the report can attribute token cost per question.
            spoken = complete_text(
                out["llm_messages"],
                role=Role.CHAT,
                retries=retries,
                raise_on_error=True,
            )
            if not isinstance(spoken, Spoken):
                spoken = Spoken(str(spoken or ""), "", None)
            answer = spoken.content
            res.prompt_tokens = spoken.prompt_tokens
        res.answer = answer or ""
        res.thinking_tail = list(out.get("thinking_steps") or [])[-4:]
        citations = out.get("citations") or []
    except Exception as exc:  # noqa: BLE001 - one broken task must not kill the run
        infra = _infra_from(exc)
        if infra is not None:
            res.failed_layer = "infra_error"
            res.error = str(infra)[:200]
        else:
            res.failed_layer = "workflow_error"
            res.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        res.latency_s = time.monotonic() - t0
        return res
    res.latency_s = time.monotonic() - t0

    executed: list[str] = []
    denied: list[str] = []
    for ev in trace:
        name = ev["tool"]
        if ev["denied"]:
            denied.append(name)
        elif name and name not in executed:
            executed.append(name)
    res.executed_tools = sorted(executed)
    res.denied_tools = sorted(set(denied))
    res.cited_doc_keys = _cited_doc_keys(citations)

    sandbox_runs = [e for e in trace if e["tool"] == "sandbox" and not e["denied"]]
    sandbox_executed = bool(sandbox_runs) and any(e["has_summary"] for e in sandbox_runs)
    calendar_used = any(e["tool"] == "calendar" and not e["denied"] for e in trace)

    # --- layer 1: routing ---
    if route_gold is not None:
        res.routing_ok = set(res.executed_tools) == set(route_gold["tools"])

    # --- layer 2: evidence ---
    tt = task["task_type"]
    gold_docs = set(task.get("gold_doc_keys") or [])
    if tt == "fact_qa":
        cited_gold = bool(gold_docs & set(res.cited_doc_keys))
        if ctx["document_keys"]:
            # Scoped task: the answer must cite a gold document inside the scope,
            # never fall back to "some sandbox run happened".
            res.evidence_ok = cited_gold
        else:
            res.evidence_ok = cited_gold or sandbox_executed
    elif tt == "compute":
        res.evidence_ok = sandbox_executed
    elif tt == "multi_tool":
        res.evidence_ok = sandbox_executed and bool(set(res.cited_doc_keys))
    elif tt == "calendar":
        res.evidence_ok = calendar_used
    # refuse / chat / no_tool: no evidence requirement

    # --- layer 3: answer ---
    verdict: Verdict = judge_completion(
        tt,
        res.answer,
        answer_spec=task.get("answer_spec"),
        routing_ok=res.routing_ok,
        cited_any=bool(res.cited_doc_keys or citations),
        sandbox_executed=(
            sandbox_executed
            if tt in ("compute", "multi_tool", "no_tool")
            else None
        ),
    )
    res.verdict = verdict.to_dict()
    res.completion_ok = verdict.ok

    if res.completion_ok:
        res.failed_layer = None
    elif res.routing_ok is False:
        res.failed_layer = "routing"
    elif res.evidence_ok is False:
        res.failed_layer = "evidence"
    else:
        res.failed_layer = "answer"
    return res


def aggregate(results: list[TaskResult], *, seed: int = 0) -> dict[str, Any]:
    infra = [r for r in results if r.failed_layer == "infra_error"]
    scored = [r for r in results if r.failed_layer != "infra_error"]
    n = len(scored)
    completion = [1.0 if r.completion_ok else 0.0 for r in scored]

    by_type: dict[str, dict[str, Any]] = {}
    for tt in sorted({r.task_type for r in scored}):
        rows = [r for r in scored if r.task_type == tt]
        by_type[tt] = {
            "n": len(rows),
            "completion_rate": round(sum(r.completion_ok for r in rows) / len(rows), 4),
        }
    mt = [r for r in scored if r.multiturn]
    st = [r for r in scored if not r.multiturn]
    # chat / calendar / refuse are nearly free passes; gate on the core types so
    # the headline number cannot ride on them.
    core = [r for r in scored if r.task_type in CORE_TASK_TYPES]

    routed = [r for r in scored if r.routing_ok is not None]
    evid = [r for r in scored if r.evidence_ok is not None]
    buckets: dict[str, int] = {}
    for r in results:
        if r.failed_layer:
            buckets[r.failed_layer] = buckets.get(r.failed_layer, 0) + 1

    total = len(results)
    return {
        "n": len(results),
        "n_scored": n,
        "n_infra_error": len(infra),
        "infra_error_rate": round(len(infra) / total, 4) if total else 0.0,
        "task_completion_rate": round(sum(completion) / n, 4) if n else 0.0,
        "task_completion_ci95": bootstrap_ci(completion, seed=seed),
        "core_completion_rate": (
            round(sum(r.completion_ok for r in core) / len(core), 4) if core else None
        ),
        "core_n": len(core),
        "by_task_type": by_type,
        "multiturn_completion_rate": round(sum(r.completion_ok for r in mt) / len(mt), 4) if mt else None,
        "singleturn_completion_rate": round(sum(r.completion_ok for r in st) / len(st), 4) if st else None,
        "routing": {
            "n": len(routed),
            "tool_exact_match": round(sum(r.routing_ok for r in routed) / len(routed), 4) if routed else None,
        },
        "evidence": {
            "n": len(evid),
            "evidence_rate": round(sum(r.evidence_ok for r in evid) / len(evid), 4) if evid else None,
        },
        "failure_layers": buckets,
        "latency_s_mean": round(sum(r.latency_s for r in results) / len(results), 2) if results else 0.0,
        "latency_s_p50": (
            round(sorted(r.latency_s for r in results)[len(results) // 2], 2) if results else 0.0
        ),
        "latency_s_max": round(max((r.latency_s for r in results), default=0.0), 2),
        # Only the fallback answer path reports usage; None means "not measured",
        # which is different from zero.
        "tokens_measured_n": sum(1 for r in results if r.prompt_tokens is not None),
        "prompt_tokens_total": sum(int(r.prompt_tokens or 0) for r in results),
    }


def run_agent_eval(
    tasks: list[dict[str, Any]] | None = None,
    *,
    dataset_path: str | Path | None = None,
    limit: int | None = None,
    skip_ingest: bool = False,
    seed: int = 0,
) -> tuple[dict[str, Any], list[TaskResult]]:
    items = list(tasks if tasks is not None else load_tasks(dataset_path))
    if limit is not None:
        items = stratified_sample(items, int(limit))

    # Reproducible: fixed "today", real hybrid retrieval + reranker like production.
    prev_today = os.environ.get("EVAL_TODAY")
    os.environ.setdefault("EVAL_TODAY", "2026-09-19")
    prev = (settings.rag_hybrid_enabled, settings.rag_rerank_enabled, settings.rag_rerank_provider)
    settings.rag_hybrid_enabled = True
    settings.rag_rerank_enabled = True

    init_db()
    db = SessionLocal()
    results: list[TaskResult] = []
    try:
        set_rls_context(db, YZ_EVAL_USER_ID)
        key_to_id: dict[str, int] = {}
        if not skip_ingest:
            key_to_id = seed_yz_corpus(db)
        for i, task in enumerate(items, start=1):
            r = run_task(db, task, key_to_id=key_to_id)
            results.append(r)
            flag = "OK " if r.completion_ok else f"FAIL[{r.failed_layer}]"
            print(f"[{i}/{len(items)}] {flag} {task['id']} ({task['task_type']}): {task['query'][:36]}", flush=True)
        report = aggregate(results, seed=seed)
        report["failures"] = [r.to_dict() for r in results if not r.completion_ok]
        return report, results
    finally:
        try:
            if not skip_ingest:
                clear_yz_corpus(db)
        finally:
            db.close()
            settings.rag_hybrid_enabled, settings.rag_rerank_enabled, settings.rag_rerank_provider = prev
            if prev_today is None:
                os.environ.pop("EVAL_TODAY", None)
            else:
                os.environ["EVAL_TODAY"] = prev_today


def format_text(report: dict[str, Any]) -> str:
    ci = report["task_completion_ci95"]
    lines = [
        f"=== Agent E2E Eval (n={report['n']}, scored={report['n_scored']}, infra_error={report['n_infra_error']}) ===",
        f"task_completion_rate  {report['task_completion_rate']:.2%}  95% CI [{ci['lo']:.2%}, {ci['hi']:.2%}]",
        f"  single-turn {report['singleturn_completion_rate']}   multi-turn {report['multiturn_completion_rate']}",
        f"routing tool_exact    {report['routing']['tool_exact_match']} (n={report['routing']['n']})",
        f"evidence rate         {report['evidence']['evidence_rate']} (n={report['evidence']['n']})",
        "by task_type: "
        + ", ".join(f"{k}={v['completion_rate']:.0%}(n={v['n']})" for k, v in report["by_task_type"].items()),
        f"failure layers: {report['failure_layers']}",
    ]
    for f in report.get("failures", [])[:15]:
        lines.append(f"  - {f['id']} [{f['failed_layer']}] {f['query'][:30]} -> {f['verdict'].get('reason') or f['error']}")
    return "\n".join(lines)
