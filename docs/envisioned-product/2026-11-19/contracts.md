# Recovery and delivery contracts

`plan.md` is the unit sequence. This file is the contract those units implement. Where an older sentence in `plan.md` disagrees with this file, this file wins. The parent review that asked for these contracts has not approved the revision.

The shapes below use compare-and-set updates that this repository already uses. They do not add a second queue or a silence kill. The session cursor uses one row lock, described in that section. It is not a separate lock service.

## Dispatch reservation and execution hold

A queued reservation is not proof that a process is alive. An execution hold is that proof, and only while `hold_until` is still in the future.

Celery already sets `task_acks_late` and `task_reject_on_worker_lost`. `visibility_timeout` is `max(3600, celery_ingest_time_limit + 300)` in `backend/app/tasks/celery_app.py` lines 22-28. That timeout covers one broker message. It does not say which database row a sweep may publish. Document ingest claims with a compare-and-set on `ingest_task_id` in `backend/app/tasks/ingestion.py` lines 93-108. `recover_stale_ingests` then marks a stale document failed (`backend/app/rag/ingestion.py` lines 39-78). This contract copies the compare-and-set. It does not copy the failure. A job with no live holder stays `running`.

`talk` adds these columns to `task_runs`. `resume` is the unit that publishes and adopts.

| Column | Meaning |
| --- | --- |
| `dispatch_token` | Set only while a delivery is reserved and not yet adopted. Null when no reservation is outstanding. |
| `dispatch_epoch` | Integer, starting at 0. It increases by one each time a new reservation is stored. |
| `dispatch_until` | The time until which that reservation may be adopted. It is not a heartbeat. |
| `exec_token` | Set only when a worker process has adopted a reservation. Null means no process holds the job. |
| `exec_epoch` | Integer, starting at 0. It increases by one on adoption. |
| `hold_until` | Renewed only by the executing process. A future value is the proof that process is alive. |
| `continuation_due` | True when a continuation has been committed and no adoption has cleared it. |

`llm_timeout` in `backend/app/core/config.py` line 23 is the reservation lifetime and the hold lifetime. Its default is 120 seconds. No new duration is added. `dispatch_until` and `hold_until` both use that value. They are not interchangeable. A reservation with no `exec_token` does not mean a process is alive.

The sweep never sets `exec_token`. The worker never reserves. The broker message is `{task_run_id, dispatch_token, dispatch_epoch, intent_version}`.

The sweep reserves an idle job with one update. This does not clear `continuation_due`.

```text
UPDATE task_runs
SET dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :now + llm_timeout
WHERE id = :id
  AND status = 'running'
  AND exec_token IS NULL
  AND (dispatch_token IS NULL OR dispatch_until < :now)
```

A dead executor is retired and reserved in a different update. The status stays `running`.

```text
UPDATE task_runs
SET exec_token = NULL,
    hold_until = NULL,
    dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :now + llm_timeout
WHERE id = :id
  AND status = 'running'
  AND exec_token IS NOT NULL
  AND hold_until < :now
```

A `waiting_person` row with `continuation_due` becomes `running` and reserved in one update. `continuation_due` stays true.

```text
UPDATE task_runs
SET status = 'running',
    dispatch_token = :token,
    dispatch_epoch = dispatch_epoch + 1,
    dispatch_until = :now + llm_timeout
WHERE id = :id
  AND status = 'waiting_person'
  AND continuation_due = true
  AND exec_token IS NULL
  AND (dispatch_token IS NULL OR dispatch_until < :now)
```

Zero rows means the sweep publishes nothing for that attempt. After a successful update of one row, the sweep publishes the message. A publish error does not change the row. The reservation expires, `continuation_due` is still true if it was true, and a later sweep may reserve a new epoch.

The worker adopts only the token in its message.

```text
UPDATE task_runs
SET exec_token = :worker,
    exec_epoch = exec_epoch + 1,
    hold_until = :now + llm_timeout,
    dispatch_token = NULL,
    dispatch_until = NULL,
    continuation_due = false
WHERE id = :id
  AND status = 'running'
  AND dispatch_token = :token
  AND dispatch_epoch = :epoch
  AND intent_version = :intent
  AND dispatch_until >= :now
  AND exec_token IS NULL
```

Zero rows means this delivery does no work. `continuation_due` is cleared only by this adoption update. It is not cleared when the sweep reserves, and it is not cleared when publish returns. If publish is lost, adoption never happens, so `continuation_due` remains true and the expired reservation can be replaced.

The executing worker renews only its own hold. Adoption sets the first `hold_until`. The worker renews again before each model or tool attempt, and while a call is in progress it renews every half of `llm_timeout`. With the default of 120 seconds, that cadence is 60 seconds. It is not a new duration and it is not a stop. A renewal does not change `last_progress_at`.

```text
UPDATE task_runs
SET hold_until = :now + llm_timeout
WHERE id = :id
  AND status = 'running'
  AND exec_token = :worker
  AND exec_epoch = :epoch
  AND intent_version = :intent
```

Zero rows means this process no longer holds the job. It discards uncommitted output and does not renew again. The sweep retires an executor only when `status = 'running' AND exec_token IS NOT NULL AND hold_until < :now`. A row that has left `running` is not retired by that update, so the leave itself must drop the hold.

Leaving `running` is one update. Returning from the worker function is not a release. `loop` uses this update for `waiting_person`, `completed`, and `stopped`. The same statement clears the hold and increments `exec_epoch`, so a delayed worker's old epoch matches nothing.

```text
UPDATE task_runs
SET status = :next,
    exec_token = NULL,
    hold_until = NULL,
    dispatch_token = NULL,
    dispatch_until = NULL,
    exec_epoch = exec_epoch + 1
WHERE id = :id
  AND status = 'running'
  AND exec_token = :worker
  AND exec_epoch = :epoch
  AND intent_version = :intent
```

`:next` is `waiting_person`, `completed`, or `stopped`. The transaction sets `continuation_due` before this statement. Opening cards while any call remains `awaiting_confirm` sets it false. A terminal exit sets it false. After commit, renewal matches zero rows because the status is no longer `running`, `exec_token` is null, and `exec_epoch` has changed. A step write matches `exec_token`, `exec_epoch`, `intent_version`, and `status = 'running'`. The delayed worker therefore cannot renew or write.

`wait_for_resource` is defined in `task_run.py` lines 307-308 and is called from `backend/tests/test_task_generation.py`. No production caller enters `waiting_resource`. The sweep does not select that status. `continue_job` may move it to `running`, but nothing in production observes a resource and sets `continuation_due`. This plan does not add that wakeup and does not claim automatic resource recovery. A later caller that does enter `waiting_resource` must use this same leave update, and the transaction that observes the resource must set `continuation_due` while `exec_token` is null. Until that caller exists, a `waiting_resource` row stays where it is.

### Wait trace

1. The adopted worker is `running` and holds `exec_token` W and `exec_epoch` 4.
2. It opens one or more confirm cards. The same transaction sets the unfinished calls to `awaiting_confirm`, sets status to `waiting_person`, sets `continuation_due` false, clears `exec_token` and `hold_until`, and sets `exec_epoch` to 5.
3. The person confirms one card. Sibling calls are still `awaiting_confirm`, so the confirm transaction stores that one tool result and leaves `continuation_due` false. The job stays `waiting_person`.
4. The person confirms the last card. That transaction stores the result, sets the step to `results_ready`, and sets `continuation_due` true. It does not set `exec_token` and it does not send the write again.
5. The sweep matches `waiting_person`, `continuation_due`, and `exec_token IS NULL`. It reserves D1 and then publishes.
6. A worker adopts D1, clears `continuation_due`, and sends the one model request.

If the last confirm commits while the status is still `running`, it sets the tool result and `continuation_due`, and it does not clear `exec_token`. The worker locks the row, sees every call terminal, does not enter `waiting_person`, clears `continuation_due`, and sends the model itself. The sweep sees a live `hold_until` and publishes nothing. If that worker crashes before sending, `hold_until` expires, the running retire reserves a new token, and the next adoption sees `results_ready`. A delayed renewal with epoch 4 matches zero rows after a leave. It also matches zero rows while a newer `exec_epoch` holds the job.

### Traces

Successful delivery:

1. The row is `running`, `exec_token` is null, and `dispatch_token` is null.
2. The sweep stores `dispatch_token` D1 and `dispatch_epoch` 1. `exec_token` stays null.
3. The sweep publishes `{id, D1, 1, intent_version}`.
4. The worker adoption matches D1 and epoch 1, sets `exec_token`, clears the reservation, and clears `continuation_due` if it was set.
5. The worker renews `hold_until` and runs the saved step.
6. A later sweep sees `exec_token` set and `hold_until` in the future. It publishes nothing.

Crash after reserve and before publish:

1. The sweep stores D1 and epoch 1, then dies before publish. No broker message exists. `exec_token` is null, so no process is alive. `continuation_due` is unchanged.
2. After `dispatch_until`, the sweep stores D2 and epoch 2 and publishes that message.
3. The worker adopts D2 and runs.
4. A late message for D1 matches zero rows and does not run.

Other required cases:

| Case | Required outcome |
| --- | --- |
| Executing holder and a sweep | `hold_until` is in the future, so both reserve updates match zero rows. Nothing is published. |
| Two workers receive the same message | One adoption matches. The other sees `exec_token` already set and does nothing. |
| Delayed message after a newer reservation | The old `dispatch_epoch` matches zero rows. |
| Broker accepted the message but the publisher crashed before seeing the ack | The reservation is still unexpired, so the sweep does not publish a second token. The worker adopts the original message. |
| Crash after tool calls are saved and before they run | The next adopted worker runs calls with no terminal result. It does not call the model first. |
| Crash after `arm_remote_claim` | `remote_request_id` is already committed (`schedule_confirm.py` lines 413-436). The next holder looks up that marker. It does not arm a second marker. |
| Old holder returns after a new adoption | The old `exec_epoch` matches zero rows. The result is discarded. |

A `waiting_person` row is reserved only by the `continuation_due` update above, and only after the leave has cleared `exec_token`. `waiting_resource` is not reserved. There is no production resource-ready event.

## Tool rounds

`loop` stores one `task_steps` row per model response and one `tool_calls` row per `tool_call_id` in that response. The step is committed before any tool runs. A failed commit does not invoke.

A step has one phase.

| Phase | When it is set | What happens next |
| --- | --- | --- |
| `calls_saved` | The assistant payload contains one or more tool calls, and those rows are committed. No call has run. | Execute calls that are not terminal. Do not call the model. |
| `awaiting_results` | At least one call has started or is waiting for a person, and at least one call is not terminal. | Keep waiting or executing. Do not call the model. |
| `results_ready` | Every tool call in this response is terminal. | Send exactly one model request that includes one tool result for every `tool_call_id`. This is not completion. |
| `final_answer` | The assistant payload contains zero tool calls. | Set the job to `completed` in that same transaction. Do not send another model request. Do not set `continuation_due`. |

`final_answer` and `results_ready` are different. A response with no tool calls is the answer, and the job ends. A response whose calls are all terminal schedules one more model request. That request produces a new step. If the new step has tool calls, it starts at `calls_saved`. If it has none, it is `final_answer` and the job completes. The worker does not feed `final_answer` back into the model.

A tool call has one state. `pending`, `running`, and `awaiting_confirm` are not terminal. `result`, `rejected`, `expired`, `cancelled`, and `unknown` are terminal. A response may mix those terminal states. For example, one call can be `result` while another is `rejected` and a third is `unknown`. The step becomes `results_ready` only when every call is terminal. `unknown` is not executed again. A saved response is never sent to the model as if its calls had already been executed.

The holder handles one saved response as follows.

1. Move a `pending` read to `running` with a compare-and-set on that call id, the worker's `exec_epoch`, and the current `intent_version`. Then run it. Commit `result` before starting the next call.
2. Do not run a write inside `gateway.invoke`. Insert the existing confirm claim, store its token on the call, and set `awaiting_confirm`. One response may contain several writes. Each write gets its own card. The step stays `awaiting_results` until every card has a terminal result.
3. A crash while a read is `running` leaves that call without a result. The next holder may run that read again. A call that already has `result` is not run again.
4. A write that already has `remote_request_id` is not armed again. The holder looks up. Desktop lookup reads whether `finish_local` stored a finish for that claim token. No finish means `unknown`. The holder does not call `perform`, `desktop.open`, or `desktop.run` for that token.
5. Rejection, expiry, and cancel of a card write that outcome as the tool result. They do not perform the write. Cancel of the job sets the job `stopped`, marks its open calls `cancelled`, and does not send another model request.
6. An unknown write stays unknown after lookup. Lookup does not send, place, open, or run again.

If the executing worker still holds `exec_token` and the job is `running` when every call is terminal, that same worker sets `results_ready` and sends the one model request. It does not release the hold and it does not set `continuation_due`.

The worker's leave, not a later return, is what drops the hold before the sweep can see `waiting_person`. If the job is `waiting_person` and `exec_token` is null when the last call becomes terminal, that confirm transaction sets `results_ready` and `continuation_due`. It does not reserve or publish. One confirmed card among several does not set `continuation_due` while any call is still `awaiting_confirm`. The sweep then reserves. Adoption clears `continuation_due`. If publish is lost, the flag stays true until a later adoption. The repair does not create a second card and does not send the write again. A `final_answer` step releases the hold and sets `continuation_due` false in the same terminal update.

A model response with more than one tool call is the same rule. Partial read results stay stored. Only calls without a terminal result are eligible. The model is not called while any call is `pending`, `running`, or `awaiting_confirm`.

## Session cursor

`task_steps.seq` orders steps inside one job. It is not a cursor for the session. An integer primary key does not order commits. One transaction can allocate id 100 and stay uncommitted while another allocates and commits id 101. A snapshot that reads 101 and then subscribes after 101 never sees 100 when the first transaction commits. `resume` therefore does not use `max(session_events.id)` as the cursor.

`resume` adds `session_cursors`, one row per session, with `published_seq` starting at 0. It also adds `session_events.seq`. The publication transaction is the serialization boundary.

On PostgreSQL the transaction is:

```text
BEGIN
SELECT published_seq FROM session_cursors WHERE session_id = :session FOR UPDATE
INSERT session_events (..., seq) VALUES (..., published_seq + 1)
UPDATE session_cursors SET published_seq = published_seq + 1
COMMIT
```

`FOR UPDATE` is held until commit. The next publisher for that session cannot allocate the next seq until this commit. On SQLite, `FOR UPDATE` is not the lock. The publication transaction starts with `BEGIN IMMEDIATE`, which takes the writer lock until commit. Either engine blocks the next publication of the same session. A lower seq cannot commit after a higher seq of that session.

The status change, the tool step, the confirm card, or the watch notice is written in that same transaction, before commit. A status or card change inserts an event even when no `task_steps` row is added. The event `seq` is the only replay cursor. Kinds remain `message`, `step`, `status`, `confirm`, and `notice`.

The jobs snapshot uses the same lock for its whole read.

```text
BEGIN
lock session_cursors for that session, as above
read published_seq
read the job rows and open claims
COMMIT
```

A publisher cannot commit during that read, so the job rows and `published_seq` include the same commits. The response field `cursor` is `published_seq`. The client opens `GET /api/sessions/{id}/events?after={cursor}` and the server returns `seq > cursor` ordered by `seq`. Reconnect sends the last seq the client applied and scans the same way. If the client sees a gap, it drops the stream and loads the snapshot again. The lock protocol does not produce a committed gap. The gap check is there so a skipped insert cannot hide forever.

The client applies each `(session_id, seq)` once. Two jobs share that session sequence. A client that has applied job A's seq still receives a later seq for job B.

The delayed-id trace under this protocol:

1. T1 locks the cursor, allocates seq 100, and has not committed.
2. T2 blocks on the same session lock. It cannot allocate 101.
3. T1 commits. A snapshot that starts after that commit can return cursor 100 and the status T1 wrote.
4. T2 then allocates 101 and commits.
5. A subscriber whose cursor is 100 receives 101. Seq 100 is not delivered after 101, because it was committed first and was already included in any snapshot that could see 101.

## Web constraint and intent version

A user message carries `web` of `allow` or `forbid`. The default for a message that does not forbid search is `allow`. The constraint belongs to the work that message targets.

| Relation | Effect on web policy |
| --- | --- |
| `new` | The new job stores this message's `web`. Other jobs keep their stored constraint. |
| `supplement` or `redirect` | The target job stores this message's `web` and increments `intent_version`. Other jobs are unchanged. |
| `cancel` | The named job stops. Its constraint is not copied onto another job. |
| `ask` | No job changes. |

An unrelated message does not clear a forbid on a job it does not target. A later message that does target that job replaces the constraint with that message's own `web`. A forbid is therefore not permanent, and a bystander message does not reset it.

`talk` stores `web_constraint`, `web_constraint_message_id`, and `intent_version` on the job. Each claim stores the `intent_version` it was created under.

The supplement or redirect transaction does all of the following before it commits.

1. Increment `intent_version`, store the new `web` value, clear `exec_token`, `hold_until`, `dispatch_token`, and `dispatch_until`, and increment `exec_epoch`. The old worker's renewal and writes then match zero rows.
2. A claim whose `remote_request_id` is already non-empty keeps status `unknown` and keeps that marker. `unknown` is the armed state already written by `arm_remote_claim` (`schedule_confirm.py` lines 413-436). The claim's `intent_version` stays at the version that armed it. Mail binding rejects any other status (`event_inbox.py` lines 731-734). Errand and mail queries list `open`, `unknown`, and `written` (`errands.py` line 482, `mail_entry.py` line 283). This plan does not add a `reconcile` status. Later lookup may record the provider result. The new intent does not arm that token again.
3. A claim that is still `open` and has an empty `remote_request_id` becomes `cancelled`. Its tool call becomes `cancelled`. Nothing was sent.
4. An armed tool call becomes `unknown` as its terminal tool result. That does not send again. If the latest step has no non-terminal call, set `continuation_due`. If that step exists, also set `results_ready`.
5. If the status is `waiting_person`, leave it. `exec_token` is already null, so the `continuation_due` reserve matches. If the status is `waiting_resource`, apply `continue_job` in this same transaction and leave the row `running` with `exec_token` null and `continuation_due` true. The sweep does not select `waiting_resource`, and no production resource-ready event exists, so the person's amendment is the wakeup. It is not automatic resource recovery. If the status is `running`, leave it `running` with the hold cleared so the idle reserve matches.

`arm_remote_claim` is the commit that allows the provider call. Its update matches the claim, an empty `remote_request_id`, the job's current `intent_version`, and the worker's `exec_token` and `exec_epoch`. Zero rows means the provider call is not sent. A read of the version before that update is not the check. `web_constraint = forbid` denies both `web` and `web_fetch` in that same execution update. A confirm whose stored version is older than the job returns closed and does not write. An armed `unknown` claim is not armed again.

## Task image bytes

`picture` accepts image bytes on the chat request and can send them on that in-flight completion. `Message.content` stays text. The bytes are not a `Document`, not an ingest OCR input, and not a memory-ledger summary.

`task_images` stores `task_run_id`, `media_type`, `byte_length`, `sha256`, and `storage_key`. The row is written when the message is bound to a job. A chat image that never binds to a job is dropped after that request. The bytes go through the existing `BlobStore.put` in `backend/app/storage/base.py` lines 14-16, under a key that starts with `task-image/`. That key is never written to `documents.stored_path`.

The request boundary decodes the base64 and inspects the bytes. JPEG must start with `FF D8`, PNG with `89 50 4E 47`, GIF with `GIF87a` or `GIF89a`, and WebP with `RIFF` at the start and `WEBP` at offset 8. A declared type that disagrees with those bytes is rejected. One decoded image is rejected when it is larger than `max_upload_mb` or larger than 32 MiB. `max_upload_mb` defaults to 20 in `backend/app/core/config.py` line 128. 32 MiB is the per-inline-image limit already recorded from the Vision guide in `review/historical-gemini/citations.md`. This revision does not replace that number.

The 48 MiB figure is the Vision guide's limit on the request body, not a limit on the sum of decoded bytes. Base64 expansion, the JSON envelope, and prior messages can make the body larger than the decoded sum. Before the provider call, the worker measures the UTF-8 length of the serialized request body it is about to send. It rejects that call when the length is greater than 48 MiB. It does not invent a different provider limit, and it does not treat the decoded sum as that measurement.

The job worker reads those bytes back into `image_url` blocks on every model request, including a request after restart. The browser does not receive the storage key. A session user can read the bytes only for their own non-terminal job.

Blob deletion does not roll back with SQL. The terminal transaction commits the status and the image states together. It does not call `BlobStore.delete`. A `ready` row becomes `pending_delete`. A `staged` row becomes `tombstone`. After that commit, cleanup deletes the blob. It then deletes a `pending_delete` row. It keeps a `tombstone` row when the blob is absent and after the blob is deleted. A later put under that key is deleted on a later sweep. If the blob delete fails, the row stays and a later sweep retries. A crash after the terminal commit and before the blob delete leaves the row, so the retry still runs. A chat transcript that is not the current job does not keep a copy.

If the job is still running and the blob is gone, the worker records that the image is unavailable. It does not call `ocr_image_bytes` and it does not invent a summary.

## External receipt

`finish_confirm` commits by itself (`schedule_confirm.py` lines 462-469). `note_external_success` also commits by itself, and it returns 404 when the receipt row is missing (`event_inbox.py` lines 180-182). Three separate calls can leave a written claim with no receipt, or a receipt that is not `external_success`. A second confirm that only sees `written` must not treat that gap as finished.

`calendar` adds one function, `record_create_success`. It does not call `submit_event`. `submit_event` routes an act and can cancel open claims. The old sentence that said `submit_event` returns the existing row is withdrawn.

`record_create_success` locks the claim and then, in one transaction:

1. Requires the armed `remote_request_id` and a non-empty provider event id already stored on the local calendar row.
2. Sets the claim to `written` if it is not already.
3. Inserts one `EventReceipt` with source `internal`, delivery id `cal:{claim token}`, `claim_token` set to that token, and the event id in the body. The unique key is the existing `(user_id, source, delivery_id)`.
4. Sets that receipt to `external_success` and sets `external_request_id` to the armed request id, which is the same match `_written_claim` already requires (`event_inbox.py` lines 562-580).

The provider event id is not written into `remote_request_id`. The provider call stays outside this transaction. A crash before the transaction leaves the claim `unknown` or armed. Repair calls `record_create_success` after the existing lookup. It does not POST again. A crash inside the transaction rolls back, and the same repair runs. A written claim with no `external_success` receipt is not shown as complete. The repair files the missing receipt from the stored event id.

Patch, delete, and RSVP are not covered. `calendar_google.py` lines 1180-1201 show that patch arms an event id. Delete and RSVP were not read. A create receipt does not make those writes safe.

Mail uses the same split. The provider call stays outside the bookkeeping transaction. On acceptance with a provider id, one transaction replaces the stored id with that provider id, inserts one `MailAcceptance`, finishes the claim, and seals. If the transport accepts the message and no provider id is stored, the claim stays `unknown`. Nothing looks up a message id that was never stored, and `send` is not called again. If the provider id is stored and the acceptance row is missing, repair inserts the acceptance and does not send. The mail unit still has no chosen transport. Its automated test is the empty-transport seam, not a real mailbox.

## Watch samples

`last_progress_at` changes for persisted output and tool progress. It does not change on a hold renewal.

During a model call, the worker commits an output batch and sets `last_progress_at` to that commit time. It commits the first output it has received, it commits again when newer output has arrived and at least five seconds have passed since the previous progress commit, and it commits again when the call ends. It does not write a row per token. The five seconds are only the gap between those output commits. They are not a stall timeout and they do not stop the job. A tool call saved, a tool execution started, a tool result saved, and a confirm card published each set `last_progress_at` immediately.

A null `last_progress_at` means no sample exists. It is not filled from `created_at`. The job is still visible as `running`.

`watch` stores two more timestamps. `observed_progress_at` is the `last_progress_at` value the last committed sweep saw. `notice_progress_at` is the stamp for which a notice was inserted. The sweep locks the row, and in that transaction it inserts one notice only when `last_progress_at` is non-null, equals `observed_progress_at`, and `notice_progress_at` is different. It then sets `observed_progress_at` to the current `last_progress_at`, including null. A restart or a second sweep delivery sees those committed columns.

The first sweep that sees a new stamp does not notify. The next sweep with that same stamp notifies once. A third sweep does not. One 60-second sweep is not a failure. The notice says there is no newer sample. The status stays `running`. `waiting_person` and `waiting_resource` get no notice and do not change status.

## Research coverage

`sources/research-bots-latest-coverage.md` is an audit of teammate briefs. It is not source confirmation and it is not an implementation request. The mechanisms already used stay the ones in `sources/oss-issue-references.md`. SuperAgent's dispatch split, Hermes tool-loop and activity timing, OpenCode's separation of a new sentence from an interrupt, OpenMausBot device revoke, DS-Bot's provider acceptance, and OpenClaw's one confirm are already the references for this plan. Their function bodies were not re-opened in this revision.

The newer names in that audit do not close the owner, tool-round, cursor, or receipt contracts. They stay deferred.

| Name | Why it stays deferred |
| --- | --- |
| Linger | Signal only. The reported license is a modified Apache-2.0. Proactive mail and calendar alerts are outside this product. |
| Nod | README and release notes only. Default-off auto-prepare is a proactive feature this product does not add. Its reported sleep-stops-work behavior is the opposite of disconnect-does-not-cancel. |
| Proton Lumo | Blog and repo announcement only. GPL-3.0. Per-schedule auto, ask, and forbid is not one of the settled 16 issues. |
| OpenBot | Release notes only. Goal-reached routine shutdown was not read in source, and it must not become a reason to stop an unrelated job. |
| OpenCode Browse | A second logged-in browser reference. The browser dependency is still an open gate. Session sharing is not adopted. |
| Browser Use 1.0 | The audit reports a cloud default and a local `Browser('chromium')` switch from a product note. That note was not re-read here. The existing candidate stays unselected. |
| pi-durable | Package README only, and the audit calls the API unstable. Retry-on-death would replay writes. This plan looks up an armed write instead. |
| Sentient OS | Signal and product page. AGPL-3.0. Overnight digest is a proactive feature this product does not add. |

No new market crawl was done for this revision. License limits are reasons not to copy code. They are not a claim that the named project cannot be read later under its license.
