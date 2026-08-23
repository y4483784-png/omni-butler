"""User-facing copy aligned with PRD §5 exception table."""

from __future__ import annotations

LLM_TIMEOUT_MESSAGE = "当前网络或大模型服务响应超时，请稍后重试。"
KB_PARSE_FAIL_MESSAGE = "文件解析失败，请确保文件未加密且格式正确。"
SANDBOX_TIMEOUT_MESSAGE = "数据处理超时，请尝试简化您的数据或分析需求。"
# PRD §3.2.2: empty retrieval must not be paraphrased by the model.
EMPTY_KB_MESSAGE = "知识库中未找到相关内容。"


def is_llm_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    needles = ("timeout", "timed out", "deadline", "apiconnection", "apitimeout")
    return any(n in name for n in needles) or any(n in msg for n in ("timeout", "timed out", "deadline exceeded"))


def llm_user_error(exc: BaseException) -> str:
    if is_llm_timeout(exc):
        return LLM_TIMEOUT_MESSAGE
    safe = f"{type(exc).__name__}: {str(exc)[:300]}"
    return f"模型调用失败：{safe}"


def ingest_user_error(exc: BaseException) -> str:
    """Map ingestion failures to PRD copy; keep format / missing-file specifics."""
    msg = str(exc)[:500]
    if "格式不支持" in msg:
        return msg
    if "上传文件丢失" in msg:
        return msg
    name = type(exc).__name__
    if name == "EmbeddingError" or "embed" in msg.lower():
        return msg
    return KB_PARSE_FAIL_MESSAGE
