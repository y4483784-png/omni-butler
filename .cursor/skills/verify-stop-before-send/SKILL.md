---
name: verify-stop-before-send
description: "Check that a stopped generation does not send a queued model request, keep a late chunk, write long-term memory, or delete a confirmed calendar event. Use when editing chat stop, stream_chat, complete_text, or the workflow cutoff."
---

# Verify stop before send

A generation that is already stopped does not send a model request that is still queued. A chunk that comes back after stop is not written into the current bubble. Stop does not write long-term memory and does not delete a confirmed calendar event.

## When this applies

Run it when a change touches any of these:

- `backend/app/core/llm.py` (`_create_sync`, `stream_chat`)
- `backend/app/api/chat.py` (the stop record and the memory gate)
- `backend/app/agents/workflow.py` (`answer_node`)
- `backend/app/agents/harness/execution.py` (`execution_denial`)
- `backend/app/core/generation_stop.py` (`is_stopped`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/stop-before-send/check.py
python verification/stop-before-send/check.py --break-send
python verification/stop-before-send/check.py --break-late
python verification/stop-before-send/check.py --break-memory
python verification/stop-before-send/check.py --break-calendar
```

The held command exits 0. Each break command exits 1 and names the invariant that failed. Exit 2 means the checker no longer matches the code, or TLC could not run. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`NoSendAfterStop` says a request that is flying or back was started before stop. The harm is a model call that had not left yet still leaves after the user stopped. In the trace, `SendWhileStopped` is that call. In the code, `_create_sync` and `_create_async` read `is_stopped` before `chat.completions.create`.

`NoLateChunk` says the bubble is not `late`. The harm is a chunk from a request already sent being appended after stop. In the trace, `AppendLate` is that write. In the code, the chat stream breaks before it appends, and `stream_chat` returns before it yields that chunk.

`NoMemoryWhenStopped` says a stopped generation does not set memory. The harm is another model call just to store a memory. In the trace, `RememberStopped` is that call. In the code, `if persisted and not is_stopped()` guards `_remember_turn_isolated`.

`CalendarStays` says the calendar count stays 1. The harm is stop deleting an event that was already confirmed. In the trace, `DropCalendar` is that delete. The chat path does not call `delete_event`.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one generation and one request. It does not model the provider network. A `create` that is already inside the HTTP client cannot be unsent, and the model allows that request to stay `flying` after stop. It does not model a second user, a tool already inside `execute`, mail, or a refund. A tool that has already entered `execute` can still commit a calendar row. Stop does not delete it.

## Keep the check with the code

The anchors were confirmed at `77cdd1c0f3f6a8cc31b3335e70c6f6a78042cc6e`. That commit adds this model and returns the workflow cutoff after `complete_text` when the generation is already stopped. Move the confirmation forward only after reading `_create_sync`, the chat stream loop, the memory gate, and `answer_node` at the new commit and seeing that the model still describes them. If the stop read moves to after `create`, or a stopped generation calls `_remember_turn_isolated`, the checker exits 2. That is drift, not a passing run.
