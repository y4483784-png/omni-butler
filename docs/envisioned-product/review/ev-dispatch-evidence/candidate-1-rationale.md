# Rationale

## Problem

`plan_tools` runs inside `plan_node`, so the compiled work graph both assigns the turn and does the work. `route` and `plan_tools` already avoid `gateway.invoke` and `publish_schedule_claim`. The defect is the call site. The work graph has to start from an assignment that was written before `run_agent_workflow`, while `answer_node` and `stream_chat` still write the reply. The move has to keep `router_history`, the existing stop check, `verify_state` inside `reflect_node`, and the claim publish that happens only after a card comes back. It must not take on stop, the confirm gate, task generation, or a single tool loop.

## Usage (caller's view)

The caller imports `assign_turn` and `TurnAssignment` from `app.agents.dispatch`, and `run_agent_workflow` from `app.agents.workflow`.

`_run_workflow_sync` calls `assign_turn` with the message, history, context dict, `use_kb`, the two availability flags, and `document_ids`. It passes the returned value as `assignment` into `run_agent_workflow` on the next line. That function still runs on the worker thread.

`run_task` does the same pair inside its existing `try` and `trace_gateway` block. It passes `context=None`, and `assign_turn` uses `ContextBundle.from_history` for the router window.

A test that already knows the queue constructs a `TurnAssignment` and passes it to `run_agent_workflow`. It does not call the router. `tool_routing` and `plan_needs` keep calling `plan_tools` so they can still read `confidence`.

## Shape

The data shape is one frozen `TurnAssignment`. The fields are `next_tool`, `pending_tools` as the tail, `needs_freshness`, `needs_kb`, `needs_web`, `needs_calendar`, `needs_sandbox`, `forced`, and `intent`. `pending_tools` is a tuple. The seed copies it to the list `WorkflowState` already uses. Per model-the-domain, that value is the opening cursor the nodes already read. The router dict stays inside `assign_turn`. The loop state stays on `WorkflowState`.

`assign_turn` is the only builder. It calls `plan_tools` on `router_history`, with the existing empty-list fallback to `history`. It splits the queue once, copies the five flags with the same `bool` and `get` calls as `plan_node`, sets `forced` from `use_kb` only, and sets `intent` through `sse_intent` with empty evidence. `sse_intent` is the current `_sse_intent` function, moved so `answer_node` and `assign_turn` share it. The opening label keeps the forced `rag` branch. `intent_from_decision` stays on the router for eval scoring. Per foundational-thinking, the seed then becomes obvious. The graph copies those fields and the loop blanks `plan_node` writes today.

Per boundary-discipline, `assign_turn` has no database argument and no tool-gateway import. Router failures propagate. The graph trusts the assignment and does not re-plan. Per type-system-discipline, `confidence` and `reasoning` are not fields, and neither are `direct_answer`, `schedule_card`, or `pending_calendar`. A low-confidence calendar decision still produces `next_tool="calendar"`. It does not open a card until the work path runs the calendar tool, which is the rule today.

Per encode-lessons-in-structure, `assignment` is required and has no default. `workflow.py` does not import `plan_tools`. Per subtract-before-you-add and laziness-protocol, `plan_node` is deleted. `START` uses `open_on`, which is the old `after_plan` check on `next_tool`. Per minimize-reader-load, there is no wrapper that assigns and then runs, and no install node that only copies.

Per make-operations-idempotent, `assign_turn` writes nothing. A second call returns the same value. A crash before `invoke` leaves no claim and no calendar row. Per migrate-callers-then-delete-legacy-apis, both production callers and the `workflow.plan_tools` patches move in the same change. The eval infra test patches `app.agents.dispatch.plan_tools` and still expects `infra_error`.

Interface depth. `assign_turn` hides the router call, the history window, the queue split, the confidence drop, and the opening label. The caller sees one function and nine fields. `run_agent_workflow` hides the tool loop and cannot plan. What stays visible is the assignment itself, because the graph has to consume those fields, and the two call sites, because hiding the pair inside one function would put the gateway back behind the same entry as the observation.

The public function is no larger than that. There is no writer API, no task id on the assignment, and no stage flags.

## Synthesis decision

filled by the parent

## Tradeoffs accepted

We accept two call sites, `_run_workflow_sync` and `run_task`, in exchange for a work function that cannot plan.

We accept that `tool_routing` and `plan_needs` still call `plan_tools`, in exchange for keeping `confidence` out of the work graph. Scoring and assigning are different results.

We accept that `reflect_node` can replace `next_tool` after the opening assignment, in exchange for leaving `verify_state` where it is.

We accept the `router_history or history` fallback, including when the empty router list falls through to answer history, in exchange for not reopening the context-window work.

We accept that `forced` follows `use_kb` only, while `document_ids` still affect `plan_tools`, in exchange for matching `plan_node`.

We accept a router call that can finish after stop is set, in exchange for not adding a stop branch to `assign_turn`. The existing entry check still returns `_halted_result` before `invoke`.

## Alternatives considered

The other whole shape hoists `plan_tools` to the top of `run_agent_workflow` and leaves the public function unchanged. Callers, and the patches on `app.agents.workflow.plan_tools`, stay put. The queue split, the confidence drop, and the tool loop all hide inside that one function, so the argument list stays smaller.

It loses on interface depth. The capability the knife asks for is an observation that cannot call `gateway.invoke` and cannot publish a claim. On the hoisted shape that capability is not on the public function. A caller who wants the assignment has to enter the function that also invokes tools. Reading the signature does not show the split. The chosen shape spends one extra function and one required argument so each function hides one job. `run_agent_workflow` has no fallback that plans when `assignment` is missing. That fallback would be a second way to assign.

## Open questions and risks

Should router scoring stay on `plan_tools` permanently, so `confidence` never becomes a field a work-graph edit can branch on?

The patches of `app.agents.workflow.plan_tools` are in `test_workflow.py`, `test_calendar_card.py`, `test_calendar_dialogue.py`, and `test_agent_e2e.py`. A missed patch calls the real router or patches a name the work module no longer has. Does the implementation wave list those four files in the same change as `dispatch.py`?

## Next implementation step

Add `backend/app/agents/dispatch.py` with `TurnAssignment` and `assign_turn`, then require `assignment` on `run_agent_workflow` and delete `plan_node`.
