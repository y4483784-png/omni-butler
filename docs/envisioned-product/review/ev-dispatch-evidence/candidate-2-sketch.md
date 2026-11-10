# Assign this turn, then run the work graph

You record this turn's tool assignment, then you start the existing work graph with that assignment. `assign_turn` reads `router_history`, calls `plan_tools`, and returns a `TurnAssignment`. `run_agent_workflow` copies that value into the invoke seed and runs the same graph edges it runs today. `answer_node` still produces `direct_answer` or `llm_messages`. The chat handler still streams that result and still calls `publish_schedule_claim` only after the graph returns a card.

`assign_turn` has no database argument. Its body calls `plan_tools` and `TurnAssignment.from_plan`. It does not call `gateway.invoke`. It does not call `publish_schedule_claim`. It does not write a calendar row, a schedule card, or the assistant reply.

The compiled graph does not call `plan_tools`. `workflow.py` does not import `plan_tools`. `plan_node` resets the work fields for this turn. `after_plan` still sends a seeded `next_tool` to `retrieve_node`, and an empty queue to `answer_node`. `reflect_node` still may set a later `next_tool` from `verify_state`. That later choice stays inside the work loop.

`TurnAssignment.intent` is the label `plan_node` stores today, before any tool runs. `answer_node` still replaces `intent` after evidence. The workflow result keeps that later label.

## Call sites

### Chat worker

`_run_workflow_sync` in `backend/app/api/chat.py` assigns on the worker thread, then starts the graph. `is_stopped` is the same check that currently returns before the graph. A stopped turn returns `halted_result()` and does not call `plan_tools`. `publish_schedule_claim` stays in the SSE handler after `wf` returns.

```python
def _run_workflow_sync(**kwargs) -> dict:
    db = SessionLocal()
    try:
        uid = int(kwargs.get("user_id") or 0)
        if uid:
            set_rls_context(db, uid)
        if is_stopped():
            return halted_result()
        assignment = assign_turn(
            message=str(kwargs.get("message") or ""),
            history=list(kwargs.get("history") or []),
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

`begin_turn`, the context bundle, and the SSE `planning` status stay where they are. The history argument above is still `answer_history`. `assign_turn` reads `router_history` from `context`.

### Eval task

`run_task` in `backend/app/eval/agent_e2e.py` assigns inside the existing `try`, then calls the graph. `RouterInfraError` still leaves that `try` as `infra_error`. Eval passes no context bundle, so `assign_turn` builds the router window with `ContextBundle.from_history`, which is the fallback `plan_node` uses today.

```python
kwargs = workflow_kwargs(task, document_ids)
assignment = assign_turn(
    kwargs["message"],
    kwargs["history"],
    context=None,
    use_kb=kwargs["use_kb"],
    has_kb_docs=kwargs["has_kb_docs"],
    has_tabular_docs=kwargs["has_tabular_docs"],
    document_ids=kwargs["document_ids"],
)
out = run_agent_workflow(db, assignment=assignment, **kwargs)
```

`tool_routing` keeps calling `plan_tools` and keeps reading `confidence` for the routing report. It does not build a `TurnAssignment` and it does not call `run_agent_workflow`. Update its module docstring so it names `plan_tools` as the router call `assign_turn` also makes.

### Graph test

A workflow test passes the assignment and does not patch `app.agents.workflow.plan_tools`. `confidence` and `reasoning` may sit on the dict. `from_plan` drops them.

```python
assignment = TurnAssignment.from_plan(
    {
        "pending_tools": ["web"],
        "needs_freshness": True,
        "needs_kb": False,
        "needs_web": True,
        "confidence": "low",
        "reasoning": "dropped",
    },
    forced=False,
)
result = run_agent_workflow(
    db,
    assignment=assignment,
    message="今天有什么 AI 新闻",
    history=[],
    use_kb=False,
    has_kb_docs=False,
)
```

`result["intent"]` and `result["citations"]` still come from the work graph.

## Types and signatures

These bodies are the contract. New functions raise `NotImplementedError` until implementation fills them in. `plan_node` shows the return it must keep, because that return is the whole change inside the node.

```python
# backend/app/agents/router.py

TOOL_ORDER: tuple[str, ...] = ("kb", "web", "calendar", "sandbox")


def ordered_tools(
    *,
    needs_kb: bool,
    needs_web: bool,
    needs_calendar: bool,
    needs_sandbox: bool,
) -> tuple[str, ...]:
    """Names in TOOL_ORDER whose flag is true.

    _decision_to_plan is the only other caller. pending_tools on the router
    dict is this tuple as a list. Confidence and reasoning stay on that dict
    for the routing report.
    """
    raise NotImplementedError


def _decision_to_plan(d: RouterDecision) -> dict[str, Any]:
    """Same dict as today. pending_tools comes from ordered_tools."""
    raise NotImplementedError
```

```python
# backend/app/agents/dispatch.py

class AssignmentError(ValueError):
    """The plan dict or the constructed fields disagree with TOOL_ORDER."""


@dataclass(frozen=True)
class TurnAssignment:
    """Tool decision for one turn, already in the split form the graph reads.

    The work graph copies graph_seed into its invoke input and then owns
    next_tool. This value has no confidence, no reasoning, no schedule card,
    and no reply text. A low router confidence cannot open a card from here,
    because no field carries confidence and no method writes a card.

    pending_tools is the tail. next_tool is the head. Both follow from the
    five needs flags through ordered_tools. needs_freshness does not add a tool.
    forced is the request use_kb flag. It can be true while needs_kb is false.
    """

    intent: Intent
    forced: bool
    next_tool: str | None
    pending_tools: tuple[str, ...]
    needs_freshness: bool
    needs_kb: bool
    needs_web: bool
    needs_calendar: bool
    needs_sandbox: bool

    def __post_init__(self) -> None:
        expected = ordered_tools(
            needs_kb=self.needs_kb,
            needs_web=self.needs_web,
            needs_calendar=self.needs_calendar,
            needs_sandbox=self.needs_sandbox,
        )
        head = expected[0] if expected else None
        tail = expected[1:]
        if self.next_tool != head or self.pending_tools != tail:
            raise AssignmentError("next_tool and pending_tools must match the needs flags")
        if self.intent != _expected_intent(self):
            raise AssignmentError("intent must match the empty-evidence label")

    def graph_seed(self) -> dict[str, Any]:
        """Nine keys the invoke seed merges.

        pending_tools is a new list. The keys are intent, forced, next_tool,
        pending_tools, needs_freshness, needs_kb, needs_web, needs_calendar,
        and needs_sandbox.
        """
        raise NotImplementedError

    @classmethod
    def from_plan(cls, plan: dict[str, Any], *, forced: bool) -> TurnAssignment:
        """Parse a plan_tools dict.

        Missing needs flags count as false. Tools come from ordered_tools.
        When pending_tools is present and differs from that tuple, raise
        AssignmentError. Do not reorder a mismatched queue. Ignore confidence
        and reasoning. intent comes from _expected_intent with this forced flag.
        """
        raise NotImplementedError


def _expected_intent(assignment: TurnAssignment) -> Intent:
    """Call workflow._sse_intent with empty evidence.

    The import of workflow is inside this function. dispatch.py has no
    module-level workflow import, because workflow imports TurnAssignment.

    The call matches plan_node today. forced is the forced argument.
    pending_had_kb is needs_kb, pending_had_web is needs_web,
    pending_had_calendar is needs_calendar, and pending_had_sandbox is
    needs_sandbox. Do not call intent_from_decision. A forced turn with no
    tool need is rag here, and chat on the eval label.
    """
    raise NotImplementedError


def assign_turn(
    message: str,
    history: list[dict] | None = None,
    *,
    context: dict | None = None,
    use_kb: bool = False,
    has_kb_docs: bool = False,
    has_tabular_docs: bool = False,
    document_ids: list[int] | None = None,
) -> TurnAssignment:
    """Observe this turn and return its assignment.

    History selection matches plan_node. When context is a dict, use it.
    Otherwise use ContextBundle.from_history(history).to_state(). The router
    history is context router_history, or history when that list is empty.
    context_line is the bundle line, or an empty string.

    Call plan_tools with that router history, has_kb_docs, forced_kb=use_kb,
    has_tabular_docs, document_ids, and context_line. Return
    from_plan(plan, forced=bool(use_kb)).

    RouterError and RouterInfraError propagate. This function does not call
    is_stopped, gateway.invoke, or publish_schedule_claim.
    """
    raise NotImplementedError
```

```python
# backend/app/agents/workflow.py

ToolName = Literal["kb", "web", "calendar", "sandbox"]


def _require_tool_order() -> None:
    """Fail import when ToolName and TOOL_ORDER disagree, including order."""
    if get_args(ToolName) != TOOL_ORDER:
        raise RuntimeError("ToolName and TOOL_ORDER list different tools")


def halted_result() -> WorkflowResult:
    """The dict today's _halted_result returns.

    Replace _halted_result with this function so the chat worker and the
    graph share one body.
    """
    raise NotImplementedError


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
    """Run the work graph from an assignment that was already written.

    When is_stopped is true, return halted_result before compile. Then, when
    assignment.forced disagrees with bool(use_kb), raise AssignmentError.
    Build the existing invoke seed and update it with assignment.graph_seed().
    The rest of the result mapping stays.
    """
    raise NotImplementedError


def plan_node(state: WorkflowState) -> dict[str, Any]:
    """Reset this turn's work fields. The tool decision is already in the seed.

    Stays nested inside run_agent_workflow, as it is today.
    """
    if is_stopped():
        return _cutoff()
    return {
        "iteration": 1,
        "evidence": [],
        "thinking_steps": [],
        "citations": [],
        "need_more": False,
        "kb_retried": False,
        "web_retried": False,
        "schedule_card": None,
        "calendar_reply": None,
        "direct_answer": None,
        "answer_reasoning": None,
        "artifact": None,
    }
```

`plan_node` does not return `next_tool`, `pending_tools`, the needs flags, `forced`, or `intent`. Those keys arrive from `graph_seed`. LangGraph already keeps keys a node omits. Today's `plan_node` return omits `message`, and `message` is still in state at `answer_node`.

Edges stay `START` to `plan_node`, `after_plan` to `retrieve_node` or `answer_node`, `retrieve_node` to `reflect_node`, `after_reflect` back or on to `answer_node`, then `END`. `retrieve_node` still calls `gateway.invoke` after its own `is_stopped` check. `reflect_node` still applies `verify_state`.

Calling `assign_turn` twice with the same inputs returns equal assignments. The second call writes no card and no claim. A crash inside `assign_turn` leaves no assignment row, because the value exists only as the return value.

## Module map

`backend/app/agents/router.py` owns `TOOL_ORDER`, `ordered_tools`, and `plan_tools`. `plan_needs` and `tool_routing` keep calling `plan_tools` and keep receiving the router dict. `run_agent_workflow` accepts a `TurnAssignment`.

`backend/app/agents/dispatch.py` owns `TurnAssignment` and `assign_turn`. It imports `plan_tools`, `ordered_tools`, and `Intent` from `router`. It imports `ContextBundle` from the context assembler. It imports `workflow` only inside `_expected_intent`.

`backend/app/agents/workflow.py` owns the graph. It imports `TurnAssignment`, defines `halted_result`, and imports `TOOL_ORDER` for the import check. The file has no `plan_tools` import.

`backend/app/api/chat.py` calls `assign_turn` and then `run_agent_workflow` inside `_run_workflow_sync`. `publish_schedule_claim` stays in the handler that reads the workflow result.

`backend/app/eval/agent_e2e.py` calls `assign_turn` and then `run_agent_workflow` inside `run_task`.

`backend/scripts/debug_data_analysis_repro.py` imports `plan_tools` from `router`.

Same change, retarget patches. Tests that patch `app.agents.workflow.plan_tools` to feed the graph pass a `TurnAssignment` instead. `test_router_infra_is_infra_error_not_workflow_error` patches `app.agents.dispatch.plan_tools`, because that call now sits in `run_task`'s `try`. A missed patch of the deleted `workflow.plan_tools` name raises `AttributeError`.

## Tests

The new file is `backend/tests/test_dispatch_boundary.py`. Gate checks use `plain_client` and an owned session, the same way `test_late_chunk_stays_out_of_the_reply` posts `/api/chat`. No browser and no new page.

```python
def test_reply_is_streamed_without_a_tool_or_a_claim(plain_client: TestClient) -> None:
    """A no-tool, low-confidence plan still streams the work-path reply.

    Patch app.agents.dispatch.plan_tools to return no tools and confidence low.
    Patch app.agents.workflow.gateway.invoke so a call fails the test.
    Patch app.api.chat.publish_schedule_claim so a call fails the test.
    Patch app.api.chat.stream_chat to yield one known token.
    Post /api/chat. The token is in the SSE body and in the saved assistant
    message. The calendar event count is unchanged.
    """
    raise NotImplementedError


def test_router_history_is_what_the_planner_receives() -> None:
    """assign_turn passes context router_history into plan_tools.

    context.router_history and the history argument differ. The list
    plan_tools receives is router_history, and the context_line argument is
    the bundle line. The returned assignment matches the stubbed needs flags.
    """
    raise NotImplementedError


def test_graph_uses_the_assignment_when_the_planner_raises() -> None:
    """A web assignment still produces the web citation.

    app.agents.dispatch.plan_tools raises. The web search stub is the one
    test_web_only_one_tool_round already uses. intent is web_search and the
    citation url is the stub url.
    """
    raise NotImplementedError


def test_confidence_does_not_change_the_assignment() -> None:
    """Two plan dicts that differ only in confidence and reasoning parse equal.

    Both need calendar. Neither value has a confidence field. Neither value
    has a schedule card.
    """
    raise NotImplementedError


def test_stopped_turn_skips_planning() -> None:
    """The existing stop check sits in front of assign_turn.

    bind a turn, mark it stopped, and call _run_workflow_sync. plan_tools is
    not called. gateway.invoke is not called. The result is halted_result.
    This uses is_stopped. It adds no stop state.
    """
    raise NotImplementedError
```
