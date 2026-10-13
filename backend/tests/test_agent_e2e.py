"""Agent E2E eval helpers (no live LLM)."""

from __future__ import annotations

import pytest

from app.eval.agent_e2e import (
    TaskResult,
    _resolve_document_ids,
    aggregate,
    stratified_sample,
    workflow_kwargs,
)
from app.eval.judges import judge_completion


@pytest.mark.unit
def test_stratified_sample_covers_types():
    items = [
        {"id": f"{tt}-{i}", "task_type": tt, "history": [] if i % 2 == 0 else [{"role": "user", "content": "x"}]}
        for tt in ("fact_qa", "compute", "refuse", "chat")
        for i in range(8)
    ]
    sample = stratified_sample(items, 8)
    types = {r["task_type"] for r in sample}
    assert types == {"fact_qa", "compute", "refuse", "chat"}
    assert len(sample) == 8


@pytest.mark.unit
def test_wrapped_llm_infra_is_not_scored_as_empty_answer(monkeypatch):
    from app.core.llm import LLMInfraError
    from app.eval.agent_e2e import run_task

    def boom(*_a, **_k):
        raise RuntimeError("answer node failed") from LLMInfraError("APITimeoutError: timed out")

    monkeypatch.setattr("app.agents.workflow.run_agent_workflow", boom)
    task = {
        "id": "t",
        "query": "你好",
        "task_type": "chat",
        "history": [],
        "route_gold": {"intent": "chat", "tools": []},
        "context": {
            "has_kb_docs": True,
            "has_tabular_docs": True,
            "document_keys": [],
            "pending_calendar": None,
        },
    }
    res = run_task(object(), task)
    assert res.failed_layer == "infra_error"
    assert res.verdict == {}
    assert "empty_answer" not in (res.error or "")


def _chat_task() -> dict:
    return {
        "id": "t",
        "query": "公司几点上班",
        "task_type": "fact_qa",
        "history": [],
        "route_gold": {"intent": "rag", "tools": ["kb"]},
        "context": {
            "has_kb_docs": True,
            "has_tabular_docs": True,
            "document_keys": [],
            "pending_calendar": None,
        },
    }


@pytest.mark.unit
def test_router_infra_is_infra_error_not_workflow_error(monkeypatch):
    from app.agents.router import RouterInfraError
    from app.eval.agent_e2e import run_task

    def boom(*_a, **_k):
        raise RouterInfraError("工具规划失败：RateLimitError: slow down")

    # Through the real graph, not a pre-wrapped workflow exception: a wrapper
    # that dropped the RouterInfraError type would score this as workflow_error.
    monkeypatch.setattr("app.agents.workflow.plan_tools", boom)
    res = run_task(object(), _chat_task())
    assert res.failed_layer == "infra_error"
    assert res.completion_ok is False


@pytest.mark.unit
def test_router_schema_error_stays_in_core_denominator(monkeypatch):
    from app.agents.router import RouterError
    from app.eval.agent_e2e import run_task

    def boom(*_a, **_k):
        raise RouterError("工具规划失败：schema 校验失败")

    monkeypatch.setattr("app.agents.workflow.run_agent_workflow", boom)
    res = run_task(object(), _chat_task())
    assert res.failed_layer == "workflow_error"

    rows = [
        TaskResult("a", "q", "fact_qa", False, completion_ok=True),
        res,
        TaskResult("c", "q", "fact_qa", False, failed_layer="infra_error"),
    ]
    report = aggregate(rows)
    # Schema miss stays scored; the infra row does not. 1 of 2 core rows completed.
    assert report["core_n"] == 2
    assert report["core_completion_rate"] == pytest.approx(0.5)
    assert report["n_infra_error"] == 1


def test_aggregate_excludes_infra_from_completion():
    rows = [
        TaskResult("a", "q", "fact_qa", False, completion_ok=True),
        TaskResult("b", "q", "fact_qa", False, failed_layer="infra_error"),
        TaskResult("c", "q", "refuse", False, completion_ok=False, failed_layer="answer"),
    ]
    rows[0].completion_ok = True
    report = aggregate(rows)
    assert report["n"] == 3
    assert report["n_scored"] == 2
    assert report["n_infra_error"] == 1
    assert report["task_completion_rate"] == 0.5
    assert "task_completion_ci95" in report


@pytest.mark.unit
def test_compute_judge_requires_sandbox():
    v = judge_completion(
        "compute",
        "合计 12",
        answer_spec={"type": "number", "value": 12},
        sandbox_executed=False,
    )
    assert not v.ok
    assert v.reason == "sandbox_not_executed"


@pytest.mark.unit
def test_workflow_kwargs_carry_availability_switches():
    """The availability clamps were untestable while these were hard-coded."""
    task = {
        "id": "t1",
        "query": "算一下库存周转天数",
        "history": [],
        "task_type": "no_tool",
        "context": {
            "has_kb_docs": True,
            "has_tabular_docs": False,
            "document_keys": [],
            "pending_calendar": None,
        },
    }
    kw = workflow_kwargs(task, None)
    assert kw["has_tabular_docs"] is False
    assert kw["has_kb_docs"] is True
    assert kw["pending_calendar"] is None
    assert kw["document_ids"] is None


@pytest.mark.unit
def test_workflow_kwargs_carry_pending_calendar_and_doc_scope():
    task = {
        "id": "t2",
        "query": "时间改成明天下午两点。",
        "history": [{"role": "user", "content": "帮我安排需求评审。"}],
        "task_type": "calendar",
        "context": {
            "has_kb_docs": True,
            "has_tabular_docs": True,
            "document_keys": ["office_policy_txt"],
            "pending_calendar": {"title": "需求评审", "action": "create"},
        },
    }
    kw = workflow_kwargs(task, [7])
    assert kw["pending_calendar"] == {"title": "需求评审", "action": "create"}
    assert kw["document_ids"] == [7]


@pytest.mark.unit
def test_document_keys_resolve_to_ids():
    key_to_id = {"office_policy_txt": 3, "product_guide_md": 4}
    assert _resolve_document_ids(["office_policy_txt", "product_guide_md"], key_to_id) == [3, 4]
    assert _resolve_document_ids(["missing_key"], key_to_id) is None
    assert _resolve_document_ids([], key_to_id) is None


@pytest.mark.unit
def test_aggregate_reports_infra_and_core_rates():
    rows = [
        TaskResult("a", "q", "fact_qa", False, completion_ok=True),
        TaskResult("b", "q", "no_tool", False, completion_ok=False, failed_layer="answer"),
        TaskResult("c", "q", "chat", False, completion_ok=True),
        TaskResult("d", "q", "fact_qa", False, failed_layer="infra_error"),
    ]
    report = aggregate(rows)
    assert report["n_infra_error"] == 1
    assert report["infra_error_rate"] == pytest.approx(0.25)
    # core = fact_qa + no_tool scored rows: 1 of 2 completed, chat excluded
    assert report["core_n"] == 2
    assert report["core_completion_rate"] == pytest.approx(0.5)
