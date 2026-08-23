"""Tests for artifact persistence helpers."""

from app.services.artifact import infer_workspace_artifact, prepare_artifact_for_storage


def test_prepare_artifact_stores_code():
    out = prepare_artifact_for_storage(
        {"kind": "code", "title": "分析代码", "language": "python", "content": "print(1)"}
    )
    assert out is not None
    assert out["kind"] == "code"
    assert out["content"] == "print(1)"


def test_prepare_artifact_keeps_svg_and_points():
    out = prepare_artifact_for_storage(
        {
            "kind": "image",
            "title": "图",
            "content": "data:image/png;base64,xx",
            "svg": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
            "chart_points": [{"label": "华东", "series": "销售额", "value": 12.5}],
        }
    )
    assert out is not None
    assert out["svg"].startswith("<svg")
    assert out["chart_points"][0]["label"] == "华东"


def test_prepare_artifact_truncates_huge_image():
    huge = "data:image/png;base64," + ("A" * 2_000_000)
    out = prepare_artifact_for_storage({"kind": "image", "title": "图", "content": huge})
    assert out is not None
    assert out.get("truncated") is True
    assert "过大" in out["content"]


def test_infer_long_document_artifact():
    text = (
        "# 差旅报销说明\n\n引言。\n\n## 适用范围\n\n"
        + ("本制度适用于全体员工。" * 20)
        + "\n\n## 报销流程\n\n"
        + ("第一步提交单据。" * 20)
    )
    art = infer_workspace_artifact(text)
    assert art is not None
    assert art["kind"] == "document"
    assert "差旅" in art["title"]


def test_infer_code_over_fifteen_lines():
    body = "\n".join(f"print({i})" for i in range(20))
    art = infer_workspace_artifact(f"如下：\n```python\n{body}\n```\n")
    assert art is not None
    assert art["kind"] == "code"
    assert art["language"] == "python"


def test_prepare_artifact_coerces_png_payload_to_image():
    out = prepare_artifact_for_storage(
        {
            "kind": "code",
            "title": "分析图表",
            "language": "png",
            "content": "data:image/png;base64,xx",
        }
    )
    assert out is not None
    assert out["kind"] == "image"


def test_infer_skips_sandbox_script_echo():
    body = "\n".join(f"print({i})" for i in range(20))
    text = "===SUMMARY===\nrows=3\n\n```python\n" + body + "\n```\n"
    assert infer_workspace_artifact(text) is None
