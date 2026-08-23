from app.services.session import auto_name, generate_session_title, _clean_llm_title


def test_auto_name_truncates_first_line():
    assert auto_name(1, "如何配置智谱 GLM 的流式接口？") == "如何配置智谱 GLM 的"


def test_auto_name_strips_markdown_noise():
    assert auto_name(1, "## 你好世界\n第二行") == "你好世界"


def test_auto_name_empty():
    assert auto_name(1, "   ") == "新会话"


def test_clean_llm_title_keeps_four_to_six_chars():
    assert _clean_llm_title("「差旅报销」") == "差旅报销"
    assert _clean_llm_title("差旅报销流程说明") == "差旅报销流程"
    assert _clean_llm_title("嗨") == ""
    assert _clean_llm_title("周会") == ""
    assert _clean_llm_title("新会话") == ""


def test_generate_session_title_uses_llm(monkeypatch):
    monkeypatch.setattr("app.services.session.settings.llm_api_key", "k")
    monkeypatch.setattr(
        "app.services.session.complete_json",
        lambda *_a, **_k: {"title": "周会安排"},
    )
    assert generate_session_title("帮我定明早十点周会", "已创建日程") == "周会安排"


def test_generate_session_title_falls_back_without_key(monkeypatch):
    monkeypatch.setattr("app.services.session.settings.llm_api_key", "")
    assert generate_session_title("如何配置智谱 GLM 的流式接口？") == auto_name(
        0, "如何配置智谱 GLM 的流式接口？"
    )
