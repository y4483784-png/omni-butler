from unittest.mock import patch

from app.agents.harness.types import ToolContext
from app.agents.tools.web import execute_web
from app.services.deepseek_search import SearchHit, SearchPage


def test_web_thinking_names_the_query_the_model_sent():
    page = SearchPage(
        results=(
            SearchHit(
                title="a",
                url="https://ex.example/1",
                snippet="s",
                summary="",
                content="",
            ),
            SearchHit(
                title="b",
                url="https://ex.example/2",
                snippet="s",
                summary="",
                content="",
            ),
        )
    )
    ctx = ToolContext(db=object(), message="今天的新闻", history=[], user_id=1, iteration=1)
    with patch("app.agents.tools.web.deepseek_search", return_value=page) as search:
        result = execute_web(ctx, {"query": "2026年人工智能行业报告"})
    assert search.call_args.args[0] == "2026年人工智能行业报告"
    assert result.thinking_steps[0] == '正在搜索:“2026年人工智能行业报告”'
    assert result.evidence[0]["url"] == "https://ex.example/1"
    assert result.evidence[1]["url"] == "https://ex.example/2"
