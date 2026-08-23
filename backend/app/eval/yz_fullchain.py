"""YZ full-chain eval: real ingest → retrieve → answer → ragas + rule metrics.

Discipline: report-only during eval — failures are logged, product code is not
changed mid-run, and gold labels are not rewritten to match bad behavior.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agents.workflow import build_pool_prompt
from app.core.config import settings
from app.core.db import SessionLocal, init_db, set_rls_context
from app.core.llm import complete_text
from app.eval.metrics import multilabel_report, retrieval_report
from app.eval.ragas_judge import run_ragas_metrics
from app.eval.tool_routing import EvalCase as RoutingEvalCase, predict_case
from app.models.models import Chunk, Document, User
from app.rag import vector_store
from app.rag.ingestion import ingest_document
from app.rag.retrieval import retrieve
from app.storage import get_blob_store, make_object_key

logger = logging.getLogger(__name__)

YZ_EVAL_USER_ID = 92002
YZ_EVAL_EXTERNAL_ID = "yz_eval_92002"
DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "yz_fullchain.jsonl"
DEFAULT_REPORT = Path(__file__).resolve().parents[2] / "reports" / "yz_eval_latest.json"
YZ_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "YZ测试文档"

YZ_FIXTURES: dict[str, str] = {
    "product_guide_md": "测试用例.md",
    "office_policy_txt": "测试用例.txt",
    "attendance_csv": "测试用例.csv",
}

_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class YZEvalCase:
    id: str
    query: str
    gold_doc_keys: list[str]
    gold_facts: list[str]
    ground_truth: str
    expect_unanswerable: bool = False
    gold: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "YZEvalCase":
        return cls(
            id=str(raw["id"]),
            query=str(raw.get("query") or ""),
            gold_doc_keys=list(raw.get("gold_doc_keys") or []),
            gold_facts=list(raw.get("gold_facts") or []),
            ground_truth=str(raw.get("ground_truth") or ""),
            expect_unanswerable=bool(raw.get("expect_unanswerable", False)),
            gold=dict(raw.get("gold") or {}),
            tags=list(raw.get("tags") or []),
        )


@dataclass
class CaseResult:
    case_id: str
    query: str
    answer: str
    contexts: list[str]
    pred_doc_ids: list[str]
    gold_doc_ids: list[str]
    fact_containment: float
    citation_hit: bool | None
    retrieval_hit: bool
    expect_unanswerable: bool
    gold_intent: str | None = None
    pred_intent: str | None = None
    gold_tools: list[str] | None = None
    pred_tools: list[str] | None = None
    routing_ok: bool | None = None
    ragas: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "query": self.query,
            "answer": self.answer[:500],
            "contexts_count": len(self.contexts),
            "pred_doc_ids": self.pred_doc_ids,
            "gold_doc_ids": self.gold_doc_ids,
            "fact_containment": round(self.fact_containment, 4),
            "citation_hit": self.citation_hit,
            "retrieval_hit": self.retrieval_hit,
            "expect_unanswerable": self.expect_unanswerable,
            "gold_intent": self.gold_intent,
            "pred_intent": self.pred_intent,
            "gold_tools": self.gold_tools,
            "pred_tools": self.pred_tools,
            "routing_ok": self.routing_ok,
            "ragas": self.ragas,
        }


@dataclass
class YZEvalReport:
    n: int
    retrieval: dict[str, float]
    fact_containment_mean: float
    citation_hit_rate: float | None
    ragas: dict[str, float | None]
    routing: dict[str, Any] | None
    failures: list[dict[str, Any]] = field(default_factory=list)
    skipped_ragas: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "retrieval": {k: round(v, 4) for k, v in self.retrieval.items()},
            "fact_containment_mean": round(self.fact_containment_mean, 4),
            "citation_hit_rate": (
                round(self.citation_hit_rate, 4) if self.citation_hit_rate is not None else None
            ),
            "ragas": self.ragas,
            "routing": self.routing,
            "skipped_ragas": self.skipped_ragas,
            "failures": self.failures,
        }

    def format_text(self) -> str:
        lines = [
            f"=== YZ Full-Chain Eval (n={self.n}) ===",
            "",
            "Retrieval",
            f"  precision@k  {self.retrieval.get('precision_at_k', 0):.2%}",
            f"  recall@k     {self.retrieval.get('recall_at_k', 0):.2%}",
            f"  MRR          {self.retrieval.get('mrr', 0):.4f}",
            "",
            f"Fact containment (mean)  {self.fact_containment_mean:.2%}",
        ]
        if self.citation_hit_rate is not None:
            lines.append(f"Citation hit rate        {self.citation_hit_rate:.2%}")
        if not self.skipped_ragas:
            lines.extend(["", "RAGAS"])
            for k, v in self.ragas.items():
                lines.append(f"  {k:<22} {v if v is not None else 'n/a'}")
        if self.routing:
            lines.extend(
                [
                    "",
                    "Routing (subset with gold.intent)",
                    f"  intent accuracy  {self.routing.get('intent_accuracy', 0):.2%}",
                    f"  tool exact-match {self.routing.get('tool_exact_match', 0):.2%}",
                ]
            )
        if self.failures:
            lines.extend(["", f"Failures / low scores ({len(self.failures)}):"])
            for f in self.failures[:20]:
                lines.append(
                    f"  - {f.get('id')}: fc={f.get('fact_containment')} "
                    f"ret_hit={f.get('retrieval_hit')} {f.get('query', '')[:40]}"
                )
            if len(self.failures) > 20:
                lines.append(f"  ... +{len(self.failures) - 20} more")
        return "\n".join(lines)


def load_cases(path: str | Path | None = None) -> list[YZEvalCase]:
    p = Path(path) if path else DEFAULT_DATASET
    out: list[YZEvalCase] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(YZEvalCase.from_dict(json.loads(line)))
    return out


def ensure_yz_eval_user(db: Session) -> None:
    """Create fixed-id eval user so documents.user_id FK / RLS can succeed.

    Do not bump users_id_seq: runtime role omni_app has USAGE/SELECT only
    (setval needs UPDATE), and MAX(id)=92002 would skip ordinary user ids.
    Explicit PK insert does not consume the serial.
    """
    existing = db.query(User).filter(User.id == YZ_EVAL_USER_ID).first()
    if existing is not None:
        return
    taken = db.query(User).filter(User.external_id == YZ_EVAL_EXTERNAL_ID).first()
    if taken is not None:
        raise RuntimeError(
            f"external_id {YZ_EVAL_EXTERNAL_ID!r} exists as users.id={taken.id}, "
            f"expected id={YZ_EVAL_USER_ID}"
        )
    db.add(
        User(
            id=YZ_EVAL_USER_ID,
            external_id=YZ_EVAL_EXTERNAL_ID,
            name="YZ Eval Fixture",
            password_hash="",
            is_active=1,
            is_admin=0,
        )
    )
    db.flush()


def clear_yz_corpus(db: Session) -> None:
    ensure_yz_eval_user(db)
    set_rls_context(db, YZ_EVAL_USER_ID)
    docs = db.query(Document).filter(Document.user_id == YZ_EVAL_USER_ID).all()
    for d in docs:
        try:
            vector_store.delete_by_document(d.id, user_id=d.user_id or YZ_EVAL_USER_ID)
        except Exception:
            pass
        db.query(Chunk).filter(Chunk.document_id == d.id).delete()
        db.delete(d)
    db.commit()


def seed_yz_corpus(db: Session) -> dict[str, int]:
    """Upload three YZ fixtures to blob store, ingest, return doc_key → id."""
    ensure_yz_eval_user(db)
    set_rls_context(db, YZ_EVAL_USER_ID)
    clear_yz_corpus(db)
    store = get_blob_store()
    key_to_id: dict[str, int] = {}

    for doc_key, filename in YZ_FIXTURES.items():
        path = YZ_FIXTURE_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"YZ fixture missing: {path}")
        data = path.read_bytes()
        object_key = make_object_key(user_id=YZ_EVAL_USER_ID, filename=filename)
        store.put(object_key, data)

        doc = Document(
            user_id=YZ_EVAL_USER_ID,
            filename=filename,
            status="pending",
            stage="pending",
            stored_path=object_key,
            char_count=len(data),
        )
        db.add(doc)
        db.flush()
        ingest_document(db, doc)
        db.refresh(doc)
        if doc.status != "ready":
            raise RuntimeError(f"Ingest failed for {filename}: {doc.error or doc.status}")
        key_to_id[doc_key] = doc.id

    db.commit()
    return key_to_id


def _hits_to_evidence(hits) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for h in hits:
        evidence.append(
            {
                "index": 0,
                "source_type": "kb",
                "tool": "kb",
                "filename": h.filename,
                "title": h.filename,
                "snippet": h.snippet,
                "content": h.content,
                "heading": h.heading,
                "page": h.page,
                "url": "",
                "document_id": h.document_id,
            }
        )
    return evidence


def generate_answer(query: str, evidence: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_prompt, included = build_pool_prompt(evidence)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]
    answer = complete_text(messages)
    return answer, included


def fact_containment(answer: str, gold_facts: list[str]) -> float:
    if not gold_facts:
        return 1.0
    text = (answer or "").lower()
    hits = sum(1 for f in gold_facts if f and str(f).lower() in text)
    return hits / len(gold_facts)


def citation_hit(
    answer: str,
    included_evidence: list[dict[str, Any]],
    gold_doc_keys: list[str],
    key_to_id: dict[str, int],
) -> bool | None:
    if not gold_doc_keys or not included_evidence:
        return None
    cited_indices = {int(m.group(1)) for m in _CITATION_RE.finditer(answer or "")}
    cited_doc_ids: set[int] = set()
    for idx in cited_indices:
        if 1 <= idx <= len(included_evidence):
            did = included_evidence[idx - 1].get("document_id")
            if did is not None:
                cited_doc_ids.add(int(did))
    gold_ids = {key_to_id[k] for k in gold_doc_keys if k in key_to_id}
    if not gold_ids:
        return None
    return bool(cited_doc_ids & gold_ids)


def _predict_routing(case: YZEvalCase, key_to_id: dict[str, int]) -> tuple[str, list[str], bool]:
    has_tabular = "attendance_csv" in key_to_id
    rc = RoutingEvalCase(
        id=case.id,
        message=case.query,
        history=[],
        context={
            "has_kb_docs": True,
            "has_tabular_docs": has_tabular,
            "forced_kb": False,
            "use_kb": False,
            # Do not pass document_ids — that is Tier-0 force-KB (user selected docs).
            "document_ids": None,
        },
        gold=case.gold,
        tags=case.tags,
    )
    result = predict_case(rc)
    gold_intent = str(case.gold.get("intent") or "chat")
    gold_tools = sorted(case.gold.get("tools") or [])
    pred_tools = sorted(result.pred_tools or [])
    ok = result.pred_intent == gold_intent and pred_tools == gold_tools
    return result.pred_intent or "chat", list(result.pred_tools or []), ok


def run_yz_fullchain_eval(
    cases: list[YZEvalCase] | None = None,
    *,
    dataset_path: str | Path | None = None,
    k: int | None = None,
    limit: int | None = None,
    skip_ragas: bool = False,
    skip_ingest: bool = False,
    use_zhipu_rerank: bool = False,
) -> YZEvalReport:
    items = list(cases if cases is not None else load_cases(dataset_path))
    if limit is not None:
        items = items[: int(limit)]

    top_k = int(k if k is not None else settings.rag_top_k)

    prev_hybrid = settings.rag_hybrid_enabled
    prev_provider = settings.rag_rerank_provider
    prev_rerank = settings.rag_rerank_enabled
    settings.rag_hybrid_enabled = True
    settings.rag_rerank_enabled = True
    settings.rag_rerank_provider = "zhipu" if use_zhipu_rerank else "none"

    init_db()
    db = SessionLocal()
    try:
        set_rls_context(db, YZ_EVAL_USER_ID)
        if skip_ingest:
            docs = db.query(Document).filter(Document.user_id == YZ_EVAL_USER_ID).all()
            key_to_id: dict[str, int] = {}
            fname_to_key = {v: k for k, v in YZ_FIXTURES.items()}
            for d in docs:
                key = fname_to_key.get(d.filename or "")
                if key:
                    key_to_id[key] = d.id
            if len(key_to_id) < len(YZ_FIXTURES):
                key_to_id = seed_yz_corpus(db)
        else:
            key_to_id = seed_yz_corpus(db)

        gold_sets: list[set[str]] = []
        pred_ranked: list[list[str]] = []
        case_results: list[CaseResult] = []
        ragas_rows: list[dict[str, Any]] = []
        routing_gold_intent: list[str] = []
        routing_pred_intent: list[str] = []
        routing_gold_tools: list[set[str]] = []
        routing_pred_tools: list[set[str]] = []
        citation_scores: list[bool] = []

        for i, case in enumerate(items, start=1):
            print(f"[{i}/{len(items)}] {case.id}: {case.query[:40]}", flush=True)
            gold_ids = {str(key_to_id[k]) for k in case.gold_doc_keys if k in key_to_id}
            hits = retrieve(db, case.query, user_id=YZ_EVAL_USER_ID, top_k=top_k)
            ranked_docs: list[str] = []
            seen: set[str] = set()
            for h in hits:
                did = str(h.document_id)
                if did not in seen:
                    seen.add(did)
                    ranked_docs.append(did)

            evidence = _hits_to_evidence(hits)
            answer, included = generate_answer(case.query, evidence)
            contexts = [str(e.get("content") or e.get("snippet") or "") for e in included]

            fc = fact_containment(answer, case.gold_facts)
            cite = citation_hit(answer, included, case.gold_doc_keys, key_to_id)
            ret_hit = bool(gold_ids.intersection(ranked_docs[:top_k])) if gold_ids else True

            gold_intent = pred_intent = None
            gold_tools = pred_tools = None
            routing_ok = None
            if case.gold.get("intent") is not None:
                pred_intent, pred_tools, routing_ok = _predict_routing(case, key_to_id)
                gold_intent = str(case.gold.get("intent") or "chat")
                gold_tools = list(case.gold.get("tools") or [])
                routing_gold_intent.append(gold_intent)
                routing_pred_intent.append(pred_intent)
                routing_gold_tools.append(set(gold_tools))
                routing_pred_tools.append(set(pred_tools or []))

            if cite is not None:
                citation_scores.append(cite)

            cr = CaseResult(
                case_id=case.id,
                query=case.query,
                answer=answer,
                contexts=contexts,
                pred_doc_ids=ranked_docs[:top_k],
                gold_doc_ids=sorted(gold_ids),
                fact_containment=fc,
                citation_hit=cite,
                retrieval_hit=ret_hit,
                expect_unanswerable=case.expect_unanswerable,
                gold_intent=gold_intent,
                pred_intent=pred_intent,
                gold_tools=gold_tools,
                pred_tools=pred_tools,
                routing_ok=routing_ok,
            )
            case_results.append(cr)

            gold_sets.append(gold_ids)
            pred_ranked.append(ranked_docs)

            if not skip_ragas and case.gold.get("intent") != "chat":
                ragas_rows.append(
                    {
                        "question": case.query,
                        "answer": answer,
                        "contexts": contexts or [""],
                        "ground_truth": case.ground_truth,
                    }
                )

        retrieval = retrieval_report(gold_sets, pred_ranked, k=top_k)
        fc_mean = sum(c.fact_containment for c in case_results) / max(len(case_results), 1)
        cite_rate = (
            sum(1 for x in citation_scores if x) / len(citation_scores) if citation_scores else None
        )

        ragas_scores: dict[str, float | None] = {}
        if not skip_ragas:
            ragas_scores = run_ragas_metrics(ragas_rows)

        routing_summary: dict[str, Any] | None = None
        if routing_gold_intent:
            intent_hits = sum(
                1 for g, p in zip(routing_gold_intent, routing_pred_intent) if g == p
            )
            tool_report = multilabel_report(
                routing_gold_tools,
                routing_pred_tools,
                labels=["kb", "web", "calendar", "sandbox"],
            )
            routing_summary = {
                "n": len(routing_gold_intent),
                "intent_accuracy": intent_hits / len(routing_gold_intent),
                "tool_exact_match": tool_report.exact_match,
                "tool_micro_f1": tool_report.micro.get("f1", 0),
            }

        failures: list[dict[str, Any]] = []
        for cr, case in zip(case_results, items):
            low_fc = bool(case.gold_facts) and cr.fact_containment < 0.5
            bad_ret = cr.gold_doc_ids and not cr.retrieval_hit
            bad_cite = cr.citation_hit is False
            bad_route = cr.routing_ok is False
            if low_fc or bad_ret or bad_cite or bad_route:
                failures.append(cr.to_dict())

        return YZEvalReport(
            n=len(items),
            retrieval=retrieval,
            fact_containment_mean=fc_mean,
            citation_hit_rate=cite_rate,
            ragas=ragas_scores,
            routing=routing_summary,
            failures=failures,
            skipped_ragas=skip_ragas,
        )
    finally:
        if not skip_ingest:
            clear_yz_corpus(db)
        db.close()
        settings.rag_hybrid_enabled = prev_hybrid
        settings.rag_rerank_provider = prev_provider
        settings.rag_rerank_enabled = prev_rerank


def write_report(report: YZEvalReport, path: str | Path | None = None) -> Path:
    out = Path(path) if path else DEFAULT_REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out
