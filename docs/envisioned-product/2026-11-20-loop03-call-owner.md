# Call owner for a running tool

This is a design for parent review. It is not a fix. No production path changes in this branch. The inventory in `docs/envisioned-product/2026-11-20-residual-closure.md` stays open.

Base `19867c51ac4e29c1ca16174668925133e4d76f9f`. Branch `cursor/loop03-call-owner-design-ba91`.

LOOP-03 and TALK-04 are one unit. A supplement clears the job hold while a tool call is `running`, the sweep publishes another `run_job`, and `begin_read` invokes that call again. An expired hold does the same thing with no supplement. Closing only the `continuation_due` assignment leaves the overlap in place.

## What the reproduction ran

Command, from `backend`:

```text
python3 ../verification/loop-owner/reproduce_overlap.py
```

Exit code 1. Log `verification/loop-owner/reproduction-log.txt`, started `2026-11-20T15:28:38Z`. Database `sqlite:////tmp/loop03-owner-repro.db`. `llm_timeout` was 3600 so the hold pulse did not fire during the run. The tool is `kb` through `app.agents.harness.gateway.invoke`. The body blocks on a barrier. No provider, mailbox, or purchase is called.

The worker is `run_job`. The supplement is `chat._apply_talk` with relation `supplement`, which calls `apply_intent`. The publisher is `watchdog.run_sweep`.

| Case | Invokes while the first call is inside the tool | Stored result |
| --- | --- | --- |
| Second `run_job` with the same dispatch arguments | 1 | `answer-1` |
| Two threads call `run_job` with those same arguments | 1 | `answer-1` |
| Supplement, then sweep | 2 | `answer-2` |
| Supplement, force `continuation_due` false, then sweep | 2 | `answer-2` |
| Set `hold_until` to 2000-01-01, then sweep | 2 | `answer-2` |
| `task_run.stop` during the tool | 1 | null, state `cancelled` |
| `enqueue_job` for a second goal during the tool | 1 on the first job | `answer-1` on the first job |

On the supplement path the job goal becomes `supplement plus more`, `intent_version` moves from 1 to 2, `exec_token` becomes null, and `continuation_due` becomes true while the call is still `running`. The sweep then completes the job. The notes are `已开始：supplement`, `answer-2`, and `done`. The model request after the second invoke sees the amended goal. `answer-1` is not stored.

The forced-false case is the same overlap with `continuation_due` false and `exec_token` null before the sweep. The idle reserve does not read `continuation_due`.

`pytest tests/test_loop_job.py::test_a_crash_after_save_leaves_the_calls_pending tests/test_loop_job.py::test_a_second_run_job_recovers_a_pending_read` passed, two tests. The recovery test records `invoke` as the first action and records one model request (`backend/tests/test_loop_job.py` lines 624-625). It does not assert an invoke count. The same-token rows in the log are the executed count of one.

SQLite showed this logical overlap because the first worker was blocked inside the tool with no open transaction. It does not prove a lock-order race. Postgres was not started.

## Where the second invoke comes from

`begin_read` returns `invoke` for `CallState.RUNNING` with no compare-and-swap.

```362:365:backend/app/services/loop_round.py
    if call_row.state == CallState.RUNNING.value:
        if not _commit_job(db, job):
            return "stale"
        return "invoke"
```

`_cas_running` matches only `pending`.

```1191:1199:backend/app/services/loop_round.py
def _cas_running(db, call_row, hold: Hold, *, require_allow: bool) -> int:
    web = "allow" if require_allow else None
    result = db.execute(
        update(ToolCall)
        .where(ToolCall.id == call_row.id)
        .where(ToolCall.state == CallState.PENDING.value)
        .where(ToolCall.intent_version == int(hold.intent_version))
        .where(exists().where(_holder_match(hold, web=web)))
        .values(state=CallState.RUNNING.value)
```

`_one_pass` calls `gateway.invoke` after that return.

```617:628:backend/app/tasks/job.py
        outcome = begin_read(db, hold=hold, tool_call_id=row.tool_call_id)
        if outcome == "stale":
            return "leave"
        if outcome == "denied":
            fresh = db.get(TaskStep, step.id)
            db.refresh(fresh)
            if fresh.phase == Phase.RESULTS_READY.value:
                return "model"
            return "again"
        db.commit()
        try:
            result = _invoke(db, job, row)
```

Adopt refuses a second worker only while `exec_token` is set.

```81:97:backend/app/services/resume.py
_ADOPT = """
UPDATE task_runs
SET exec_token = :worker,
    exec_epoch = exec_epoch + 1,
    hold_until = :until,
    dispatch_token = NULL,
    dispatch_until = NULL,
    continuation_due = false
WHERE id = :id
  AND kind = 'job'
  AND status = 'running'
  AND dispatch_token = :token
  AND dispatch_epoch = :epoch
  AND intent_version = :intent
  AND dispatch_until >= :now
  AND exec_token IS NULL
"""
```

`run_job` returns before `_drive` when adopt matches zero rows (`backend/app/tasks/job.py` lines 166-176). Adopt also requires the dispatch token, epoch, intent, and window (`backend/app/services/resume.py` lines 89-96), and a successful adopt clears `dispatch_token` (line 87). In the same-token log the first worker has `exec_token` set and `dispatch_token` null before the second `run_job` runs. That second call matches zero rows for both reasons. The concurrent pair used the same arguments and also invoked once.

`apply_intent` clears the fence on every open job.

```548:559:backend/app/services/task_run.py
    if row.status == "waiting_resource":
        _apply_event(db, row, "continue_job")
    row.intent_version = int(row.intent_version or 1) + 1
    row.web_constraint = _stored_web(web)
    row.web_constraint_message_id = web_constraint_message_id
    row.exec_token = None
    row.hold_until = None
    row.dispatch_token = None
    row.dispatch_until = None
    row.exec_epoch = int(row.exec_epoch or 0) + 1
    row.goal = f"{row.goal or ''}{text or ''}"
    row.continuation_due = True
```

`_apply_talk` is the chat caller (`backend/app/api/chat.py` lines 396-404).

The idle reserve matches a running job with a null token. It does not mention `continuation_due`.

```41:51:backend/app/services/resume.py
_RESERVE_IDLE = """
UPDATE task_runs
SET dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :until
WHERE id = :id
  AND kind = 'job'
  AND status = 'running'
  AND exec_token IS NULL
  AND (dispatch_token IS NULL OR dispatch_until < :now)
"""
```

The holder retire matches a running job whose `hold_until` is in the past, then clears the token and publishes.

```53:65:backend/app/services/resume.py
_RETIRE_HOLDER = """
UPDATE task_runs
SET exec_token = NULL,
    hold_until = NULL,
    dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :until
WHERE id = :id
  AND kind = 'job'
  AND status = 'running'
  AND exec_token IS NOT NULL
  AND hold_until < :now
"""
```

`run_sweep` calls `dispatch_task` for each open job (`backend/app/tasks/watchdog.py` lines 103-109). `dispatch_task` tries idle reserve, then retire, then the waiting reserve.

The inventory's close for TALK-04 says a non-terminal call should leave `continuation_due` false. That sentence is not sufficient. The forced-false case still invoked twice. `contracts.md` lines 262-268 already says the supplement clears `exec_token`, and that `continuation_due` is set when the latest step has no non-terminal call. The code sets the flag on every open job. The publisher for a running job uses the null token.

A lost job hold also drops the result the first worker already has. `save_read_result` loads the job with `_lock_holder`, which requires the same `exec_token`, `exec_epoch`, and `intent_version` (`backend/app/services/loop_round.py` lines 393-396 and 1045-1057). The cancel case is the executed form of that drop. One invoke, then `task_run.stop`, and the stored result stays null. `_one_pass` also returns `leave` without saving when the pulse is already lost (`backend/app/tasks/job.py` lines 630-634).

## Tool classes

`claim_call` is true for `mail.send`, `errand.place`, `desktop.open`, `desktop.run`, and calendar write actions (`backend/app/services/loop_round.py` lines 40-56 and 261-267). `begin_read` returns `stale` for those rows and does not invoke. They become confirm cards in `open_cards`.

The invoke path is the other tools. The registry marks `kb`, `web`, `web_fetch`, `sandbox`, and `calendar` with `effect="read"`. `web` still sends a search request. `sandbox` still runs code. Those labels are not a replay license.

This reproduction executed the invoke path with `kb`. It did not execute a second search POST, a second mail send, or a second calendar write. The implementation must not send an armed write through the read path that retries a call.

## Two designs

Both designs keep one current execution of a call. Both refuse to treat lease expiry, a missed heartbeat, a cleared token, or elapsed time as proof that the old process has stopped. Both leave a pending call recoverable by one compare-and-swap. Both keep the lock order claim, then job, then cursor.

### Design A. The call row owns execution

Add `tool_calls.owner_token`, nullable. The pending-to-running update sets it to a new value and returns that value only to the worker that won the update. `begin_read` on a `running` row returns `busy` and does not invoke. The winner invokes once. A later `run_job` does not have the token.

`save_read_result` for that winner updates the row where `owner_token` matches. It does not require the job's current `exec_token` or `intent_version`. The goal is not written back. `_one_pass` calls that persist after `gateway.invoke` returns, including when `pulse.lost()` is already set. The lost check at `backend/app/tasks/job.py` lines 630-634 currently returns `leave` before the save. That order has to change, or a supplement that breaks the next renew drops the result the tool already returned. The reproduction could not show that drop. `llm_timeout` was 3600 and the barrier was 20 seconds, so the pulse, which waits half of `llm_timeout` (`backend/app/tasks/job.py` line 260), did not tick. The cancel case is the executed drop, and it goes through the hold check rather than through the pulse.

`apply_intent`, while any call on the latest step is `pending` or `running`, still appends the goal, stores `web`, and increments `intent_version`. It does not clear `exec_token` or `hold_until`, it does not clear the dispatch columns, it does not increment `exec_epoch`, and it does not set `continuation_due`. Incrementing `exec_epoch` would make `renew_hold` miss at `backend/app/services/task_run.py` lines 609-610 on the next pulse. With no `pending` or `running` call, it keeps today's transaction, including the epoch increment.

`renew_hold` keeps matching `exec_token` and `exec_epoch`. While that token is still the holder and the latest step has a `pending` or `running` call, the renew does not also require `intent_version`. A supplement then does not kill the heartbeat of the worker that is inside the tool. The model boundary still requires the job intent. A worker whose intent is stale does not call the model.

A step can hold several calls (`backend/app/services/loop_round.py` lines 323-337). `_one_pass` invokes one, then comes back for the next (`backend/app/tasks/job.py` line 644). The compare-and-swap for a call that is still `pending` matches `pending`, the holder token, and the holder epoch. It does not require `call.intent_version` to equal the job's current `intent_version`. The call keeps the version it was saved with. The web check still reads the job's current `web_constraint`, so a forbid denies the next search. Releasing `exec_token` waits until every call on that step is terminal. Releasing when the in-flight call becomes `result` and a sibling is still `pending` would let the next driver adopt the new job version and then match zero rows in today's compare-and-swap (`backend/app/services/loop_round.py` lines 1184 and 1197).

`_RESERVE_IDLE` and `_RETIRE_HOLDER` gain `AND NOT EXISTS` a `running` tool call for that job. A pending-only job can still be retired after `hold_until`, which is how a crash before the compare-and-swap recovers. The new holder claims those pending rows with the rule above. The pulse exception handler stays as it is. It rolls back and continues, and it does not mark the hold lost (`backend/app/tasks/job.py` lines 273-276).

When every call on the step is terminal, the worker that no longer matches the job intent clears `exec_token` only where that token is still its own, and sets `continuation_due` when the intent has moved. It does not call the model. The sweep publishes one driver. That driver sees the stored results and the new goal.

### Design B. The job hold owns the call and the next model turn

No new call column. While a call is `running`, `apply_intent` appends the supplement to a new `pending_amendment` field and changes nothing else. The same worker saves the result with today's hold check, then copies the amendment onto the goal, bumps `intent_version`, and calls the model itself. Reserve and retire also refuse while a call is `running`.

Readers of `goal`, `web_constraint`, and `intent_version` keep the pre-amendment values for the whole call. The person has already sent the amendment. The job row is doing two jobs, execution and intent, so those readers stay coupled to the tool process.

## Chosen design

Design A. The call row is the only writer of "this call is in flight." The job row is the only writer of intent. A supplement can change intent while the call runs, and a second driver still cannot enter `gateway.invoke`.

Design B avoids a column by hiding the amendment until the same process finishes the tool. That couples every intent reader to the tool process. The reproduction already shows the goal text changing immediately. Design A keeps that and adds the owner the save path lacks.

Resetting `running` to `pending` on an expired hold is not a candidate. It is the shortcut the reproduction is here to reject.

## State machine

Call states stay the existing set. `busy` is a return from `begin_read`, not a stored state. `owner_token` is null unless the row has entered `running` through the compare-and-swap.

- `pending` and no owner. The current job holder may run the compare-and-swap. One update wins and invokes. The loser gets `stale` and does not invoke.
- `running` with an owner. Every `begin_read` returns `busy`. The process that won the compare-and-swap is the only process allowed to call the tool, and it has already passed that update.
- Owner persist from `running` writes `result` and moves the call to `result` when the job was not stopped.
- Explicit `stop` moves a `pending` or `running` call to `cancelled` and stops the job. It does not call the tool.
- A late owner persist after `cancelled` writes the result text and leaves `cancelled` and `stopped`.
- There is no edge from `running` back to `pending`.

Job hold, for this unit:

- A supplement while the latest step has a `pending` or `running` call changes intent and keeps the current `exec_token` and `exec_epoch`.
- An expired `hold_until` during `running` does not clear that token and does not publish.
- An expired `hold_until` with only `pending` calls may retire and publish. The new holder runs the compare-and-swap once, including when the job intent has moved since the call was saved.
- The holder that is inside a step finishes every saved `pending` sibling before it releases the token. It does not call the model on a stale intent.
- A new `enqueue_job` inserts another job. The open call stays on the first job.

`open_cards` still locks claims, then the job, then the cursor through `publish_event`. `apply_intent` already locks claims, then the job, then the cursor. The new read of tool calls happens after the job lock. Persist locks the job, then the call, and publishes last if it publishes. It does not lock a claim. Those orders do not invert claim before job, and they do not lock a call and then wait for a claim.

## What can be known

The owner process proves it finished by storing the result under `owner_token`. Explicit cancel proves the person stopped waiting. It does not prove the tool process has returned. The late result is kept so an effect that already happened is still on the row.

This repository has no signal that the worker process exited. `hold_until`, a missed pulse, a null `exec_token`, and a long duration do not provide that signal. `_owner_gone` already says a missing confirm lease is not proof the caller died (`backend/app/services/loop_round.py` lines 854-861). The job hold should follow the same rule.

A crash after `save_turn` and before the compare-and-swap leaves `pending` and no owner. Recovery invokes once. That path was re-run.

A crash after the compare-and-swap commits and before the owner persist leaves `running`. The tool may or may not have been accepted outside this process. A new driver does not invoke. The job stays `running` until the person cancels or a later process-exit proof exists. That proof is not this unit. There is no distributed exactly-once contract for a search POST, a sandbox run, or an armed provider write.

`llm_timeout` is 120 seconds (`backend/app/core/config.py` line 23). The pulse extends `hold_until` while the token still matches, so a long tool is not on a 120 second kill timer. The expired-hold case is the executed stand-in for a pulse that stopped updating the lease while `run_job` was still inside the tool. The fix does not add a maximum duration.

Chat disconnect on the request stream calls `task_run.stop` for that request's generation (`backend/app/api/chat.py` lines 690-693). This unit does not change that line. The job worker is `run_job` on the queue. The events stream tests were not re-run here.

Armed writes stay on the confirm path. An `unknown` claim is not sent again. This design does not replay them from a `running` row.

## Migration and API

One nullable `owner_token` string on `tool_calls`, revision `0026` after `0025_confirm_until`. No new status, no new table, no route change.

`begin_read` may return `busy`. Callers in `_one_pass` treat `busy` like `leave`.

The supplement transaction in `contracts.md` lines 262-268 changes in the implementation, not in this branch. While the latest step has a `pending` or `running` call, the transaction still updates the goal, the web constraint, and `intent_version`, and it leaves the hold columns, `exec_epoch`, and `continuation_due` unchanged. The sentence that always clears `exec_token` becomes false for that case. With no such call, the current transaction stays.

## Implementation plan

One implementation pull request after this design is accepted. It branches from the release that contains this review, not from a stack on paused pull requests.

1. Land the behavior tests first. They drive `run_job`, `_apply_talk`, and `run_sweep` with a barrier, and they expect one invoke, the owner result, the amended goal, and no second model request while the first call is inside the tool. The same file expects the pending recovery with an invoke count, the same-token delivery, cancel, and a second job. It also expects two saved calls on one step, a supplement during the first, one invoke of each call, and a result stored after `pulse.lost()` is forced. Today the overlap assertions fail. That is why they are not in `backend/tests` on this branch.
2. Add the column, the compare-and-swap, `busy`, the owned persist after invoke returns, the `apply_intent` hold exception, the renew exception for a stale intent, and the two reserve predicates.
3. On Postgres, two sessions interleave persist and `apply_intent`, and two sessions race adopt of one pending call. The assert is one invoke, both transactions commit, and the lock order is claim, then job, then cursor. SQLite is not the proof for that race. The existing Postgres job in `.github/workflows/eval.yml` already runs `tests/test_resume_lock_order.py`. The new test belongs in that job.
4. Extend the loop check so a break that makes `begin_read` return `invoke` for a `running` row fails the workflow. A string match on `continuation_due` is not the proof. `verification/loop/Loop.tla` still has no crash after a saved call. That gap is LOOP-01 and stays out of this unit except for the pending behavior test, which must stay green.

Out of this unit: confirm-lease changes, the pulse `except` body, calendar arm, desktop recovery, and every other residual in the inventory.

## Tradeoffs

The reproduction did not tick the hold pulse and did not save two calls on one step. Those two holes are from the source cited above. The implementation tests have to execute them. The parent is choosing the stuck-call rule. After a crash in the `running` window, the job stays `running` and does not start the tool again. The person can cancel. A later supervisor that can see process exit would be a new unit, and even that supervisor could not prove an external request was accepted once.

Design A stores the amendment immediately and pays one nullable column plus a persist path that ignores the job hold. Design B stores the amendment late and pays no call column. Design A is the recommendation because the call and the intent then have one owner each.

A quiet lease will not publish a second driver. The cost is that a dead worker during `running` is not repaired by the sweep. A dead worker during `pending` still is.

The owned result of a read is stored once. A second search is not started to fill a gap. An armed write is not started from this path at all.
