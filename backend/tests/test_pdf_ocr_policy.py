from app.rag.pdf_ocr_policy import PageSignals, decide_ocr, merge_native_and_ocr, useful_char_count


def test_useful_char_count_ignores_whitespace():
    assert useful_char_count("  a 你 \n") == 2
    assert useful_char_count("   \n\t") == 0


def test_decide_ocr_when_has_image():
    need, reason = decide_ocr(
        PageSignals(page_index=0, useful_chars=500, image_count=2, image_area_ratio=0.8),
        min_useful=40,
    )
    assert need is True
    assert "有图" in reason


def test_decide_ocr_skip_text_only_rich():
    need, reason = decide_ocr(
        PageSignals(page_index=0, useful_chars=300, image_count=0, image_area_ratio=0.0),
        min_useful=40,
    )
    assert need is False
    assert "纯文本" in reason


def test_decide_ocr_poor_text_fallback():
    need, reason = decide_ocr(
        PageSignals(page_index=0, useful_chars=5, image_count=0, image_area_ratio=0.0),
        min_useful=40,
    )
    assert need is True
    assert "贫文本" in reason


def test_merge_keeps_both_layers():
    out = merge_native_and_ocr("标题A\n页脚", "公式内容\n标题A")
    assert "标题A" in out
    assert "公式内容" in out
    assert "页脚" in out


if __name__ == "__main__":
    test_useful_char_count_ignores_whitespace()
    test_decide_ocr_when_has_image()
    test_decide_ocr_skip_text_only_rich()
    test_decide_ocr_poor_text_fallback()
    test_merge_keeps_both_layers()
    print("ok")
