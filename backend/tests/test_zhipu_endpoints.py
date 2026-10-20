from __future__ import annotations

import logging
from pathlib import Path

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.core import config, embeddings
from app.core.config import (
    EndpointConfigError,
    Settings,
    ZhipuService,
    check_zhipu_endpoints,
    zhipu_endpoint,
)
from app.rag import rerank, vision_ocr
from app.rag.expand import ExpandedHit
from app.rag.rerank import HeuristicReranker, rerank_hits, resolve_reranker

ENV_VARS = {
    ZhipuService.EMBEDDING: ("EMBEDDING_BASE_URL", "EMBEDDING_API_KEY"),
    ZhipuService.VISION: ("VISION_BASE_URL", "VISION_API_KEY"),
    ZhipuService.RERANK: ("RERANK_BASE_URL", "RERANK_API_KEY"),
}
SWITCH_ON = {
    ZhipuService.EMBEDDING: {"rag_hybrid_enabled": True},
    ZhipuService.VISION: {"vision_ocr_enabled": True},
    ZhipuService.RERANK: {"rag_rerank_enabled": True, "rag_rerank_provider": "zhipu"},
}
ALL_OFF = {"rag_hybrid_enabled": False, "vision_ocr_enabled": False, "rag_rerank_enabled": False}
CHAT_URL = "http://chat.test/v1/"
CHAT_KEY = "chat-key"


@pytest.fixture(autouse=True)
def _no_endpoint_env(monkeypatch):
    for names in ENV_VARS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**ALL_OFF, **overrides})


def _full_pair(service: ZhipuService) -> dict[str, str]:
    url_var, key_var = ENV_VARS[service]
    return {url_var.lower(): f"http://{service.name.lower()}.test/v4/", key_var.lower(): f"{service.name.lower()}-key"}


@pytest.fixture
def live_settings(monkeypatch):
    s = config.settings
    monkeypatch.setattr(s, "llm_base_url", CHAT_URL)
    monkeypatch.setattr(s, "llm_api_key", CHAT_KEY)

    def apply(**values):
        for name, value in values.items():
            monkeypatch.setattr(s, name, value)

    return apply


@pytest.fixture
def openai_requests(monkeypatch):
    seen: list[httpx.Request] = []
    body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    real = openai.OpenAI

    def factory(**kwargs):
        return real(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(openai, "OpenAI", factory)
    return seen, lambda payload: body.update(payload)


@pytest.fixture
def httpx_requests(monkeypatch):
    seen: list[httpx.Request] = []
    reply: dict = {"status": 200, "json": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(reply["status"], json=reply["json"])

    real = httpx.Client

    def factory(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return seen, lambda status, payload: reply.update(status=status, json=payload)


def _hits() -> list[ExpandedHit]:
    return [
        ExpandedHit(chunk_id=1, document_id=1, filename="a.md", content="天气", score=0.1, seed_content="天气"),
        ExpandedHit(chunk_id=2, document_id=1, filename="差旅.md", content="差旅报销", score=0.1, seed_content="差旅报销"),
    ]


@pytest.mark.parametrize("service", list(ZhipuService), ids=lambda s: s.name)
def test_each_service_reads_its_own_env_vars(monkeypatch, service):
    url_var, key_var = ENV_VARS[service]
    monkeypatch.setenv(url_var, " http://own.test/v4/ ")
    monkeypatch.setenv(key_var, "own-key")

    endpoint = zhipu_endpoint(service, _settings())

    assert endpoint.base_url == "http://own.test/v4/"
    assert endpoint.api_key == "own-key"


@pytest.mark.parametrize("service", list(ZhipuService), ids=lambda s: s.name)
def test_service_never_falls_back_to_llm_pair(monkeypatch, service):
    monkeypatch.setenv("LLM_BASE_URL", CHAT_URL)
    monkeypatch.setenv("LLM_API_KEY", CHAT_KEY)

    with pytest.raises(EndpointConfigError) as err:
        zhipu_endpoint(service, _settings())

    assert err.value.missing == ENV_VARS[service]


@pytest.mark.parametrize(
    ("service", "empty_var"),
    [(svc, var) for svc, names in ENV_VARS.items() for var in names],
    ids=lambda v: v.name if isinstance(v, ZhipuService) else v,
)
def test_check_fails_naming_the_empty_variable(service, empty_var):
    values = {**SWITCH_ON[service], **_full_pair(service), empty_var.lower(): ""}

    with pytest.raises(EndpointConfigError) as err:
        check_zhipu_endpoints(_settings(**values))

    assert err.value.missing == (empty_var,)
    assert empty_var in str(err.value)


@pytest.mark.parametrize("service", list(ZhipuService), ids=lambda s: s.name)
def test_check_passes_when_switch_off_and_pair_empty(service):
    others_on: dict = {}
    for other in ZhipuService:
        if other is not service:
            others_on |= SWITCH_ON[other] | _full_pair(other)

    check_zhipu_endpoints(_settings(**others_on))


def test_check_lists_every_gap_at_once():
    all_on = {k: v for svc in ZhipuService for k, v in SWITCH_ON[svc].items()}

    with pytest.raises(EndpointConfigError) as err:
        check_zhipu_endpoints(_settings(**all_on))

    assert err.value.missing == tuple(var for names in ENV_VARS.values() for var in names)
    message = str(err.value)
    for switch in ("RAG_HYBRID_ENABLED=false", "VISION_OCR_ENABLED=false", "RAG_RERANK_ENABLED=false"):
        assert switch in message


def test_whitespace_only_value_counts_as_empty():
    values = {**SWITCH_ON[ZhipuService.VISION], **_full_pair(ZhipuService.VISION), "vision_api_key": "   "}

    with pytest.raises(EndpointConfigError) as err:
        check_zhipu_endpoints(_settings(**values))

    assert err.value.missing == ("VISION_API_KEY",)


@pytest.mark.parametrize("provider", ["none", "heuristic", "off"])
def test_rerank_provider_none_needs_no_rerank_pair(provider):
    check_zhipu_endpoints(_settings(rag_rerank_enabled=True, rag_rerank_provider=provider))


def test_embed_texts_posts_to_embedding_base_url(live_settings, openai_requests):
    live_settings(**_full_pair(ZhipuService.EMBEDDING))
    seen, respond = openai_requests
    respond({"object": "list", "model": "embedding-3", "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.25]}]})

    assert embeddings.embed_texts(["你好"]) == [[0.5, 0.25]]

    assert [str(r.url) for r in seen] == ["http://embedding.test/v4/embeddings"]
    assert seen[0].headers["authorization"] == "Bearer embedding-key"


def test_embed_query_cache_key_uses_embedding_base_url(live_settings, monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(embeddings, "json_get", lambda key: keys.append(key) or [1.0])

    def key_for(embedding_url: str, chat_url: str) -> str:
        live_settings(embedding_base_url=embedding_url, embedding_api_key="k", llm_base_url=chat_url)
        embeddings.embed_query("年假")
        return keys[-1]

    base = key_for("http://embedding-a.test/", "http://chat-a.test/")
    assert key_for("http://embedding-a.test/", "http://chat-b.test/") == base
    assert key_for("http://embedding-b.test/", "http://chat-a.test/") != base


def test_ocr_posts_to_vision_base_url(live_settings, openai_requests):
    live_settings(vision_ocr_enabled=True, **_full_pair(ZhipuService.VISION))
    seen, respond = openai_requests
    respond(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "glm-4.6v",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "识别文字"}, "finish_reason": "stop"}],
        }
    )

    assert vision_ocr.ocr_image_bytes(b"\x89PNG") == "识别文字"

    assert [str(r.url) for r in seen] == ["http://vision.test/v4/chat/completions"]
    assert seen[0].headers["authorization"] == "Bearer vision-key"


def test_zhipu_reranker_posts_to_rerank_base_url(live_settings, httpx_requests):
    live_settings(**SWITCH_ON[ZhipuService.RERANK], **_full_pair(ZhipuService.RERANK))
    seen, respond = httpx_requests
    respond(200, {"results": [{"index": 1, "relevance_score": 0.9}]})
    diag: dict = {}

    out = rerank_hits("差旅", _hits(), top_n=1, diag=diag)

    assert [h.hit.chunk_id for h in out] == [2]
    assert diag["rerank_fallback"] is False
    assert [str(r.url) for r in seen] == ["http://rerank.test/v4/rerank"]
    assert seen[0].headers["authorization"] == "Bearer rerank-key"


def test_ragas_embeddings_use_embedding_pair_and_judge_keeps_chat_pair(live_settings):
    pytest.importorskip("langchain_openai")
    from app.eval.ragas_judge import build_ragas_llm_embeddings

    live_settings(llm_model="chat-model", **_full_pair(ZhipuService.EMBEDDING))

    llm, emb = build_ragas_llm_embeddings()

    assert (llm.model_name, llm.openai_api_base, llm.openai_api_key.get_secret_value()) == (
        "chat-model",
        CHAT_URL,
        CHAT_KEY,
    )
    assert (emb.openai_api_base, emb.openai_api_key.get_secret_value()) == ("http://embedding.test/v4/", "embedding-key")


@pytest.mark.parametrize("provider", ["zhipu", "auto", ""])
@pytest.mark.parametrize("empty_var", ENV_VARS[ZhipuService.RERANK])
def test_resolve_reranker_raises_when_rerank_pair_missing(live_settings, provider, empty_var):
    live_settings(rag_rerank_enabled=True, rag_rerank_provider=provider, **_full_pair(ZhipuService.RERANK))
    live_settings(**{empty_var.lower(): ""})

    with pytest.raises(EndpointConfigError) as err:
        resolve_reranker()

    assert err.value.missing == (empty_var,)


def test_resolve_reranker_is_heuristic_when_rerank_off(live_settings):
    live_settings(rag_rerank_enabled=False, rerank_base_url="", rerank_api_key="")

    assert isinstance(resolve_reranker(), HeuristicReranker)


def test_rerank_http_500_logs_error_and_sets_fallback(live_settings, httpx_requests, caplog):
    live_settings(**SWITCH_ON[ZhipuService.RERANK], **_full_pair(ZhipuService.RERANK))
    seen, respond = httpx_requests
    respond(500, {"error": "boom"})
    diag: dict = {}

    with caplog.at_level(logging.ERROR, logger="app.rag.rerank"):
        out = rerank_hits("差旅报销", _hits(), top_n=1, diag=diag)

    assert [h.hit.chunk_id for h in out] == [2]
    assert len(seen) == 1
    assert diag["rerank_fallback"] is True
    assert "500" in diag["rerank_error"]
    errors = [r for r in caplog.records if r.name == "app.rag.rerank" and r.levelno == logging.ERROR]
    assert errors and "rerank failed" in errors[0].getMessage()


class _ConfigBoom:
    def rerank(self, query, hits, *, top_n):  # noqa: ANN001
        raise EndpointConfigError(("RERANK_API_KEY",), ("RAG_RERANK_ENABLED=false",))


@pytest.mark.parametrize("injected", [None, _ConfigBoom()], ids=["resolved", "injected"])
def test_endpoint_config_error_does_not_set_rerank_fallback(live_settings, caplog, injected):
    live_settings(**SWITCH_ON[ZhipuService.RERANK], rerank_base_url="http://rerank.test/v4/", rerank_api_key="")
    diag: dict = {}

    with caplog.at_level(logging.ERROR, logger="app.rag.rerank"), pytest.raises(EndpointConfigError):
        rerank_hits("差旅报销", _hits(), top_n=1, reranker=injected, diag=diag)

    assert diag["rerank_fallback"] is False
    assert "rerank_error" not in diag
    assert not [r for r in caplog.records if "rerank failed" in r.getMessage()]


@pytest.mark.parametrize("module", [embeddings, vision_ocr, rerank], ids=lambda m: m.__name__)
def test_client_modules_never_read_the_chat_pair(module):
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "llm_base_url" not in source
    assert "llm_api_key" not in source


def test_api_startup_fails_naming_rerank_key(live_settings, monkeypatch):
    from app import main

    live_settings(**SWITCH_ON[ZhipuService.RERANK], rerank_base_url="http://rerank.test/v4/", rerank_api_key="")
    init_calls: list[bool] = []
    monkeypatch.setattr(main, "init_db", lambda: init_calls.append(True))

    with pytest.raises(EndpointConfigError) as err:
        with TestClient(main.app):
            pass

    assert "RERANK_API_KEY" in str(err.value)
    assert init_calls == []


def test_worker_boot_exits_naming_variable(live_settings):
    from celery.signals import worker_init

    from app.tasks.celery_app import _refuse_misconfigured_worker

    live_settings(vision_ocr_enabled=True, vision_base_url="", vision_api_key="vision-key")
    assert _refuse_misconfigured_worker in _signal_receivers(worker_init)

    with pytest.raises(SystemExit) as err:
        worker_init.send(sender=None)

    assert "VISION_BASE_URL" in str(err.value)


def test_worker_boots_when_every_service_is_off(live_settings):
    from celery.signals import worker_init

    from app.tasks.celery_app import _refuse_misconfigured_worker

    live_settings(**ALL_OFF)
    assert _refuse_misconfigured_worker in _signal_receivers(worker_init)

    worker_init.send(sender=None)


def _signal_receivers(signal):
    found = []
    for receiver in signal.receivers:
        func = receiver[1]
        found.append(func() if callable(func) else func)
    return found
