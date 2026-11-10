# Rationale

## Problem

`plan_tools` runs inside `plan_node`, so assigning the turn and doing the work are one compiled graph. The call has been there since the initial commit. Later commits added `router_history`, task generation, and `is_stopped` around it, and this change leaves those in place. The graph reads a split seed, `next_tool`, the `pending_tools` tail, five `needs_*` flags, `forced`, and `intent`. The router dict also carries `confidence` and `reasoning`, which that seed drops. A low confidence must not open a calendar card by itself. `gateway.invoke` in production is `retrieve_node`. `publish_schedule_claim` runs in the chat handler after the graph returns a card. `verify_state` may still set a later `next_tool` inside `reflect_node`. `plan_tools` is also how `tool_routing` and `plan_needs` score the router, and `run_agent_workflow` is also how `agent_e2e` runs a task. The work graph has to grow one assignment type, or those callers keep two entries into the same decision.

## Usage (caller's view)

You call `assign_turn`, then you pass the result to `run_agent_workflow`. The chat worker does that inside `_run_workflow_sync`. When `is_stopped()` is already true, that worker returns `halted_result()` and leaves `assign_turn` uncalled. Eval does the same two calls inside `run_task`'s existing `try`, with `context=None`. A graph test builds the assignment with `TurnAssignment.from_plan` and passes that object to `run_agent_workflow`.

```python
assignment = assign_turn(
    message,
    history,
    context=ctx_state,
    use_kb=req.use_kb,
    has_kb_docs=has_kb_docs,
    has_tabular_docs=tabular,
    document_ids=req.document_ids or None,
)
result = run_agent_workflow(db, assignment=assignment, message=message, history=history, use_kb=req.use_kb)
```

`assignment.graph_seed()` is what the graph merges into the invoke input. The reply the user sees is still `result["direct_answer"]` or the stream of `result["llm_messages"]`.

## Shape

The data shape is one frozen `TurnAssignment`. The fields are `intent`, `forced`, `next_tool`, `pending_tools`, `needs_freshness`, `needs_kb`, `needs_web`, `needs_calendar`, and `needs_sandbox`. `pending_tools` is the tail. The head and the tail are the tools `ordered_tools` builds from the four tool flags, in `TOOL_ORDER` of kb, web, calendar, sandbox. `needs_freshness` does not add a tool. `forced` is the request's `use_kb` flag and can be true while `needs_kb` is false. `__post_init__` rejects a head, a tail, or an `intent` that disagrees with those flags, per model-the-domain and type-system-discipline.

`from_plan` is the boundary parse, per boundary-discipline. It reads the needs flags, checks `pending_tools` when the dict has that key, and ignores `confidence` and `reasoning`. `assign_turn` selects `router_history` with the same fallback `plan_node` uses, calls `plan_tools`, and returns `from_plan`. The graph trusts the frozen value. `run_agent_workflow` raises `AssignmentError` when `assignment.forced` and `use_kb` disagree, so the two copies of that flag cannot drift. Per foundational-thinking, the flags are the source and the queue is derived.

`plan_node` stays the first node and resets iteration, evidence, and the card slot. It does not plan. The seed already holds the assignment, and LangGraph keeps keys the node omits. `answer_node` still recomputes `intent` after evidence. The initial `intent` uses `_sse_intent` with empty evidence, imported inside `_expected_intent` so `dispatch.py` does not import `workflow` at module level. `intent_from_decision` stays the eval label. Those two functions already disagree when `use_kb` is true and no tool is needed, and this change does not merge them.

Per laziness-protocol and subtract-before-you-add, the graph edges, `verify_state`, the node stop checks, task generation, and the claim publish stay. The new public function is `assign_turn`. Per minimize-reader-load the caller sees those two calls and the same graph. Per encode-lessons-in-structure, `workflow.py` has no `plan_tools` import, and import fails when `ToolName` and `TOOL_ORDER` differ. Per make-operations-idempotent the assignment is a return value. A second call returns an equal value and writes no claim. A crash leaves no row. Per migrate-callers-then-delete-legacy-apis, the same change moves `agent_e2e`, the debug script, and the tests off `app.agents.workflow.plan_tools`.

Interface depth is the two calls. `assign_turn` hides the history window, the router cache, the queue split, the dropped confidence, and the initial label. The caller passes one object through. The graph hides `gateway.invoke`, the later `verify_state` choice, `after_plan`, and the reply. Eval still calls `plan_tools` when it scores the router dict. `run_agent_workflow` requires a `TurnAssignment`, so that score stays on the report.

Screened against the red flags:

- Shallow module. The caller invokes `assign_turn` once. History selection, `route`, and the queue split stay inside that function.
- Information leakage. The router dict stays in the router and in the routing report. The graph receives `TurnAssignment`.
- Temporal decomposition. `dispatch.py` owns the decision. The graph owns the run. Parse and assign live in the same module.
- Pass-through method. `assign_turn` selects history and parses. `plan_node` resets work fields. Neither forwards the other's arguments unchanged.
- Split ownership. The frozen value is written by `assign_turn`. Graph state is a copy. `reflect_node` replaces `next_tool` only in graph state. The claim stays in the chat handler.
- Two ways to do one task. `assign_turn` is the only writer of a `TurnAssignment`. The graph file has no `plan_tools` import. The routing score still consumes the router dict, and `run_agent_workflow` requires a `TurnAssignment`.
- Importable internals. The planner name is gone from `workflow.py`. Confidence is not a field on the assignment.
- Hand-synced list. `TOOL_ORDER` is the queue order. `ordered_tools` and the `ToolName` import check share it. `_sse_intent` remains the only graph label.

## Synthesis decision

filled by the parent

## Tradeoffs accepted

- We accept a node still named `plan_node` in exchange for leaving the graph edges and the work-field reset where they are.
- We accept two call sites that each assign and then run, in exchange for a required argument the graph cannot satisfy by planning again.
- We accept `plan_tools` remaining for `tool_routing` and `plan_needs` in exchange for a graph signature that rejects the router dict.
- We accept two intent labels, `_sse_intent` on the work graph and `intent_from_decision` in eval, in exchange for leaving both scores as they are.
- We accept an in-memory assignment with no row to resume, in exchange for a crash leaving nothing for a later turn to misread.
- We accept that `from_plan` treats a missing needs flag as false, in exchange for the partial dicts current graph tests already build.

## Alternatives considered

The other whole shape is an early reply. One function returns either a finished `WorkflowResult` or a `TurnAssignment`. An empty queue writes the assistant text in the dispatch step and skips the work graph. A non-empty queue returns the assignment and the caller starts the graph.

That shape loses on interface depth. The public result is a union, so chat, eval, and every test branch on an internal stage and learn the empty-queue rule that `after_plan` already applies. Dispatch and `answer_node` would both write the reply. The chosen calls hide that stage. Every caller assigns, then runs. An empty queue still reaches `answer_node`. The reply stays on the work path.

This sketch does not average the two. Dispatch does not attach a preview string, and the graph does not keep a second planner for the empty queue.

## Open questions and risks

Should the routing report keep scoring `confidence` after the work path has no field that can read it?

A missed test patch of `app.agents.workflow.plan_tools` raises `AttributeError` once that import is gone. That fails closed. The infra test has to move its patch to `app.agents.dispatch.plan_tools` in the same change, or a router failure inside `run_task` will no longer be the exception that test raises.

## Next implementation step

Add `TurnAssignment` and `assign_turn` in `backend/app/agents/dispatch.py`, derive `pending_tools` from `TOOL_ORDER`, and change `_run_workflow_sync` so it assigns before `run_agent_workflow`.
