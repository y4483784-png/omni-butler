---
name: verify-unknown-write
description: "Check that an unknown calendar confirm is not inserted again and that a stopped generation does not execute a tool. Use when editing schedule confirm, calendar insert, the tool gateway, or the side-effect contract."
---

# Verify an unknown calendar write

One confirmed calendar write stores one remote event. A later click looks that event up. It does not insert again. A stopped generation does not execute a tool.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/schedule_confirm.py` (`consume_schedule_claim`, `arm_remote_claim`)
- `backend/app/services/calendar_google.py` (`_insert_google_event`)
- `backend/app/agents/harness/gateway.py` (`invoke`)
- `backend/app/agents/harness/execution.py` (`execution_denial`)
- `backend/app/agents/harness/effects.py` (`decide_replay`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/unknown-write/check.py
python verification/unknown-write/check.py --break-blind
python verification/unknown-write/check.py --break-stopped-execute
```

The held command exits 0. Each break command exits 1 and names the invariant that failed. Exit 2 means the checker no longer matches the code, or TLC could not run. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`NoBlindPost` says a click while the claim is unknown does not send another insert. The harm is two remote events for one confirmation. In the trace, `Blind` is that second click. In the code, that is `consume_schedule_claim` raising `unknown`, and `_insert_google_event` looking the request id up instead of posting again.

`NoStoppedExecute` says a tool does not run after the generation is stopped. The harm is a side effect after the user or the watchdog stopped the turn. In the trace, `BrokenRun` is that call. In the code, `execution_denial` returns before `spec.execute`.

`AtMostOneRemote` says the model stores at most one remote event. A known rejection does not store one, so a later send from `open` still stores one.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model has one claim and one tool call. It does not model Google's network, token refresh, patch, delete, or two users. A refresh that fails before `arm_remote_claim` commits is a rollback in `refresh_access_token`, covered by `tests/test_tool_contract.py`. The lookup request itself is a GET. The model treats it as not an insert.

## Keep the check with the code

The anchors were confirmed at `8c5db6cc5f08419222362196d63bfc6205d8e20a`, the commit that added `arm_remote_claim` and `execution_denial`. They were run again after 408, 409, 412, 429, and a quota 403 were classified as unknown, and after the occupied index started covering `unknown`. That later edit does not move the arm or the stop check. Move the confirmation forward only after reading those functions at the new commit and seeing that the model still describes them. If `consume_schedule_claim` stops refusing `unknown`, or the insert is posted before `_arm_user_write`, the checker exits 2. That is drift, not a passing run.
