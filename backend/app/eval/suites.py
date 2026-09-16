"""Suite registry: single source of truth for eval CLI + dashboard catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

EvalTarget = Literal["venv", "services"]
ProfileName = Literal["smoke", "full"]


@dataclass(frozen=True)
class SuiteSpec:
    id: str
    title: str
    kind: Literal["pytest", "eval"]
    description: str
    requires: tuple[str, ...] = ()
    targets: tuple[EvalTarget, ...] = ("venv", "services")
    key_metrics: tuple[str, ...] = ()
    # profile_name -> extra argv fragments passed to the suite implementation
    profiles: dict[str, list[str]] = field(default_factory=dict)
    # For pytest suites: pytest paths per profile
    pytest_paths: dict[str, list[str]] = field(default_factory=dict)
    # Import path for build_parser / run (eval kind), e.g. "scripts.run_rag_eval"
    module: str | None = None
    default_target: EvalTarget = "venv"
    needs_blob_stub: bool = False
    # True: even under EVAL_TARGET=services, bind scratch SQLite (avoid live FORCE RLS).
    isolate_app_db: bool = True


SUITES: dict[str, SuiteSpec] = {
    "pytest": SuiteSpec(
        id="pytest",
        title="离线回归（pytest）",
        kind="pytest",
        description="单元与契约测试；强制 SQLite，不连生产中间件。",
        requires=(),
        targets=("venv",),
        key_metrics=("metrics.passed", "metrics.failed", "metrics.skipped"),
        pytest_paths={
            "smoke": [
                "tests/test_health.py",
                "tests/test_router.py",
                "tests/test_harness_gateway.py",
                "tests/test_eval_metrics.py",
            ],
            "full": ["tests"],
        },
        default_target="venv",
    ),
    "routing": SuiteSpec(
        id="routing",
        title="办公路由评测",
        kind="eval",
        description="Intent / Tool-set / Recall@k（office_tool_routing.jsonl）。",
        requires=("llm",),
        targets=("venv", "services"),
        key_metrics=(
            "raw.intent.accuracy",
            "raw.intent.macro_f1",
            "raw.tools.exact_match_accuracy",
        ),
        profiles={
            "smoke": [],  # use disk cache if present
            "full": ["--refresh-cache", "--min-intent-acc", "0.82", "--min-tool-exact", "0.78"],
        },
        module="scripts.run_office_eval",
        default_target="venv",
        needs_blob_stub=True,
    ),
    "rag": SuiteSpec(
        id="rag",
        title="RAG 检索评测",
        kind="eval",
        description="Precision@k / Recall@k / MRR（rag_retrieval.jsonl）。",
        requires=(),
        targets=("venv", "services"),
        key_metrics=(
            "raw.metrics.precision_at_k",
            "raw.metrics.recall_at_k",
            "raw.metrics.mrr",
        ),
        profiles={
            "smoke": [],
            "full": ["--zhipu-rerank"],
        },
        module="scripts.run_rag_eval",
        default_target="venv",
        needs_blob_stub=True,
    ),
    "yz": SuiteSpec(
        id="yz",
        title="YZ 全链路评测",
        kind="eval",
        description="upload → ingest → retrieve → answer（+ 可选 ragas）。",
        requires=("llm", "minio", "qdrant"),
        targets=("services",),
        key_metrics=(
            "raw.retrieval.recall_at_k",
            "raw.fact_containment_mean",
            "raw.citation_hit_rate",
        ),
        profiles={
            "smoke": ["--limit", "10", "--skip-ragas"],
            "full": ["--zhipu-rerank"],
        },
        module="scripts.run_yz_eval",
        default_target="services",
        isolate_app_db=False,
    ),
    "grounding": SuiteSpec(
        id="grounding",
        title="依据核验忠实度",
        kind="eval",
        description="ground_and_repair + ragas faithfulness（48 题）。",
        requires=("llm", "minio"),
        targets=("services", "venv"),
        key_metrics=(
            "raw.faithfulness_mean",
            "raw.repair_rate",
            "raw.disclaimer_rate",
        ),
        profiles={
            "smoke": ["--skip-ragas", "--limit", "8"],
            "full": ["--score-drafts"],
        },
        module="scripts.run_grounding_eval",
        default_target="services",
        isolate_app_db=False,
    ),
    "tabular": SuiteSpec(
        id="tabular",
        title="办公表格短答案",
        kind="eval",
        description="Plan / Exec 准确率（office_tabular.jsonl）。",
        requires=(),  # smoke=dry-compile; full live → sandbox-runner (services) or local docker (venv)
        targets=("venv", "services"),
        key_metrics=(
            "raw.accuracy",
            "raw.plan_accuracy",
            "raw.smoke_accuracy",
        ),
        profiles={
            "smoke": ["--dry-compile", "--split", "smoke"],
            "full": ["--split", "full"],
        },
        module="scripts.run_office_tabular_eval",
        default_target="venv",
        needs_blob_stub=True,
    ),
    "context": SuiteSpec(
        id="context",
        title="ContextManager 回放",
        kind="eval",
        description="摘要 / 窗口 / 多臂对照（context_recall.jsonl）。",
        requires=(),
        targets=("venv", "services"),
        key_metrics=("raw.n", "raw.invariants.violations"),
        profiles={
            "smoke": ["--summarizer", "extractive", "--split", "smoke", "--arms", "legacy,full", "--gate"],
            "full": [
                "--summarizer",
                "extractive",
                "--split",
                "full",
                "--arms",
                "legacy,window,window_summary,full,oracle",
                "--gate",
            ],
        },
        module="scripts.run_context_eval",
        default_target="venv",
        needs_blob_stub=True,
    ),
}


def get_suite(suite_id: str) -> SuiteSpec:
    if suite_id not in SUITES:
        known = ", ".join(sorted(SUITES))
        raise KeyError(f"unknown suite {suite_id!r}; known: {known}")
    return SUITES[suite_id]


def list_suites() -> list[SuiteSpec]:
    return list(SUITES.values())


def profile_argv(suite: SuiteSpec, profile: ProfileName) -> list[str]:
    if suite.kind == "pytest":
        return list(suite.pytest_paths.get(profile) or suite.pytest_paths.get("smoke") or [])
    return list(suite.profiles.get(profile) or [])


def load_suite_module(suite: SuiteSpec) -> Any:
    """Import suite module that exposes build_parser() and run(args)."""
    if not suite.module:
        raise RuntimeError(f"suite {suite.id} has no module")
    import importlib

    return importlib.import_module(suite.module)


# Type alias for dashboard adapters
SuiteLoader = Callable[[], list[SuiteSpec]]
