# Call owner for a running tool

This document is the design for parent review. This branch does not change production code. The inventory in `docs/envisioned-product/2026-11-20-residual-closure.md` stays open.

Base `19867c51ac4e29c1ca16174668925133e4d76f9f`. Pull request 107 targets `release` at that commit. Branch `cursor/loop03-call-owner-design-ba91`.

LOOP-03 and TALK-04 are one unit. A supplement clears the job hold while a tool call is `running`, the sweep publishes another `run_job`, and `begin_read` invokes that call again. An expired hold does the same thing while the first worker is still inside the tool. Forcing `continuation_due` false still invokes twice. The idle reserve matches a null `exec_token` and does not read the flag.

## What the reproduction ran

Command, from `backend`:

```text
python3 ../verification/loop-owner/reproduce_overlap.py
```

Exit code 1. Log `verification/loop-owner/reproduction-log.txt`, started `2026-11-20T16:11:03Z`. Database `sqlite:////tmp/loop03-owner-repro.db`. The tool is `kb` or `web` through `app.agents.harness.gateway.invoke`. No provider, mailbox, or purchase is called.

The worker under test is `run_job`. The supplement is `chat._apply_talk` with relation `supplement`, which calls `apply_intent`. The publisher is `watchdog.run_sweep`.

| Case | What was alive | Invokes | Stored result |
| --- | --- | --- | --- |
| Second `run_job` with the same dispatch arguments | First worker inside the tool | 1 `kb` | `answer-1` |
| Two threads call `run_job` with those same arguments | Both threads | 1 `kb` | `answer-1` |
| Supplement, then sweep | First worker inside the tool | 2 `kb` | `answer-2` |
| Supplement, force `continuation_due` false, then sweep | First worker inside the tool | 2 `kb` | `answer-2` |
| Set `hold_until` to 2000-01-01, then sweep | First worker still inside the tool | 2 `kb` | `answer-2` |
| `task_run.stop`, then let the tool return | First worker inside the tool, then it returns | 1 `kb` | null, state `cancelled`, job `stopped` |
| `enqueue_job` for a second goal | First worker inside the tool | 1 `kb` on the first job | `answer-1` on the first job |
| Pulse at `llm_timeout` 0.4, then supplement, then sweep | First worker inside the tool | 2 `kb` | `answer-2` |
| Two saved calls, supplement `web=forbid`, then sweep | First `kb` inside the tool, `web` still `pending` | 2 `kb`, 0 `web` | `kb` is `answer-2` at call intent 1. `web` stays `pending` |
| Spawn a child, `SIGKILL`, sweep while `hold_until` is in the future | Child is dead. Exit code `-9` | 0 from that sweep | Call stays `running`, result null |
| Same dead child, then expire the hold and sweep | Child is already dead | 1 `kb` from the lease sweep | `answer-1`, job `completed` |

The expired-hold row is a live first worker. It is not process death. The `SIGKILL` row is process death. The lease sweep after that kill is the same retire SQL as the live expired-hold row. The exit code is not an input to that SQL.

On the pulse row, `renew_hold` returned `1, 1, 1, 0, 1, 1, 1`. The miss is the first worker after `apply_intent` cleared `exec_token` and moved `intent_version` from 1 to 2. The later successes are the second driver. The model request after the second invoke saw `pulse-tick plus more`. The notes are `已开始：pulse-tick`, `answer-2`, and `done`. `answer-1` is not stored. `_one_pass` returns `leave` when `pulse.lost()` is set, before `save_read_result` (`backend/app/tasks/job.py` lines 630-634).

On the two-call row the second driver stored `answer-2` on `call-a`. That call's `intent_version` stayed 1. The job `intent_version` was 2, `web_constraint` was `forbid`, and the goal was `two-call plus forbid`. The model ran once, on the original goal. It did not run again. `call-b` stayed `pending` with a null result. The job stayed `running` with the second driver's `exec_token`. The forbid did not become a `rejected` row.

On the cancel row the tool returned after `stop`. The call stayed `cancelled`, the result stayed null, and the job stayed `stopped`. The notes stayed `已开始：cancel`.

On the death row, `multiprocessing` spawn ran `run_job` in a child. The parent called `Process.kill` after the child wrote the ready file from inside `gateway.invoke`. `exitcode` was `-9`. The job was `running`, the same `exec_token` was set, and `hold_until` was about one hour ahead. `run_sweep` published 0 messages. After the reproduction then set `hold_until` to 2000-01-01, the sweep invoked `kb` once and completed the job. A progress notice said the task was still running. That notice is not an exit record.

`pytest tests/test_loop_job.py::test_a_crash_after_save_leaves_the_calls_pending tests/test_loop_job.py::test_a_second_run_job_recovers_a_pending_read` passed, two tests. The recovery test does not assert an invoke count. `test_a_later_drive_runs_a_running_read_again` (`backend/tests/test_loop_job.py` lines 495-521) expects `invokes == ["kb", "kb"]` and `result == "again"`. That test records the current replay. The implementation replaces it. This branch does not edit it, because the new expectation would fail on today's code.

SQLite showed the blocked-tool overlap because `begin_read` commits before `gateway.invoke`. Postgres was not run. `psql` is not installed here, and no database server was listening.

## Where the second invoke comes from

`begin_read` returns `invoke` for `CallState.RUNNING` with no compare-and-swap (`backend/app/services/loop_round.py` lines 362-365). `_cas_running` matches only `pending` (lines 1191-1200).

`run_job` returns before `_drive` when adopt matches zero rows (`backend/app/tasks/job.py` lines 166-176). Adopt also requires the dispatch token, epoch, intent, and window (`backend/app/services/resume.py` lines 89-96). A successful adopt clears `dispatch_token`. The same-token log has `exec_token` set and `dispatch_token` null before the second `run_job`. That call matches zero rows. The concurrent pair also invoked once.

`apply_intent` always increments `intent_version` and `exec_epoch`, clears `exec_token`, `hold_until`, and the dispatch columns, and sets `continuation_due` (`backend/app/services/task_run.py` lines 550-559). `_RESERVE_IDLE` matches `running` and a null `exec_token` (`backend/app/services/resume.py` lines 41-51). It does not read `continuation_due`. `_RETIRE_HOLDER` clears a token whose `hold_until` is past (lines 53-65) and does not read `continuation_due` either.

The inventory close for TALK-04, a false `continuation_due` while a call is open, leaves this publisher in place. The forced-false case still invoked twice.

## Execution owner and model intent

`_lock_holder` requires `exec_token`, `exec_epoch`, and `intent_version` (`backend/app/services/loop_round.py` lines 1045-1057). `begin_read` calls it before it looks at the call (lines 347-351). A pending sibling cannot pass that lock after a supplement, even if the pending compare-and-swap and `renew_hold` stop requiring the intent.

The same intent predicate is on every hold check in this chain.

| Site | What it allows | Intent required today |
| --- | --- | --- |
| `_lock_holder` | Every locked job read below | Yes, lines 1045-1057 |
| `begin_read` | Start or repeat a saved read | Yes, through `_lock_holder` |
| `_holder_match` | The SQL `exists` behind the call update | Yes, lines 1177-1188 |
| `_cas_running` | `pending` to `running` | Yes, call intent and holder intent, lines 1191-1200 |
| `_deny_web` | Pending or running web to `rejected` | Yes, call intent and holder intent, lines 1150-1160 |
| `save_read_result` | `SAVE_RESULT` or `DENY` | Yes, through `_lock_holder`, lines 393-396 |
| `renew_hold` | Extend `hold_until` | Yes, lines 603-614 |
| `_HoldPulse._run` | Calls `renew_hold` | Yes, lines 265-272 |
| `_renew` | The drive loop's renew | Yes, lines 380-388 |
| `_drive` | Enter the drive | Yes, once, at lines 296-298 |
| `save_turn` | Save a model turn | Yes, through `_lock_holder`, line 297 |
| `commit_job_output` | Write streaming answer text | Yes, lines 64-74 |
| `open_cards` | Open confirm cards | Yes, through `_lock_holder`, line 466, and the job update at lines 502-511 |
| `observe_ready` | See a ready step | Yes, line 525 |
| `note_unknown` and `lookup_desktop_write` | Armed-write lookup | Yes, through `_lock_holder` |
| `claim_web_execution` | A web claim used by tests | Yes, lines 727-735. `run_job` does not call it |

Two matches have to exist. The execution match is `user_id`, `generation`, `status = running`, `exec_token`, and `exec_epoch`. The model match adds `intent_version`.

Execution match is what `begin_read`, the pending compare-and-swap, `_deny_web`, the pulse, and the owned result write use while a saved call is `pending` or `running`. Those updates do not require `tool_calls.intent_version` to equal the job's current `intent_version`. The call keeps the version it was saved with. `_deny_web` still reads the job's current `web_constraint`. A forbid denies the pending web sibling through the existing `DENY` edge. That edge is legal from `pending` and from `running` (`backend/app/services/loop_round.py` lines 110 and 113).

Model match stays on `save_turn`, `commit_job_output`, the model iteration of `_renew`, `open_cards`, `note_unknown`, `lookup_desktop_write`, and `claim_web_execution`. A worker whose stored intent is behind the job does not call the model, does not save a turn, and does not open a card. Confirm-lease checks stay as they are.

`_leave`, `_clear_due_if_set`, and `release_hold` also require the intent today (lines 1242-1265 in `loop_round.py`, and `task_run.py` lines 630-639). The release after the last saved call uses the execution match. If the intent has moved, that release sets `continuation_due` and does not call the model. The model path's `_leave` keeps the intent match.

The pulse `except` body stays. It rolls back and continues, and it does not set `lost` (`backend/app/tasks/job.py` lines 273-276). `lost` stays a renew rowcount other than 1.

## Tool classes

`claim_call` is true for `mail.send`, `errand.place`, `desktop.open`, `desktop.run`, and calendar write actions (`backend/app/services/loop_round.py` lines 40-56 and 261-267). `begin_read` returns `stale` for those rows and does not invoke. They become confirm cards in `open_cards`.

The invoke path is `kb`, `web`, `web_fetch`, `sandbox`, and calendar reads. The registry `effect="read"` is not a replay license. `web` sends a search request. `sandbox` runs code. The two-call reproduction showed the current web forbid failing closed into a stuck `pending` sibling, not a second search POST. The implementation still must not send an armed write through a read retry.

Replay after a recorded process exit depends on a handoff marker, not on the registry label.

- `kb` is a local read. A new attempt is allowed after exit when the rules below say the previous attempt did not need to stay unique. The same attempt is not invoked again.
- `web` and `web_fetch` can have left the process. After a committed handoff, exit does not start another attempt of that call.
- `sandbox` is in that same bucket. A sandbox run is not safe to repeat just because the process exited.
- Armed writes stay on the confirm path. This design does not replay them from a `running` row. `_owner_gone` stays unchanged (`backend/app/services/loop_round.py` lines 854-861).

## Two ownership designs

Both designs keep one current execution of a call. Both refuse to treat lease expiry, a missed heartbeat, a cleared token, or elapsed time as proof that the old process has stopped. Both leave a `pending` call recoverable by one compare-and-swap. Both keep the lock order claim, then job, then cursor. Neither resets `running` to `pending` because a lease expired.

### Design A. The call row owns execution

Add nullable `tool_calls.owner_token`, `attempt`, and `handed_off_at`. The pending-to-running update sets `owner_token` to a new value and `attempt` to 1, or to the previous attempt plus one when the rules below allow a replacement. The update returns that owner only to the worker that won it. `begin_read` on a `running` row returns `busy` and does not invoke. The winner invokes once.

The worker commits `handed_off_at` on that owner and attempt, then calls `gateway.invoke`. The commit is the boundary between "this attempt has not made the request" and "this attempt may have been accepted."

`apply_intent`, while any call on the latest step is `pending` or `running`, still appends the goal, stores `web`, and increments `intent_version`. It does not clear `exec_token` or `hold_until`, it does not clear the dispatch columns, it does not increment `exec_epoch`, and it does not set `continuation_due`. With no such call, today's transaction stays.

The execution renew does not require `intent_version`, so a supplement does not kill the heartbeat. The model renew still does.

`_RESERVE_IDLE` and `_RETIRE_HOLDER` gain `AND NOT EXISTS` a `running` tool call for that job. A pending-only job can still be retired after `hold_until`. That is the crash before the compare-and-swap. The new holder claims those pending rows with the execution match.

### Design B. The job hold owns the call and the next model turn

No new call column. While a call is `running`, `apply_intent` appends the supplement to a `pending_amendment` field and changes nothing else. The same worker saves the result with today's hold check, then copies the amendment onto the goal and calls the model itself.

Readers of `goal`, `web_constraint`, and `intent_version` keep the pre-amendment values for the whole call. The two-call reproduction already needs the current `web_constraint` to deny the sibling during the first call. Design B hides that forbid until the same process finishes. A dead process never applies the amendment.

## Chosen ownership

Design A. The call row is the only writer of "this call is in flight." The job row is the only writer of intent. A supplement can change intent while the call runs, and a second driver still cannot enter `gateway.invoke`.

Design B avoids a column by hiding the amendment until the same process finishes the tool. The two-call case needs the forbid to be visible to the sibling before that process finishes. Design A keeps the goal text immediate, which the supplement log already shows, and it gives the save path an owner.

## Recovery designs

The ownership choice does not by itself say what happens after the process is gone. These are the recovery designs. They are not the amendment designs above.

### Parent reap

Celery's prefork pool runs the task in a child. The parent supervisor reads `worker.exitcode` after the operating system has reaped the child, including `SIGKILL` (`billiard/pool.py` around lines 1289-1332, Celery 5.4.0 as installed here). If that child had accepted the job and the job is not ready, `AsynPool.on_job_process_lost` raises `WorkerLostError` (`celery/concurrency/asynpool.py` lines 1140-1147).

`Request.on_failure` sees that exception. With `task_acks_late` and `task_reject_on_worker_lost`, it requeues the message and does not send `task_failure` (`celery/worker/request.py` lines 602-611). The current configuration throws away the exit code. `celery_app.py` lines 22-28 set those flags. `visibility_timeout` is `max(3600, celery_ingest_time_limit + 300)`. The comment on line 27 says the timeout must exceed the hard ingest limit so a healthy long task is not redelivered.

The chosen design writes the exit in that parent path, before the requeue. The row is keyed by the Celery request id. Adopt already knows the job id and the `exec_token` it minted. The child stores that pair before `_drive`. The parent update sets `owner_exited_at` and `exitcode` on running calls for that token. A missing handoff stays null.

`run_job` is routed to queue `default` (`backend/app/tasks/job.py` line 146). The compose worker command listens to `ingest,maintenance` only (`backend/entrypoint.sh` line 19). The implementation adds `default` to that command so this parent is the process that can reap the child. The hook is still the pool parent of whatever consumes the task. This reproduction did not boot that compose worker. It killed a spawned child and read `exitcode` `-9`.

### Child Python exit

`worker_process_shutdown` is sent from `process_destructor` (`celery/concurrency/prefork.py` lines 82-89). Billiard runs that callback from `Worker._do_exit` inside the child (`billiard/pool.py` lines 300-317). The child reaches it only when it exits through Python. `SIGKILL` skips it. The death reproduction is that skip. Exit code `-9` was visible to the parent, and the child wrote no shutdown record.

### Broker redelivery

A redelivered message, or `visibility_timeout`, is not an exit code. A live `run_job` longer than the timeout is redelivered while the process is still in the tool. Treating that flag as death would start a second attempt beside a live call. The same-token case already shows a second delivery invoking once only because adopt fails. After a cleared token, a redelivery is the overlap in the supplement log.

### Lease retire

`_RETIRE_HOLDER` publishes when `hold_until` is past. The expired-hold case did that while the first worker was blocked in the tool and invoked twice. The death case did it after `SIGKILL` and invoked once. Both are the same SQL. The lease is not a death proof, and it is not a license to replay.

## Chosen recovery

Parent reap is the death proof. The child-exit hook is not sufficient, because it misses `SIGKILL`. Broker redelivery is not death. Lease retire is not death and does not start an attempt.

The resolution uses the handoff marker and the exit row.

Pending, before the compare-and-swap. The call is `pending`, `owner_token` is null, and `handed_off_at` is null. The request has not been made. A later holder runs the compare-and-swap once and invokes once. That is the existing pending recovery. It is safe for every class because this attempt did not call out. The pending tests stay, and the implementation asserts the invoke count.

After the compare-and-swap, before the handoff commit. The call is `running`, an owner is set, and `handed_off_at` is null. If the parent exit row matches that owner, this attempt did not reach `gateway.invoke`. One replacement attempt is allowed for every class, under a new `owner_token` and a new `attempt`. The old attempt is not invoked. `begin_read` still returns `busy` for the old owner. If no exit row exists, nobody starts that replacement.

After the handoff commit. The request may have been accepted. The same owner and attempt are never passed to `gateway.invoke` again. Process exit does not change that.

- For `kb`, one new attempt may run after a matching exit row. The old attempt remains in the effect audit below.
- For `web`, `web_fetch`, and `sandbox`, the call takes a new legal edge, `(running, note_unknown) -> unknown`. The sentence is stored as the call result. The job moves to `waiting_person` with `continuation_due` false. The sweep does not start a driver. A later person message is a new intent and can only create a new call id. It does not reuse this owner.
- Armed writes do not use this invoke path.

The handoff commit and the external accept are not one atomic step. A kill after the commit and before the syscall is "may have been accepted." That attempt becomes `unknown` for `web`, `web_fetch`, and `sandbox`. It is not invoked again.

No exit row. This is the machine crash, or a parent that dies with the child, or a legacy process that never wrote a worker row. The call is not marked `unknown`, because that would claim an observation the parent did not make. `begin_read` returns `busy`. Retire does not publish a new attempt. When `hold_until` is past and the exit row is still missing, the sweep parks the job as `waiting_person`, sets `continuation_due` false, and sets `unresolved_at` on the running call. The call state stays `running`, so a live owner's later `SAVE_RESULT` is still legal. The park does not say the process died. It stops attaching a driver. A person message is the continuation. There is no second invoke of this attempt.

That park is the supported resolution for the unobserved case. It is not a silent cancel, and it is not a blind rerun. A confirmed exit does not wait for the lease. It resolves in the parent reap.

## Late result

`apply` has no `(cancelled, save_result)` edge. The edges are listed at `backend/app/services/loop_round.py` lines 108-122. A missing edge raises `IllegalPhase` (lines 199-201). `save_read_result` then stores nothing (lines 402-407). The cancel reproduction is that drop. The result stayed null, the state stayed `cancelled`, and the job stayed `stopped`.

The implementation does not add `(cancelled, save_result) -> result`, and it does not move a cancelled call back to `running` so that edge can fire. `SAVE_RESULT` remains the edge from `running` to `result` only.

The audit is a new table `tool_call_effects`. One row is `(tool_call row id, owner_token, attempt, body)`. The unique key is those three identity columns. `record_owner_result` does the following.

1. Lock the call row. If `owner_token` or `attempt` differs, write nothing.
2. Insert the effect row if that key is absent. A second insert of the same key writes nothing.
3. If `state` is `running` and `result` is null or already equals `body`, call `apply(SAVE_RESULT)` and set `result`. That is the existing edge.
4. If `state` is `cancelled`, stop after the insert. Do not call `apply`. Leave `state` as `cancelled` and leave `result` null. Do not change `task_runs.status`. Do not set `continuation_due`. Do not insert a `messages` row. Do not publish a session event of kind `message`.
5. If `state` is `result` or `unknown` and `result` is already set, leave `result` and `state` as they are. The effect insert from step 2 still keeps this attempt's body.
6. A stopped job stays stopped. A `waiting_person` job stays `waiting_person`. The write does not set `status` back to `running`.

The current UI reads `messages` and the call `result`. The cancel path writes neither the late body. The effect table is the evidence that an accept happened. A newer call id is a different row, so this update cannot move that newer result.

`_one_pass` calls `record_owner_result` after `gateway.invoke` returns, including when `pulse.lost()` is already set. The lost check stops the next model request. It does not skip this write.

## Existing running rows

Revision `0026` adds the nullable call columns and the effect table. It does not set `owner_token`, `attempt`, or `handed_off_at`. It does not change `state`. A null owner is not a manufactured owner.

`begin_read` on `running` with a null `owner_token` returns `busy` and does not invoke.

The first `renew_hold` that still matches that job's `exec_token` and `exec_epoch` may stamp `owner_token` with that token and `attempt` with 1, and only when `owner_token` is null. That is the live holder writing the token it already holds. A dead row does not renew, so it stays null. The migration itself does not do this stamp.

A legacy `running` row that never stamps an owner is the unobserved case above. The park shows `unresolved_at` and does not invoke. A stamp that arrives from a live holder can still `SAVE_RESULT`. That write does not move a parked job back to `running`.

## Lock order

These orders are from the current source. The new persist takes the job lock before the call row, the same way `save_read_result` does, and it does not take a claim lock.

| Path | Order |
| --- | --- |
| `apply_intent` | All of the user's claims `FOR UPDATE` ordered by token, then the job `FOR UPDATE`, then the cursor in `publish_event` |
| `open_cards` | `lock_open_claims`, then `_lock_holder`, then the cursor |
| `confirm_call`, `note_unknown`, `lookup_desktop_write` | The claim, then the job |
| `cancel_open_calls` | The job `FOR UPDATE`, then the call rows, then the job status update, then the cursor. It does not lock a claim |
| `save_read_result` and `begin_read` | The job, then the call update, then the cursor when a note is published |
| Owned persist | The job when it is still `running`, otherwise the call row alone, then the effect insert. No claim. No cursor `message` |

`cancel_open_calls` holds the job and then the cursor. `open_cards` holds claims and then waits for the job. Those two do not form a cycle, because cancel does not take the claim. `apply_intent` and `open_cards` use the same claim, job, cursor order.

Postgres interleavings were not run. The ones the implementation runs, in the existing Postgres job that already runs `tests/test_resume_lock_order.py`, are:

- owned persist against `cancel_open_calls`
- owned persist against `apply_intent`
- `open_cards` against `cancel_open_calls`

The assert is that both transactions finish, the effect row is present when the owner matches, the job stays `stopped` after cancel, and a second session does not invoke. SQLite is not that proof.

## State machine

Call states stay the existing set, plus the use of `unknown` from `running` after a recorded exit and a committed handoff. `busy` is a return from `begin_read`, not a stored state.

- `pending` and no owner. One compare-and-swap wins and invokes.
- `running` with an owner, no exit row. `begin_read` returns `busy`.
- `running` with an exit row and a null handoff. One replacement attempt, new owner, new attempt.
- `running` with an exit row and a handoff, for `web`, `web_fetch`, or `sandbox`. `(running, note_unknown) -> unknown`. Job `waiting_person`. No replacement of that call.
- `running` to `result` only through `SAVE_RESULT`, and only for the matching owner and attempt, and only when `result` is null or already that body.
- `cancelled` stays `cancelled`. The effect table holds the body. `apply` is not called.
- There is no edge from `running` back to `pending`.

Job hold:

- A supplement during `pending` or `running` changes intent and keeps the current token and epoch.
- An expired `hold_until` does not clear a token that still has a `running` call, and it does not publish, unless the unobserved park above applies. That park publishes nothing.
- The holder finishes saved siblings before it releases the token. A forbid denies a pending web sibling. The holder does not call the model on a stale intent.
- A new `enqueue_job` inserts another job. The open call stays on the first job.

## Tests the implementation replaces

`test_a_later_drive_runs_a_running_read_again` currently expects a second `kb` invoke and the result `again`. After this design is accepted, that test expects one invoke of a `running` call, the owner result, and no second `gateway.invoke`. The pending recovery test asserts `invoke` count 1.

The behavior file drives `run_job`, `_apply_talk`, and `run_sweep` with a barrier. It expects one invoke while the first call is inside the tool, the owner result, the amended goal, and no second model request until the step is terminal. It includes the pulse tick, the two-call forbid sibling, the cancel late effect, and a spawned child whose `exitcode` is `-9`. The `-9` case expects no invoke from the sweep while `hold_until` is in the future, and no invoke from a later lease retire. The current overlap assertions fail on this base. That is why they stay in `verification/loop-owner` until the implementation.

## Migration and API

Revision `0026` after `0025_confirm_until`. Nullable `owner_token`, `attempt`, `handed_off_at`, `owner_exited_at`, `exitcode`, and `unresolved_at` on `tool_calls`. Table `tool_call_effects`. No backfill. `begin_read` may return `busy`.

The supplement transaction in `contracts.md` lines 262-268 changes in the implementation, not in this branch. While the latest step has a `pending` or `running` call, the transaction still updates the goal, the web constraint, and `intent_version`, and it leaves the hold columns, `exec_epoch`, and `continuation_due` unchanged.

The worker command gains the `default` queue. No new container.

## Tradeoffs

Design A stores the amendment immediately and adds the call columns and the effect table. Design B stores the amendment late and cannot deny the web sibling from the current constraint while the first call is open.

Parent reap needs the consumer of `default` to be a prefork worker. The child shutdown hook misses `SIGKILL`, which this log recorded as `-9`. Broker redelivery and lease retire both fire for a live worker. The unobserved park is the remaining gap when no parent writes an exit row. It leaves the call `running`, shows `unresolved_at`, and does not invoke. A live holder can still save. A person message is required before a new model turn.

Out of this unit: the pulse `except` body, confirm-lease changes, calendar arm, desktop recovery, and the rest of the inventory.
