"""Pluggable passage rerank: Zhipu /paas/v4/rerank + heuristic fallback."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx

from app.core.config import settings
from app.core.redis import model_slot
from app.rag.expand import ExpandedHit

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z0-9_]+")
_RERANK_MAX_DOC_CHARS = 4000


@dataclass
class RankedHit:
    hit: ExpandedHit
    score: float


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[ExpandedHit], *, top_n: int) -> list[RankedHit]:
        ...


def _query_tokens(q: str) -> list[str]:
    stop = {
        "的", "了", "是", "在", "和", "与", "或", "我", "你", "吗", "呢", "啊",
        "什么", "哪些", "一下", "这个", "那个", "如何", "怎么",
    }
    raw: list[str] = []
    for m in _LATIN_RE.finditer(q or ""):
        t = m.group(0).lower()
        if t not in stop:
            raw.append(t)
    for m in _CJK_RE.finditer(q or ""):
        s = m.group(0)
        if len(s) <= 2:
            if s not in stop:
                raw.append(s)
            continue
        for n in (2, 3):
            if len(s) < n:
                continue
            for i in range(len(s) - n + 1):
                gram = s[i : i + n]
                if gram not in stop:
                    raw.append(gram)
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:40]


def _passage_for_api(hit: ExpandedHit) -> str:
    text = (hit.content or hit.seed_content or "").strip() or " "
    return text[:_RERANK_MAX_DOC_CHARS]


class HeuristicReranker:
    """RRF score + token coverage + filename boost (no network)."""

    def rerank(self, query: str, hits: list[ExpandedHit], *, top_n: int) -> list[RankedHit]:
        toks = _query_tokens(query)
        ranked: list[RankedHit] = []
        for h in hits:
            text = (h.content or "").lower()
            fn = (h.filename or "").lower()
            cover = 0.0
            for t in toks:
                if t in text:
                    cover += 1.0
                if t in fn:
                    cover += 0.5
            score = float(h.score) * 10.0 + cover
            ranked.append(RankedHit(hit=h, score=score))
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked[: max(1, top_n)] if ranked else []


class ZhipuReranker:
    """POST {base}/rerank — Zhipu text rerank API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.llm_api_key) or ""
        base = (base_url if base_url is not None else settings.llm_base_url) or ""
        self.base_url = base.rstrip("/") + "/"
        self.model = (model if model is not None else settings.rerank_model) or "rerank"
        self.timeout = float(timeout if timeout is not None else min(settings.llm_timeout, 30.0))

    def rerank(self, query: str, hits: list[ExpandedHit], *, top_n: int) -> list[RankedHit]:
        if not hits:
            return []
        if not self.api_key:
            raise RuntimeError("missing api key")

        documents = [_passage_for_api(h) for h in hits]
        url = urljoin(self.base_url, "rerank")
        payload = {
            "model": self.model,
            "query": (query or "")[:4096],
            "documents": documents,
            "top_n": max(1, min(top_n, len(documents))),
            "return_documents": False,
            "return_raw_scores": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with model_slot(self.model):
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

        results = data.get("results") or []
        ranked: list[RankedHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(hits):
                continue
            try:
                score = float(item.get("relevance_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            ranked.append(RankedHit(hit=hits[idx], score=score))

        if not ranked:
            raise RuntimeError("empty rerank results")
        # API usually returns sorted; enforce score order + top_n
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked[: max(1, top_n)]


def resolve_reranker() -> Reranker:
    """Pick Zhipu when enabled + key present; else heuristic."""
    if not settings.rag_rerank_enabled:
        return HeuristicReranker()
    provider = (settings.rag_rerank_provider or "zhipu").strip().lower()
    if provider in ("none", "heuristic", "off"):
        return HeuristicReranker()
    if provider in ("zhipu", "auto", ""):
        if settings.llm_api_key:
            return ZhipuReranker()
        return HeuristicReranker()
    # Unknown provider → heuristic
    return HeuristicReranker()


def rerank_hits(
    query: str,
    hits: list[ExpandedHit],
    *,
    top_n: int,
    reranker: Reranker | None = None,
) -> list[RankedHit]:
    """Rerank with soft-fail to heuristic."""
    if not hits:
        return []
    n = max(1, int(top_n))
    primary = reranker or resolve_reranker()
    heuristic = HeuristicReranker()
    try:
        if isinstance(primary, HeuristicReranker):
            return primary.rerank(query, hits, top_n=n)
        return primary.rerank(query, hits, top_n=n)
    except Exception as e:
        logger.warning("rerank failed, falling back to heuristic: %s", e)
        return heuristic.rerank(query, hits, top_n=n)
