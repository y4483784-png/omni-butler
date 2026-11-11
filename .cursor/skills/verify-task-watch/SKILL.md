---
name: verify-task-watch
description: "Check that a waiting or completed generation is not stopped, that sleep credit is not counted as silence, that stopping an older generation leaves a newer generation and another user running, and that the sweep publishes no confirm card. Use when editing the task watchdog, its beat entry, or task_run.stop."
---

# Verify the task watchdog

A sweep stops only a `running` generation whose silence, after sleep credit, has reached 20 minutes. `waiting_person`, `waiting_resource`, `completed`, and `stopped` stay as they are. Sleep credit from a long tick gap stays on the ledger, so the next sweep does not count that gap again. Stopping one generation does not stop a newer generation or another user. The sweep does not publish a confirm card.

The question is an order of steps, so the check is TLC on a small model of those steps. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/tasks/watchdog.py` (`_EFFECT`, `_sleep_gap`, `decide`, `load_runs`, `run_sweep`)
- `backend/app/tasks/celery_app.py` (the `watch-task-runs` beat entry)
- `backend/app/services/task_run.py` (`stop`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/task-watch/check.py
python verification/task-watch/check.py --break-wait
python verification/task-watch/check.py --break-done
python verification/task-watch/check.py --break-sleep
python verification/task-watch/check.py --break-newer
python verification/task-watch/check.py --break-other
python verification/task-watch/check.py --break-card
```

The held command exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`WaitStays` says a generation that was parked waiting is still `waiting`. The harm is the sweep treating a person or a resource wait as a stall. In the trace, `Sweep` sets `oldStatus` to `stopped` while `parked` is true when `breakWait` is true. In the code, `_EFFECT` maps `waiting_person` and `waiting_resource` to `leave`, and `run_sweep` calls `task_run.stop` only when a fresh read still maps to `stop`.

`CompletedStays` says a generation that was finished is still `completed`. The harm is the sweep turning a finished generation into `stopped`. In the trace, `Sweep` sets `oldStatus` to `stopped` while `finished` is true when `breakDone` is true. In the code, `_EFFECT` maps `completed` to `leave`, and `task_run.stop` on `completed` keeps `completed`.

`SleepNotSilence` says a watchdog stop happens only while sleep credit is still 0. The harm is a later sweep counting a slept gap as silence. In the trace, `Sweep` increments `watchStops` while `credit` is 1 when `breakSleep` is true. In the code, `_sleep_gap` returns the whole gap only when it is longer than three check intervals, and `decide` adds that gap to the credit already stored for the generation.

`NewerStays` says the newer generation is still `running`. The harm is a stop of the older generation also stopping the newer one. In the trace, `Sweep` sets `newer` to `stopped` when `breakNewer` is true. In the code, `task_run.stop` takes that `user_id` and that `generation` and does not query other generations.

`OtherStays` says the other user's generation is still `running`. The harm is one user's stall stopping another user's task. In the trace, `Sweep` sets `other` to `stopped` when `breakOther` is true. In the code, the stop call carries the row's own `user_id`.

`NoCard` says `claims` stays 0. The harm is the sweep publishing a second confirm card. In the trace, `Sweep` sets `claims` to 1 when `breakCard` is true. In the code, `watchdog.py` does not call `publish_schedule_claim` and does not mention `CalendarEvent`.

`TypeOK` says the model variables stay inside the sets above. TLC also checks deadlock. A deadlock is a path that cannot settle. `Done` stutters after the first sweep.

If the trace cannot be replayed as `decide` and `run_sweep`, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one older generation, one newer generation of the same user, and one other user's running generation. Credit is 0 or 1. The sweep runs at most twice. It does not model a progress log, a worker restart, a second maintenance worker, an open claim row, or a reminder message. `NoCard` only says the sweep itself does not add a claim. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at `89b536df825b8fc9120fe1d77e7fa63b7a40476d`. Move the confirmation forward only after reading `_EFFECT`, `_sleep_gap`, `decide`, `run_sweep`, and the `watch-task-runs` beat entry, and seeing that the model still describes them. If `waiting_person` or `completed` maps to `stop`, if the sleep credit is not added to the stored credit, or if `run_sweep` calls `publish_schedule_claim`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
