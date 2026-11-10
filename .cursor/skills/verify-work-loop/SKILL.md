---
name: verify-work-loop
description: "Check that the work loop reads stop before it calls a tool, does not insert again after an unknown write, does not write the calendar before the confirm card, and that dispatch still does not invoke, publish, or write the reply. Use when editing retrieve_node, reflect_node, answer_node, settle_unknown, or the chat reply branch."
---

# Verify the work loop

A stopped generation does not call a tool. An unknown write is settled by `decide_replay` and, only when that choice is reconcile, by `lookup_inserted_event`. The loop does not insert a calendar row. Dispatch still only records the assignment.

## When this applies

Run it when a change touches any of these:

- `backend/app/agents/workflow.py` (`retrieve_node`, `reflect_node`, `answer_node`, `admit`, `settle_unknown`)
- `backend/app/agents/harness/types.py` (`UnknownWrite`)
- `backend/app/api/chat.py` (the reply branch that calls `stream_chat`)
- `backend/app/agents/dispatch.py` (`assign_turn`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/work-loop/check.py
python verification/work-loop/check.py --break-stop
python verification/work-loop/check.py --break-unknown
python verification/work-loop/check.py --break-calendar
python verification/work-loop/check.py --break-dispatch
python verification/work-loop/check.py --break-lookup
```

The held command exits 0. Each break command exits 1 and names the invariant that failed. Exit 2 means the checker no longer matches the code, or TLC could not run. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`StopBlocksInvoke` says a call that already sees the stop bit does not set `invokedAfterStop`. The harm is another tool call after the generation has stopped. In the trace, `Call` sets that flag when `breakStop` is true. In the code, `retrieve_node` returns before `gateway.invoke` when `is_stopped()` is true, and `admit` reads the same bit before `execution_denial`.

`NoSecondInsert` says `insertKind` is never `second`. The harm is another insert after an unknown write. In the trace, `SecondInsert` sets that kind when `breakUnknown` is true. In the code, `reflect_node` calls `settle_unknown` and clears the queue. `settle_unknown` does not call `create_event`.

`CalendarStaysUnwritten` says `insertKind` is never `draft`. The harm is the loop inserting a row while it shows a confirm card. In the trace, `WriteFromLoop` sets that kind when `breakCalendar` is true. In the code, a calendar step still calls `execute_calendar`, which returns a draft, and the loop has no `create_event`.

`LookupFollowsChoice` says a lookup happens only when the choice is reconcile. The harm is a lookup that is really another send. In the trace, `LookupOnSend` looks up while the choice is send. In the code, `settle_unknown` returns before `lookup_inserted_event` when `decide_replay` does not return `reconcile`.

`DispatchStaysQuiet` says dispatch does not invoke, publish a claim, or write a row. The harm is the assignment step doing the work. In the trace, `Assign` sets those flags when `breakDispatch` is true. In the code, `assign_turn` returns a `TurnAssignment` and does not call `gateway.invoke`, `publish_schedule_claim`, or `stream_chat`.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one generation. It does not model a second user, `verify_state` adding a later read, the click that writes the calendar, or an empty 403. A stop that becomes true during `gateway.invoke` can still enter that call. The post-invoke `is_stopped()` check then replaces the graph output with the halted result. Dispatch may still record the assignment after stop. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at `8d59f5f46d0e4a9a5d97b273982bfbeeb62ea139`, the commit that adds `admit` and `settle_unknown`. Move the confirmation forward only after reading `retrieve_node`, `admit`, `settle_unknown`, `reflect_node`, and the `elif llm_messages` branch, and seeing that the model still describes them. If `retrieve_node` calls `gateway.invoke` before `if is_stopped():`, or `settle_unknown` looks up before `decide_replay` refuses, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
