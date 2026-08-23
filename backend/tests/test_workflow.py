from unittest.mock import MagicMock, patch

import asyncio
from contextlib import contextmanager
from datetime import datetime

from app.agents.harness.verify import web_is_stale
from app.agents.router import classify_intent, plan_needs, plan_tools
from app.agents.workflow import (
    build_pool_prompt,
    run_agent_workflow,
)
from app.services.calendar import EventDraft
from app.core.llm import _mock_stream
from app.core.prompts import max_pool_chars, today_str
from app.services.web_search import (
    SearchHit,
    SearchOptions,
    SearchOutcome,
    _rewrite_query,
    plan_search,
    rewrite_queries,
    search,
    search_api,
    search_planned,
)

_GROUNDING_PASS = {
    "grounded": True,
    "addresses_question": True,
    "unsupported": [],
}


@contextmanager
def _grounding_pass(draft: str = "根据证据的简要说明。[1]"):
    """Keep workflow tests off the live LLM while the answer node critiques."""
    with (
        patch("app.agents.workflow.complete_text", return_value=draft),
        patch(
            "app.agents.harness.critique.complete_json_schema",
            return_value=_GROUNDING_PASS,
        ),
        patch("app.agents.harness.critique.settings.llm_api_key", "fake"),
    ):
        yield


def test_tier1_kb_deixis_via_plan_tools(monkeypatch):
    import app.agents.router as router_mod

    def boom(*a, **k):
        raise AssertionError("Tier-1 deixis must not reach the LLM")

    monkeypatch.setattr(router_mod, "complete_json_schema", boom)
    p = plan_tools("文档里总结一下架构", [], has_kb_docs=True, forced_kb=False)
    assert p["needs_kb"] is True
    assert "kb" in p["pending_tools"]


def test_classify_no_docs_clamps_kb(monkeypatch):
    import app.agents.router as router_mod

    def boom(*a, **k):
        raise AssertionError("LLM should not be called for exact greeting")

    monkeypatch.setattr(router_mod, "complete_json_schema", boom)
    assert classify_intent("你好", [], has_kb_docs=True) == "chat"


def test_plan_tools_queue_order(monkeypatch):
    import app.agents.router as router_mod

    def fake_schema(messages, *, schema, name, model=None, max_attempts=None):
        return {
            "reasoning": "multi",
            "needs_kb": True,
            "needs_web": True,
            "needs_calendar": True,
            "needs_sandbox": True,
            "needs_freshness": True,
            "confidence": "high",
        }

    monkeypatch.setattr(router_mod, "complete_json_schema", fake_schema)
    p = plan_tools(
        "结合方案看最近政策并安排会议",
        [],
        has_kb_docs=True,
        forced_kb=False,
        has_tabular_docs=True,
    )
    assert p["pending_tools"] == ["kb", "web", "calendar", "sandbox"]
    n = plan_needs(
        "结合方案看最近政策并安排会议",
        [],
        has_kb_docs=True,
        forced_kb=False,
        has_tabular_docs=True,
    )
    assert n["needs_kb"] and n["needs_web"] and n["needs_calendar"] and n["needs_sandbox"]


def test_plan_tools_sandbox_without_forced_kb(monkeypatch):
    import app.agents.router as router_mod

    def fake_schema(messages, *, schema, name, model=None, max_attempts=None):
        return {
            "reasoning": "画图统计",
            "needs_kb": False,
            "needs_web": False,
            "needs_calendar": False,
            "needs_sandbox": True,
            "needs_freshness": False,
            "confidence": "high",
        }

    monkeypatch.setattr(router_mod, "complete_json_schema", fake_schema)
    p = plan_tools(
        "按报考专业分类画出所有考生四门科目的平均值的柱状图",
        [],
        has_kb_docs=True,
        forced_kb=False,
        has_tabular_docs=True,
    )
    assert p["needs_sandbox"] is True
    assert p["needs_kb"] is False
    assert p["pending_tools"] == ["sandbox"]


def test_recency_ladder_widens_on_retry():
    with patch("app.services.web_search.rewrite_queries", return_value=["微博热搜"]):
        o1 = plan_search("搜索现在的微博热搜前十条", iteration=1)
        o2 = plan_search("搜索现在的微博热搜前十条", iteration=2)
    assert o1.recency_filter == "oneDay"
    assert o2.recency_filter == "oneWeek"
    assert o1.count >= 8
    assert o2.count == 8


def test_anchored_rewrite_fallback():
    assert "新闻" in _rewrite_query("请帮我搜索一下今天的新闻是什么")
    assert "搜索一下" not in _rewrite_query("请帮我搜索一下AI政策")


def test_rewrite_iter1_skips_llm():
    with patch("app.services.web_search.complete_json") as mock_json:
        qs = rewrite_queries("请帮我搜索一下今天的新闻是什么", iteration=1)
    mock_json.assert_not_called()
    assert len(qs) == 1
    assert len(qs[0]) >= 4


def test_llm_rewrite_on_retry():
    with patch(
        "app.services.web_search.complete_json",
        return_value={"queries": ["人工智能行业政策 2026", "AI 监管 2026"]},
    ):
        qs = rewrite_queries("帮我搜一下现在有什么最新的AI政策", iteration=2)
    assert len(qs) <= 2
    assert "人工智能行业政策 2026" in qs


def test_search_planned_uses_search_api():
    fake = SearchOutcome(
        results=[SearchHit(title="t", url="https://ex.com/1", snippet="s")],
        query_used="rewritten-q",
        count=8,
        recency_filter="oneDay",
        query_rewritten=True,
    )
    with (
        patch("app.services.web_search.rewrite_queries", return_value=["rewritten-q"]),
        patch("app.services.web_search.search_api", return_value=fake) as api_mock,
    ):
        out = search_planned("帮我搜索今天新闻", iteration=1)
    api_mock.assert_called_once()
    assert api_mock.call_args[0][0] == "rewritten-q"
    assert out.results


def test_search_without_options_skips_llm():
    with patch("app.services.web_search.complete_json") as mock_json:
        with patch("app.services.web_search.search_api", return_value=SearchOutcome(query_used="q")):
            search("北京天气")
    mock_json.assert_not_called()


def test_search_api_is_http_only():
    with patch("app.services.web_search.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value.json.return_value = {
            "search_result": []
        }
        client_cls.return_value.__enter__.return_value.post.return_value.raise_for_status = lambda: None
        with patch("app.core.config.settings.llm_api_key", "test-key"):
            search_api("天气", options=SearchOptions(query="天气", count=5))
    client_cls.return_value.__enter__.return_value.post.assert_called_once()


def test_web_is_stale_fresh_dates():
    web = [
        {"title": "新闻", "publish_date": today_str(), "snippet": "x"},
        {"title": "快讯", "publish_date": today_str(), "snippet": "y"},
    ]
    assert not web_is_stale(web)


def test_web_is_stale_old_dates():
    web = [
        {"title": "旧闻", "publish_date": "2020-01-01", "snippet": "x"},
        {"title": "回顾", "publish_date": "2019-06-01", "snippet": "y"},
    ]
    assert web_is_stale(web)


def test_pool_prompt_and_citations_aligned():
    long = "字" * 2000
    budget = max_pool_chars()
    evidence = [
        {
            "index": 1,
            "source_type": "kb",
            "filename": "a.md",
            "content": long,
            "snippet": long,
        },
        {
            "index": 2,
            "source_type": "web",
            "filename": "news",
            "title": "标题",
            "url": "https://ex.com",
            "content": "短",
            "snippet": "短",
            "publish_date": "2026-08-04",
        },
    ]
    prompt, included = build_pool_prompt(evidence)
    assert today_str() in prompt
    assert "今天是" in prompt
    assert "已截断" in prompt
    assert len(included) <= len(evidence)
    assert included[-1]["index"] == len(included)
    assert included[0]["index"] == 1


def test_pool_omits_excess_evidence():
    chunk = "x" * 3000
    evidence = [
        {
            "index": i,
            "source_type": "kb",
            "filename": f"f{i}.md",
            "content": chunk,
            "snippet": chunk,
        }
        for i in range(1, 12)
    ]
    prompt, included = build_pool_prompt(evidence)
    assert len(included) < len(evidence)
    assert "略去" in prompt


def test_search_api_uses_refer_fallback():
    sample = {
        "search_result": [
            {
                "title": "t2",
                "link": "",
                "content": "b2",
                "refer": "https://fallback.com/b",
            }
        ]
    }
    with patch("app.services.web_search.httpx.Client") as client_cls:
        resp = MagicMock()
        resp.json.return_value = sample
        resp.raise_for_status = lambda: None
        client_cls.return_value.__enter__.return_value.post.return_value = resp
        with patch("app.core.config.settings.llm_api_key", "test-key"):
            out = search_api("q", options=SearchOptions(query="q", count=1))
    assert out.results[0].url == "https://fallback.com/b"


def test_search_api_falls_back_when_std_has_no_links():
    no_link = {
        "search_result": [
            {"title": "t1", "link": "", "content": "c1", "refer": "ref_1"},
        ]
    }
    with_link = {
        "search_result": [
            {
                "title": "t2",
                "link": "https://example.com/a",
                "content": "c2",
                "refer": "ref_1",
            },
        ]
    }
    with patch("app.services.web_search.httpx.Client") as client_cls:
        resp1 = MagicMock()
        resp1.json.return_value = no_link
        resp1.raise_for_status = lambda: None
        resp2 = MagicMock()
        resp2.json.return_value = with_link
        resp2.raise_for_status = lambda: None
        client_cls.return_value.__enter__.return_value.post.side_effect = [resp1, resp2]
        with (
            patch("app.core.config.settings.llm_api_key", "test-key"),
            patch("app.core.config.settings.web_search_engine", "search_std"),
        ):
            out = search_api("q", options=SearchOptions(query="q", count=1))
    assert out.results[0].url == "https://example.com/a"
    assert client_cls.return_value.__enter__.return_value.post.call_count == 2


def test_web_only_one_tool_round():
    fake = SearchOutcome(
        results=[SearchHit(title="新闻", url="https://n.example/1", snippet="摘要", media="站")],
        query_used="q",
        count=8,
        recency_filter="oneDay",
        query_rewritten=True,
    )
    with (
        patch("app.agents.tools.web.search_planned", return_value=fake),
        patch(
            "app.agents.workflow.plan_tools",
            return_value={
                "pending_tools": ["web"],
                "needs_freshness": True,
                "needs_kb": False,
                "needs_web": True,
            },
        ),
        _grounding_pass(),
    ):
        r = run_agent_workflow(object(), message="今天有什么 AI 新闻", history=[], has_kb_docs=False)  # type: ignore[arg-type]
    assert r["intent"] == "web_search"
    assert r["citations"][0]["url"] == "https://n.example/1"
    assert any("正在搜索" in s for s in r["thinking_steps"])
    assert r["llm_messages"] == []
    assert r["direct_answer"]
    assert "依据核验说明" not in r["direct_answer"]
    assert any("正在根据证据核对结论" in s for s in r["thinking_steps"])
    assert any("依据核验通过" in s for s in r["thinking_steps"])
    assert not any("改写后重试" in s for s in r["thinking_steps"])


def test_kb_then_web_separate_rounds():
    fake = SearchOutcome(
        results=[
            SearchHit(
                title="政策",
                url="https://gov.example/p",
                snippet="最近政策",
                media="官网",
                publish_date=today_str(),
            )
        ],
        query_used="q",
        count=8,
        recency_filter="oneDay",
    )

    class C:
        filename = "方案.md"
        snippet = "流程"
        content = "流程"
        heading = ""
        page = None

    with (
        patch(
            "app.agents.workflow.plan_tools",
            return_value={
                "pending_tools": ["kb", "web"],
                "needs_freshness": True,
                "needs_kb": True,
                "needs_web": True,
            },
        ),
        patch("app.agents.tools.kb.retrieve", return_value=[C()]),
        patch("app.agents.tools.web.search_planned", return_value=fake) as web_mock,
        _grounding_pass(),
    ):
        r = run_agent_workflow(
            object(),  # type: ignore[arg-type]
            message="结合我上传的方案看看最近政策更新",
            history=[],
            has_kb_docs=True,
        )
    assert {c["source_type"] for c in r["citations"]} == {"kb", "web"}
    assert r["llm_messages"] == []
    assert r["direct_answer"]
    assert any("知识库" in s for s in r["thinking_steps"])
    assert any("正在搜索" in s for s in r["thinking_steps"])
    assert any("继续下一工具" in s for s in r["thinking_steps"])
    assert web_mock.call_count == 1
    assert not any("改写后重试" in s for s in r["thinking_steps"])


def test_mock_stream_evidence_pool():
    async def _run():
        parts = []
        async for ch in _mock_stream(
            [
                {"role": "system", "content": "规则\n【证据池】\n[1] 知识库"},
                {"role": "user", "content": "总结"},
            ]
        ):
            parts.append(ch)
        return "".join(parts)

    text = asyncio.run(_run())
    assert "证据池" in text or "演示模式" in text
    assert "总结" in text


def test_calendar_missing_fields_clarifies():
    with (
        patch(
            "app.agents.workflow.plan_tools",
            return_value={
                "pending_tools": ["calendar"],
                "needs_freshness": False,
                "needs_kb": False,
                "needs_web": False,
                "needs_calendar": True,
            },
        ),
        patch(
            "app.agents.tools.calendar_tool.extract_event",
            return_value=EventDraft(title="周会", missing_fields=["end_at", "participants"]),
        ),
    ):
        r = run_agent_workflow(object(), message="帮我定明天十点周会", history=[], has_kb_docs=False)  # type: ignore[arg-type]
    assert r["intent"] == "calendar"
    assert r["schedule_card"] is None
    assert "结束时间" in r["direct_answer"]
    assert "参与人" in r["direct_answer"]
    assert r["pending_calendar"]["missing_fields"] == ["end_at", "participants"]
    assert r["llm_messages"] == []


def test_calendar_success_creates_card():
    draft = EventDraft(
        title="周会",
        start_at=datetime(2026, 8, 6, 10, 0),
        end_at=datetime(2026, 8, 6, 11, 0),
        participants=["张三"],
        missing_fields=[],
    )

    class E:
        id = 11
        title = "周会"
        start_at = datetime(2026, 8, 6, 10, 0)
        end_at = datetime(2026, 8, 6, 11, 0)
        participants = '["张三"]'
        status = "active"

    with (
        patch(
            "app.agents.workflow.plan_tools",
            return_value={
                "pending_tools": ["calendar"],
                "needs_freshness": False,
                "needs_kb": False,
                "needs_web": False,
                "needs_calendar": True,
            },
        ),
        patch("app.agents.tools.calendar_tool.extract_event", return_value=draft),
        patch("app.agents.tools.calendar_tool.check_conflict", return_value=[]),
        patch("app.agents.tools.calendar_tool.create_event", return_value=E()),
    ):
        r = run_agent_workflow(object(), message="帮我定明天十点到十一点周会", history=[], has_kb_docs=False)  # type: ignore[arg-type]
    assert r["intent"] == "calendar"
    assert r["schedule_card"]["id"] == 11
    assert any("日程创建成功" in s for s in r["thinking_steps"])
    assert "已为你安排" in r["direct_answer"]


def test_pending_calendar_skips_planner_and_fills_multiple_fields():
    pending = EventDraft(
        title="会议",
        start_at=datetime(2026, 8, 6, 3, 0),
        end_at=None,
        participants=[],
        missing_fields=["end_at", "participants"],
        original_request="帮我定明天早上3点的会议",
    )
    filled = EventDraft(
        title="会议",
        start_at=datetime(2026, 8, 6, 3, 0),
        end_at=datetime(2026, 8, 6, 5, 0),
        participants=["张三", "李四"],
        missing_fields=[],
        original_request="帮我定明天早上3点的会议",
    )

    class E:
        id = 12
        title = "会议"
        start_at = datetime(2026, 8, 6, 3, 0)
        end_at = datetime(2026, 8, 6, 5, 0)
        participants = '["张三","李四"]'
        status = "active"

    with (
        patch("app.agents.workflow.plan_tools") as planner,
        patch("app.agents.tools.calendar_tool.fill_pending_calendar", return_value=filled),
        patch("app.agents.tools.calendar_tool.check_conflict", return_value=[]),
        patch("app.agents.tools.calendar_tool.create_event", return_value=E()),
    ):
        r = run_agent_workflow(
            object(),  # type: ignore[arg-type]
            message="到5点，参与人张三、李四",
            history=[{"role": "assistant", "content": "还缺结束时间、参与人。"}],
            has_kb_docs=False,
            pending_calendar=pending.to_dict(),
        )
    planner.assert_not_called()
    assert r["intent"] == "calendar"
    assert r["schedule_card"]["id"] == 12
    assert r["pending_calendar"] is None
    assert r["llm_messages"] == []


def test_pending_calendar_partial_fill_keeps_calendar_flow():
    pending = EventDraft(
        title="会议",
        start_at=datetime(2026, 8, 6, 3, 0),
        end_at=None,
        participants=[],
        missing_fields=["end_at", "participants"],
        original_request="帮我定明天早上3点的会议",
    )
    partial = EventDraft(
        title="会议",
        start_at=datetime(2026, 8, 6, 3, 0),
        end_at=None,
        participants=["张三"],
        missing_fields=["end_at"],
        original_request="帮我定明天早上3点的会议",
    )
    with (
        patch("app.agents.workflow.plan_tools") as planner,
        patch("app.agents.tools.calendar_tool.fill_pending_calendar", return_value=partial),
    ):
        r = run_agent_workflow(
            object(),  # type: ignore[arg-type]
            message="参与人张三",
            history=[],
            has_kb_docs=False,
            pending_calendar=pending.to_dict(),
        )
    planner.assert_not_called()
    assert r["intent"] == "calendar"
    assert r["schedule_card"] is None
    assert r["pending_calendar"]["missing_fields"] == ["end_at"]
    assert "结束时间" in r["direct_answer"]


def test_sandbox_tool_produces_artifact():
    from app.services.data_analysis import AnalysisOutcome

    outcome = AnalysisOutcome(
        ok=True,
        steps=["选用数据文件：sales.csv", "沙箱执行成功"],
        stdout="sum=10",
        evidence_text="sum=10",
        filename="sales.csv",
        artifact={
            "kind": "image",
            "title": "分析图表",
            "language": "png",
            "content": "data:image/png;base64,xx",
        },
    )
    with (
        patch(
            "app.agents.workflow.plan_tools",
            return_value={
                "pending_tools": ["sandbox"],
                "needs_freshness": False,
                "needs_kb": False,
                "needs_web": False,
                "needs_calendar": False,
                "needs_sandbox": True,
            },
        ),
        patch("app.agents.tools.sandbox_tool.run_analysis", return_value=outcome),
        _grounding_pass(),
    ):
        r = run_agent_workflow(
            object(),  # type: ignore[arg-type]
            message="汇总销售额并画图",
            history=[],
            has_kb_docs=True,
            has_tabular_docs=True,
        )
    assert r["intent"] == "data_analysis"
    assert r["artifact"]["kind"] == "image"
    assert any("沙箱" in s for s in r["thinking_steps"])


def test_tier1_greeting_skips_llm(monkeypatch):
    import app.agents.router as router_mod

    def boom(*a, **k):
        raise AssertionError("LLM should not run for certain chat")

    monkeypatch.setattr(router_mod, "complete_json_schema", boom)
    p = plan_tools("你好", [], has_kb_docs=True, forced_kb=False)
    assert p["pending_tools"] == []


def test_tier1_kb_deixis_skips_llm(monkeypatch):
    import app.agents.router as router_mod

    def boom(*a, **k):
        raise AssertionError("LLM should not run for certain KB deixis")

    monkeypatch.setattr(router_mod, "complete_json_schema", boom)
    p = plan_tools("文档里总结架构", [], has_kb_docs=True, forced_kb=False)
    assert p["needs_kb"] is True


def test_empty_kb_uses_prd_copy():
    from app.core.messages import EMPTY_KB_MESSAGE

    with (
        patch(
            "app.agents.workflow.plan_tools",
            return_value={
                "pending_tools": ["kb"],
                "needs_freshness": False,
                "needs_kb": True,
                "needs_web": False,
                "needs_calendar": False,
                "needs_sandbox": False,
            },
        ),
        patch("app.agents.tools.kb.retrieve", return_value=[]),
    ):
        r = run_agent_workflow(
            object(),  # type: ignore[arg-type]
            message="制度里正常工作时间是几点",
            history=[],
            use_kb=True,
            has_kb_docs=True,
        )
    assert r["direct_answer"] == EMPTY_KB_MESSAGE
    assert r["llm_messages"] == []
    assert r["citations"] == []


if __name__ == "__main__":
    # Router tests need the monkeypatch fixture; run them via pytest.
    test_recency_ladder_widens_on_retry()
    test_anchored_rewrite_fallback()
    test_rewrite_iter1_skips_llm()
    test_llm_rewrite_on_retry()
    test_search_planned_uses_search_api()
    test_search_without_options_skips_llm()
    test_search_api_is_http_only()
    test_web_is_stale_fresh_dates()
    test_web_is_stale_old_dates()
    test_pool_prompt_and_citations_aligned()
    test_pool_omits_excess_evidence()
    test_search_api_uses_refer_fallback()
    test_search_api_falls_back_when_std_has_no_links()
    test_web_only_one_tool_round()
    test_kb_then_web_separate_rounds()
    test_mock_stream_evidence_pool()
    test_calendar_missing_fields_clarifies()
    test_calendar_success_creates_card()
    test_pending_calendar_skips_planner_and_fills_multiple_fields()
    test_pending_calendar_partial_fill_keeps_calendar_flow()
    test_sandbox_tool_produces_artifact()
    print("ok")
