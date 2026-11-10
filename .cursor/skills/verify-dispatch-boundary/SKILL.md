---
name: verify-dispatch-boundary
description: "Check that dispatch only records this turn's assignment. It does not invoke a tool, publish a schedule claim, or write the calendar, and stop still blocks a later gateway call for that generation. Use when editing assign_turn, run_agent_workflow, or the chat worker that calls them."
---

# Verify dispatch boundary

`assign_turn` records this turn's assignment and returns. The work graph is the only path that calls the gateway. A schedule claim is published only after that graph returns a card. A calendar row is written only after the claim is consumed. A generation that is already stopped does not make a later gateway call.

## When this applies

Run it when a change touches any of these:

- `backend/app/agents/dispatch.py` (`assign_turn`, `TurnAssignment`)
- `backend/app/agents/workflow.py` (`run_agent_workflow`, `retrieve_node`)
- `backend/app/api/chat.py` (`_run_workflow_sync`, `publish_schedule_claim`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/dispatch-boundary/check.py
python verification/dispatch-boundary/check.py --break-invoke
python verification/dispatch-boundary/check.py --break-claim
python verification/dispatch-boundary/check.py --break-calendar
python verification/dispatch-boundary/check.py --break-stop
```

The held command exits 0. Each break command exits 1 and names the invariant that failed. Exit 2 means the checker no longer matches the code, or TLC could not run. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`DispatchDoesNotInvoke` says `dispatchInvoked` stays false. The harm is the assignment step calling a tool. In the trace, `Assign` sets that flag when `breakInvoke` is true. In the code, `assign_turn` calls `plan_tools` and does not call `gateway.invoke`.

`DispatchDoesNotClaim` says `dispatchClaimed` stays false. The harm is a schedule claim minted before the work graph returns a card. In the trace, `Assign` sets that flag when `breakClaim` is true. In the code, `publish_schedule_claim` runs in `chat.py` after `wf = await wf_task`.

`DispatchDoesNotWrite` says dispatch did not write a row, and a calendar count of 1 implies a claim. The harm is an insert that never waited for the click. In the trace, `Assign` sets `dispatchWrote` and `calendar` to 1 when `breakCalendar` is true. In the code, `assign_turn` has no calendar write. `Confirm` is the click after `Publish`.

`StopBlocksGateway` says `invokedAfterStop` stays false. The harm is a gateway call for a generation that is already stopped. In the trace, `WorkCall` sets that flag when `breakStop` is true. In the code, `run_agent_workflow` returns `_halted_result` when `is_stopped()` is already true, and `retrieve_node` returns `_cutoff` before `gateway.invoke`.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one generation. It does not model a second user, a tool already inside `execute`, a router model call, or `verify_state` choosing another tool after the assignment. Dispatch may still call `plan_tools` after stop. That call is not a gateway call. A claim published after the graph returns is the chat path, not dispatch. The click that writes the calendar is outside this knife.

## Keep the check with the code

The anchors were confirmed at `d2d30dd16dedc98db4312f2086beba93deb1f8fa`. That commit adds `assign_turn` and removes `plan_node`. Move the confirmation forward only after reading `assign_turn`, the stop check at the start of `run_agent_workflow`, the stop check in `retrieve_node`, and the claim publish after `wf = await wf_task`, and seeing that the model still describes them. If `assign_turn` gains `gateway.invoke` or `publish_schedule_claim`, or the worker calls `run_agent_workflow` before `assign_turn`, the checker exits 2. That is drift, not a passing run.
