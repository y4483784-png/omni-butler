from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from pydantic import Field
from pydantic import AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look in backend/.env first, then project-root .env — so the key is found
        # no matter whether uvicorn is launched from backend/ or the repo root.
        env_file=(".env", "../.env"),
        extra="ignore",
    )

    # App
    app_name: str = "Omni-Butler"

    # LLM (provider-agnostic via OpenAI-compatible base_url)
    # ADR-005 (revised): one async-generator seam; we call the OpenAI Python SDK
    # against a configurable base_url so OpenAI / 智谱 GLM / 本地 vLLM 都可切换，
    # 无需沉重的 LiteLLM 网关依赖。超时由 client 控制，防止连接挂死。
    # 兼容旧字段名 OPENAI_API_KEY（你当前 .env 里就是用它）。
    llm_api_key: str = Field(default="", validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"))
    llm_base_url: str = "https://api.openai.com/v1"  # OpenAI 兼容端点；智谱填 https://open.bigmodel.cn/api/paas/v4/
    llm_model: str = "gpt-4o-mini"
    chat_model: str = ""                             # empty → llm_model
    llm_timeout: float = 120.0                       # 单次流式请求超时（秒）
    llm_max_retries: int = 3                         # PRD §5：超时后自动重试 3 次
    # Generation knobs, recorded in every eval report so a run's numbers can be
    # reproduced (or explained) later.
    llm_temperature: float = 0.2
    llm_max_tokens: int = 2048
    # Tool router: lighter model preferred; empty → planner_model → llm_model
    planner_model: str = ""
    router_model: str = ""
    # Eval judge: scoring with the model that produced the answer is self-grading
    # (and measured 0.34 answer_relevancy on this family), so keep it separate.
    judge_model: str = "glm-4-air"
    router_max_attempts: int = 2                     # schema validate + retry bound
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "embedding-3"
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = "glm-4.6v"
    vision_ocr_enabled: bool = True
    # PDF：有嵌入图即 OCR；无图时 useful_chars 低于此值才 OCR（兜底）
    ocr_min_useful_chars: int = Field(
        default=40,
        validation_alias=AliasChoices("OCR_MIN_USEFUL_CHARS", "OCR_MIN_CHARS_PER_PAGE"),
    )
    ocr_max_pages: int = 80  # 单文档最多 OCR 页数，防止费用失控

    # Storage (default SQLite so the skeleton runs without Docker;
    # production uses Postgres, e.g. postgresql+psycopg://omni:omni@localhost:5432/omni_butler)
    database_url: str = "sqlite:///./omni_butler.db"
    # Superuser/owner URL for CREATE ROLE / Alembic / CREATE TABLE. Empty = database_url.
    # Compose sets this to omni; runtime DATABASE_URL is omni_app so RLS is not bypassed.
    database_migrate_url: str = ""
    db_pool_size: int = 10        # Postgres only; ignored for SQLite
    db_max_overflow: int = 20
    enforce_secure_secrets: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "omni_chunks"
    # User uploads: MinIO/S3 only; DB stores object keys, never host paths
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "omni-uploads"
    s3_region: str = "us-east-1"

    # Async / cache
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    router_cache_ttl: int = 3 * 24 * 60 * 60
    embedding_cache_ttl: int = 7 * 24 * 60 * 60
    web_cache_ttl: int = 5 * 60
    chat_rate_limit: int = 30
    chat_rate_window_seconds: int = 60
    # Comma-separated exact model overrides, e.g. "glm-4-air=100,glm-4-flashx=50".
    llm_model_concurrency_limits: str = ""
    llm_concurrency_default: int = 10
    llm_slot_lease_seconds: int = 180
    # Durable background jobs. Empty broker URL reuses redis_url.
    celery_broker_url: str = ""
    celery_task_always_eager: bool = False
    celery_ingest_soft_time_limit: int = 1740
    celery_ingest_time_limit: int = 1800
    celery_stale_after_seconds: int = 3600
    celery_stale_check_seconds: int = 300

    # Chat behaviour (PRD: last 10 turns sliding window of verbatim turns)
    max_context_turns: int = 10
    # ContextManager (H5): budget + summary + working state; false → legacy window only
    context_manager_enabled: bool = True
    context_max_tokens: int = 32000  # soft budget, not model physical window
    context_reserve_ratio: float = 0.25  # headroom for output / grounding / tools
    context_warn_ratio: float = 0.70
    context_compact_ratio: float = 0.85
    context_emergency_ratio: float = 0.95
    context_cjk_chars_per_token: float = 1.6  # Zhipu GLM ~1:1.6
    context_latin_chars_per_token: float = 4.0
    context_summary_enabled: bool = True
    context_summary_max_chars: int = 1200
    context_summary_min_overflow_turns: int = 2
    context_router_turns: int = 3
    context_router_chars: int = 300
    context_tool_turns: int = 4
    context_working_state_max_chars: int = 600
    context_pool_chars_warn: int = 8000
    context_pool_chars_emergency: int = 4000
    # Post-draft grounding critique (JSON call before SSE emit)
    grounding_enabled: bool = True
    # Fail → one Reflexion-style rewrite with critique feedback; then stop
    grounding_repair_enabled: bool = True


    # RAG：关键词 + 向量混合（Qdrant 不可达时自动降级为关键词）
    chunk_size: int = 600
    chunk_overlap: int = 80
    chunk_min_size_target: int = 180
    rag_top_k: int = 6
    rag_vector_top_k: int = 8
    rag_hybrid_enabled: bool = True
    # retrieval-resume: candidates → adjacency expand → rerank → top_k
    rag_candidate_k: int = 20
    rag_expand_window: int = 1
    rag_expand_max_chars: int = 2400
    rag_rerank_enabled: bool = True
    rag_rerank_provider: str = "zhipu"  # zhipu | auto | none（none=仅启发式）
    rerank_base_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = "rerank"
    max_upload_mb: int = 20

    # Tabular ingest (csv/xlsx full-table text for RAG)
    tabular_max_rows: int = 5000
    tabular_max_chars: int = 800_000
    tabular_batch_rows: int = 40

    # Web Search（智谱 POST {llm_base_url}/web_search；复用 LLM_API_KEY）
    web_search_engine: str = "search_pro_quark"  # search_std/pro omit link; quark/sogou include URLs
    web_search_count: int = 5

    # Code sandbox (ADR-004): docker run --network=none --read-only
    sandbox_enabled: bool = True
    sandbox_image: str = "omni-sandbox"
    sandbox_timeout_sec: int = 30
    sandbox_memory: str = "256m"
    sandbox_pids_limit: int = 64
    sandbox_max_retries: int = 3  # self-correct attempts after first failure
    # When set (Compose api), temp files used as docker -v sources live here so the
    # engine sees the same host path. Empty → OS temp (venv / pytest).
    sandbox_tmp_dir: str = ""
    # Compose api → http://sandbox-runner:8002. Empty → local docker run (venv / pytest).
    sandbox_runner_url: str = ""

    # Long-term memory (Harness H3 / PRD Phase 4 MVP)
    memory_enabled: bool = True
    memory_use_llm: bool = True  # false → heuristic-only extraction
    memory_max_chars: int = 800  # injection budget into system prompt
    memory_max_items: int = 40  # auto-extract stops adding beyond this

    # Sensitive-word filter (PRD 4.2): input blocked, output redacted
    sensitive_filter_enabled: bool = True
    sensitive_use_builtin: bool = True
    sensitive_words: str = ""  # comma-separated extra terms

    # Tabular ingest (csv/xlsx full-table text for RAG)
    tabular_max_rows: int = 5000
    tabular_max_chars: int = 800_000
    tabular_batch_rows: int = 40

    # Auth / session (multi-user isolation)
    secret_key: str = Field(default="change-me-in-production", validation_alias="SECRET_KEY")
    session_cookie_name: str = "omni_session"
    session_max_age_seconds: int = 7 * 24 * 60 * 60
    session_cookie_secure: bool = False  # set true behind HTTPS
    admin_username: str = Field(default="admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="", validation_alias="ADMIN_PASSWORD")
    # Google Calendar OAuth. Empty client id keeps calendar writes on the local provider.
    # CALENDAR_TOKEN_KEY is a Fernet key (urlsafe base64 of 32 bytes), not derived from SECRET_KEY.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    calendar_token_key: str = ""

    # CORS
    cors_origins: str = "http://localhost:5173"


settings = Settings()

# 友好默认：若未显式配置 base_url 且模型是智谱 GLM，则自动指向智谱的
# OpenAI 兼容端点，让你现有的 backend/.env（OPENAI_API_KEY + LLM_MODEL=glm-*）开箱即用。
_DEFAULT_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4/"
if settings.llm_base_url == "https://api.openai.com/v1" and settings.llm_model.lower().startswith("glm"):
    settings.llm_base_url = _DEFAULT_ZHIPU_BASE


_ZHIPU_RERANK_PROVIDERS = frozenset({"zhipu", "auto", ""})


def _zhipu_rerank_selected(s: Settings) -> bool:
    return s.rag_rerank_enabled and (s.rag_rerank_provider or "zhipu").strip().lower() in _ZHIPU_RERANK_PROVIDERS


@dataclass(frozen=True)
class _Row:
    base_url_env: str
    api_key_env: str
    in_use: Callable[[Settings], bool]
    off_switch: str

    @property
    def base_url_field(self) -> str:
        return self.base_url_env.lower()

    @property
    def api_key_field(self) -> str:
        return self.api_key_env.lower()


class ZhipuService(Enum):
    EMBEDDING = _Row(
        "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY", lambda s: s.rag_hybrid_enabled, "RAG_HYBRID_ENABLED=false"
    )
    VISION = _Row("VISION_BASE_URL", "VISION_API_KEY", lambda s: s.vision_ocr_enabled, "VISION_OCR_ENABLED=false")
    RERANK = _Row("RERANK_BASE_URL", "RERANK_API_KEY", _zhipu_rerank_selected, "RAG_RERANK_ENABLED=false")


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.base_url.strip() or not self.api_key.strip():
            raise ValueError("Endpoint needs base_url and api_key; build it with zhipu_endpoint()")


class EndpointConfigError(RuntimeError):
    """A Zhipu service is in use, or was asked for, and its URL or key is empty.

    ``missing`` holds env var names in ZhipuService order, URL before key.
    """

    def __init__(self, missing: tuple[str, ...], off_switches: tuple[str, ...]) -> None:
        self.missing = missing
        self.off_switches = off_switches
        super().__init__(
            f"Zhipu endpoint settings are empty: {', '.join(missing)}. "
            "Set them in backend/.env (LLM_BASE_URL and LLM_API_KEY are chat-only), "
            f"or turn the service off with {', '.join(off_switches)}."
        )


def _gaps(service: ZhipuService, s: Settings) -> tuple[str, ...]:
    row = service.value
    pairs = ((row.base_url_env, row.base_url_field), (row.api_key_env, row.api_key_field))
    return tuple(env for env, field_name in pairs if not getattr(s, field_name).strip())


def zhipu_endpoint(service: ZhipuService, s: Settings | None = None) -> Endpoint:
    """Ignores the service's switch: a caller that reaches here intends to call Zhipu."""
    s = s or settings
    missing = _gaps(service, s)
    if missing:
        raise EndpointConfigError(missing, (service.value.off_switch,))
    row = service.value
    return Endpoint(getattr(s, row.base_url_field).strip(), getattr(s, row.api_key_field).strip())


def zhipu_in_use(service: ZhipuService, s: Settings | None = None) -> bool:
    return service.value.in_use(s or settings)


def check_zhipu_endpoints(s: Settings | None = None) -> None:
    s = s or settings
    bad = [(svc, gaps) for svc in ZhipuService if svc.value.in_use(s) and (gaps := _gaps(svc, s))]
    if bad:
        raise EndpointConfigError(
            tuple(name for _, gaps in bad for name in gaps),
            tuple(svc.value.off_switch for svc, _ in bad),
        )
