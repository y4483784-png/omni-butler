"""Session naming: immediate fallback + async LLM title (PRD 3.1.2).

Industry pattern (Open WebUI task model, DeerFlow TitleMiddleware):
  show a cheap fallback instantly, then replace with a 4–6 字 title after the
  first turn completes — never block TTFB on the title call.
"""

from __future__ import annotations

import logging
import re

from app.core.config import settings
from app.core.llm import complete_json, resolved_planner_model

logger = logging.getLogger(__name__)

_TITLE_SYSTEM = """为办公对话生成侧栏短标题。
只输出 JSON：{"title":"四到六个汉字"}

规则：
- 必须 4–6 个汉字（可含少量字母或数字，总可见长度不超过 6）
- 概括用户本轮要办的事，不要标点、引号、emoji、书名号
- 不要用「新会话」「助手」「你好」这类空泛词
- 用中文"""


def auto_name(_session_id: int, first_user_msg: str) -> str:
    """Immediate fallback title from the first user message (no LLM)."""
    text = (first_user_msg or "").strip()
    if not text:
        return "新会话"
    line = text.splitlines()[0].strip()
    line = re.sub(r"^[#>*\-\s`]+", "", line)
    line = line.strip("`\"' ")
    if not line:
        return "新会话"
    title = line[:12]
    return title or "新会话"


def _clean_llm_title(raw: str) -> str:
    t = (raw or "").strip().strip("\"'`「」《》【】")
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t, flags=re.UNICODE)
    t = t[:6]
    if len(t) < 4 or t in {"新会话", "助手", "你好"}:
        return ""
    return t


def generate_session_title(user_message: str, assistant_message: str = "") -> str:
    """4–6 字 LLM title; falls back to auto_name. Never raises."""
    fallback = auto_name(0, user_message)
    if not settings.llm_api_key:
        return fallback
    asst = (assistant_message or "").strip()
    if asst.startswith("模型调用失败") or asst.startswith("当前网络") or asst.startswith("⚠️"):
        return fallback
    try:
        parsed = complete_json(
            [
                {"role": "system", "content": _TITLE_SYSTEM},
                {
                    "role": "user",
                    "content": f"用户：{(user_message or '')[:400]}\n助手：{asst[:400]}",
                },
            ],
            model=resolved_planner_model(),
        )
        if isinstance(parsed, dict):
            cleaned = _clean_llm_title(str(parsed.get("title") or ""))
            if cleaned:
                return cleaned
    except Exception:
        logger.warning("generate_session_title failed", exc_info=True)
    return fallback
