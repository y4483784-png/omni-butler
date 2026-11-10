# ev-task review

Each accepted chat turn has one `TaskRun`. The status is `running`, `waiting_person`, `waiting_resource`, `completed`, or `stopped`. A stored answer is `completed`. `stop` is `stopped`. Those two are different statuses. There is no reason column.

`generation` counts turns for one user. The unique key is `user_id` and `generation`. It has no action and no fingerprint. `UnfinishedWriteKey` is `user_id`, `action`, and `fingerprint`. This knife does not store that key and does not look it up.

## Model check

`python3 verification/task-run/check.py` exits 0. The rules that hold are `TypeOK`, `CompletedStays`, `StoppedStays`, `NotRunningAgain`, and `WaitingNeverCompletes`.

`python3 verification/task-run/check.py --break-completed-stop` exits 1. `CompletedStays` fails when stop rewrites a finished turn. The product code does not do that. `stop` on `completed` keeps `completed`.

The model is one session and at most two turns. It follows the move table in `task_run.py`. It does not model the page, the database lock, or a later confirm click. The chart is `ev-task-statechart.mmd` in this directory, and the same file is `verification/task-run/statechart/chart.mmd`.

## Tests

From `backend`, with `DATABASE_URL=sqlite+pysqlite:///./ci-eval.db`, `REDIS_ENABLED=false`, `CELERY_TASK_ALWAYS_EAGER=true`, `SANDBOX_RUNNER_URL` empty, `SECRET_KEY=test-secret-key`, and `ADMIN_PASSWORD=test-admin-pass`:

`python3 -m pytest tests/test_task_generation.py tests/test_chat_sse_routing.py -q` exits 0.

A normal `finish` reads back `completed`. A later message leaves that row `completed`. A later message while the row is open sets it to `stopped`. `stop` and then `finish` raises `LateResult` and the row stays `stopped`. A second user does not see the first user's row.

## Perf

One run each, in this order, on a machine with load average 0.00 and 4 cores. Wall time includes pytest startup. Exit code 0 on every run. The fail lines are 60 seconds for the chat routing file and 30 seconds for the task file.

- Trunk chat routing, first run: 4.47 seconds.
- Head chat routing: 4.28 seconds.
- Trunk chat routing, second run: 4.15 seconds.
- Head task generation: 2.68 seconds.

Both head times are under the fail lines. This is not a claim that the head is faster. The runs are one each, as the plan's probe asks.

## Live lanes

The operator moved running-product checks to the end of the whole envisioned-product plan. Lanes 1 through 10 of this knife are deferred. They are not passed.

This repo still has no `control-ui` skill, and `docker` is not installed here. Those facts are why the lanes were not driven on the first pass. They are not a pass, and the deferral does not close them.

## Formal evidence

Raw logs, exit codes, the jar identity, and the rendered chart are in `ev-task-evidence/` in this directory. `chart.png` is the statechart script's Mermaid, rendered for review. The Mermaid source remains `ev-task-statechart.mmd`.
