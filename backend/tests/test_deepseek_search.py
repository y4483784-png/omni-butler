from __future__ import annotations

import httpx

from app.agents.harness.types import ToolContext
from app.agents.tools.web import execute_web, execute_web_fetch
from app.core.config import settings
from app.services.deepseek_search import ToolError, deepseek_fetch, deepseek_search
from app.tasks.job import _result_text

_TITLE = "DeepSeek V4 model released"
_URL = "https://example.com/deepseek-v4"
_SNIPPET = "DeepSeek V4 is now available, with tool calling and long-context support."
_SUMMARY = (
    "DeepSeek has released the V4 model. "
    "The release adds improved tool calling and long-context support for agent workflows."
)
_CONTENT = "DeepSeek V4 is now generally available..."
_HIT = {
    "rerank_score": 2.4,
    "title": _TITLE,
    "url": _URL,
    "snippet": _SNIPPET,
    "summary": _SUMMARY,
    "content": _CONTENT,
    "site_name": "Example News",
    "site_icon": "https://example.com/favicon.ico",
    "date": "2026-06-01T00:00:00+00:00",
    "authority": 3,
}


class _Response:
    def __init__(self, status: int, payload: object):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Capture:
    def __init__(self):
        self.clients = 0
        self.posts: list[dict] = []
        self.queue: list[tuple[int, object]] = []

    def install(self, monkeypatch) -> _Capture:
        capture = self

        class Client:
            def __init__(self, *args, **kwargs):
                capture.clients += 1
                self.timeout = kwargs.get("timeout")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                capture.posts.append(
                    {"url": url, "json": json, "headers": headers, "timeout": self.timeout}
                )
                status, payload = capture.queue.pop(0)
                return _Response(status, payload)

        monkeypatch.setattr("app.services.deepseek_search.httpx.Client", Client)
        return self


def _ctx() -> ToolContext:
    return ToolContext(db=object(), message="不要把这句话当成检索词", history=[], user_id=1)


def _arm(monkeypatch, capture: _Capture) -> None:
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example/")
    monkeypatch.setattr(settings, "llm_api_key", "llm-secret")
    monkeypatch.setattr(settings, "llm_timeout", 17.0)
    monkeypatch.setattr(settings, "web_search_base_url", "https://open.bigmodel.cn/api/paas/v4/web_search")
    monkeypatch.setattr(settings, "web_search_count", 5)
    capture.install(monkeypatch)


def test_search_posts_the_deepseek_body(monkeypatch):
    capture = _Capture()
    _arm(monkeypatch, capture)
    capture.queue.append((200, {"results": []}))
    query = "  news"
    result = execute_web(_ctx(), {"query": query})
    assert result.ok is True
    assert capture.clients == 1
    posted = capture.posts[0]
    assert posted["url"] == "https://llm.example/v1/tools/search"
    assert "web_search" not in posted["url"]
    assert posted["headers"]["Authorization"] == "Bearer llm-secret"
    assert posted["timeout"] == 17.0
    assert posted["json"]["query"] == query
    assert posted["json"]["need_content"] is True
    assert posted["json"]["count"] == 5
    assert set(posted["json"]) == {"query", "count", "need_content"}
    assert "localization" not in posted["json"]

    capture.queue.append((200, {"results": []}))
    execute_web(_ctx(), {"query": "news", "ori_question": "  original"})
    body = capture.posts[1]["json"]
    assert body["ori_question"] == "  original"
    assert set(body) == {"query", "count", "need_content", "ori_question"}

    full = "a" * 128
    capture.queue.append((200, {"results": []}))
    execute_web(_ctx(), {"query": full})
    assert capture.posts[2]["json"]["query"] == full


def test_invalid_query_does_not_construct_a_client(monkeypatch):
    capture = _Capture()
    _arm(monkeypatch, capture)
    for query in ("a" * 129, "", "   ", "\n\t ", None, 12):
        page = deepseek_search(query)
        result = execute_web(_ctx(), {"query": query})
        assert isinstance(page, ToolError)
        assert result.ok is False
        assert result.denied is False
        assert result.direct_answer == page.message
    missing = execute_web(_ctx(), {})
    assert missing.ok is False
    assert missing.denied is False
    assert missing.direct_answer
    page = deepseek_search("news", ori_question="   ")
    result = execute_web(_ctx(), {"query": "news", "ori_question": "   "})
    assert isinstance(page, ToolError)
    assert result.ok is False
    assert result.denied is False
    assert capture.clients == 0
    assert capture.posts == []


def test_count_uses_five_unless_the_setting_is_in_range(monkeypatch):
    assert settings.model_fields["web_search_count"].default == 5
    capture = _Capture()
    _arm(monkeypatch, capture)
    seen: list[int] = []
    for raw, expected in ((5, 5), (0, 5), (99, 5), (50, 50)):
        monkeypatch.setattr(settings, "web_search_count", raw)
        capture.queue.append((200, {"results": []}))
        execute_web(_ctx(), {"query": "news"})
        seen.append(capture.posts[-1]["json"]["count"])
        assert seen[-1] == expected
    assert seen == [5, 5, 5, 50]


def test_http_errors_are_tool_errors_and_not_unknown_writes(monkeypatch):
    capture = _Capture()
    _arm(monkeypatch, capture)
    for status in (402, 500):
        capture.queue.append((status, {"error": {"message": "no", "type": "invalid_request_error"}}))
        before = len(capture.posts)
        page = deepseek_search("news")
        assert len(capture.posts) == before + 1
        assert isinstance(page, ToolError)
        capture.queue.append((status, {"error": {"message": "no", "type": "invalid_request_error"}}))
        before = len(capture.posts)
        result = execute_web(_ctx(), {"query": "news"})
        assert len(capture.posts) == before + 1
        assert result.ok is False
        assert result.denied is False
        assert result.direct_answer
        assert str(status) in result.direct_answer
        assert result.unknown_write is None
        assert result.evidence == []


def test_a_timeout_posts_once(monkeypatch):
    created: list[int] = []

    class Client:
        def __init__(self, *args, **kwargs):
            created.append(1)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise httpx.TimeoutException("slow")

    monkeypatch.setattr(settings, "llm_api_key", "llm-secret")
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example")
    monkeypatch.setattr("app.services.deepseek_search.httpx.Client", Client)
    result = execute_web(_ctx(), {"query": "news"})
    assert created == [1]
    assert result.ok is False
    assert result.denied is False
    assert result.direct_answer
    assert result.unknown_write is None


def test_empty_results_are_a_successful_search(monkeypatch):
    capture = _Capture()
    _arm(monkeypatch, capture)
    capture.queue.append((200, {"results": []}))
    result = execute_web(_ctx(), {"query": "news"})
    assert result.ok is True
    assert result.denied is False
    assert result.evidence == []
    assert result.direct_answer == "没有找到结果。"
    assert _result_text(result) == "没有找到结果。"


def test_a_hit_keeps_guide_fields_and_renders_a_link(monkeypatch):
    capture = _Capture()
    _arm(monkeypatch, capture)
    capture.queue.append((200, {"results": [_HIT]}))
    result = execute_web(_ctx(), {"query": "DeepSeek V4 release"})
    assert result.ok is True
    assert result.direct_answer is None
    hit = result.evidence[0]
    assert hit["index"] == 0
    assert hit["source_type"] == "web"
    assert hit["tool"] == "web"
    assert hit["filename"] == "example.com"
    assert hit["title"] == _TITLE
    assert hit["url"] == _URL
    assert hit["snippet"] == _SNIPPET
    assert hit["summary"] == _SUMMARY
    assert hit["content"] == _CONTENT
    assert hit["content"] != hit["snippet"]
    assert "rerank_score" not in hit
    assert "site_name" not in hit
    rendered = "\n".join(
        [
            f"[{_TITLE}]({_URL})",
            _SNIPPET,
            _SUMMARY,
            _CONTENT,
        ]
    )
    assert _result_text(result) == rendered

    capture.queue.append(
        (
            200,
            {
                "results": [
                    _HIT,
                    {
                        "title": "Second",
                        "url": "https://other.example/b",
                        "snippet": "s2",
                        "summary": "",
                        "content": "",
                    },
                ]
            },
        )
    )
    doubled = execute_web(_ctx(), {"query": "two"})
    assert _result_text(doubled) == rendered + "\n\n[Second](https://other.example/b)\ns2"

    capture.queue.append((200, {"results": [{"url": "https://example.com/only"}]}))
    sparse = execute_web(_ctx(), {"query": "only"})
    item = sparse.evidence[0]
    assert item["title"] == ""
    assert item["snippet"] == ""
    assert item["summary"] == ""
    assert item["content"] == ""
    assert item["filename"] == "example.com"
    assert _result_text(sparse) == "[https://example.com/only](https://example.com/only)"


def test_fetch_posts_only_the_url(monkeypatch):
    capture = _Capture()
    _arm(monkeypatch, capture)
    for url in (
        "ftp://example.com/a",
        "https://user:pass@example.com/a",
        "https://",
        "x" * 8193,
        "",
    ):
        page = deepseek_fetch(url)
        result = execute_web_fetch(_ctx(), {"url": url})
        assert isinstance(page, ToolError)
        assert result.ok is False
        assert result.denied is False
        assert result.direct_answer
    missing = execute_web_fetch(_ctx(), {})
    assert missing.ok is False
    assert missing.denied is False
    assert missing.direct_answer
    assert capture.clients == 0

    url = "https://example.com/article"
    title = "Example article"
    content = "# Example article\n\nArticle content."
    capture.queue.append((200, {"title": title, "content": content}))
    result = execute_web_fetch(_ctx(), {"url": url})
    assert capture.clients == 1
    posted = capture.posts[0]
    assert posted["url"] == "https://llm.example/v1/tools/fetch"
    assert posted["json"] == {"url": url}
    assert "web_search" not in posted["url"]
    assert result.ok is True
    assert result.denied is False
    assert result.direct_answer == ""
    evidence = result.evidence[0]
    assert evidence["title"] == title
    assert evidence["url"] == url
    assert evidence["content"] == content
    assert evidence["source_type"] == "web"
    assert evidence["tool"] == "web_fetch"
    text = _result_text(result)
    assert url in text
    assert title in text
    assert content in text


def test_execute_web_does_not_call_complete_json_or_search_planned(monkeypatch):
    called: list[str] = []

    def boom(name):
        def _inner(*_args, **_kwargs):
            called.append(name)

        return _inner

    monkeypatch.setattr("app.core.llm.complete_json", boom("complete_json"))
    monkeypatch.setattr("app.services.web_search.search_planned", boom("search_planned"))
    monkeypatch.setattr("app.services.web_search.search_api", boom("search_api"))
    import app.agents.tools.web as web_mod

    for name in ("complete_json", "search_planned", "search_api"):
        if hasattr(web_mod, name):
            monkeypatch.setattr(web_mod, name, boom(name))
    capture = _Capture()
    _arm(monkeypatch, capture)
    capture.queue.append((200, {"results": []}))
    execute_web(_ctx(), {"query": "news"})
    assert called == []
    assert capture.posts[0]["url"].endswith("/v1/tools/search")
