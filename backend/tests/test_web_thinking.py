from unittest.mock import patch

from app.agents.harness.types import ToolContext
from app.agents.tools.web import execute_web
from app.services.web_search import SearchHit, SearchOutcome


def test_web_thinking_follows_prd_script():
    fake = SearchOutcome(
        results=[
            SearchHit(title="a", url="https://ex.example/1", snippet="s", media="m", publish_date="2026-08-18"),
            SearchHit(title="b", url="https://ex.example/2", snippet="s", media="m", publish_date="2026-08-18"),
        ],
        query_used="2026年人工智能行业报告",
        count=5,
        recency_filter="oneDay",
    )
    ctx = ToolContext(db=object(), message="今天的新闻", history=[], user_id=1, iteration=1)
    with patch("app.agents.tools.web.search_planned", return_value=fake):
        result = execute_web(ctx, {})
    assert result.thinking_steps[0] == '正在搜索:“2026年人工智能行业报告”'
    assert result.thinking_steps[1] == "正在阅读 2 个网页"
    assert result.thinking_steps[2] == "正在总结"
