from app.core.messages import (
    KB_PARSE_FAIL_MESSAGE,
    LLM_TIMEOUT_MESSAGE,
    SANDBOX_TIMEOUT_MESSAGE,
    ingest_user_error,
    llm_user_error,
)


def test_llm_timeout_copy():
    assert llm_user_error(TimeoutError("deadline exceeded")) == LLM_TIMEOUT_MESSAGE


class APITimeoutError(Exception):
    pass


def test_llm_timeout_by_exception_name():
    assert llm_user_error(APITimeoutError("read timed out")) == LLM_TIMEOUT_MESSAGE


def test_llm_other_errors_keep_generic():
    msg = llm_user_error(ValueError("bad key"))
    assert msg.startswith("模型调用失败")
    assert "bad key" in msg


def test_ingest_maps_encrypted_and_parse_to_prd_copy():
    assert ingest_user_error(ValueError("文件解析失败，请确保文件未加密且格式正确。")) == KB_PARSE_FAIL_MESSAGE
    assert ingest_user_error(ValueError("分块结果为空")) == KB_PARSE_FAIL_MESSAGE


def test_ingest_keeps_unsupported_format():
    err = ingest_user_error(ValueError("格式不支持：.exe，支持 .pdf"))
    assert "格式不支持" in err


def test_sandbox_timeout_copy_constant():
    assert SANDBOX_TIMEOUT_MESSAGE == "数据处理超时，请尝试简化您的数据或分析需求。"


def test_prd_timeout_copy_constant():
    assert LLM_TIMEOUT_MESSAGE == "当前网络或大模型服务响应超时，请稍后重试。"
    assert KB_PARSE_FAIL_MESSAGE == "文件解析失败，请确保文件未加密且格式正确。"
