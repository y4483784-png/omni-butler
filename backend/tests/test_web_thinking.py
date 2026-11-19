from unittest.mock import patch

from app.agents.harness.types import ToolContext
from app.agents.tools.web import execute_web


def test_web_tool_does_not_pretend_it_read_pages():
    ctx = ToolContext(db=object(), message="今天的新闻", history=[], user_id=1, iteration=1)
    with patch("app.services.web_search.httpx.Client") as client:
        result = execute_web(ctx, {})
    client.assert_not_called()
    assert result.evidence == []
    assert result.thinking_steps == ["网页搜索改由回答请求附带，这里不再另发"]
