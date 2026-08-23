"""Zhipu Web Search API client (POST /paas/v4/web_search)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.llm import complete_json
from app.core.prompts import REWRITE_SYSTEM, rewrite_user
from app.core.redis import json_get, json_set, model_slot, stable_key

_MAX_QUERY_LEN = 70
_RECENCY_LADDER = ["oneDay", "oneWeek", "oneMonth", "noLimit"]
# search_std / search_pro often omit link; quark/sogou variants return URLs.
_ENGINES_NO_LINK = frozenset({"search_std", "search_pro"})
_LINK_FALLBACK_ENGINE = "search_pro_quark"
_URL_IN_TEXT = re.compile(r"https?://[^\s\]\)\"<>]+")
_PREFIX = re.compile(r"^(请?\s*(帮我|你|问)?\s*(搜索|搜|查询|查|告诉我|看看)\s*(一下)?\s*)+", re.I)
_SUFFIX = re.compile(r"(是什么|有哪些|吗|呢|啊|谢谢)+[?？。！!]*$")


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    media: str = ""
    publish_date: str = ""
    refer: str = ""


@dataclass
class SearchOptions:
    query: str
    count: int
    content_size: str = "medium"
    recency_filter: str = "noLimit"
    query_rewritten: bool = False
    queries: list[str] = field(default_factory=list)


@dataclass
class SearchOutcome:
    results: list[SearchHit] = field(default_factory=list)
    error: str | None = None
    query_used: str = ""
    count: int = 0
    recency_filter: str = "noLimit"
    content_size: str = "medium"
    query_rewritten: bool = False


def _web_search_url() -> str:
    base = (settings.llm_base_url or "").rstrip("/")
    if not base:
        return "https://open.bigmodel.cn/api/paas/v4/web_search"
    return f"{base}/web_search"


def _truncate_query(query: str) -> str:
    q = (query or "").strip()
    if len(q) <= _MAX_QUERY_LEN:
        return q
    return q[:_MAX_QUERY_LEN]


def _rewrite_query(query: str) -> str:
    """Rule fallback: strip leading imperatives / trailing particles only."""
    q = (query or "").strip()
    q = _PREFIX.sub("", q).strip()
    q = _SUFFIX.sub("", q).strip()
    q = " ".join(q.split())
    return _truncate_query(q) or _truncate_query(query)


def _extract_link(item: dict) -> str:
    for key in ("link", "url"):
        v = str(item.get(key) or "").strip()
        if v.startswith("http://") or v.startswith("https://"):
            return v
    refer = str(item.get("refer") or "").strip()
    if refer.startswith("http://") or refer.startswith("https://"):
        return refer
    content = str(item.get("content") or "")
    m = _URL_IN_TEXT.search(content)
    if m:
        return m.group(0).rstrip(".,;)")
    return str(item.get("link") or item.get("url") or "").strip()


def is_freshness_query(message: str) -> bool:
    text = message or ""
    return any(k in text for k in ("现在", "目前", "今天", "今日", "最新", "最近", "刚刚", "实时", "近期", "本周", "本月"))


def _needs_llm_rewrite_first(message: str, rule_q: str) -> bool:
    if not rule_q or len(rule_q) < 4:
        return True
    msg = message or ""
    if "、" in msg or "和" in msg:
        return True
    return False


def _llm_rewrite_queries(message: str, *, max_n: int, iteration: int) -> list[str]:
    parsed = complete_json(
        [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": rewrite_user(message, iteration=iteration)},
        ]
    )
    out: list[str] = []
    if isinstance(parsed, dict):
        raw = parsed.get("queries")
        if isinstance(raw, list):
            for item in raw:
                q = _truncate_query(str(item or ""))
                if q and q not in out:
                    out.append(q)
                if len(out) >= max_n:
                    break
        elif parsed.get("query"):
            q = _truncate_query(str(parsed["query"]))
            if q:
                out.append(q)
    return out[:max_n]


def rewrite_queries(message: str, *, iteration: int = 1) -> list[str]:
    """Rule-first on iter1; LLM on retry or when rule output is insufficient."""
    max_n = 1 if iteration < 2 else 2
    rule_q = _rewrite_query(message)

    if iteration < 2 and rule_q and len(rule_q) >= 4 and not _needs_llm_rewrite_first(message, rule_q):
        return [rule_q]

    out = _llm_rewrite_queries(message, max_n=max_n, iteration=iteration)
    if not out and rule_q:
        out = [rule_q]
    return out[:max_n]


def plan_search(message: str, *, iteration: int = 1, query: str | None = None) -> SearchOptions:
    fresh = is_freshness_query(message)
    if query is not None:
        q = _truncate_query(query)
        rewritten = True
        queries = [q] if q else []
    else:
        queries = rewrite_queries(message, iteration=iteration)
        q = queries[0] if queries else _truncate_query(message)
        rewritten = q != _truncate_query(message)

    count = settings.web_search_count or 5
    if fresh:
        count = max(count, 8)
    if iteration >= 2:
        count = min(max(count, 8), 8)

    content_size = "high" if fresh or iteration >= 2 else "medium"

    if fresh:
        base = 0
        if any(k in (message or "") for k in ("最近", "近期", "这周", "本周")):
            base = 1
        if any(k in (message or "") for k in ("本月", "这个月")):
            base = max(base, 2)
        step = min(base + max(0, iteration - 1), len(_RECENCY_LADDER) - 1)
        recency = _RECENCY_LADDER[step]
    else:
        recency = "noLimit"

    return SearchOptions(
        query=q,
        count=max(1, min(50, int(count))),
        content_size=content_size,
        recency_filter=recency,
        query_rewritten=rewritten,
        queries=queries or ([q] if q else []),
    )


def _hits_from_response(data: dict) -> list[SearchHit]:
    raw = data.get("search_result") or []
    hits: list[SearchHit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = _extract_link(item)
        content = str(item.get("content") or "").strip()
        if not (title or link or content):
            continue
        hits.append(
            SearchHit(
                title=title or link or "未命名",
                url=link,
                snippet=content,
                media=str(item.get("media") or "").strip(),
                publish_date=str(item.get("publish_date") or "").strip(),
                refer=str(item.get("refer") or "").strip(),
            )
        )
    return hits


def search_api(
    query: str,
    max_results: int | None = None,
    *,
    options: SearchOptions,
) -> SearchOutcome:
    """Pure HTTP call to Zhipu Web Search; never invokes LLM."""
    q = _truncate_query(options.query or query)
    if not q:
        return SearchOutcome(error="搜索词为空", query_used="")

    count = max_results if max_results is not None else options.count
    count = max(1, min(50, int(count)))
    engine = (settings.web_search_engine or "search_pro_quark").strip()
    cache_key = stable_key(
        "web-search",
        {
            "query": q,
            "count": count,
            "engine": engine,
            "content_size": options.content_size,
            "recency_filter": options.recency_filter,
        },
    )
    cached = json_get(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("results"), list):
        try:
            return SearchOutcome(
                results=[SearchHit(**item) for item in cached["results"]],
                query_used=q,
                count=count,
                recency_filter=options.recency_filter,
                content_size=options.content_size,
                query_rewritten=options.query_rewritten,
            )
        except (TypeError, ValueError):
            pass

    api_key = (settings.llm_api_key or "").strip()
    if not api_key:
        return SearchOutcome(error="未配置 API Key，无法联网搜索", query_used=q)

    payload: dict[str, Any] = {
        "search_query": q,
        "search_engine": engine,
        "search_intent": False,
        "count": count,
        "content_size": options.content_size,
        "search_recency_filter": options.recency_filter,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def _request(client: httpx.Client, body: dict[str, Any]) -> dict:
        resp = client.post(_web_search_url(), headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    try:
        # Web Search is a separate provider entitlement but shares the same
        # account-level 429 risk; use its own distributed concurrency bucket.
        with model_slot("web-search"):
            with httpx.Client(timeout=30.0) as client:
                data = _request(client, payload)
                hits = _hits_from_response(data)
                if hits and not any(h.url for h in hits) and engine in _ENGINES_NO_LINK:
                    fallback_body = {**payload, "search_engine": _LINK_FALLBACK_ENGINE}
                    data = _request(client, fallback_body)
                    hits2 = _hits_from_response(data)
                    if any(h.url for h in hits2):
                        hits = hits2
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.text[:200]
        except Exception:
            pass
        return SearchOutcome(
            error=f"联网搜索失败 HTTP {e.response.status_code}: {detail}",
            query_used=q,
            count=count,
            recency_filter=options.recency_filter,
            content_size=options.content_size,
            query_rewritten=options.query_rewritten,
        )
    except Exception as e:
        return SearchOutcome(
            error=f"联网搜索失败：{type(e).__name__}: {e}",
            query_used=q,
            count=count,
            recency_filter=options.recency_filter,
            content_size=options.content_size,
            query_rewritten=options.query_rewritten,
        )

    outcome = SearchOutcome(
        results=hits,
        query_used=q,
        count=count,
        recency_filter=options.recency_filter,
        content_size=options.content_size,
        query_rewritten=options.query_rewritten,
    )
    json_set(
        cache_key,
        {"results": [asdict(hit) for hit in hits]},
        ttl=settings.web_cache_ttl,
    )
    return outcome


def search(
    query: str,
    max_results: int | None = None,
    *,
    options: SearchOptions | None = None,
) -> SearchOutcome:
    """HTTP search wrapper. Without options, uses truncated query only (no LLM)."""
    if options is None:
        q = _truncate_query(query)
        options = SearchOptions(
            query=q,
            count=max(1, min(50, int(max_results or settings.web_search_count or 5))),
        )
    return search_api(query, max_results=max_results, options=options)


def search_planned(message: str, *, iteration: int = 1) -> SearchOutcome:
    """Rewrite queries then search; iteration>=2 may run up to 2 queries and merge."""
    planned = plan_search(message, iteration=iteration)
    queries = planned.queries or ([planned.query] if planned.query else [])
    if not queries:
        return SearchOutcome(error="搜索词为空", query_used="")

    merged: list[SearchHit] = []
    seen: set[str] = set()
    errors: list[str] = []
    last_out: SearchOutcome | None = None

    for q in queries:
        opt = SearchOptions(
            query=q,
            count=planned.count,
            content_size=planned.content_size,
            recency_filter=planned.recency_filter,
            query_rewritten=True,
            queries=queries,
        )
        out = search_api(q, options=opt)
        last_out = out
        if out.error and not out.results:
            errors.append(out.error)
        for h in out.results:
            key = h.url or f"{h.title}|{h.snippet[:80]}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)

    query_used = " | ".join(queries)
    if not merged:
        return SearchOutcome(
            results=[],
            error=errors[0] if errors else (last_out.error if last_out else None),
            query_used=query_used,
            count=planned.count,
            recency_filter=planned.recency_filter,
            content_size=planned.content_size,
            query_rewritten=planned.query_rewritten,
        )

    return SearchOutcome(
        results=merged,
        error=None,
        query_used=query_used,
        count=planned.count,
        recency_filter=planned.recency_filter,
        content_size=planned.content_size,
        query_rewritten=planned.query_rewritten,
    )


def citation_filename(hit: SearchHit) -> str:
    if hit.media:
        return hit.media
    if hit.url:
        try:
            host = urlparse(hit.url).netloc
            if host:
                return host
        except Exception:
            pass
    return hit.title or "web"
