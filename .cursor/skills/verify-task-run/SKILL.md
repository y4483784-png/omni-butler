---
name: verify-task-run
description: >
  Check that a TaskRun stays completed after a normal finish, stays stopped
  after stop, and does not return to running. Use when editing TaskRun,
  TASK_RUN_STATUSES, task_run.py, begin_turn, finish, stop, wait_for_person,
  wait_for_resource, or the chat calls that start a turn.
---

# Verify task run

A finished turn stays `completed`. A stopped turn stays `stopped`. A confirm card stays `waiting_person` when the answer is stored. Nothing moves back to `running`. Exit 0 means every invariant holds.

The code this check describes is commit `ca5fe61`. Move that commit forward only after you confirm the spec still describes `task_run.py` and the three chat calls.

## Run the check

Run this from the repo root. Java 21 must be on `PATH`.

```
python3 verification/task-run/check.py
```

Exit 0. `CompletedStays`, `StoppedStays`, `NotRunningAgain`, and `WaitingNeverCompletes` hold, along with `TypeOK`.

```
python3 verification/task-run/check.py --break-completed-stop
```

Exit 1. `CompletedStays` is violated because stop rewrites a finished turn.

The script loads `verification/tlc/tla2tools.jar`. The pin is sha256 `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`, manifest `2.0 2026-11-06`, revision `d8fb231de4e4b5d02c1b2ed1ac759c086f694021`. A missing file, a hash or manifest mismatch, a TLC timeout, a deadlock, or an anchor that no longer matches the code exits 2.

To draw the chart, read the statechart skill and run it on `verification/task-run/TaskRun.tla` with `verification/task-run/statechart/view.mjs`. Set `TLA2TOOLS` to `verification/tlc/tla2tools.jar`. The last chart is `verification/task-run/statechart/chart.mmd`. The raw TLC log from the last drawing is `docs/envisioned-product/review/ev-task-evidence/tlc.out`.
