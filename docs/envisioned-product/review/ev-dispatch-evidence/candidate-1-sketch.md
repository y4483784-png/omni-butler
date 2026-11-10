# Turn assignment

A chat turn observes intent once, writes one `TurnAssignment`, and then runs the existing work graph from that value. The assignment is the only plan the work graph sees. The final reply is still the workflow result. `stream_chat` and `answer_node` write it. `assign_turn` returns no reply text.

`assign_turn` reads `router_history` from the context bundle. Retrieval still reads `tool_history`. The answer path still reads answer history. The call uses the same fallback `plan_node` uses today.

```python
router_hist = list(ctx.get("router_history") or history or [])
```

An empty `router_history` still falls through to `history`. That fallback stays. This change does not reopen context windows.

The call sits on the worker thread, in the line before `run_agent_workflow`. It does not move into `agent_event_gen` ahead of `asyncio.to_thread`. The router wait stays off the event loop. `RouterError` and `RouterInfraError` still leave the worker task and hit the handlers that already exist. `assign_turn` does not catch them.

`assign_turn` takes no database session. It does not call `gateway.invoke`. It does not call `publish_schedule_claim`. It does not write a calendar row. Low confidence is not a field on the assignment, so it cannot open a card by itself. A later calendar tool inside the work graph can still produce a card. That path is unchanged, and `chat.py` still publishes the claim only after the workflow returns a card.

`verify_state` stays inside `reflect_node` and may still set a later `next_tool`. That later value is the work cursor. `reflect_node` leaves `assign_turn` alone.

Stop stays the check already at the top of `run_agent_workflow`. A stopped generation still returns `_halted_result` before `invoke`, so it still does not reach `gateway.invoke`. `assign_turn` adds no stop policy.

## Call sites

Chat worker, `backend/app/api/chat.py`, inside `_run_workflow_sync`, immediately before the workflow call.

```python
def _run_workflow_sync(**kwargs) -> dict:
    db = SessionLocal()
    try:
        uid = int(kwargs.get("user_id") or 0)
        if uid:
            set_rls_context(db, uid)
        assignment = assign_turn(
            kwargs.get("message") or "",
            list(kwargs.get("history") or []),
            context=kwargs.get("context") if isinstance(kwargs.get("context"), dict) else None,
            use_kb=bool(kwargs.get("use_kb")),
            has_kb_docs=bool(kwargs.get("has_kb_docs")),
            has_tabular_docs=bool(kwargs.get("has_tabular_docs")),
            document_ids=kwargs.get("document_ids") or None,
        )
        return run_agent_workflow(db, assignment=assignment, **kwargs)
    finally:
        db.close()
```

Eval end to end, `backend/app/eval/agent_e2e.py`, inside the existing `try` and `trace_gateway` block in `run_task`. `workflow_kwargs` still builds the workflow arguments. `context` is absent there, so `assign_turn` builds the router window with `ContextBundle.from_history`, which is what `plan_node` does today when the seed has no context dict.

```python
kwargs = workflow_kwargs(task, document_ids)
with trace_gateway(trace):
    assignment = assign_turn(
        kwargs["message"],
        list(kwargs["history"]),
        context=None,
        use_kb=kwargs["use_kb"],
        has_kb_docs=kwargs["has_kb_docs"],
        has_tabular_docs=kwargs["has_tabular_docs"],
        document_ids=kwargs.get("document_ids"),
    )
    out = run_agent_workflow(db, assignment=assignment, **kwargs)
```

A workflow test supplies the assignment and does not patch `app.agents.workflow.plan_tools`. The graph runs that queue. `plan_tools` is not consulted.

```python
assignment = TurnAssignment(
    next_tool="web",
    pending_tools=(),
    needs_freshness=True,
    needs_kb=False,
    needs_web=True,
    needs_calendar=False,
    needs_sandbox=False,
    forced=False,
    intent="web_search",
)
result = run_agent_workflow(
    db,
    assignment=assignment,
    message="今天有什么 AI 新闻",
    history=[],
    has_kb_docs=False,
)
```

`backend/app/eval/tool_routing.py` and `plan_needs` keep calling `plan_tools`. They score the router dict, including `confidence`. They do not build a `TurnAssignment` and they do not run the graph.

## Types and signatures

New module `backend/app/agents/dispatch.py`. `workflow.py` imports `TurnAssignment` and `sse_intent` from it. `workflow.py` does not import `plan_tools`. The unused `ToolName` alias in `workflow.py` is deleted so the literal lives once.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.agents.router import Intent

ToolName = Literal["kb", "web", "calendar", "sandbox"]


@dataclass(frozen=True)
class TurnAssignment:
    """Opening cursor for one turn.

    pending_tools is the tail after next_tool. The full queue is next_tool followed by that tail.
    confidence and reasoning are absent on purpose.
    direct_answer, schedule_card, and pending_calendar are absent on purpose.
    """

    next_tool: ToolName | None
    pending_tools: tuple[ToolName, ...]
    needs_freshness: bool
    needs_kb: bool
    needs_web: bool
    needs_calendar: bool
    needs_sandbox: bool
    forced: bool
    intent: Intent


def sse_intent(
    *,
    forced: bool,
    evidence: list[dict],
    pending_had_kb: bool,
    pending_had_web: bool,
    pending_had_calendar: bool,
    pending_had_sandbox: bool = False,
) -> Intent:
    """Moved from workflow._sse_intent. Same branches. answer_node calls this too."""
    # TODO: copy the current _sse_intent body unchanged.
    # forced returns "rag" before evidence is considered.
    # Otherwise sandbox, kb, calendar, web, from evidence source_type, then from the pending flags.
    # Empty evidence and no flags returns "chat".
    raise NotImplementedError


def assign_turn(
    message: str,
    history: list[dict],
    *,
    context: dict | None = None,
    use_kb: bool = False,
    has_kb_docs: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
) -> TurnAssignment:
    """Observe this turn and return its assignment.

    No database argument. Does not call gateway.invoke.
    Does not call publish_schedule_claim. Does not write the calendar.
    Does not catch RouterError or RouterInfraError.
    Does not check is_stopped.
    """
    # TODO: ctx = context when it is a dict, otherwise ContextBundle.from_history(history).to_state().
    # TODO: router_hist = list(ctx.get("router_history") or history or []).
    # TODO: context_line = str(ctx.get("context_line") or "").
    # TODO: do not read tool_history or answer_history here.
    # TODO: planned = plan_tools(message, router_hist, has_kb_docs=has_kb_docs,
    #           forced_kb=use_kb, has_tabular_docs=has_tabular_docs,
    #           document_ids=document_ids, context_line=context_line).
    # TODO: queue = list(planned["pending_tools"]).
    # TODO: next_tool = queue[0] if queue else None. Tail is tuple(queue[1:]) if queue else ().
    # TODO: drop planned["confidence"] and planned["reasoning"]. Do not store them.
    # TODO: forced = bool(use_kb). This matches plan_node. document_ids still affect plan_tools only.
    # TODO: intent = sse_intent(forced=forced, evidence=[], pending_had_kb=bool(planned["needs_kb"]),
    #           pending_had_web=bool(planned["needs_web"]),
    #           pending_had_calendar=bool(planned.get("needs_calendar")),
    #           pending_had_sandbox=bool(planned.get("needs_sandbox"))).
    # TODO: copy the five needs flags with the same bool() and .get() calls plan_node uses.
    # TODO: return TurnAssignment(...).
    raise NotImplementedError
```

`run_agent_workflow` gains a required `assignment` argument and no default. There is no path that calls `plan_tools` or `assign_turn` when the argument is missing. `retrieve_node`, `reflect_node`, and `answer_node` stay, including the `gateway.invoke` call in `retrieve_node` and the `verify_state` call in `reflect_node`. `answer_node` calls `sse_intent` from `dispatch`. The private copy in `workflow.py` goes away with `plan_node`.

```python
def run_agent_workflow(
    db: Session,
    *,
    assignment: TurnAssignment,
    message: str,
    history: list[dict],
    use_kb: bool = False,
    has_kb_docs: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
    user_id: int = 1,
    pending_calendar: dict | None = None,
    context: dict | None = None,
    task_id: int | None = None,
    generation: int | None = None,
) -> WorkflowResult:
    """Work from an assignment that was written before this function.

    Does not call plan_tools. Does not call assign_turn.
    is_stopped at entry still returns _halted_result before invoke.
    GenerationStopped from invoke still returns _halted_result.
    is_stopped after invoke still returns _halted_result.
    """
    # TODO: keep the three stop returns exactly where they are.
    # TODO: build one invoke seed. Copy assignment fields in:
    #       next_tool, list(pending_tools), five needs flags, forced, intent.
    # TODO: keep the input keys the seed already passes:
    #       history, message, use_kb, has_kb_docs, has_tabular_docs, document_ids,
    #       user_id, task_id, generation, pending_calendar, context.
    # TODO: keep the loop blanks plan_node writes today:
    #       iteration=1, evidence=[], thinking_steps=[], citations=[],
    #       need_more=False, kb_retried=False, web_retried=False,
    #       schedule_card=None, calendar_reply=None, direct_answer=None,
    #       answer_reasoning=None, artifact=None.
    # TODO: do not put the TurnAssignment object or the raw plan dict on WorkflowState.
    # TODO: delete plan_node.
    # TODO: START goes through open_on, below. retrieve still edges to reflect.
    #       after_reflect is unchanged. answer still edges to END.
    raise NotImplementedError


def open_on(state: WorkflowState) -> Literal["retrieve_node", "answer_node"]:
    """First node from the seeded next_tool. Replaces after_plan."""
    return "retrieve_node" if state.get("next_tool") else "answer_node"
```

`open_on` is the old `after_plan` condition pointed at `START`. Its body is that one expression. It is not a second planner.

## Tests

New file `backend/tests/test_dispatch_boundary.py`. Assertions are what a caller can see. The same change migrates patches of `app.agents.workflow.plan_tools` in `test_workflow.py`, `test_calendar_card.py`, `test_calendar_dialogue.py`, and `test_agent_e2e.py`. Those tests pass a `TurnAssignment`, or they patch `app.agents.dispatch.plan_tools` when they still need the router to raise. `test_router_infra_is_infra_error_not_workflow_error` patches `app.agents.dispatch.plan_tools` with `RouterInfraError` and still expects `failed_layer == "infra_error"`, because `run_task` calls `assign_turn` inside the existing `try` and `assign_turn` does not wrap the error.

```python
def test_assign_turn_does_not_invoke_or_publish(monkeypatch):
    # plan_tools returns a calendar queue with confidence "low" and a reasoning string.
    # gateway.invoke and publish_schedule_claim raise AssertionError if called.
    # assign_turn returns next_tool "calendar" and pending_tools ().
    # The return value has no confidence, reasoning, schedule_card, or direct_answer.
    # No calendar row is inserted.
    raise NotImplementedError


def test_workflow_uses_the_assignment_and_does_not_plan(monkeypatch):
    # plan_tools raises if called.
    # run_agent_workflow receives a web assignment and calls gateway.invoke once with "web".
    # The reply intent is still produced by the work path.
    raise NotImplementedError


def test_stopped_entry_does_not_invoke(monkeypatch):
    # is_stopped is true before run_agent_workflow.
    # The assignment names a tool.
    # The result is the existing halted result.
    # gateway.invoke raises if called.
    raise NotImplementedError


def test_chat_reply_comes_from_the_work_path(plain_client, monkeypatch):
    # plain_client posts /api/chat for an existing session.
    # assign_turn is patched to a no-tool chat assignment.
    # stream_chat yields one token, "工作路径回复".
    # gateway.invoke and publish_schedule_claim raise if called.
    # The SSE body contains that token.
    # The assistant message stored for the session is that token.
    raise NotImplementedError
```

## Module map

`backend/app/agents/dispatch.py` owns `ToolName`, `TurnAssignment`, `sse_intent`, and `assign_turn`.

`backend/app/agents/workflow.py` owns the work graph. It consumes a `TurnAssignment` and does not plan.

`backend/app/agents/router.py` owns `route`, `plan_tools`, and `plan_needs`. Unchanged.

`backend/app/api/chat.py` calls `assign_turn` then `run_agent_workflow` inside `_run_workflow_sync`. Claim publishing stays where it is, after the workflow returns a card.

`backend/app/eval/agent_e2e.py` calls `assign_turn` then `run_agent_workflow` inside `run_task`.

## Flags avoided

The shallow-module flag is avoided because one function returns one value. Queue split, confidence drop, and the opening label sit inside that function.

The information-leakage flag is avoided because `RouterDecision`, `confidence`, `reasoning`, and the raw plan dict stay inside `assign_turn`. Callers receive `TurnAssignment`.

The temporal-decomposition flag is avoided because dispatch is one module and the work graph is another. `plan_node` is removed, so no stage remains whose only job is to forward.

The pass-through flag is avoided because `assign_turn` returns `TurnAssignment`, which is a different shape from the plan dict. `open_on` is the graph's first branch on `next_tool`.

The split-ownership flag is avoided because only `assign_turn` builds a `TurnAssignment`. The graph copies the fields into the seed once. A later `next_tool` from `verify_state` is work state.

The two-ways flag is avoided because `run_agent_workflow` cannot call `plan_tools`, and `workflow.py` does not import it. Router scoring stays on `plan_tools` so `confidence` can remain on the router dict.

The importable-internals flag is avoided because `_decision_to_plan` stays private and dispatch exports no split helper.

The hand-synced-list flag is avoided because `assign_turn` splits the queue `plan_tools` already returned. It does not list `kb`, `web`, `calendar`, and `sandbox` again.
