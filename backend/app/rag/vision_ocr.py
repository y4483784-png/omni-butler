"""Zhipu / OpenAI-compatible multimodal OCR via chat.completions + image_url."""

from __future__ import annotations

import base64

from app.core.config import settings
from app.core.redis import model_slot

OCR_PROMPT = (
    "请完整提取这张图片或文档页中的全部可读文字（含标题、正文、列表、表格内容、公式旁标注）。"
    "按阅读顺序输出纯文本；表格用 Markdown 表格或用 | 分隔。"
    "不要总结、不要翻译、不要添加解释。若几乎无字，只输出空字符串。"
)


def ocr_image_bytes(image_bytes: bytes, mime: str = "image/png", model: str | None = None) -> str:
    """Synchronously OCR one image using the configured vision model."""
    if not settings.llm_api_key:
        raise ValueError("未配置 LLM_API_KEY，无法使用多模态 OCR")
    if not settings.vision_ocr_enabled:
        raise ValueError("多模态 OCR 已关闭（VISION_OCR_ENABLED=false）")

    from openai import OpenAI

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=1,
    )
    try:
        model_name = model or settings.vision_model
        with model_slot(model_name):
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": OCR_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
            )
        content = (resp.choices[0].message.content or "").strip()
        return content
    finally:
        try:
            client.close()
        except Exception:
            pass
