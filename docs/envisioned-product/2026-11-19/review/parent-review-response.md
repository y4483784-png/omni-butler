# Response to the parent review of `059b643`

This note answers the review of pull request 94 at `059b643c81c351f5716395ade7ab93ed169aad88`. It is not that review, and it is not approval to implement. The same-model Grok reviews that produced the first plan remain same-model reviews.

The normative changes are in `../contracts.md`. `../plan.md` now points at that file and no longer gives the contradictory instructions named below.

## 1. Recovery ownership

The finding is accepted. `resume` said a unique `(task_run_id, seq)` prevented duplicate execution. That unique key prevents a second row. Celery `visibility_timeout` in `celery_app.py` lines 22-28 covers one broker message. A sweep that selects every `running` job publishes a new message, so those two mechanisms do not say which worker owns the job.

The contract copies the compare-and-set shape from `ingestion.py` lines 93-108. It does not copy `recover_stale_ingests`, which marks a stale document failed. Silence is not the reclaim rule. `owner_token`, `owner_epoch`, and `hold_until` live on `task_runs`. The hold interval is the existing `llm_timeout`.

Acceptance for this contract is in `resume`. It includes an active holder plus a sweep, two deliveries, a crash after the tool calls are saved, a crash after arm, and an old holder returning after a new epoch.

## 2. Tool-loop recovery

The finding is accepted. Sending the next model turn from a saved `model_result` can skip calls that were saved and never run, and it can send a history with no tool results.

`tool_calls` is one row per `tool_call_id`. The next model request is legal only from phase `ready_for_model`, after every call has a terminal result. Desktop `unknown` does not call `perform`. `continuation_due` stays set until a later claim wins, so a crash between finish and enqueue is repaired by the sweep.

## 3. Session cursor

The finding is accepted. One integer `seq` cannot order two jobs, and a status or confirm change may add no step.

`session_events.id` is the cursor. The jobs snapshot and `max(id)` are one transaction. Acceptance includes two interleaved jobs and a confirm that adds no step.

## 4. Web policy and amendments

The finding is accepted. The old sentence stored forbid on the job and let the next user message default the world back to allow. That both made a forbid last for the life of the job and let an unrelated message clear it.

The message's own `web` value applies only to the job that message creates or targets. `intent_version` increments on supplement and redirect. Older open cards are cancelled in that transaction. Execution reads the constraint again. Acceptance covers an unrelated allow job, an amending allow message, a redirect that forbids, and a confirm of an old card.

No new product question is opened. This is the settled per-turn rule applied to the job that the turn targets.

## 5. Image retention

The finding is accepted. `Message.content` stays text, and a restart otherwise has no image bytes.

`task_images` plus `BlobStore.put` keeps the bytes for that non-terminal job only. The request check uses decoded bytes and file signatures. Historical chat text does not keep a copy. The bytes are not added to the knowledge base.

## 6. External receipt

The finding is accepted. `finish_confirm` and `note_external_success` each commit. A written claim can exist without a receipt, and `note_external_success` returns 404 when the receipt is absent (`event_inbox.py` lines 180-182).

`record_create_success` does the bookkeeping in one transaction and does not call the provider. The sentence that said `submit_event` returns the existing row is removed. Patch, delete, and RSVP stay unread and out of scope. Mail keeps the same repair rule and still has no transport. The empty-transport test is a scaffold. A lost acceptance with no provider id stays unknown and is not resent.

## Watch, mail, evidence, order

Watch now names the progress samples, including the first persisted output and the start of a tool. `observed_progress_at` is a committed column. One unchanged sweep is a sample, not a failure. The notice is not a stop.

The mail unit is labeled a scaffold. Provider and credential choice stay open.

The original English candidate `plan.md` was missing from the pull request. It is restored from `/tmp/arena-plan/candidate-grok-xhigh/plan.md`. Its sha256 is `30c84e1961e4e161cc4bd9aed6521d46db3c2c5f812cb39e1929cf1f261c7f46`. That file still says to copy `created_at` into `last_progress_at` and to file source `calendar`. It is evidence, not the contract.

On 2026-11-19 this run read cloud-agent status for `bc-89428bea-b017-5ad6-8355-db2428877b5f` as `IDLE`. The earlier record stays true. The planning run could not stop it while it was `RUNNING`, and no kill tool was available then. The parent later reported cancelling it and seeing it finished around 20:00 SGT. This index does not show `isKilled`, so the cancel action itself is the parent's report. Late files from that run are still not the plan.

The landing list already started with `picture`, while the directory README told the operator to start with `talk`. The operator order is now the numbered list, one unit at a time, each from the latest `release`. The parent may merge a reviewed unit without a new user prompt. This pull request does not merge and does not count as that review.

## Follow-up on `a5c2431`

This follow-up is not approval. The two blockers in that review are closed in `../contracts.md`. `../plan.md` no longer states the old single owner claim or the autoincrement cursor.

### Dispatch handoff

The finding is accepted. The previous owner update was used both by the sweep before publish and by the worker before work. The worker then matched zero rows.

`contracts.md` section "Dispatch reservation and execution hold" splits `dispatch_token` from `exec_token`. The sweep reserves and publishes `{task_run_id, dispatch_token, dispatch_epoch, intent_version}`. Only the worker sets `exec_token`, and only when that message still matches. `hold_until` is the executing process. `dispatch_until` is only how long the queued token may be adopted. `continuation_due` is cleared only in the adoption update. It stays true when publish is lost.

The same section has the successful trace and the crash-before-publish trace. A dead executor is reserved again and stays `running`. `talk` adds the columns. `resume` publishes and adopts. `loop` does not publish.

### Session cursor

The finding is accepted. `max(id)` under concurrent transactions can skip a lower id that commits later. One read transaction under read committed is not a snapshot by itself.

`contracts.md` section "Session cursor" assigns `session_events.seq` while holding `session_cursors` until commit. PostgreSQL uses `FOR UPDATE`. SQLite uses `BEGIN IMMEDIATE`. The jobs read holds that same lock, so a status change cannot commit between the job read and the cursor. The delayed-id trace in that section shows T2 blocked until T1 commits seq 100. The client still dedupes `(session_id, seq)`, and two jobs share the session sequence. `resume` owns this table.

### Compact clarifications

`results_ready` schedules one model request. `final_answer` sets the job `completed` and does not. Mixed `result`, `rejected`, and `unknown` stay terminal. This is in "Tool rounds". `loop` implements it.

A supplement clears `exec_token` in the same transaction as the version bump, so the old worker cannot renew. An armed claim becomes `reconcile` and keeps `remote_request_id`. Only an unarmed open claim is `cancelled`. `arm_remote_claim` matches `intent_version` and `exec_epoch` in the update that commits the marker. This is in "Web constraint and intent version". `talk` stores the transition. `loop` puts the match on the arm update.

Decoded size is per file, against `max_upload_mb` and the recorded 32 MiB inline limit. The 48 MiB check is the UTF-8 length of the serialized request body. `purge_state = pending_delete` is committed with the terminal status. `BlobStore.delete` runs after that commit and retries. This is in "Task image bytes". `picture` implements it.

Output batches after the first also set `last_progress_at`, at most one extra commit per five seconds of continued output, plus tool boundaries. A hold renewal does not. The notice is still not a stop. This is in "Watch samples". `watch` implements the notice. `loop` writes the samples.

## Research audit

`sources/research-bots-latest-coverage.md` is preserved. Its newer project names are deferred in `../contracts.md`. They were not source-read, because none of them decides owner compare-and-set, tool-call accounting, the session cursor, or the receipt transaction. Already adopted mechanisms stay the ones in the OSS report. Unverified briefs are not treated as source confirmation. No proactive or auto-send feature is added.
