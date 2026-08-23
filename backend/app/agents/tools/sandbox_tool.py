"""Sandbox / data-analysis tool."""

from __future__ import annotations

from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ToolSpec, register
from app.core.messages import SANDBOX_TIMEOUT_MESSAGE
from app.services.data_analysis import run_analysis


def execute_sandbox(ctx: ToolContext, _args: dict) -> ToolResult:
    steps = [f"第 {ctx.iteration} 轮：数据分析沙箱"]
    feedback = ""
    prior_ir = None
    # ToolContext may be extended via getattr from workflow state stashed on db session — prefer args
    if isinstance(_args, dict):
        feedback = str(_args.get("feedback") or "")
        prior = _args.get("prior_ir")
        if isinstance(prior, dict):
            prior_ir = prior
    outcome = run_analysis(
        ctx.db,
        message=ctx.message,
        history=ctx.history,
        user_id=ctx.user_id,
        document_ids=ctx.document_ids or None,
        prior_ir=prior_ir,
        feedback=feedback,
    )
    steps.extend(outcome.steps)
    content = outcome.evidence_text or outcome.error or "无输出"
    evidence = [
        {
            "index": 0,
            "source_type": "sandbox",
            "tool": "sandbox",
            "filename": outcome.filename or "数据分析",
            "title": "沙箱执行结果",
            "snippet": (outcome.stdout or outcome.error or "")[:400],
            "content": content,
            "url": "",
            "metrics": list((outcome.summary or {}).get("metrics") or []),
            "asked_ids": list(outcome.asked_ids or []),
            "analysis_missing": list((outcome.summary or {}).get("missing") or []),
        }
    ]
    direct = None
    if not outcome.ok and outcome.error:
        if outcome.error == SANDBOX_TIMEOUT_MESSAGE:
            direct = SANDBOX_TIMEOUT_MESSAGE
        else:
            direct = (
                f"数据分析未能完成：{outcome.error}\n"
                "请确认已上传并选中 csv/xlsx，且宿主机已构建沙箱镜像（docker compose build sandbox）。"
            )
    return ToolResult(
        ok=bool(outcome.ok),
        evidence=evidence,
        thinking_steps=steps,
        artifact=outcome.artifact,
        direct_answer=direct,
        risk="high",
        analysis_ir=outcome.ir or None,
        analysis_summary=outcome.summary or None,
        analysis_asked_ids=list(outcome.asked_ids or []),
        analysis_uncomputable=bool(outcome.analysis_uncomputable),
    )


register(
    ToolSpec(
        name="sandbox",
        risk="high",
        execute=execute_sandbox,
        description="Run tabular analysis inside the Docker sandbox",
    )
)
