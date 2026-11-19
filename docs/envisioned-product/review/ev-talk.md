# ev-talk review

One `TaskRun` row is a turn or a job. `kind` is `turn` or `job`. A new turn still stops other open turns in that session and leaves a `kind=job` row running. The reply stream ends when the interaction answer is stored. The job stays in the same session. The resident button labeled 停止生成 is gone. The composer stays available after that stream ends.

`goal` is on the row. A supplement appends the user text onto that goal. Two jobs share a session, so the transcript is not the goal.

## Model check

`python3 verification/talk/check.py` exits 0. The rules that hold are `JobSurvivesNewTurn`, `JobSurvivesDisconnect`, `CancelStopsNamedJob`, and `AmbiguousCancelAsks`.

`python3 verification/task-run/check.py` exits 0. The rules that hold are `TypeOK`, `CompletedStays`, `StoppedStays`, `NotRunningAgain`, and `WaitingNeverCompletes`. `continue_job` is not in that spec. OpenJDK 21.0.13 ran both checkers. The jar is `verification/tlc/tla2tools.jar`.

The talk model is one user, two jobs, and one interaction turn. A new turn does not stop either job. Disconnect stops the turn and leaves both jobs running. A named cancel stops job 1 and leaves job 2 running. An ambiguous cancel sets `asked` and leaves both jobs running.

The model does not cover an HTTP call already inside the provider, a second browser, or Celery redelivery.

## Tests

From `backend`, on a fresh `ci-talk.db`. The system `python3` has no pytest, so the interpreter was `/tmp/talk-venv/bin/python`. The environment was `DATABASE_URL=sqlite+pysqlite:///./ci-talk.db`, `REDIS_ENABLED=false`, `CELERY_TASK_ALWAYS_EAGER=true`, `SANDBOX_RUNNER_URL` empty, `SECRET_KEY=test-secret-key`, and `ADMIN_PASSWORD=test-admin-pass`.

`/tmp/talk-venv/bin/python -m pytest tests/test_talk.py tests/test_task_generation.py tests/test_chat_sse_routing.py tests/test_schedule_confirm_claims.py -q --tb=line` exits 0.

`tests/test_talk.py` includes the SQLite alembic upgrade, downgrade to `0018_picture`, and upgrade again. `psql` is not installed here, so the Postgres branch of `0019_talk` was not executed.

A `kind=job` row stays `running` after `begin_turn` for the same session. Stopping the interaction generation leaves the job `running`. A classified cancel of that generation leaves it `stopped`, and a second open job for the same user stays `running`. A sentence that contains the word cancel and is classified `new` starts a job and leaves the earlier job `running`.

A forbid on job A stays `forbid` when a later message creates job B with `allow`. A supplement on job A increments `intent_version`, clears `exec_token`, and sets `continuation_due`. An armed claim stays `unknown` and keeps `remote_request_id`. An open claim with an empty `remote_request_id` becomes `cancelled`. The old worker's `renew_hold` returns 0. A redirect to `forbid` makes a later `claim_web_execution` return 0. Confirming the cancelled unarmed card returns 409 and inserts no calendar row. A second `arm_remote_claim` on the armed unknown claim returns false.

`continue_job` from `running`, `completed`, or `stopped` raises `LateResult`. `finish` and `stop` on a completed row keep `completed`. `test_stop_after_finish_stays_completed` and `test_later_begin_leaves_the_finished_turn_completed` stay in `tests/test_task_generation.py` and pass. `waiting_resource` plus `apply_intent` ends `running`, with `exec_token` null and `continuation_due` true. `waiting_person` plus `apply_intent` stays `waiting_person`. A router failure asks and does not stop or start a job. An ambiguous cancel with two open jobs in the session asks and both stay `running`.

The same environment also ran `tests/test_message_actions.py`, `tests/test_calendar_card.py`, and `tests/test_event_inbox.py`. That command exits 0. A regenerate does not classify and does not stop the job.

## Perf

This pass did not time the suite against trunk. There is no comparison.

## Live lanes

No browser lane was run. This repo has no `control-ui` skill. The composer change was read in source: the 停止生成 button and the `.stop-btn` rules are gone, and `busy` is still the reply-stream flag.

## Formal evidence

The two checker commands above are the formal evidence. There is no separate evidence directory. No second model reviewed this diff. The kickoff forbids subagents.

## What this unit does not do

`run_job` inserts the row, writes one assistant line in the session, and returns while the row is `running`. It calls no model and no search. It does not set `exec_token` and it does not call `release_hold`.

This unit does not reserve a dispatch token, publish a replacement, or adopt an expired reservation. An expired `dispatch_until` is not a stop. The watchdog file is unchanged. There is no provider cancel API, no `task_steps` table, no `tool_calls` table, no reconcile status, no beat entry, and no queue besides `default`.

A dead worker stays `running` until a later resume unit. A claim that already has a `remote_request_id` stays `unknown` or `written`. Cancel does not delete a calendar event, unsend mail, or refund a payment. Closing the reply stream does not withdraw an HTTP call that has already entered the provider.
