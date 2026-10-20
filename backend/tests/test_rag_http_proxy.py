"""Zhipu rerank and embedding clients must see the same proxy env as chat."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Endpoint, settings
from app.rag.expand import ExpandedHit
from app.rag.rerank import ZhipuReranker

_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)


class _RecordingClient(httpx.Client):
    def __init__(self, *args, captured: dict, **kwargs):
        captured["kwargs"] = kwargs
        super().__init__(*args, **kwargs)


def test_zhipu_rerank_client_receives_https_proxy(monkeypatch: pytest.MonkeyPatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8888")
    captured: dict = {}

    class RecordingClient(_RecordingClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, captured=captured, **kwargs)

        def post(self, *args, **kwargs):
            request = httpx.Request("POST", args[0] if args else kwargs.get("url"))
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.4}]}, request=request)

    monkeypatch.setattr("app.rag.rerank.httpx.Client", RecordingClient)
    hit = ExpandedHit(chunk_id=1, document_id=1, filename="a.md", content="差旅", score=0.2)
    ZhipuReranker(Endpoint("http://rerank.test/v4", "rerank-key")).rerank("差旅", [hit], top_n=1)

    assert captured["kwargs"]["proxy"] == "http://proxy.example:8888"


def test_embedding_client_receives_https_proxy(monkeypatch: pytest.MonkeyPatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://lower.example:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8888")
    monkeypatch.setattr(settings, "embedding_base_url", "http://embedding.test/v4/")
    monkeypatch.setattr(settings, "embedding_api_key", "embedding-key")
    monkeypatch.setattr(settings, "embedding_model", "embedding-3")
    captured: dict = {}

    class RecordingClient(_RecordingClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, captured=captured, **kwargs)

    class FakeEmbeddings:
        def create(self, **kwargs):
            class _Item:
                index = 0
                embedding = [0.25, 0.5]

            class _Resp:
                data = [_Item()]

            return _Resp()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["openai"] = kwargs
            self.embeddings = FakeEmbeddings()

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", RecordingClient)
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    from app.core.embeddings import embed_texts

    assert embed_texts(["年假"]) == [[0.25, 0.5]]
    assert captured["kwargs"]["proxy"] == "http://proxy.example:8888"
    assert captured["openai"]["http_client"] is not None


def test_zhipu_rerank_omits_blank_proxy(monkeypatch: pytest.MonkeyPatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "   ")
    captured: dict = {}

    class RecordingClient(_RecordingClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, captured=captured, **kwargs)

        def post(self, *args, **kwargs):
            request = httpx.Request("POST", args[0] if args else kwargs.get("url"))
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.4}]}, request=request)

    monkeypatch.setattr("app.rag.rerank.httpx.Client", RecordingClient)
    hit = ExpandedHit(chunk_id=1, document_id=1, filename="a.md", content="差旅", score=0.2)
    ZhipuReranker(Endpoint("http://rerank.test/v4", "rerank-key")).rerank("差旅", [hit], top_n=1)

    assert captured["kwargs"].get("proxy") in (None, "")
