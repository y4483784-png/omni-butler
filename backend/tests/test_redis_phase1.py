from __future__ import annotations

import asyncio
import json

from app.agents import router as router_mod
from app.core import embeddings
from app.core import redis as redis_core
from app.services import web_search


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.slots: set[str] = set()

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        assert ttl > 0
        self.values[key] = value

    def eval(self, script, numkeys, key, *args):
        assert numkeys == 1
        if "ZREMRANGEBYSCORE" in script and "ZRANGE" in script:
            limit = int(args[2])
            if len(self.slots) >= limit:
                return [0, len(self.slots), 1]
            self.slots.add(str(args[3]))
            return [1, len(self.slots), 0]
        if "ZREMRANGEBYSCORE" in script:
            limit = int(args[2])
            token = str(args[3])
            if len(self.slots) >= limit:
                return 0
            self.slots.add(token)
            return 1
        self.slots.discard(str(args[0]))
        return 1


def test_json_cache_round_trip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_core, "get_redis", lambda: fake)
    assert redis_core.json_set("k", {"中文": [1, 2]}, ttl=30)
    assert redis_core.json_get("k") == {"中文": [1, 2]}


def test_stable_key_is_order_independent():
    assert redis_core.stable_key("x", {"a": 1, "b": 2}) == redis_core.stable_key(
        "x", {"b": 2, "a": 1}
    )


def test_model_slot_and_sliding_window(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_core, "get_redis", lambda: fake)
    monkeypatch.setattr(redis_core.settings, "llm_model_concurrency_limits", "glm-x=2")

    with redis_core.model_slot("glm-x"):
        assert len(fake.slots) == 1
    assert not fake.slots

    allowed, count, retry = redis_core.sliding_window_allow(
        "u1", limit=1, window_seconds=60
    )
    assert allowed and count == 1 and retry == 0
    allowed, _, retry = redis_core.sliding_window_allow(
        "u1", limit=1, window_seconds=60
    )
    assert not allowed and retry >= 1


def test_sliding_window_allow_async(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_core, "get_redis", lambda: fake)
    allowed, count, retry = asyncio.run(
        redis_core.sliding_window_allow_async("u1", limit=1, window_seconds=60)
    )
    assert allowed and count == 1 and retry == 0


def test_router_cache_skips_llm(monkeypatch):
    cached = {
        "reasoning": "cached",
        "needs_kb": True,
        "needs_web": False,
        "needs_calendar": False,
        "needs_sandbox": False,
        "needs_freshness": False,
        "confidence": "high",
    }
    monkeypatch.setattr(router_mod, "json_get", lambda _key: cached)

    def boom(*_args, **_kwargs):
        raise AssertionError("LLM must not run on a valid route cache hit")

    monkeypatch.setattr(router_mod, "_llm_decide", boom)
    decision = router_mod.route("正常工作时间", [], has_kb_docs=True)
    assert decision.needs_kb is True


def test_query_embedding_cache_skips_provider(monkeypatch):
    monkeypatch.setattr(embeddings, "json_get", lambda _key: [1, 2.5])

    def boom(*_args, **_kwargs):
        raise AssertionError("embedding provider must not run on cache hit")

    monkeypatch.setattr(embeddings, "embed_texts", boom)
    assert embeddings.embed_query("重复问题") == [1.0, 2.5]


def test_web_cache_skips_http_and_rehydrates_hits(monkeypatch):
    monkeypatch.setattr(
        web_search,
        "json_get",
        lambda _key: {
            "results": [
                {
                    "title": "新闻",
                    "url": "https://example.com",
                    "snippet": "摘要",
                    "media": "站点",
                    "publish_date": "2026-08-16",
                    "refer": "",
                }
            ]
        },
    )

    class NoHttp:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("HTTP must not run on web cache hit")

    monkeypatch.setattr(web_search.httpx, "Client", NoHttp)
    out = web_search.search_api(
        "今日新闻",
        options=web_search.SearchOptions(query="今日新闻", count=5),
    )
    assert out.results[0].url == "https://example.com"
    assert out.error is None


def test_cache_payload_is_json_serializable():
    # Protect the cache contract from accidental dataclass/object insertion.
    payload = {"results": [{"title": "t", "url": "", "snippet": "s"}]}
    assert json.loads(json.dumps(payload)) == payload
