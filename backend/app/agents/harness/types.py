"""Shared types for harness tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolContext:
    db: Any
    message: str
    history: list[dict]
    user_id: int = 1
    document_ids: list[int] | None = None
    iteration: int = 1
    pending_calendar: dict | None = None
    needs_freshness: bool = False
    forced_kb: bool = False
    needs_kb: bool = False
    needs_web: bool = False
    needs_calendar: bool = False
    needs_sandbox: bool = False


@dataclass
class ToolResult:
    ok: bool = True
    evidence: list[dict] = field(default_factory=list)
    thinking_steps: list[str] = field(default_factory=list)
    schedule_card: dict | None = None
    direct_answer: str | None = None
    pending_calendar: dict | None = None
    update_pending_calendar: bool = False
    artifact: dict | None = None
    need_more: bool | None = None
    denied: bool = False
    deny_reason: str = ""
    risk: str = "low"
    elapsed_ms: int = 0
    # Sandbox analysis extras (optional)
    analysis_ir: dict[str, Any] | None = None
    analysis_summary: dict[str, Any] | None = None
    analysis_asked_ids: list[str] | None = None
    analysis_uncomputable: bool | None = None

    def as_state_update(self, *, base_evidence: list[dict] | None = None) -> dict[str, Any]:
        """Merge into LangGraph state (retrieve_node return)."""
        evidence = list(base_evidence or [])
        new_sources = {e.get("source_type") for e in self.evidence if e.get("source_type")}
        if new_sources:
            evidence = [e for e in evidence if e.get("source_type") not in new_sources]
        evidence.extend(self.evidence)
        for i, e in enumerate(evidence, start=1):
            e["index"] = i
        out: dict[str, Any] = {
            "thinking_steps": list(self.thinking_steps),
            "evidence": evidence,
        }
        if self.schedule_card is not None:
            out["schedule_card"] = self.schedule_card
        if self.direct_answer is not None:
            out["direct_answer"] = self.direct_answer
        if self.update_pending_calendar or self.denied:
            out["pending_calendar"] = self.pending_calendar
        if self.artifact is not None:
            out["artifact"] = self.artifact
        if self.need_more is not None:
            out["need_more"] = self.need_more
        if self.analysis_ir is not None:
            out["analysis_ir"] = self.analysis_ir
        if self.analysis_summary is not None:
            out["analysis_summary"] = self.analysis_summary
        if self.analysis_asked_ids is not None:
            out["analysis_asked_ids"] = self.analysis_asked_ids
        if self.analysis_uncomputable is not None:
            out["analysis_uncomputable"] = self.analysis_uncomputable
        return out
