# Recovery and delivery contracts

`plan.md` is the unit sequence. This file is the contract those units implement. Where an older sentence in `plan.md` disagrees with this file, this file wins. The parent review that asked for these contracts has not approved the revision.

The shapes below use tables and compare-and-set updates that this repository already uses. They do not add a lock service, a second queue, or a silence kill.

## Owner claim

Celery already sets `task_acks_late` and `task_reject_on_worker_lost`. `visibility_timeout` is `max(3600, celery_ingest_time_limit + 300)` in `backend/app/tasks/celery_app.py` lines 22-28. That timeout stops one broker message from being handed to a second worker while the first delivery is still inside the timeout. It does not stop a sweep from publishing a different message for the same job. A unique `(task_run_id, seq)` insert stops a second row. It does not stop the second worker from performing the action before the insert fails.

Document ingest already claims with a compare-and-set on `ingest_task_id` in `backend/app/tasks/ingestion.py` lines 93-108. `recover_stale_ingests` then marks a stale document failed (`backend/app/rag/ingestion.py` lines 39-78). The job worker copies the compare-and-set. It does not copy the failure. A job whose holder is gone stays `running`.

`talk` adds three columns to `task_runs`.

| Column | Meaning |
| --- | --- |
| `owner_token` | Null when no worker holds the job. Otherwise the token of the worker that won the claim. |
| `owner_epoch` | Integer, starting at 0. It increases by one on every successful claim. |
| `hold_until` | Null when no worker holds the job. Otherwise the time through which that claim is valid. |

A worker claims with one update.

```text
UPDATE task_runs
SET owner_token = :token,
    owner_epoch = owner_epoch + 1,
    hold_until = :now + llm_timeout
WHERE id = :id
  AND status = 'running'
  AND (owner_token IS NULL OR hold_until < :now)
```

`llm_timeout` is the existing setting in `backend/app/core/config.py` line 23. Its default is 120 seconds. No new duration is added. Zero rows means this worker does not read tools, does not call the model, and does not write a step.

The holder renews `hold_until` to `:now + llm_timeout` before each model attempt and on a timer of half that value while a call or a tool is still in progress. The renewal matches `owner_token`, `owner_epoch`, and `status = running`. Zero rows means the holder stops and discards any result it has not yet committed. A renewal does not change `last_progress_at`.

Every step write, tool-call update, and session-event insert for that job includes `owner_epoch` and `intent_version` in the `WHERE` clause. Zero rows means the write is discarded. The discarded result is not shown as current.

The sweep in `resume` publishes `run_job` only after this update claims the row. It does not select every `running` row. A row whose `hold_until` is still in the future is left with its current holder.

`waiting_person` and `waiting_resource` are not claimed by the sweep. The only exception is the continuation repair in the tool-round contract.

### Crash cases

| Case | Required outcome |
| --- | --- |
| Active holder and a sweep | The sweep updates zero rows and publishes nothing. |
| Two deliveries of the same job | One update wins. The other gets zero rows and does not execute. |
| Crash after the tool calls are saved and before they execute | The saved calls remain. The next holder executes the calls that have no result. It does not call the model first. |
| Crash after `arm_remote_claim` | `remote_request_id` is already committed (`schedule_confirm.py` lines 413-436). The next holder looks up that marker. It does not arm a second marker and does not send the write again. |
| Old holder returns after a new claim | The old epoch matches no row. The old result is discarded. The new holder continues from the saved calls. |

## Tool rounds

`loop` stores one `task_steps` row per model response and one `tool_calls` row per `tool_call_id` in that response. The step is committed before any tool runs. A failed commit does not invoke.

A step has one phase.

| Phase | When it is set |
| --- | --- |
| `calls_saved` | The assistant payload and every tool call row are committed. No call has run. |
| `awaiting_results` | At least one call has started or is waiting for a person, and at least one call has no terminal result. |
| `ready_for_model` | The response had no tool calls, or every tool call has a terminal result. |

A tool call has one state. `pending`, `running`, and `awaiting_confirm` are not terminal. `result`, `rejected`, `expired`, `cancelled`, and `unknown` are terminal. Each terminal state has a tool-result payload. The next model request is sent only from `ready_for_model`, and that request includes one tool result for every `tool_call_id` in the saved response. A saved response is never sent to the model as if its calls had already been executed.

The holder handles one saved response as follows.

1. Move a `pending` read to `running` with a compare-and-set on that call id, the current epoch, and the current `intent_version`. Then run it. Commit `result` before starting the next call.
2. Do not run a write inside `gateway.invoke`. Insert the existing confirm claim, store its token on the call, and set `awaiting_confirm`. One response may contain several writes. Each write gets its own card. The step stays `awaiting_results` until every card has a terminal result.
3. A crash while a read is `running` leaves that call without a result. The next holder may run that read again. A call that already has `result` is not run again.
4. A write that already has `remote_request_id` is not armed again. The holder looks up. Desktop lookup reads whether `finish_local` stored a finish for that claim token. No finish means `unknown`. The holder does not call `perform`, `desktop.open`, or `desktop.run` for that token.
5. Rejection, expiry, and cancel of a card write that outcome as the tool result. They do not perform the write. Cancel of the job sets the job `stopped`, marks its open calls `cancelled`, and does not send another model request.
6. An unknown write stays unknown after lookup. Lookup does not send, place, open, or run again.

When the last call becomes terminal, the same transaction sets the step to `ready_for_model` and sets `task_runs.continuation_due`. If the job is `waiting_person`, the confirm path then tries `continue_job`, claims the owner row, and enqueues `run_job`. `continuation_due` is cleared only after that claim updates one row.

If the process dies after the claim is finished and before the enqueue, `continuation_due` is still true. The `resume` sweep is the repair. For a `waiting_person` row with `continuation_due`, it applies `continue_job`, then the owner update, then the enqueue. If `continue_job` already happened and the enqueue was lost, the row is `running` with a null or expired holder. The normal owner claim enqueues it. The repair does not create a second confirm card and does not send the write again.

A model response with more than one tool call is the same rule. Partial read results stay stored. Only calls without a terminal result are eligible. The model is not called while any call is `pending`, `running`, or `awaiting_confirm`.

## Session cursor

`TaskStep.seq` orders steps inside one job. It is not a cursor for the session. `resume` adds `session_events`.

| Column | Meaning |
| --- | --- |
| `id` | Integer primary key. This is the only replay cursor. |
| `session_id` | The session that receives the event. |
| `task_run_id` | The job, when the event belongs to a job. |
| `kind` | `message`, `step`, `status`, `confirm`, or `notice`. |
| `subject_id` | The id of the message, step, claim, or job row. |
| `intent_version` | The job version that produced the event. Null for a session message that is not a job. |

Every job status change, tool step, confirm card, and watch notice inserts one event in the same transaction as the change it reports. A status change or a new card inserts an event even when no `task_steps` row is added.

`GET /api/sessions/{id}/jobs` and the read of `max(session_events.id)` for that session run in one transaction. The response includes that max as `cursor`. The client opens `GET /api/sessions/{id}/events?after={cursor}` and applies events with a greater id. An event committed after the snapshot has a greater id. An event included in the snapshot has an id less than or equal to `cursor`.

Reconnect sends the last id the client applied. The server may send an id the client already applied. The client applies each id once. There is no poll interval.

Two jobs in one session are ordered by `session_events.id`, not by either job's `seq`. A client that has applied job A's event still receives a later event for job B.

## Web constraint and intent version

A user message carries `web` of `allow` or `forbid`. The default for a message that does not forbid search is `allow`. The constraint belongs to the work that message targets.

| Relation | Effect on web policy |
| --- | --- |
| `new` | The new job stores this message's `web`. Other jobs keep their stored constraint. |
| `supplement` or `redirect` | The target job stores this message's `web` and increments `intent_version`. Other jobs are unchanged. |
| `cancel` | The named job stops. Its constraint is not copied onto another job. |
| `ask` | No job changes. |

An unrelated message does not clear a forbid on a job it does not target. A later message that does target that job replaces the constraint with that message's own `web`. A forbid is therefore not permanent, and a bystander message does not reset it.

`talk` stores `web_constraint`, `web_constraint_message_id`, and `intent_version` on the job. Open confirm claims store the `intent_version` they were armed under. The same transaction that increments `intent_version` sets those older open claims to `cancelled`.

`loop` checks the stored constraint at execution, not only when the tool list is built. `web_constraint = forbid` denies both `web` and `web_fetch` inside `execution_denial`. A model that still names either tool does not run it. A worker whose `intent_version` is older than the row does not invoke and does not publish its result. A confirm whose stored version is older than the job returns closed and does not write.

## Task image bytes

`picture` accepts image bytes on the chat request and can send them on that in-flight completion. `Message.content` stays text. The bytes are not a `Document`, not an ingest OCR input, and not a memory-ledger summary.

`task_images` stores `task_run_id`, `media_type`, `byte_length`, `sha256`, and `storage_key`. The row is written when the message is bound to a job. A chat image that never binds to a job is dropped after that request. The bytes go through the existing `BlobStore.put` in `backend/app/storage/base.py` lines 14-16, under a key that starts with `task-image/`. That key is never written to `documents.stored_path`.

The request boundary decodes the base64 and inspects the bytes. JPEG must start with `FF D8`, PNG with `89 50 4E 47`, GIF with `GIF87a` or `GIF89a`, and WebP with `RIFF` at the start and `WEBP` at offset 8. A declared type that disagrees with those bytes is rejected. One decoded image larger than `max_upload_mb` is rejected. `max_upload_mb` defaults to 20 in `backend/app/core/config.py` line 128. The sum of the decoded image bytes and the UTF-8 text is rejected when it exceeds 48 MiB, which is the body limit stated in the Vision guide opened for this design. The check uses the decoded size, not the declared type and not the base64 length alone.

The job worker reads those bytes back into `image_url` blocks on every model request, including a request after restart. The browser does not receive the storage key. A session user can read the bytes only for their own non-terminal job. When the job becomes `completed` or `stopped`, the same transaction schedule deletes the blob and the `task_images` row. A chat transcript that is not the current job does not keep a copy.

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

`last_progress_at` changes only for user-visible activity.

- The first persisted output of a model call.
- A tool call saved.
- A tool execution started.
- A tool result saved.
- A confirm card published.

A holder renewal does not change it. A null `last_progress_at` means no sample exists. It is not filled from `created_at`. The job is still visible as `running`.

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
