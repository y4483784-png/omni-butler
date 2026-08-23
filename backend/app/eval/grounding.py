"""Grounding faithfulness eval: fixed cases → ground_and_repair → ragas.

Discipline: report-only — failures are logged; gold labels are not rewritten
to match bad model behavior.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents.harness.critique import (
    CritiqueResult,
    ground_and_repair_answer,
    sandbox_number_mismatches,
    should_apply_sandbox_number_gate,
)
from app.agents.workflow import build_pool_prompt
from app.core.config import settings
from app.core.db import SessionLocal, init_db, set_rls_context
from app.core.llm import complete_text
from app.eval.ragas_judge import run_ragas_metrics
from app.eval.yz_fullchain import (
    YZ_EVAL_USER_ID,
    _hits_to_evidence,
    seed_yz_corpus,
)
from app.rag.retrieval import retrieve

logger = logging.getLogger(__name__)

DEFAULT_DATASET = (
    Path(__file__).resolve().parents[2] / "data" / "eval" / "grounding_faithfulness.jsonl"
)
DEFAULT_REPORT = (
    Path(__file__).resolve().parents[2] / "reports" / "grounding_eval_latest.json"
)

_DISCLAIMER_MARK = "依据核验说明"


@dataclass
class GroundingEvalCase:
    id: str
    source: str
    query: str
    gold_doc_keys: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)
    needs_sandbox: bool = False
    expect_unanswerable: bool = False
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GroundingEvalCase":
        return cls(
            id=str(raw["id"]),
            source=str(raw.get("source") or "kb"),
            query=str(raw.get("query") or ""),
            gold_doc_keys=list(raw.get("gold_doc_keys") or []),
            contexts=list(raw.get("contexts") or []),
            needs_sandbox=bool(raw.get("needs_sandbox", False)),
            expect_unanswerable=bool(raw.get("expect_unanswerable", False)),
            tags=list(raw.get("tags") or []),
        )


@dataclass
class CaseResult:
    case_id: str
    source: str
    query: str
    answer: str
    draft: str
    contexts: list[str]
    repaired: bool
    critique_passed: bool
    addresses_question: bool
    has_disclaimer: bool
    sandbox_gate: bool
    sandbox_rule_hits: int
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    draft_faithfulness: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "source": self.source,
            "query": self.query,
            "answer": (self.answer or "")[:500],
            "contexts_count": len(self.contexts),
            "repaired": self.repaired,
            "critique_passed": self.critique_passed,
            "addresses_question": self.addresses_question,
            "has_disclaimer": self.has_disclaimer,
            "sandbox_gate": self.sandbox_gate,
            "sandbox_rule_hits": self.sandbox_rule_hits,
            "faithfulness": self.faithfulness,
            "answer_relevancy": self.answer_relevancy,
            "draft_faithfulness": self.draft_faithfulness,
        }


@dataclass
class GroundingEvalReport:
    n: int
    by_source: dict[str, dict[str, Any]]
    faithfulness_mean: float | None
    answer_relevancy_mean: float | None
    repair_rate: float
    disclaimer_rate: float
    sandbox_rule_hit_rate: float
    addresses_fail_rate: float
    skipped_ragas: bool = False
    repair_enabled: bool = True
    failures: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "by_source": self.by_source,
            "faithfulness_mean": self.faithfulness_mean,
            "answer_relevancy_mean": self.answer_relevancy_mean,
            "repair_rate": round(self.repair_rate, 4),
            "disclaimer_rate": round(self.disclaimer_rate, 4),
            "sandbox_rule_hit_rate": round(self.sandbox_rule_hit_rate, 4),
            "addresses_fail_rate": round(self.addresses_fail_rate, 4),
            "skipped_ragas": self.skipped_ragas,
            "repair_enabled": self.repair_enabled,
            "failures": self.failures,
            "cases": self.cases,
        }

    def format_text(self) -> str:
        lines = [
            f"=== Grounding Faithfulness Eval (n={self.n}) ===",
            f"repair_enabled={self.repair_enabled}  skipped_ragas={self.skipped_ragas}",
            "",
            f"faithfulness_mean       {self.faithfulness_mean if self.faithfulness_mean is not None else 'n/a'}",
            f"answer_relevancy_mean   {self.answer_relevancy_mean if self.answer_relevancy_mean is not None else 'n/a'}",
            f"repair_rate             {self.repair_rate:.2%}",
            f"disclaimer_rate         {self.disclaimer_rate:.2%}",
            f"sandbox_rule_hit_rate   {self.sandbox_rule_hit_rate:.2%}",
            f"addresses_fail_rate     {self.addresses_fail_rate:.2%}",
            "",
            "By source:",
        ]
        for src, stats in sorted(self.by_source.items()):
            fm = stats.get("faithfulness_mean")
            lines.append(
                f"  {src}: n={stats.get('n')} "
                f"faithfulness={fm if fm is not None else 'n/a'} "
                f"repair={stats.get('repair_rate', 0):.2%} "
                f"disclaimer={stats.get('disclaimer_rate', 0):.2%}"
            )
        if self.failures:
            lines.extend(["", f"Failures ({len(self.failures)}):"])
            for f in self.failures[:20]:
                lines.append(
                    f"  - {f.get('id')}: faithfulness={f.get('faithfulness')} "
                    f"repaired={f.get('repaired')} {f.get('query', '')[:40]}"
                )
            if len(self.failures) > 20:
                lines.append(f"  ... +{len(self.failures) - 20} more")
        return "\n".join(lines)


def load_cases(path: str | Path | None = None) -> list[GroundingEvalCase]:
    p = Path(path) if path else DEFAULT_DATASET
    out: list[GroundingEvalCase] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(GroundingEvalCase.from_dict(json.loads(line)))
    return out


def write_report(report: GroundingEvalReport, path: str | Path | None = None) -> Path:
    p = Path(path) if path else DEFAULT_REPORT
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _frozen_evidence(source: str, contexts: list[str], *, title: str) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for i, text in enumerate(contexts, start=1):
        body = str(text or "").strip()
        evidence.append(
            {
                "index": i,
                "source_type": source,
                "tool": source,
                "filename": title,
                "title": title,
                "snippet": body[:240],
                "content": body,
                "url": f"https://eval.example/{source}/{i}" if source == "web" else "",
            }
        )
    return evidence


def build_evidence_for_case(
    case: GroundingEvalCase,
    *,
    db,
    top_k: int,
) -> list[dict[str, Any]]:
    if case.source == "kb":
        hits = retrieve(db, case.query, user_id=YZ_EVAL_USER_ID, top_k=top_k)
        return _hits_to_evidence(hits)
    if case.source == "web":
        return _frozen_evidence("web", case.contexts, title="冻结联网公告")
    if case.source == "sandbox":
        return _frozen_evidence("sandbox", case.contexts, title="冻结沙箱 SUMMARY")
    raise ValueError(f"unknown source: {case.source}")


def generate_grounded_answer(
    query: str,
    evidence: list[dict[str, Any]],
    *,
    needs_sandbox: bool = False,
    repair_enabled: bool = True,
) -> tuple[str, str, list[dict[str, Any]], CritiqueResult, bool, bool]:
    """Return final, draft, included, critique, repaired, sandbox_gate."""
    pool_text, included = build_pool_prompt(evidence)
    messages = [
        {"role": "system", "content": pool_text},
        {"role": "user", "content": query},
    ]
    if any(e.get("source_type") == "sandbox" for e in included or evidence):
        sandbox_note = (
            "【数据分析说明】沙箱已在隔离环境中对上传表格**全量**计算。"
            "数值、汇总与图表结论必须以【数据分析】证据中的 ===SUMMARY=== 或分组均值表为准；"
            "禁止根据 df.head() 或样例行估算，勿写「基于样本数据」。"
        )
        messages[0] = {
            "role": "system",
            "content": sandbox_note + "\n\n" + pool_text,
        }
    draft = complete_text(messages)
    sandbox_gate = should_apply_sandbox_number_gate(
        evidence=included or evidence,
        needs_sandbox=needs_sandbox,
    )
    final, critique, repaired = ground_and_repair_answer(
        question=query,
        draft=draft,
        evidence=evidence,
        included=included,
        messages=messages,
        sandbox_gate=sandbox_gate,
        repair_enabled=repair_enabled,
    )
    return final, draft, included, critique, repaired, sandbox_gate


def _mean(vals: list[float | None]) -> float | None:
    nums = [float(v) for v in vals if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _source_stats(case_results: list[CaseResult]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[CaseResult]] = {}
    for cr in case_results:
        by.setdefault(cr.source, []).append(cr)
    out: dict[str, dict[str, Any]] = {}
    for src, rows in by.items():
        n = len(rows)
        out[src] = {
            "n": n,
            "faithfulness_mean": _mean([r.faithfulness for r in rows]),
            "answer_relevancy_mean": _mean([r.answer_relevancy for r in rows]),
            "repair_rate": sum(1 for r in rows if r.repaired) / max(n, 1),
            "disclaimer_rate": sum(1 for r in rows if r.has_disclaimer) / max(n, 1),
            "sandbox_rule_hit_rate": sum(1 for r in rows if r.sandbox_rule_hits > 0) / max(n, 1),
            "addresses_fail_rate": sum(1 for r in rows if not r.addresses_question) / max(n, 1),
        }
    return out


def run_grounding_eval(
    cases: list[GroundingEvalCase] | None = None,
    *,
    dataset_path: str | Path | None = None,
    k: int | None = None,
    limit: int | None = None,
    skip_ragas: bool = False,
    skip_ingest: bool = False,
    repair_enabled: bool = True,
    score_drafts: bool = False,
    include_relevancy: bool = True,
) -> GroundingEvalReport:
    items = list(cases if cases is not None else load_cases(dataset_path))
    if limit is not None:
        items = items[: int(limit)]

    top_k = int(k if k is not None else settings.rag_top_k)
    need_kb = any(c.source == "kb" for c in items)

    init_db()
    db = SessionLocal()
    case_results: list[CaseResult] = []
    try:
        set_rls_context(db, YZ_EVAL_USER_ID)
        if need_kb and not skip_ingest:
            seed_yz_corpus(db)
        elif need_kb and skip_ingest:
            # Ensure corpus exists; seed if empty
            from app.models.models import Document

            docs = db.query(Document).filter(Document.user_id == YZ_EVAL_USER_ID).all()
            if not docs:
                seed_yz_corpus(db)

        for i, case in enumerate(items, start=1):
            print(f"[{i}/{len(items)}] {case.id}: {case.query[:40]}", flush=True)
            evidence = build_evidence_for_case(case, db=db, top_k=top_k)
            final, draft, included, critique, repaired, sandbox_gate = generate_grounded_answer(
                case.query,
                evidence,
                needs_sandbox=case.needs_sandbox,
                repair_enabled=repair_enabled,
            )
            contexts = [str(e.get("content") or e.get("snippet") or "") for e in included]
            if not contexts and case.contexts:
                contexts = list(case.contexts)
            rule_hits = sandbox_number_mismatches(
                final,
                evidence,
                sandbox_gate=sandbox_gate,
            )
            case_results.append(
                CaseResult(
                    case_id=case.id,
                    source=case.source,
                    query=case.query,
                    answer=final,
                    draft=draft,
                    contexts=contexts,
                    repaired=repaired,
                    critique_passed=bool(critique.passed),
                    addresses_question=bool(critique.addresses_question),
                    has_disclaimer=_DISCLAIMER_MARK in (final or ""),
                    sandbox_gate=sandbox_gate,
                    sandbox_rule_hits=len(rule_hits),
                )
            )
    finally:
        db.close()

    skipped_ragas = skip_ragas
    if not skip_ragas:
        metric_names = ["faithfulness"]
        if include_relevancy:
            metric_names.append("answer_relevancy")
        ragas_rows = [
            {
                "question": cr.query,
                "answer": cr.answer,
                "contexts": cr.contexts or [""],
            }
            for cr in case_results
        ]
        scores = run_ragas_metrics(ragas_rows, metrics=metric_names)
        # Per-case scores: re-run is expensive; approximate by attaching means only
        # when batch evaluate returns aggregate. Prefer per-row if available via
        # a second path — for now assign mean to all and refine if pandas has rows.
        # Better: call evaluate ourselves for per-row. Keep aggregate + best-effort.
        per_row = _per_row_ragas(ragas_rows, metrics=metric_names)
        for idx, cr in enumerate(case_results):
            if idx < len(per_row):
                cr.faithfulness = per_row[idx].get("faithfulness")
                cr.answer_relevancy = per_row[idx].get("answer_relevancy")
            else:
                cr.faithfulness = scores.get("faithfulness")
                cr.answer_relevancy = scores.get("answer_relevancy")

        if score_drafts:
            draft_rows = [
                {
                    "question": cr.query,
                    "answer": cr.draft,
                    "contexts": cr.contexts or [""],
                }
                for cr in case_results
                if cr.repaired
            ]
            if draft_rows:
                draft_per = _per_row_ragas(draft_rows, metrics=["faithfulness"])
                repaired_idxs = [i for i, cr in enumerate(case_results) if cr.repaired]
                for j, i in enumerate(repaired_idxs):
                    if j < len(draft_per):
                        case_results[i].draft_faithfulness = draft_per[j].get("faithfulness")
    else:
        scores = {}

    n = len(case_results)
    repair_rate = sum(1 for r in case_results if r.repaired) / max(n, 1)
    disclaimer_rate = sum(1 for r in case_results if r.has_disclaimer) / max(n, 1)
    sandbox_rule_hit_rate = sum(1 for r in case_results if r.sandbox_rule_hits > 0) / max(n, 1)
    addresses_fail_rate = sum(1 for r in case_results if not r.addresses_question) / max(n, 1)

    failures: list[dict[str, Any]] = []
    for cr in case_results:
        low_f = cr.faithfulness is not None and cr.faithfulness < 0.5
        if low_f or cr.sandbox_rule_hits > 0 or (not cr.critique_passed and not cr.repaired):
            failures.append(cr.to_dict())

    return GroundingEvalReport(
        n=n,
        by_source=_source_stats(case_results),
        faithfulness_mean=_mean([r.faithfulness for r in case_results]),
        answer_relevancy_mean=_mean([r.answer_relevancy for r in case_results]),
        repair_rate=repair_rate,
        disclaimer_rate=disclaimer_rate,
        sandbox_rule_hit_rate=sandbox_rule_hit_rate,
        addresses_fail_rate=addresses_fail_rate,
        skipped_ragas=skipped_ragas,
        repair_enabled=repair_enabled,
        failures=failures,
        cases=[c.to_dict() for c in case_results],
    )


def _per_row_ragas(
    rows: list[dict[str, Any]],
    *,
    metrics: list[str],
) -> list[dict[str, float | None]]:
    """Return per-sample ragas scores; empty list on skip/error."""
    if not rows:
        return []
    if not settings.llm_api_key:
        return [{m: None for m in metrics} for _ in rows]
    try:
        from datasets import Dataset
        from ragas import evaluate

        from app.eval.ragas_judge import _resolve_metrics, build_ragas_llm_embeddings
    except ImportError as exc:
        logger.warning("ragas not available for per-row: %s", exc)
        return [{m: None for m in metrics} for _ in rows]

    try:
        metric_objs = _resolve_metrics(metrics)
    except ValueError as exc:
        logger.warning("%s", exc)
        return [{m: None for m in metrics} for _ in rows]

    llm, embeddings = build_ragas_llm_embeddings()
    dataset = Dataset.from_dict(
        {
            "question": [r["question"] for r in rows],
            "answer": [r["answer"] for r in rows],
            "contexts": [r["contexts"] for r in rows],
        }
    )
    try:
        result = evaluate(dataset, metrics=metric_objs, llm=llm, embeddings=embeddings)
        df = result.to_pandas()
    except Exception as exc:
        logger.warning("per-row ragas failed: %s", exc)
        return [{m: None for m in metrics} for _ in rows]

    out: list[dict[str, float | None]] = []
    for i in range(len(rows)):
        row_scores: dict[str, float | None] = {}
        for m in metrics:
            if m in df.columns and i < len(df):
                val = df[m].iloc[i]
                row_scores[m] = float(val) if val == val else None
            else:
                row_scores[m] = None
        out.append(row_scores)
    return out
