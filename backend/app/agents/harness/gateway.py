"""Governed tool gateway — aiming-lab AutoHarness when available, local fallback.

Pipeline: parse → (AutoHarness evaluate | local risk/permission) → execute → sanitize → audit
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.agents.harness.audit import write_audit
from app.agents.harness.constitution import (
    autoharness_available,
    get_autoharness_pipeline,
    load_constitution,
)
from app.agents.harness.types import ToolContext, ToolResult
from app.agents.tools.registry import ensure_builtin_tools, get_tool


def _deny(tool: str, reason: str, *, risk: str = "high", steps: list[str] | None = None) -> ToolResult:
    msg = f"工具治理拒绝「{tool}」：{reason}"
    return ToolResult(
        ok=False,
        denied=True,
        deny_reason=reason,
        risk=risk,
        thinking_steps=[*(steps or []), msg],
        direct_answer=msg,
        need_more=False,
    )


def _local_classify_risk(tool: str, args: dict[str, Any], constitution: dict[str, Any]) -> tuple[str, str | None]:
    risks = constitution.get("tool_risk") or {}
    risk = str(risks.get(tool) or "medium")
    blob = " ".join(str(v) for v in args.values()) + " " + " ".join(args.keys())
    for pat in constitution.get("risk_patterns") or []:
        pattern = pat.get("pattern") or ""
        if not pattern:
            continue
        try:
            if re.search(pattern, blob) and pat.get("action") == "deny":
                return risk, f"命中风险规则 {pat.get('id') or pattern}"
        except re.error:
            continue
    if tool == "sandbox":
        sb = constitution.get("sandbox") or {}
        for key in sb.get("deny_arg_keys") or []:
            if key in args and args.get(key) not in (None, False, "", "none"):
                return "high", f"sandbox 禁止参数 {key}={args.get(key)!r}"
        for sub in sb.get("deny_arg_substrings") or []:
            if sub and sub in blob:
                return "high", f"sandbox 参数包含禁止片段 {sub!r}"
    return risk, None


def _local_permission_ok(tool: str, constitution: dict[str, Any]) -> str | None:
    allowed = set(constitution.get("allowed_tools") or [])
    if tool not in allowed:
        return f"工具不在白名单：{tool}"
    return None


def _autoharness_precheck(tool: str, args: dict[str, Any]) -> tuple[str | None, str, str]:
    """Return (deny_reason|None, risk, engine_tag)."""
    pipeline = get_autoharness_pipeline()
    if pipeline is None:
        return None, "medium", "local"

    try:
        from autoharness.core.types import ToolCall
    except ImportError:
        return None, "medium", "local"

    try:
        decision = pipeline.evaluate(ToolCall(tool_name=tool, tool_input=args))
    except Exception as e:
        # Soft-fail to local rules so a schema mismatch does not brick tools
        return None, "medium", f"ah_error:{type(e).__name__}"

    risk = "medium"
    if getattr(decision, "risk_level", None) is not None:
        risk = getattr(decision.risk_level, "value", None) or str(decision.risk_level)

    action = getattr(decision, "action", "allow")
    reason = getattr(decision, "reason", "") or "blocked by AutoHarness"

    if action == "deny":
        return reason, risk, "autoharness"
    if action == "ask":
        # Non-interactive: deny high-risk sandbox asks; allow others with audit note
        if tool == "sandbox" or risk in ("high", "critical"):
            return f"需要确认但当前为非交互：{reason}", risk, "autoharness"
    return None, risk, "autoharness"


def _sanitize_evidence(evidence: list[dict], tool: str, constitution: dict[str, Any]) -> list[dict]:
    limits = constitution.get("limits") or {}
    patterns = [
        p for p in (constitution.get("risk_patterns") or []) if p.get("action") == "sanitize"
    ]
    out: list[dict] = []
    for e in evidence:
        item = dict(e)
        content = str(item.get("content") or "")
        snippet = str(item.get("snippet") or "")
        for pat in patterns:
            try:
                content = re.sub(pat.get("pattern") or "", "[REDACTED]", content)
                snippet = re.sub(pat.get("pattern") or "", "[REDACTED]", snippet)
            except re.error:
                continue
        if tool == "web":
            snippet = snippet[: int(limits.get("web_snippet_max_chars") or 400)]
            content = content[: int(limits.get("web_content_max_chars") or 2000)]
        elif tool == "kb":
            content = content[: int(limits.get("kb_content_max_chars") or 4000)]
        elif tool == "sandbox":
            snippet = snippet[: int(limits.get("sandbox_snippet_max_chars") or 400)]
        else:
            content = content[: int(limits.get("evidence_content_max_chars") or 8000)]
        item["content"] = content
        item["snippet"] = snippet
        out.append(item)
    return out


def invoke(tool_name: str | None, ctx: ToolContext, args: dict[str, Any] | None = None) -> ToolResult:
    """Single governed tool invocation."""
    ensure_builtin_tools()
    constitution = load_constitution()
    args = dict(args or {})
    started = time.time()
    tool = (tool_name or "").strip()
    engine = "local"

    def _emit(event: dict[str, Any]) -> None:
        event.setdefault("user_id", getattr(ctx, "user_id", None))
        write_audit(event)

    if not tool:
        result = _deny("", "未指定工具")
        result.elapsed_ms = int((time.time() - started) * 1000)
        _emit(
            {
                "tool": "",
                "decision": "deny",
                "reason": result.deny_reason,
                "engine": engine,
                "args": args,
                "elapsed_ms": result.elapsed_ms,
            }
        )
        return result

    # Official AutoHarness pre-check (when installed)
    ah_deny, ah_risk, engine = _autoharness_precheck(tool, args)
    risk = ah_risk
    if ah_deny:
        result = _deny(tool, ah_deny, risk=risk)
        result.elapsed_ms = int((time.time() - started) * 1000)
        _emit(
            {
                "tool": tool,
                "decision": "deny",
                "reason": ah_deny,
                "risk": risk,
                "engine": engine,
                "args": args,
                "elapsed_ms": result.elapsed_ms,
            }
        )
        return result

    # Local defense-in-depth (Omni sandbox arg guards + whitelist)
    risk2, risk_deny = _local_classify_risk(tool, args, constitution)
    if risk2 in ("high", "critical"):
        risk = risk2
    if risk_deny:
        result = _deny(tool, risk_deny, risk=risk)
        result.elapsed_ms = int((time.time() - started) * 1000)
        _emit(
            {
                "tool": tool,
                "decision": "deny",
                "reason": risk_deny,
                "risk": risk,
                "engine": f"{engine}+local",
                "args": args,
                "elapsed_ms": result.elapsed_ms,
            }
        )
        return result

    perm_deny = _local_permission_ok(tool, constitution)
    if perm_deny:
        result = _deny(tool, perm_deny, risk=risk)
        result.elapsed_ms = int((time.time() - started) * 1000)
        _emit(
            {
                "tool": tool,
                "decision": "deny",
                "reason": perm_deny,
                "risk": risk,
                "engine": f"{engine}+local",
                "args": args,
                "elapsed_ms": result.elapsed_ms,
            }
        )
        return result

    spec = get_tool(tool)
    if spec is None:
        result = _deny(tool, "未注册的工具", risk=risk)
        result.elapsed_ms = int((time.time() - started) * 1000)
        _emit(
            {
                "tool": tool,
                "decision": "deny",
                "reason": "unregistered",
                "risk": risk,
                "engine": engine,
                "args": args,
                "elapsed_ms": result.elapsed_ms,
            }
        )
        return result

    try:
        result = spec.execute(ctx, args)
    except Exception as e:
        result = _deny(tool, f"执行异常：{type(e).__name__}: {str(e)[:160]}", risk=risk)
        result.elapsed_ms = int((time.time() - started) * 1000)
        _emit(
            {
                "tool": tool,
                "decision": "error",
                "reason": result.deny_reason,
                "risk": risk,
                "engine": engine,
                "args": args,
                "elapsed_ms": result.elapsed_ms,
            }
        )
        return result

    result.risk = risk or result.risk or spec.risk
    result.evidence = _sanitize_evidence(result.evidence, tool, constitution)
    result.elapsed_ms = int((time.time() - started) * 1000)

    _emit(
        {
            "tool": tool,
            "decision": "allow",
            "risk": result.risk,
            "ok": result.ok,
            "evidence_count": len(result.evidence),
            "denied": result.denied,
            "engine": engine,
            "autoharness": autoharness_available(),
            "elapsed_ms": result.elapsed_ms,
            "args": args,
            "analysis_ir": (result.analysis_ir or {}) if tool == "sandbox" else {},
            "analysis_metrics": (
                list((result.analysis_summary or {}).get("metrics") or [])[:20]
                if tool == "sandbox"
                else []
            ),
        }
    )
    if constitution.get("mode") == "standard" and result.risk in ("high", "medium"):
        tag = "AutoHarness" if engine == "autoharness" else "local-harness"
        result.thinking_steps = [
            *result.thinking_steps,
            f"治理({tag})：已执行 {tool}（risk={result.risk}，{result.elapsed_ms}ms）",
        ]
    return result
