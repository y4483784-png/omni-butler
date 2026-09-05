"""ContextManager: assemble physical LLM context from session checkpoint + memory."""

from __future__ import annotations

from app.agents.context.assembler import (
    ContextBundle,
    build_context,
    compose_answer_messages,
)
from app.agents.context.budget import TokenBudget, estimate_messages_tokens, estimate_tokens
from app.agents.context.summary import (
    invalidate_summary_if_needed,
    refresh_session_context,
    summarize_incremental,
)
from app.agents.context.window import clip_history_for_prompt, recent_turns, to_logical_turns
from app.agents.context.working_state import (
    dump_working_state,
    format_working_block,
    load_working_state,
    merge_working_state,
)

__all__ = [
    "ContextBundle",
    "TokenBudget",
    "build_context",
    "clip_history_for_prompt",
    "compose_answer_messages",
    "dump_working_state",
    "estimate_messages_tokens",
    "estimate_tokens",
    "format_working_block",
    "invalidate_summary_if_needed",
    "load_working_state",
    "merge_working_state",
    "recent_turns",
    "refresh_session_context",
    "summarize_incremental",
    "to_logical_turns",
]
