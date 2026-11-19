# Omni-Butler design from release `d7710d68` to the 2026-11-19 architecture

This file is the plan an implementer follows. The unmodified English candidate is `review/candidate-grok-xhigh/plan.md`, sha256 `30c84e1961e4e161cc4bd9aed6521d46db3c2c5f812cb39e1929cf1f261c7f46`. That file still contains sentences this plan rejected, including the `created_at` copy and receipt source `calendar`. The same-model reviews are `review/review-grok-a/review.md` and `review/review-grok-b/review.md`. They used `grok-4.7-xhigh-fast`. They are not a cross-model check. The Chinese candidate remains in `review/candidate-grok-high/` as evidence. Do not implement from either candidate where this file disagrees.

`contracts.md` is the recovery, cursor, web-policy, image, and receipt contract. It was added after the parent review of `059b643`. Where this file disagrees with `contracts.md`, follow `contracts.md`. That review has not approved implementation.

Corrections applied here, and not left as optional grafts:

- `talk` does not copy `created_at` into `last_progress_at`. A null stamp means no progress sample yet.
- `watch` sends one notice only after it has already seen the same non-null `last_progress_at` on a previous sweep and has not yet notified that stamp. It does not notify on the sweep that first records the stamp.
- `loop` registers `mail.send`, `errand.place`, `desktop.open`, and `desktop.run` as confirm-only tools. `gateway.invoke` does not perform those writes.
- `search` registers `web_fetch` as a second read tool. A forbid omits and denies both `web` and `web_fetch`. The job does not send the Responses tool type `web_search`, because that tool runs on the server for `deepseek-v4-pro` and is ignored on `deepseek-flash`, so the executor cannot refuse it before the request leaves.
- `calendar` files source `internal`, sets `claim_token` to the confirm token, and passes the armed `requestId` to `note_external_success`. The Google event id is stored in the receipt body. The call does not go through `submit_event`. The calendar unit updates the two checkers that currently forbid the string `note_external_success` in `backend/app/api/calendar.py`.
- `desktop` may call `perform`. `perform` returns false unless `kind` is `armed`, so the open callback does not run. The acceptance predicate is the open callback, not "perform was never entered."
- `devices` replaces `PackageProof.__eq__` so two proofs are equal only when the device id matches. The class today treats every proof as equal and rejects attribute writes.

The Chinese plan's rejected choices stay rejected. Do not seal a calendar or errand receipt with the Google event id or the merchant order id. Do not call `submit_event` to record a calendar success. Do not turn off the compose channel when no connector row exists. Do not add a watchdog effect named `report`. Do not leave silence with no notice until a duration is chosen. Mail, calendar, and errand do not depend on each other's behavior. Desktop does depend on devices. The operator still merges the numbered list one unit at a time.

Release `d7710d687d147aeeda502d412454ab8a3829af66` already records a chat turn as a `TaskRun`, stops a request that has not left, keeps confirm claims and the event inbox, files mail locally, places an errand only after a claim, and refuses a web login that asks for a local tool. The person using the product still cannot keep talking while work continues. A second message is blocked in the browser, and `begin_turn` stops the previous open turn. The page shows a resident Stop control. Closing the stream marks that turn stopped. A turn still running twenty minutes after `created_at` is stopped even when it is still producing output. The model never receives tools. Web search is a separate Zhipu call. A chat message cannot carry the original image. A sent mail is a local id. A calendar create can succeed without an `external_success` receipt. An errand page can drop the correlation id and still be stored as placed. One shared desktop token covers every install package, and the package only offers two fixed probes.

After these units, the person can send while a job continues, with no resident Stop control. A clear cancel stops the named job. The job runs its own tool loop, asks for confirmation before a write, and continues the same job after one confirmation. Search and fetch go to DeepSeek, and the answer model stays the chat role. A sentence that forbids search removes the search tool and the executor refuses search. A chat image is sent to Flash. Embeddings, rerank, and ingest image summary stay on the current Zhipu path. Silence produces one check notice and does not stop the job. Closing the page does not cancel. A second window reads the same server rows. A worker restart continues from the saved step and does not repeat an armed write. Mail is called sent only after the transport the operator chose accepts it. A calendar create records success after the event id is in hand. An errand reply carries the correlation id and the merchant order id, and an unknown result is looked up. Each package has its own hashed credential. A local step names the device, the file, and the action, runs once, and returns to the same job.

The Python API and the TypeScript page stay. The existing SQLAlchemy database stays. No new database product and no new process are added. The fourteen merged knives stay the current code. This design changes only the behaviors the 2026-11-19 architecture still requires. PR 93 is not a source and is not a destination.

## Units in landing order

1. **picture**: chat images go to the chat model, usage is stored as returned or unknown, and the example file states the code defaults.
2. **talk**: a chat turn and a background job are different rows. A new message does not stop the job. Text cancel stops the named job. The resident Stop control goes away.
3. **watch**: the sweep uses the latest progress time. It no longer stops a running row because of `created_at`. One notice is sent for an unchanged progress stamp.
4. **loop**: the job runs a model tool loop. A write waits for the existing confirm claim. A job that forbids search cannot call search.
5. **search**: the job's web tool calls DeepSeek Search and Fetch. The chat model does not change. Zhipu web search leaves this path.
6. **mail**: a send is accepted only by the transport the operator configures. Until then the product does not say the mail was sent.
7. **calendar**: a create records `external_success` only after the provider returns an event id. A lost response is looked up and not inserted again.
8. **errand**: the public reply echoes the armed correlation id, and `placed` requires that echo plus a merchant order id.
9. **devices**: each install package enrolls its own token. The server stores the hash and can revoke one device.
10. **resume**: disconnect drops the subscription, a second window reads server rows, and a redelivered worker continues at the saved step.
11. **desktop**: a local step is a card bound to one device, one file, and one action. One confirmation runs it once and continues the same job.

Run this list one unit at a time. Start each unit from the latest `release` after the previous unit has merged. Do not start the next unit from an unmerged head. `resume` also waits until `talk`, `loop`, and `watch` are on `release`. `desktop` also waits until `talk`, `loop`, and `devices` are on `release`. Alembic revisions stay a single line. The next unit takes the revision after the one already on `release`.

The parent may review a unit and merge it without asking the user for a fresh merge prompt. That authorization belongs to the parent. This pull request does not merge, does not deploy, and is not that review. A unit is not started until the parent has accepted the previous one.

## Dependency graph

| Unit | Branches from | Waits for | Files it may touch |
| --- | --- | --- | --- |
| picture | `release` | none | `backend/app/core/llm.py`, `backend/app/core/config.py`, `backend/app/models/models.py`, `backend/alembic/versions/` (next revision), `backend/app/api/chat.py`, `frontend/src/api/client.ts`, `frontend/src/components/ChatWindow.tsx`, `.env.example` |
| talk | `release` | none for behavior. Rebase onto `picture` if both have edited `chat.py` | `backend/app/services/task_run.py`, `backend/app/models/models.py`, `backend/alembic/versions/`, `backend/app/api/chat.py`, `backend/app/agents/dispatch.py`, `backend/app/tasks/celery_app.py`, `backend/app/tasks/job.py` (new module in the existing worker), `frontend/src/components/ChatWindow.tsx`, `frontend/src/services/chatStream.ts`, `frontend/src/store/useStore.ts` |
| watch | `talk` | `talk` | `backend/app/tasks/watchdog.py`, `backend/app/services/task_run.py`, `backend/app/tasks/job.py` |
| loop | `talk` after `picture` is merged | `talk`, `picture` | `backend/app/core/llm.py`, `backend/app/agents/workflow.py`, `backend/app/agents/harness/gateway.py`, `backend/app/agents/harness/execution.py`, `backend/app/agents/tools/registry.py`, `backend/app/agents/harness/constitution.py`, `backend/app/agents/harness/constitution.yaml`, `backend/app/models/models.py`, `backend/alembic/versions/`, `backend/app/tasks/job.py`, `backend/app/services/schedule_confirm.py` |
| search | `loop` | `loop` | `backend/app/services/web_search.py`, `backend/app/agents/tools/web.py`, `backend/app/agents/harness/constitution.py`, `backend/app/agents/harness/constitution.yaml`, `.env.example` (the web-search comment only) |
| mail | `release` | none | `backend/app/services/mail_entry.py`, `backend/app/core/config.py`, `.env.example` |
| calendar | `release` | none | `backend/app/api/calendar.py`, `backend/app/services/calendar_google.py`, `backend/app/services/event_inbox.py`, `verification/errand-place/check.py`, `verification/mail-entry/check.py` |
| errand | `release` | none | `backend/app/services/errands.py`, `services/errand-compose/server.py` |
| devices | `release` | none | `backend/app/agents/harness/desktop_channel.py`, `backend/app/models/models.py`, `backend/alembic/versions/`, `backend/app/api/desktop.py`, `backend/app/core/config.py`, `desktop/main.js` |
| resume | `loop` after `watch` | `talk`, `loop`, `watch` | `backend/app/api/chat.py`, `backend/app/tasks/job.py`, `backend/app/tasks/watchdog.py`, `frontend/src/services/chatStream.ts`, `frontend/src/store/useStore.ts`, `frontend/src/components/FollowupCard.tsx`, `frontend/src/api/client.ts` |
| desktop | `loop` and `devices` | `talk`, `loop`, `devices` | `backend/app/services/local_action.py`, `backend/app/services/schedule_confirm.py`, `backend/app/api/desktop.py`, `desktop/executor.py`, `desktop/preload.js`, `desktop/confirm_local.py`, `frontend/src/components/ChatWindow.tsx` |

The `Branches from` column is the technical dependency. The operator order is the numbered list. Cut every unit from the latest `release` after the previous number has merged, even when the technical dependency is older.

The web gateway's local-tool denial stays in place for every unit. `ServerSurface` remains `WEB` for a logged-in page. A package is recognized only by `PackageProof`.

## picture

### User-visible behavior

A person can attach a JPEG, PNG, GIF, or WebP image to a chat message. The chat model receives the original image with the text. The ingest OCR path is not used for that message. A reply with no usage object is shown and stored as unknown usage, and a missing count is not stored as zero. The example environment file tells an operator the names the code uses when the variables are unset: embedding `embedding-3`, vision `glm-4.6v`.

### Code anchors

- `backend/app/core/llm.py:244-295` refuses tools and reasoning fields, and `_wire_messages` forces every `content` to a string.
- `backend/app/core/llm.py:134-154` has four roles: chat, planner, router, judge. Chat defaults to `deepseek-flash` with thinking on (`backend/app/core/config.py:25-26`).
- `backend/app/core/llm.py:356-376` adds `prompt_tokens or 0` into the process counter.
- `backend/app/api/chat.py:54-59` and `frontend/src/api/client.ts:309-314` send text, session, knowledge-base flags, and an optional regenerate id.
- `backend/app/rag/vision_ocr.py:17-35` is the ingest OCR call. It posts to the vision endpoint with the vision model.
- `.env.example:25` says `EMBEDDING_MODEL=embedding-3-pro`. `.env.example:31` says `VISION_MODEL=glm-4v-flash`. `backend/app/core/config.py:36` defaults to `embedding-3`. `backend/app/core/config.py:39` defaults to `glm-4.6v`.
- The Models and Pricing page opened for this design says `deepseek-flash` supports Vision and `deepseek-v4-pro` does not. The Vision guide opened for this design shows a user `content` array with `{"type":"text","text":...}` and `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}`. Images in assistant messages are rejected by that guide.

### Modules, types, and state

`ChatRequest` gains an optional `images` list. Each item is the raw bytes' base64 and a declared type. The server builds one user message whose `content` is a list of a text block and one `image_url` block per image, using a `data:` URL. The chat role stays `Role.CHAT`. No temperature, top-p, or output-token field is added. Thinking stays the existing `chat_thinking` value, passed as it is today through `extra_body.thinking.type` and `reasoning_effort`.

A new `LlmCall` row records `user_id`, `generation`, `role`, `model`, `prompt_tokens`, and `completion_tokens`. Either token column is null when the response has no such number. The in-memory `_STATS` counter may keep counting calls. The durable row does not coerce a missing usage to zero.

`_wire_messages` keeps stripping `tool_calls`, `reasoning_content`, and `role: tool` on this unit. It passes a content list through only when every block is a text block or an `image_url` block on a user message. Any other shape is refused before the client is created.

### Interfaces

`POST /api/chat` accepts the current body plus `images`, an array of `{ "media_type": string, "data_base64": string }`. `media_type` is one of `image/jpeg`, `image/png`, `image/gif`, or `image/webp`. The server decodes the base64 and checks the signatures in `contracts.md`. A declared type that disagrees with the bytes is rejected. One decoded image is rejected when it exceeds `max_upload_mb` (`backend/app/core/config.py` line 128, default 20) or 32 MiB. The 48 MiB figure is the Vision guide's limit on the serialized request body. Before the provider call, the worker measures that serialized body and rejects it when the UTF-8 length is over 48 MiB. The decoded sum is not that measurement. No new provider limit is introduced.

`complete_text` and `stream_chat` write one `LlmCall` after a response. Fields read from the response are the ones this code already reads: `prompt_tokens` and `completion_tokens` (`backend/app/core/llm.py:356-376` and `459-474`). Other usage names were not on a page opened for this design, so they are not stored.

### Persistence and migration

One Alembic revision creates `llm_calls` and `task_images`. Existing messages stay text. A chat image is not written into `documents` and is not passed to `ocr_image_bytes`. The message row stores the user's text. The decoded bytes are available to the chat completion that is in flight. They are written with `BlobStore.put` under a `task-image/` key only when that message is bound to a job, as `contracts.md` specifies. They are not copied into the long-term memory ledger. A non-terminal job keeps those bytes so `loop` and `resume` can rebuild the `image_url` blocks. A `completed` or `stopped` job marks the image `pending_delete` in that commit. Blob deletion happens after the commit and retries while the row remains, as `contracts.md` specifies. A chat image that never binds to a job is not kept after that request. A historical transcript does not keep a copy.

`.env.example` is edited so the embedding and vision lines say: unset means the code default `embedding-3` and `glm-4.6v`; the previous example names are overrides, not the defaults. `backend/app/core/config.py` defaults stay as they are. `LLM_MODEL_CONCURRENCY_LIMITS` on `.env.example:109` gets the same sentence. No index is rebuilt.

### Concurrency, disconnect, and restart

This unit does not change `begin_turn` or the disconnect stop. An image on a chat turn that never became a job is not resumed. Bytes stored on a job stay available until that job is terminal, including across a process restart. The `LlmCall` row is written only after the provider response is in hand. A dropped stream with no usage leaves the token columns null.

### Confirm, cancel, and unknown results

An image on a chat message is a read of that message. It does not confirm a write, send mail, or open a local tool. Stop still prevents a model call that has not left, by the existing `is_stopped` check. Missing usage is unknown. It is not a failed turn and it is not zero.

### Reference version and what may be copied

The request shape comes from the Vision guide and the Models and Pricing page opened during this design:

- `https://api-docs.deepseek.com/guides/vision`
- `https://api-docs.deepseek.com/quick_start/pricing`

Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` `agent/vision_message_prep.py` is recorded in `oss-issue-references_8ae0.md` as a direct-image versus transcribe split. That file was not re-opened. The behavior copied is the split itself: chat images stay on the chat model, ingest transcription stays on its own client. The Hermes source is not copied. No Files API upload is added. The chat path uses the Chat Completions `image_url` block. The Responses `input_image` shape was opened on 2026-11-19 and is not used. The parent re-opened the Vision guide and the Responses guide. The parent did not re-open the pricing page in this run, so "deepseek-v4-pro has no vision" stays a claim from that page and from the 2026-11-19 plan snapshot, not a fresh parent check.

### Explicit non-goals

Do not change ingest OCR, embeddings, or rerank. Do not add a tool loop here. Do not set a temperature, a top-p, or an output limit. Do not display a computed price. Do not accept an image on an assistant message. Do not treat the example-file names as the production environment.

### Acceptance

Unit assertion: a chat request with one PNG whose bytes start with `89 50 4E 47` builds a user content array that contains an `image_url` data URL, calls the model resolved for `Role.CHAT`, and does not call `ocr_image_bytes`. A payload whose declared type is `image/png` and whose bytes do not match that signature is rejected. A response with `usage is None` inserts an `LlmCall` whose token columns are null. Binding that message to a job stores one `task_images` row. A chat image with no job stores none. After the linked job is `stopped`, the row is `pending_delete` even if the blob delete has not finished. A retry removes the blob and then the row. The message text remains. A failed blob delete does not roll back the terminal status. A restarted job worker can rebuild the `image_url` block from the row while the job is running.

Formal property: `ImageStaysOnChatModel` and `MissingUsageStaysNull`. The model is one request. It does not cover the provider's image safety review, a second user, or a restart during the HTTP call.

Live scenario: attach a PNG in the chat box and send a question about it. Pass predicate: the reply is displayed, the logged model is the chat role's model, and the vision OCR endpoint receives no request for that turn.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

None. Cut from the latest `release` when this is the first unit.

### Risks

`_wire_messages` is also the guard that strips tool fields. A content list must not become a way to smuggle `tool_calls`. The allow-list of block types is the guard. Pydantic currently declares `tabular_max_rows` twice in `Settings` (`config.py:130-133` and `164-167`). This unit does not touch those lines.

### Open points for this unit

None.

## talk

### User-visible behavior

While a job is running, the composer stays available. The resident "停止生成" button is gone. The person can send a supplement, a redirect, a new task, or a cancel in words. A supplement or a redirect does not stop the job. A new task starts another job and leaves the earlier job running. A cancel that names one job stops that job. If more than one job is open and the cancel or redirect does not identify which one, the page asks and changes nothing. Closing the reply stream does not mark the job stopped. The job's progress and result appear in the same session.

### Code anchors

- `backend/app/services/task_run.py:187-219` stops every other open row in the session except a card-action marker. `begin_turn` at `262-279` uses that path. `open_alongside` at `253-259` inserts without stopping others.
- `backend/app/services/task_run.py:15-46` has no edge back to `running`. `_validate_moves` rejects a target of `running` (`56-57`).
- `backend/app/api/chat.py:429` starts the turn. `526-529`, `590-591`, `645-647`, and `699-707` call `task_run.stop` on disconnect or cancel. `751-771` remembers only a completed turn.
- `frontend/src/components/ChatWindow.tsx:47-56` and `140-148` refuse send while `busy` and show the stop button. `frontend/src/services/chatStream.ts:287-293` returns immediately when `busy` is true. `frontend/src/store/useStore.ts:17-25` keeps `busy` in the tab.
- `backend/app/agents/dispatch.py:87-109` records a tool assignment. It does not classify a new message against an existing job.
- `backend/app/core/generation_stop.py:22-35` binds one user and one generation for the request.

### Modules, types, and state

`TaskRun` gains `kind`, either `turn` or `job`. Existing rows migrate to `turn`. `begin_turn` still stops other open `turn` rows in that session, and it skips `kind=job` the same way it skips a card-action marker. A job is inserted with `open_alongside` and `kind=job`.

The five statuses stay. One new event, `continue_job`, is added to the move table. It writes `waiting_person` or `waiting_resource` to `running`. From `running`, `completed`, or `stopped` it rejects. The validator allows a target of `running` only for that event from those two waiting statuses. `finish` on a `completed` row still keeps `completed`. `stop` on a `completed` row still keeps `completed`.

The interaction request binds the `turn` generation. The worker binds the `job` generation. `is_stopped` in the worker therefore reads the job, not the chat turn.

The interaction layer asks `Role.ROUTER` for one JSON object:

- `relation`: `supplement`, `redirect`, `new`, `cancel`, or `ask`
- `target_generation`: an integer or null
- `web`: `allow` or `forbid`
- `ask`: a short question when `relation` is `ask`

That object is this application's schema. It is not a provider field. The spoken reply still uses `Role.CHAT`. If the router call fails, the relation is `ask` and no job is stopped or started.

A supplement appends the user text onto the target job and does not stop it. A redirect appends the text for the next model boundary and does not stop the job. A cancel calls `task_run.stop` on that job only. A new task inserts another job. Each message carries its own `web` value, defaulting to `allow` when that message does not forbid search. `new` stores that value on the new job only. `supplement` and `redirect` run the intent transaction in `contracts.md`. That transaction increments `intent_version`, clears the execution hold and the reservation, sets `continuation_due`, keeps an already armed claim as `reconcile`, and cancels only a claim that was never armed. Other jobs are unchanged. The sweep then reserves a delivery for the new version. The old worker's renewal matches zero rows. An unrelated message does not clear a forbid.

### Interfaces

`POST /api/chat` still streams the interaction reply. The stream ends when that reply is done. It does not stay open for the life of the job. Job progress is written as session messages by the worker.

The worker is a Celery task `app.tasks.job.run_job` on the existing default queue. `backend/app/tasks/celery_app.py` already has that queue. No new container and no new beat entry are added in this unit. The task returns when the job reaches `waiting_person`, `waiting_resource`, `completed`, or `stopped`. Continuing after a wait is a new message enqueued by the confirm path, which this unit only reserves: `continue_job` exists, and no write tool calls it yet.

### Persistence and migration

The revision adds `task_runs.kind`, `web_constraint` (`allow` or `forbid`, default `allow`), `web_constraint_message_id`, `intent_version` (starting at 1), `last_progress_at` (nullable), `dispatch_token`, `dispatch_epoch` (starting at 0), `dispatch_until`, `exec_token`, `exec_epoch` (starting at 0), `hold_until`, and `continuation_due` (default false). Backfill sets `kind=turn` and leaves `last_progress_at` null. Do not copy `created_at` into that column. This unit does not publish or adopt. `resume` does. Until then, an expired reservation is not a stop. This unit does not yet store tool steps. That table arrives with `loop`.

Before `loop` lands, `run_job` inserts the job as `running`, stores the goal on the row, calls no model tool and no search, and returns. The unit passes when a second chat message leaves the previous job `running`.

`void_draft_schedule_cards` (`chat.py:185-188`) still runs for a new interaction turn. It must not cancel a claim whose linked row is a `kind=job` in `waiting_person`. A supplement must not void the job's open card.

### Concurrency, disconnect, and restart

Disconnect and `CancelledError` call `task_run.stop` only for the bound `turn`. They do not receive the job id. Two tabs can both post. Each post is classified. Neither post stops a job unless the classification is `cancel` and the target is that job.

A worker death leaves the job `running` with no Celery acknowledgement. This unit does not recover it. `resume` does. Until `resume` lands, a dead worker's job stays `running` and is not marked stopped by the chat disconnect.

An in-flight model HTTP is not withdrawn. This design does not add a provider cancel call. Whether DeepSeek accepts a cancel was not found in the pages opened here.

### Confirm, cancel, and unknown results

Confirm claims stay on `ScheduleConfirmClaim`. This unit does not consume them. Text cancel stops the job row. It does not delete a calendar event, unsend mail, or refund a payment. A claim already `unknown` or `written` stays that way. A cancel that does not name a single open job asks and stops nothing. There is no resident stop button, so the only user cancel is this classification.

### Reference version and what may be copied

DeepSeek's super-agent split, as named on the 2026-11-19 diagram: the conversation receives and answers, and a separate executor works. That repository was not opened. The local mechanism is the existing `TaskRun` plus `open_alongside`.

OpenCode `79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294` `packages/app/src/session/composer/queue.ts` is recorded in the OSS report as queue versus steer versus a separate interrupt. The file was not re-opened. What may be used is the separation: a new sentence is not an interrupt. The report's default `followUpBehavior: "steer"` is not copied. DS-Bot `5e9b7da8882da3b68d8a0624342708c2150a9156` `ds-bot/src/host/steer.js` is recorded as cancelling a running model call. That is not copied.

Hermes `gateway/session_authority.py` attach and detach are recorded as multiple subscribers. The "last subscriber closes the session" behavior is not copied. This unit does not build the subscriber yet. `resume` does.

### Explicit non-goals

Do not put every new message on one queue. Do not keyword-route the four relations. Do not run the model tool loop in this unit. Do not change the watchdog interval or its stop rule here. Do not add a provider cancel API. Do not fork Hermes.

### Acceptance

Unit assertion: creating a `kind=job` row and then calling `begin_turn` for the same session leaves the job `running`. Stopping the interaction generation leaves the job `running`. A classified cancel of that generation leaves the job `stopped`, and a second open job on the same user stays `running`. A forbid on job A stays forbid when a later message creates job B with allow. A supplement that targets job A with allow changes only job A, increments its `intent_version`, clears `exec_token`, and sets `continuation_due`. An armed claim on the old version becomes `reconcile` and keeps `remote_request_id`. An open claim with an empty `remote_request_id` becomes `cancelled`. The old worker's renewal updates zero rows. A redirect of an allow job to forbid denies a later web execution. Confirming the cancelled unarmed card does not write. Confirming the `reconcile` card does not arm a second write.

Formal property: `JobSurvivesNewTurn`, `JobSurvivesDisconnect`, `CancelStopsNamedJob`, `AmbiguousCancelAsks`. The model is one user, two jobs, and one interaction turn. It does not cover an HTTP call already inside the provider, a second browser, or Celery redelivery.

Live scenario: start a job that reaches `waiting_person`, then send a sentence that the fixture classifier marks as a supplement. Pass predicate: the job status is still `waiting_person`, the new sentence has its own assistant reply, and the composer has no stop button.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Cut from the latest `release` after `picture`. `watch` and `loop` wait for this unit.

### Risks

The router's JSON can name the wrong job. The acceptance fixture pins the classifier. A live sentence that is genuinely ambiguous must ask. `continue_job` widens the old "no edge targets running" rule. The new validator is part of the unit, and the old completed-stays-completed cases stay in the suite.

### Open points for this unit

None.

## watch

### User-visible behavior

A job that is still saving progress is not stopped at twenty minutes after creation. A running job whose progress stamp has not changed receives one notice in the session and keeps running. A second sweep with the same stamp does not send another notice. `waiting_person` and `waiting_resource` are left alone. The job ends `stopped` when the person cancels it or when a tool returns a definite failure the executor already treats as terminal. Silence is not that failure. Waiting is not that failure.

### Code anchors

- `backend/app/tasks/watchdog.py:16-19` sets `CHECK_INTERVAL` to 60 seconds, `ORDINARY_STALL` to 20 minutes, and `STALL_FLOOR` to 60 seconds.
- `backend/app/tasks/watchdog.py:99-119` computes silence as `now - created_at - credit` and stops a `running` row at `ORDINARY_STALL`.
- `backend/app/tasks/watchdog.py:29-35` maps only `running` to `stop`. The other four statuses are `leave`.
- `backend/app/tasks/watchdog.py:122-137` loads every `TaskRun`. `158-164` keeps the credit ledger in the worker process.
- `backend/app/tasks/celery_app.py:50-54` runs `watch-task-runs` on schedule `60`.
- `docs/envisioned-product/review/ev-watch.md` describes this clock and says a stall becomes `stopped` with no notice.

### Modules, types, and state

`decide` stops using `created_at` as the silence clock. It reads `last_progress_at`. The worker updates that stamp on each committed output batch in `contracts.md`, including later batches in the same call, and on a tool save, a tool start, a tool result, and a confirm card. A hold renewal does not move it. A null stamp is not a sample and is not filled from `created_at`. The job remains visible as `running`.

`observed_progress_at` stores the stamp the last committed sweep saw. `notice_progress_at` stores the stamp already notified. The sweep locks the row and follows the transaction in `contracts.md`. It does not call `task_run.stop`. `_validate_effect` in `watchdog.py` lines 37-48 requires `running` to be `stop`. This unit changes that check so `running` is `leave`. It does not add an effect named `report`. One 60-second sweep that sees an unchanged stamp only records `observed_progress_at`. The notice on the following sweep is an inspection message, not a confirmed failure. `waiting_person` and `waiting_resource` stay unchanged.

`notice_progress_at` is a column on `TaskRun`, written in this unit's revision if `talk` has not already added it. The in-process `SweepLedger` is no longer the source of the notice. Sleep credit is not applied.

The beat entry stays `watch-task-runs` with schedule `60`. That number is the existing beat, not a new silence threshold. `ORDINARY_STALL` is removed from the stop rule and is not replaced with another duration.

`load_runs` filters to rows whose status is `running`, `waiting_person`, or `waiting_resource`. It does not load the whole history into the credit map.

A definite tool failure is a job status the executor sets to `stopped` in `loop`. This unit does not invent a failure class. User cancel remains `task_run.stop` from `talk`.

### Interfaces

No new HTTP route. The notice is an assistant message in the job's session: the job has had no new progress since the stored stamp, and it is still running. The message includes the generation. It does not include a stop button.

### Persistence and migration

Add `observed_progress_at` and `notice_progress_at` if they are not on the row. No new table. Both columns are durable, so a restarted sweep sees the stamp it already observed or notified.

### Concurrency, disconnect, and restart

The sweep uses `maintenance_session`. Notice dedupe uses the committed `observed_progress_at` and `notice_progress_at` columns, not a value remembered only inside one process. A null `last_progress_at` produces no notice. The sweep that first records a new stamp does not send the notice. A disconnect of a browser does not enter this sweep. This sweep does not reserve a dispatch token and does not publish `run_job`. Reclaim belongs to `resume`.

### Confirm, cancel, and unknown results

The sweep does not publish a claim, does not consume a claim, and does not treat `unknown` as silence or as success. `waiting_person` and `waiting_resource` stay `leave`.

### Reference version and what may be copied

Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` `gateway/session_stall.py` is recorded in the OSS report as a last-activity check that does not use the turn's start time, and as a notice that does not kill the process. The file was not re-opened. The notice-rather-than-stop outcome is what this unit uses. The report's 300 second default and 60 second activity write are not copied. No new second count is added.

### Explicit non-goals

Do not keep the twenty-minute creation stop. Do not add a stall duration. Do not stop a job for silence. Do not page the whole `TaskRun` history. Do not publish a confirm card from the sweep.

### Acceptance

Unit assertion: a `running` job with `created_at` older than twenty minutes and a newer `last_progress_at` is not stopped. A holder renewal alone does not move `last_progress_at`. A null `last_progress_at` produces no notice. The first sweep that sees a stamp sets `observed_progress_at` and inserts no notice. The next sweep with that stamp inserts one notice. A process restart between those sweeps does not insert a second notice. A `waiting_person` row is not stopped and gets no notice.

Formal property: `CreationAgeDoesNotStop`, `OneNoticePerProgressStamp`, `WaitingLeftAlone`. The model is one job and three sweeps. It does not cover Celery visibility, a provider timeout, or a second user.

Live scenario: a fixture job writes a step, the clock is moved so `created_at` is older than twenty minutes, and the sweep runs twice without a new step. Pass predicate: the status stays `running`, the session contains one notice, and the second sweep does not add another.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Waits for `talk`, which provides `last_progress_at` and the job kind. Branches from the `talk` head.

### Risks

A job that never writes `last_progress_at` gets no notice, because a null stamp is not an unchanged stamp. The worker sets the stamp when it saves a message. Old rows are not backfilled from `created_at`.

### Open points for this unit

The number of minutes that should count as silence is unset. This unit does not need that number: any unchanged stamp is one notice, and the job is not stopped. Choosing a longer threshold later does not block this unit.

## loop

### User-visible behavior

A background job looks at its goal, chooses a tool, reads the result, and continues. The person sees the tool result and the following answer in the session. A write shows a confirm card and does not run until the existing claim is confirmed once. After that confirmation the same job continues. A message that forbids search removes search from the work that message targets, and the executor refuses search again at the call. A later message changes that constraint only when it targets the same work. Ordinary chat turns that do not carry tools still omit previous reasoning, as they do today.

### Code anchors

- `backend/app/core/llm.py:244-256` raises `ToolsRefused` when `tools` is set or a message contains `reasoning_content`, `role == "tool"`, or `tool_calls`.
- `backend/app/agents/workflow.py:411-495` invokes the gateway from a server-built queue. `answer_node` at `582-794` then asks the chat model to write, with tools still refused.
- `backend/app/agents/router.py:82-101` and `323-350` turn router booleans into `kb`, `web`, `calendar`, and `sandbox`. `_decision_to_plan` does not read a forbid-search flag.
- `backend/app/agents/harness/constitution.py:13-17` allows those four tools, all with effect `read`.
- `backend/app/agents/tools/calendar_tool.py:965-972` registers `calendar` as `effect="read"` and the description says it prepares a change for confirmation.
- `backend/app/agents/workflow.py:290-311` admits a calendar step as `RunConfirm`. `330-351` settles `UnknownWrite` by lookup and does not insert.
- `backend/app/agents/harness/execution.py:17-42` denies a stopped generation, a card-action row, and a name absent from `allowed_tools`. `task_id is None` returns without a denial.
- `backend/app/services/schedule_confirm.py:347-436` moves `open` to `unknown` and arms `remote_request_id` before the external call.

### Modules, types, and state

The job worker builds a tool list from the constitution plus four confirm-only names: `mail.send`, `errand.place`, `desktop.open`, and `desktop.run`. Those four return a claim and do not perform the write inside `gateway.invoke`. When `web_constraint` is `forbid`, `web` and `web_fetch` are omitted. `execution_denial` also returns a denial when the job forbids either name, so a model that names one anyway does not execute it.

`stream_chat` and `complete_text` gain a job path that may send `tools`. The messages on that path keep `reasoning_content` and `tool_calls` on assistant messages, and tool results use `role: "tool"` with `tool_call_id` and `content`, matching the Thinking Mode guide opened for this design. That guide says a request that carries `tools` must send previous `reasoning_content` back or the API returns 400, and a request without `tools` does not need it. Ordinary chat turns stay on the no-tools path and still strip reasoning.

The executor role resolves to `settings.chat_model` and `settings.chat_thinking` when `EXECUTOR_MODEL` and `EXECUTOR_THINKING` are unset. No temperature and no output limit are set. `tool_choice` is omitted. The thinking-mode sample that was opened does not set `tool_choice`.

A `task_steps` row stores one model response. Its phase is `calls_saved`, `awaiting_results`, `results_ready`, or `final_answer`. A `tool_calls` row stores one `tool_call_id`, its state, its claim token, and its `intent_version`. `results_ready` schedules one model request. `final_answer` completes the job and does not schedule another request. The worker commits the assistant payload and every tool-call row before `gateway.invoke`. A failed commit does not invoke. `continuation_due` is set when the last waiting call becomes terminal. Adoption clears it. The sweep does not.

Calendar, mail, errand, and desktop writes from the loop return a confirm card through the existing claim helpers. They do not perform the write inside `invoke`. One model response may open more than one card. On confirm, the existing consume-and-arm path runs. The tool result is saved in the same transaction that can set `continuation_due`. That transaction does not publish. `arm_remote_claim` matches the current `intent_version` and `exec_epoch` in the update that commits the marker. Zero rows means the provider call is not sent. If the process dies after `continuation_due` is set and before publish, the flag stays true until adoption. The repair does not arm the write again. A changed fingerprint still mismatches and does not write (`schedule_confirm.py` lines 378-380). An armed desktop call with no `finish_local` result is `unknown` and does not call `perform`. Before every tool runs, the worker reads `web_constraint` and `intent_version` again. A forbid denies `web` and `web_fetch`. An older `intent_version` does not run the tool and does not publish the result. The worker also loads `task_images` for the job and puts those bytes back into the model request.

`assign_turn` remains the interaction layer's current helper and is not the job's tool queue. The job no longer starts from `plan_tools`.

### Interfaces

Tool definitions sent to the model use the function shape in the opened Thinking Mode sample: `type: function`, `function.name`, `function.description`, `function.parameters`. Names are the existing tool names. `web` is absent when the job forbids it. The parameters for `web` are specified in `search`. This unit's `web` tool, until `search` lands, may still call the current `execute_web`. The forbid check does not depend on that swap.

`execution_denial` requires a `task_id` for a job tool. A missing task id denies the job path. The old `task_id is None` allowance stays only for callers that are not the job worker, so current tests that omit it keep their behavior until they are pointed at a job.

### Persistence and migration

The revision creates `task_steps`. Payloads store tool names, arguments, and redacted results. They do not store pay secrets, login secrets, or the desktop token. A step is the restart point for `resume`. This unit's worker, if the process dies, is not yet recovered. The rows are still durable.

### Concurrency, disconnect, and restart

The worker checks `is_stopped` on the job generation before the model call and before `invoke`, using the existing checks in `llm.py` and `retrieve_node`. A stop that arrives during `invoke` can still enter that call. The result is saved only if the job is still the same status and the same `seq` is the latest. A late result for an older seq is dropped.

Disconnect of the browser does not stop the job (`talk`). The worker is independent of the SSE task.

`continue_job` is the only way back to `running`. A `completed` or `stopped` job does not continue.

### Confirm, cancel, and unknown results

The confirm gate stays in the executor. One claim is consumed once by `consume_schedule_claim`. An `unknown` claim is not executed again. `decide_replay` for `non_replayable_write` returns `reconcile`. The loop looks up and does not send. `UnknownWrite` remains the signal. Production calendar, web, kb, and sandbox tools do not construct it today (`unknown_write` appears in `workflow.py` and in tests, not in those tools). This unit does not pretend they do. Writes that already arm a claim keep that arm path. The loop treats an armed claim with no result as `unknown` and does not call the tool again.

Cancel is the job `stop` from `talk`. A stopped job's next `invoke` is denied by `execution_denial`.

### Reference version and what may be copied

The Thinking Mode guide opened here is the wire contract: `https://api-docs.deepseek.com/guides/thinking_mode`.

Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` `agent/conversation_loop.py` and `agent/turn_tool_round.py` are recorded in the OSS report as "save the assistant tool call, and do not execute if the save fails." Those files were not re-opened. That order is what this unit implements on `TaskStep`. The report's space-padding of empty reasoning is not copied. The opened guide does not describe that padding.

OpenClaw `069c3e2b02bcdac1f9030e4a4eb6d021e2607176` `src/gateway/exec-approval-lifecycle.ts` is recorded as one allow-once decision, then the same path continues. Allow-always and unattended allow are not copied. FullDots `e625654e779149738847ea286fa21a3d5d43b803` `src/server/approval-gate.ts` is recorded as a parameter fingerprint that cannot be consumed twice. Rule bypass is not copied. Rowboat `c37ffda3b002499da5b0d76785cc7c3275be1a7f` `apps/harbor/packages/server/src/core/invocations.ts` is recorded as resuming after a decision. Automatic allow is not copied.

### Explicit non-goals

Do not replace the interaction layer with this loop. Do not add a browser tool. Do not add Browser Use, Playwright, or a new service. Do not auto-allow a write. Do not send tools on an ordinary chat turn that has no tool call. Do not set `tool_choice` to a value this design did not read. Do not swap Zhipu search in this unit.

### Acceptance

Unit assertion: the worker commits a `calls_saved` step and one `tool_calls` row per id before `gateway.invoke`. A response with two tool calls stays off the model until both are terminal, including one `result` and one `rejected`. That step is `results_ready` and schedules one model request. A later response with zero tool calls is `final_answer`, sets the job `completed`, and does not schedule another request. A crash after the tool rows are committed and before invoke leaves the calls pending, and recovery runs the pending read instead of calling the model. A calendar tool becomes a claim and does not call `create_event`. A job with `web_constraint=forbid` denies `web` inside the execution update even if the tool was named. A second confirm with the same token does not write again. An armed `desktop.open` with no finish does not call `perform`. A crash after `continuation_due` is set and before publish leaves `continuation_due` true. Adoption of the later reservation clears it once and does not create a second card.

Formal property: `SaveBeforeExecute`, `ResultBeforeNextModel`, `WriteWaitsForClaim`, `ForbidWebBlocksSearch`, `StoppedJobDoesNotExecute`, `ContinuationDueSurvivesCrash`. The model is one job, one response with two tool calls, and a crash before enqueue. It does not cover a provider 400. A crash after `arm_remote_claim` stays on the lookup path in `contracts.md`.

Live scenario: ask the job to create a calendar event. Pass predicate: a confirm card is visible, the calendar table has no new row, and confirming once creates one row and the same job posts a following sentence. A second click does not create a second row.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Waits for `talk` and `picture`. Branches from that head.

### Risks

Forgetting `reasoning_content` on a tool-bearing request is a 400 on the path the opened guide describes. The step payload stores it and the next request sends it. A crash between arm and the provider result stays `unknown` and is not retried as a send.

### Open points for this unit

None.

## search

### User-visible behavior

When a job chooses to search, the page shows sources with links. The answer that follows is still the chat role's model. A round with no search does not call a search API. Zhipu `/web_search` is not called on this path. Embeddings, rerank, and ingest OCR stay. A job that forbids search still cannot search, from `loop`.

### Code anchors

- `backend/app/services/web_search.py:58-62` posts to `{base}/web_search`, defaulting the base to Zhipu.
- `backend/app/services/web_search.py:213-323` sends `search_query`, `search_engine`, `search_intent`, `count`, `content_size`, and `search_recency_filter`.
- `backend/app/agents/tools/web.py:10-36` calls `search_planned` and returns evidence with `url`, `title`, and `snippet`.
- `backend/app/core/config.py:135-138` holds the Zhipu web-search settings. `config.py:198-199` says web search does not block startup.
- The Search and Fetch guide opened here names `POST https://api.deepseek.com/v1/tools/search` and `POST https://api.deepseek.com/v1/tools/fetch`.

### Modules, types, and state

`execute_web` calls a new `deepseek_search` function. The request body may contain only fields read from that guide: `query` (required, 1 to 128 characters), `count` (optional, 1 to 50), `ori_question` (optional), `need_content` (optional boolean). `localization` is not sent. The bearer token is the existing `settings.llm_api_key`. The base is the existing `settings.llm_base_url` plus `/v1/tools/search` and `/v1/tools/fetch`.

`count` is the existing `settings.web_search_count` when that integer is between 1 and 50. The code default is 5 (`config.py:138`), which is inside the documented range. No new count is introduced. `need_content` is true so the documented `content` field is filled. The evidence passed back to the model uses `title`, `url`, `snippet`, `summary`, and `content` from the guide. A fetch is a separate tool call the model chooses, with the documented `url` field, and it returns the documented `title` and `content`.

If the model sends a `query` outside 1 to 128 characters, the executor returns a tool error and does not POST. The HTTP client timeout is the existing `settings.llm_timeout` (`backend/app/core/config.py:23`). No new timeout is added. The opened guide says HTTP 408, 429, and 5xx may be retried, and that other 4xx statuses should not. This unit does not add a retry count. One tool invocation sends one POST. A non-success, including 402, is a tool error and does not change the chat model. A later model turn may choose search again.

### Interfaces

The model sees two read tools, `web` and `web_fetch`. `web_fetch` is added to the constitution as `effect: read`. Forbidding web search omits and denies both names. There is no fallback that keeps only the old four names.

The Zhipu function `search_api` remains in the file for ingest or other callers only if a caller still imports it. The chat and job path does not call it. A repository search at implementation time must show `execute_web` and the job worker do not reference `_web_search_url`.

### Persistence and migration

Search hits are `tool_result` steps from `loop`. No new table. The Zhipu web-search cache key is not reused for DeepSeek results. `.env.example` states that job search uses the DeepSeek key and base, and that `WEB_SEARCH_*` is not the chat search path. The Zhipu variables stay for any remaining non-chat caller. They are not deleted in this unit if a non-chat caller still imports them. If the only caller was `execute_web`, the implementation removes that call and leaves the settings unused rather than deleting embedding settings.

### Concurrency, disconnect, and restart

Search is a read. `decide_replay` allows another read. A redelivered worker does not repeat a search whose `tool_result` step is already `saved`. It continues to the next model call. Disconnect does not stop the job.

### Confirm, cancel, and unknown results

Search and fetch are not confirmations. A failed search is a tool error step, not an `unknown` write, and it is not retried as a write. Cancel stops the job before the next search call.

### Reference version and what may be copied

Opened pages:

- `https://api-docs.deepseek.com/guides/search`
- `https://api-docs.deepseek.com/quick_start/pricing` for the separate Search and Fetch prices

DeepSeek Harness `ca33fd6843b205e279c7ccb6cb73a472c78f5760` `packages/web/web-deepseek-api/src/search.ts` is recorded in the OSS report as a separate POST that does not change the chat model. That file was not re-opened. The field names in this unit come from the Search guide above, not from that file. Hermes `tools/web_tools.py` is recorded as keeping `agent.model` while search uses another provider. The source is not copied. PR 93's Pro Responses path is not used. The Responses guide at `https://api-docs.deepseek.com/zh-cn/guides/responses_api` was opened on 2026-11-19. Its `web_search` tool runs on the server for `deepseek-v4-pro` and is ignored on `deepseek-flash`. This unit does not declare that tool. The executor cannot refuse a search that the provider runs before the response returns.

### Explicit non-goals

Do not switch the chat model to Pro. Do not add an earth-button setting. Do not remove Zhipu embeddings, rerank, or vision OCR. Do not send `localization`. Do not invent a query rewriter beyond the model's own tool arguments. The current `_rewrite_query` path is not the job's search path.

### Acceptance

Unit assertion: `execute_web` posts JSON to `{llm_base_url}/v1/tools/search` with `query` and `need_content: true`, and the test HTTP log contains no `web_search` path. The following completion uses `resolve(Role.CHAT).model`. A `forbid` job does not construct that request.

Formal property: `OneSearchProvider` and `ChatModelUnchanged`. The model is one job round. It does not cover result quality, billing, or a second search provider.

Live scenario: ask a job for today's news and allow search. Pass predicate: the reply contains at least one `http` citation, the completion model is the chat role's model, and the process log has a search POST to the DeepSeek tools path and no POST to `open.bigmodel.cn` `/web_search`.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Waits for `loop`.

### Risks

A query longer than 128 characters is a tool error, and the model must shorten it. The executor does not silently clip. An empty `results` array is a successful empty search, which the guide says is not billed, and the model is allowed to answer that nothing was found.

### Open points for this unit

None.

## mail

### User-visible behavior

Confirming a send does not by itself mean the mail was sent. If the operator has not chosen a transport, the card says the mailbox is not connected and no acceptance row is written. After a transport is configured, the card says sent only when that transport returns an acceptance id. A failure stays a failure. A timeout or a dropped response stays unknown and is not sent again. Acceptance is not a claim that the recipient read the mail. The meeting-prep Gmail search stays read-only. The mailto link still only opens a draft.

### Code anchors

- `backend/app/services/mail_entry.py:226-273` consumes the claim, generates `uuid4()`, arms it, inserts `MailAcceptance`, finishes the claim, and calls `note_external_success`. The review states that this row is the provider.
- `backend/app/services/mail_entry.py:397-461` inserts that row and seals.
- `backend/app/services/gmail.py:1-64` is read-only meeting search. `GOOGLE_MAIL_SCOPES` is `https://www.googleapis.com/auth/gmail.readonly` (`backend/app/services/calendar_secrets.py:41`).
- `backend/app/services/schedule_confirm.py:42-49` uses the sentence "结果未知，未再次写入。请先核对日历。" for every unknown claim, including mail.
- `frontend/src/components/FollowupCard.tsx:183-187` opens `mailtoHref` and proposes a send. The proposal is not a send.

### Modules, types, and state

A `MailTransport` object lives in `mail_entry.py`. It has two operations: `send(draft) -> accepted(provider_id) | rejected | unknown`, and `lookup(provider_id) -> accepted | absent | unknown`. `provider_id` is a non-empty string the transport supplies. A local `uuid4` is not a `provider_id`.

`settings.mail_transport` defaults to empty. Empty selects no transport. `commit_send` checks that before `consume_schedule_claim`. With no transport it raises `MailClosed` and leaves the claim `open`. It does not arm and does not insert `MailAcceptance`.

With a transport, the order is: consume, arm a client attempt id that is stored only as `remote_request_id`, call `send` once, and on `accepted` with a non-empty provider id replace the stored remote id with that provider id, insert one `MailAcceptance`, `finish_confirm`, and `note_external_success`. On `rejected`, `reopen_known_rejection` runs and the card does not say sent. On `unknown`, the claim stays `unknown`. The next commit calls `lookup` when a provider id is already stored. If the attempt was armed and no provider id exists, `lookup` is not invented. The claim stays `unknown` and `send` is not called again.

The calendar unknown sentence is not rewritten. Mail returns its own sentence at the `MailClosed` boundary: the result is unknown and the mail was not sent again.

### Interfaces

`POST /api/mail/send` keeps its current path. The response gains a `state` of `sent`, `rejected`, `unknown`, or `not_connected`. `sent` is returned only after `note_external_success`. No Gmail or SMTP field is added, because the transport is not chosen here.

### Persistence and migration

No new table. `MailAcceptance.remote_request_id` stores the provider id only after acceptance. An armed attempt id that is not a provider id is kept on the claim and is not copied into `MailAcceptance`. No migration is required if those columns already exist (`models.py:287-300` and `ScheduleConfirmClaim.remote_request_id`).

### Concurrency, disconnect, and restart

`consume_schedule_claim` and `arm_remote_claim` already commit the unknown state before the external call. A crash after arm and before the transport returns leaves `unknown`. Restart does not call `send`. The person can ask the page to look up. Lookup runs only when the transport exists and a provider id is stored. Two confirms serialize on the claim row lock.

### Confirm, cancel, and unknown results

Confirm is required. The claim is consumed once. A second confirm of a `written` claim does not call `send`. Unknown does not resend. Cancel of the job does not unsend a message the transport already accepted. The mailto control is not a send.

The gap that stays: if the transport accepts the message and the response is lost before any provider id is stored, this design has no lookup key. The claim stays `unknown`. It is not marked sent and it is not resent. No RFC message-id lookup is specified, because no transport document was opened.

### Reference version and what may be copied

DS-Bot `5e9b7da8882da3b68d8a0624342708c2150a9156` is recorded in the OSS report as waiting for `smtp.sendMail` or Gmail `users.me.messages.send` before marking sent, and as sometimes skipping the confirm card. The source was not re-opened. Waiting for the transport return is the part used. Skipping confirm, ignoring SMTP rejected recipients, and treating a client-generated id as acceptance are not used. nanoMuse is not copied.

### Explicit non-goals

Do not choose Gmail or SMTP in this unit. Do not add `gmail.send` to the calendar consent screen. Do not change `search_meeting_mail`. Do not treat the mailto link as a send. Do not rewrite the calendar unknown sentence.

This unit is a scaffold. An empty `mail_transport` proves the product does not say sent. It is not acceptance against a real mailbox. Provider and credential choice stay open. Other units may still be merged in the numbered order. Do not describe the target as complete while the transport is empty.

### Acceptance

Scaffold assertion: with `mail_transport` empty, `commit_send` leaves the claim `open`, inserts no `MailAcceptance`, and does not call `note_external_success`. With a fake transport that returns one id, one acceptance row and one `external_success` exist, and a second commit does not call `send`. A transport that raises after arm leaves `unknown` and the following commit does not call `send`. A stored provider id with a missing acceptance row is repaired by inserting the row, and `send` is not called. A lost response with no provider id stays `unknown` and is not sent again. The last case is an unresolved limit, not a passing lookup.

Formal property: `NoAcceptWithoutProvider`, `OneSendPerClaim`, `UnknownDoesNotResend`. The model is one claim. It does not cover the recipient's inbox, a transport with no id, or two users.

Live scenario: confirm a send while no transport is configured. Pass predicate: the card does not say the mail was sent, and the acceptance table has no new row.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Cut from the latest `release` after `search`. Rebase `config.py` if an earlier unit has edited it.

### Risks

A transport implementer might return the armed uuid as the provider id. The interface requires the id to come from the transport's acceptance result, and the test double returns a fixed provider id that is not the armed uuid.

### Open points for this unit

The operator still chooses the transport and where its credentials live. This unit does not make that choice. Live acceptance against a real mailbox waits for it. The numbered order may continue. The empty-transport scaffold is not that live acceptance. If the transport accepts a message and the response is lost before a provider id is stored, there is still no lookup key. That limit stays unresolved. `send` is not retried to fill it.

## calendar

### User-visible behavior

A calendar create still shows a card and writes only after confirm. The session shows success only after the provider response contains an event id and that id is stored. A second confirmation does not create another event. If the response is lost after the request was armed, the existing lookup runs and does not insert again. A miss stays unknown. The event id is not treated as proof that a later, different create is a duplicate.

### Code anchors

- `backend/app/api/calendar.py:188-244` consumes the claim, calls `create_event`, and `_seal_schedule_write` which is `finish_confirm` (`498-501`). It does not call `note_external_success`.
- `backend/app/api/calendar.py:519-558` `_keep_found_create` looks up by the armed marker and saves. It does not insert.
- `backend/app/services/calendar_google.py:1321-1380` builds `extendedProperties.private.requestId`, arms that id, POSTs, and on a missing id or a network error calls `_adopt_inserted`.
- `backend/app/services/calendar_google.py:1234-1266` GET with `privateExtendedProperty=requestId=...` and returns the item only when `id` is non-empty. It does not insert.
- `backend/app/services/event_inbox.py:164-198` and `562-581` set `external_success` only when the claim is `written` and `remote_request_id` equals the marker passed in.
- `backend/app/services/calendar_google.py:1180-1201` arms a patch with the event id before PATCH. Delete and RSVP bodies were not read.

### Modules, types, and state

Create already has the two ids: the armed `requestId` on the claim, and the provider `id` on the Google payload and the local `CalendarEvent.external_id`. Success sealing gains one step after `finish_confirm`: if the provider id is non-empty, insert one `EventReceipt` with source `internal`, delivery id `cal:{claim token}`, and `claim_token` set to that confirm token. Do not call `submit_event`. That function routes an act and can cancel open claims. Then call `note_external_success` with the armed `requestId` so `_written_claim` matches. The receipt body stores the provider event id. A call with an empty event id does not seal. `_SOURCES` already allows `internal` and rejects `calendar`.

`verification/errand-place/check.py` and `verification/mail-entry/check.py` currently require that `note_external_success` does not appear in `backend/app/api/calendar.py`. This unit changes those two assertions so they require the armed `requestId` match and the event id in the body. The errand checker that forbids `note_external_success` in `errands.py` stays.

`note_external_success` is not changed to treat the event id as the claim's `remote_request_id`. Those values are different on purpose. The receipt's `external_request_id` remains the armed request id that the claim already stores. The event id lives in the receipt body. A reader treats success as: state `external_success`, body event id non-empty, claim written.

The lost-response path stays `_adopt_inserted` and `_keep_found_create`. They do not POST again. If they find an id, the same seal runs. If they do not, the claim stays `unknown`.

### Interfaces

`POST /api/calendar` keeps its body. The JSON it already returns includes the stored event. The implementation adds the receipt. No new Google request field is added. `private.requestId` and the GET parameter are already in `calendar_google.py`.

### Persistence and migration

No new column. `EventReceipt` already exists. `record_create_success` in `contracts.md` inserts the receipt and sets `external_success` in one transaction. It does not call `submit_event`. The unique `(user_id, source, delivery_id)` key makes a repeated repair keep the same row. A claim that is `written` without that receipt is not shown as complete. Repair files the receipt from the stored event id and does not POST.

### Concurrency, disconnect, and restart

The claim row lock is the existing one. A disconnect after arm does not insert. Restart does not insert. The next click of an `unknown` claim takes the adopt path. Two clicks cannot both insert because the second sees `unknown` or `written`. A crash after `finish_confirm` and before the receipt, or after the receipt and before `external_success`, is repaired by `record_create_success`. The repair does not call the provider. Patch, delete, and RSVP are not repaired by this create path.

### Confirm, cancel, and unknown results

Confirm once, arm, then insert. Unknown looks up. A definite rejection still uses `reopen_known_rejection` where that path already does. This unit does not change the empty-403 classification. That function was not read. Delete and RSVP are not edited. Patch already arms the event id. This unit does not add a second patch. A patch success receipt is out of scope until the patch return path is edited in a later change that has read the whole function.

### Reference version and what may be copied

Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` `skills/productivity/google-workspace/scripts/google_api.py` `calendar_create` is recorded as printing created after `result["id"]`, and as inserting again on a second call. The file was not re-opened. Recording success after an id is the part used. A second insert is not. The lookup already in this repository is the idempotency mechanism. No new Google idempotency field is introduced.

### Explicit non-goals

Do not retitle-match events. Do not insert when the response is missing. Do not call `note_external_success` with the Google event id in the slot that must equal `remote_request_id`. Do not edit delete or RSVP in this unit. Do not change the mail seal.

### Acceptance

Unit assertion: a create whose provider double returns an id writes one local event and one `external_success` receipt whose body contains that id and whose `external_request_id` is the armed request id. A second confirm does not insert. A provider double that drops the response after arm leads to one lookup GET and zero additional inserts. A crash after the claim is `written` and before the receipt leaves the create incomplete. `record_create_success` then inserts the receipt and does not POST. The same repair covers a receipt that is not yet `external_success`.

Formal property: `SuccessAfterEventId`, `NoSecondInsert`, `LookupNotInsert`, `WrittenWithoutReceiptIsIncomplete`. The model is one create claim and one crash between the bookkeeping writes. It does not cover patch, delete, RSVP, an empty 403 body, or two calendars.

Live scenario: confirm one create against the test calendar double. Pass predicate: one event row, one receipt in `external_success`, and the second click leaves the event count unchanged.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Cut from the latest `release` after `mail`.

### Risks

Sealing with the request id while the person-facing id is in the body can be misread as "the receipt id is the event id." The receipt body is the event id, and the match key is the armed request id. The acceptance test checks both.

### Open points for this unit

None.

## errand

### User-visible behavior

A placed order shows the merchant order id and is tied to the confirmation that armed it. A reply that says placed but does not echo that armed id is unknown. It is not stored as placed and it is not ordered again. A bare page that only says "已下单" stays unknown unless the armed id is present, which is already how the text match works. Payment and login material stay off the public reply. If no connector exists, the current compose service remains the channel. No new browser product is added.

### Code anchors

- `backend/app/services/errands.py:131-171` consumes, requires pay and channel, arms `uuid4()`, and calls `_place` once.
- `backend/app/services/errands.py:225-249` looks up and does not call `_place`.
- `backend/app/services/errands.py:348-377` sets `named` from the armed id plus "已下单". For `outcome == "placed"` it returns `placed` with `named=True` when the echo is missing or equal. A differing echo is not placed.
- `services/errand-compose/server.py:15-27` returns `outcome`, `order_id`, `told`, and `clicks_after_wall`. It drops `idempotency_key`.
- `backend/app/services/errand_order.py:5` and `100-109` reject `password`, `pan`, `cvv`, `cookie`, and `secret`.
- `docs/envisioned-product/review/ev-errands.md` says a page that only says "已下单" does not insert. The `outcome == "placed"` branch with an empty echo still returns `named=True`. That review sentence and the code disagree. The code is the source for this unit.

### Modules, types, and state

`_public` adds `idempotency_key` from the task when that value is a string. It still drops the sealed names, including `pay` and `login`. `interpret` treats `outcome == "placed"` as placed only when `echoed_key == armed` and `order_id` is non-empty. An empty echo is `unknown`. `_save` already stores `order_id` on `ErrandRecord` (`models.py:303-313`). `_reconcile` is unchanged in its refusal to place.

The compose driver is not rewritten. `driver.py` was not read. The public function is the change.

### Interfaces

The compose `POST /tasks` response gains `idempotency_key`. Connector `place_url` responses are interpreted with the same rule. A connector that omits the echo is unknown and is looked up. The lookup body already sends `idempotency_key` (`errands.py:296`).

### Persistence and migration

No new table. `ErrandRecord.idempotency_key` remains the armed id. `order_id` remains the merchant id. No `note_external_success` call is added. The errand review records that this path does not seal the inbox, and this unit keeps that boundary.

### Concurrency, disconnect, and restart

Arm commits before the HTTP call. A crash or `RemoteDown` enters `_reconcile`. Lookup does not place. A second click on `unknown` reconciles. A `written` claim does not place.

### Confirm, cancel, and unknown results

The `errand.place` claim is required. Unknown looks up by the armed id. It does not order again. A price over the cap, an app wall, and a known rejection keep `reopen_known_rejection` where `_apply_reply` already uses it (`200-219`). Secrets stay out of the card and the public body.

### Reference version and what may be copied

The OSS report says the repositories it opened do not contain a full order-reconciliation module. No upstream order file is copied. Poke and Vesta are named on the plan page as product examples. They are not dependencies. Hermes plus Browser Use is a candidate to verify, not a library to add in this unit.

### Explicit non-goals

Do not add Browser Use, a new Playwright dependency, or a new service. Do not rewrite the compose driver. Do not place on an empty echo. Do not put payment or login fields in `_public`. Do not call the inbox seal from this path.

### Acceptance

Unit assertion: `_public` of a result that includes `idempotency_key` returns that key and omits `pay`. `interpret` of `{outcome: placed, order_id: "m-1"}` with an empty echo does not return `placed`. `_reconcile` does not call `_place`.

Formal property: `EchoRequiredForPlaced`, `LookupDoesNotPlace`, `SecretsDropped`. The model is one claim and one reply. It does not cover a live merchant, a browser agent, or a lost TCP connection after the merchant charged a card. That last case stays `unknown` and is looked up, not ordered again.

Live scenario: the compose test double returns `outcome=placed` and an order id without the armed key. Pass predicate: no `ErrandRecord` is inserted, the claim stays `unknown`, and a second commit does not POST `kind=place`.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Cut from the latest `release` after `calendar`. The browser choice is a gate in `open.md`. It does not block this unit.

### Risks

A connector that cannot echo the key will stay unknown and rely on lookup. That is the intended failure. A lookup endpoint that itself places would break the property. The connector lookup payload stays `{"idempotency_key": armed}` as it is today.

### Open points for this unit

None inside this unit. Adding a browser dependency is a separate gate.

## devices

### User-visible behavior

Each install package enrolls as its own device. The person sees the token once. The server stores a hash, not the token. Revoking one device leaves the others able to propose. A page that sends the word `desktop`, or a chat body that claims to be desktop, still cannot run a local tool. A device token does not skip the action card.

### Code anchors

- `backend/app/agents/harness/desktop_channel.py:30-41` compares the header to one `settings.desktop_package_token` with `hmac.compare_digest`. The words `desktop` and `web` return none. An empty token recognizes nothing.
- `backend/app/core/config.py:186` defines that shared token.
- `backend/app/agents/harness/surface.py:6-23` has only `WEB`, and `local_tool_denial` refuses a local tool on that surface.
- `backend/app/api/chat.py:54-59` has no desktop field. `backend/app/api/desktop.py:34-38` rejects a header that does not become a `PackageProof`.
- `desktop/main.js` passes `DESKTOP_PACKAGE_TOKEN` from the environment. The line was seen in the earlier search of that file.

### Modules, types, and state

`DesktopDevice` stores `id`, `user_id`, `token_hash`, `label`, `created_at`, and `revoked_at`. `token_hash` is SHA-256 of the token bytes. `recognize_package` hashes the presented header and compares with `hmac.compare_digest` against a non-revoked row for any user, then checks the row's user against the authenticated user at the desktop route. A match returns a `PackageProof` that carries the device id. The proof still cannot be built from the string `desktop`. The current class has empty slots, rejects attribute assignment, and compares every proof as equal (`desktop_channel.py` lines 10-21). This unit replaces those three behaviors so a proof holds a device id and two proofs compare equal only when that id matches. List responses return id, label, and created time. They do not return the hash or the token.

Enrollment is chosen here so it does not stay open: the signed-in user enrolls from the package after login. The response shows the token once. The package stores it in the main process. The web settings list revokes by device id. Revoke sets `revoked_at` on that id only. A failed save rolls back.

The shared `DESKTOP_PACKAGE_TOKEN` no longer authenticates. Existing installs enroll again. The old token is not migrated into a hash, because the server must not keep a raw token to hash it later.

### Interfaces

`POST /api/desktop/devices` with a label returns `{ "id", "token", "label" }` once. `GET /api/desktop/devices` returns the list without secrets. `POST /api/desktop/devices/{id}/revoke` revokes one row owned by the user. `X-Omni-Package` still carries the secret. The words `desktop` and `web` still fail before the hash lookup.

### Persistence and migration

One revision creates `desktop_devices` with a unique `token_hash`. RLS follows the existing user-scoped tables. The hash is not reversible.

### Concurrency, disconnect, and restart

Revoke and recognize use the row. A restart does not restore a revoked device. Two enrollments get two rows. A reused presented token hits the unique hash and does not create a second device.

### Confirm, cancel, and unknown results

The device row is not a confirm claim. `propose` and `commit` still require `ScheduleConfirmClaim`. A valid device token with no claim does not open a file. Cancel of a job does not revoke a device.

### Reference version and what may be copied

OpenMausBot HEAD `35d044295697ef2b79b889461af6902c85a1b456` and mechanism pin `083fd7b51405e6d0c01e189b2f65ab0322509b5a` `companion/src/devices.ts` are recorded in the OSS report as SHA-256 storage, constant-time compare, list without the hash, and `revoke(id)` with rollback. The file was not re-opened. That mechanism is what this unit implements. Phone pairing and the relay are not copied. Open Super Agent `3a0303559c7dc3d3b83142e59da636c872df6e65` `osa/computer/link.py` is recorded as also revoking by id, and as deleting rows by a device string. The string delete and any auto-allow are not copied.

### Explicit non-goals

Do not share one token across packages. Do not store the raw token. Do not let the device token skip a card. Do not add a local tool to the web surface. Do not copy a pairing relay.

### Acceptance

Unit assertion: two devices enroll, revoke of the first leaves the second able to produce a `PackageProof`, the stored column equals SHA-256 of the token, the list JSON has no hash, the header `desktop` is denied, and `commit_local` without a claim token is denied for a valid device.

Formal property: `HashOnly`, `RevokeOne`, `SpoofDenied`, `TokenIsNotAConfirm`. The model is two devices and one web request. It does not cover the package keychain or a stolen token already copied off the machine.

Live scenario: enroll two packages, revoke the first, propose from both. Pass predicate: the revoked package receives 403, the other receives a confirm offer, and a web chat request still cannot execute a local tool.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Cut from the latest `release` after `errand`. Take the next Alembic revision after the one already on that release.

### Risks

Operators with a working shared token must enroll before desktop actions work. That is the intended cutover. The package must stop sending the old shared token after enrollment.

### Open points for this unit

None. Enrollment by the signed-in user is the choice this design makes. It can be replaced later without changing the hash or the revoke row.

## resume

### User-visible behavior

Closing the page or losing the network does not cancel a job. Opening the session again shows the job's progress, its result if it has one, and a card that is still waiting. A second window of the same session shows the same status and the same card. A restarted worker continues from the last saved step. It does not run a write that was already armed. A late result for an older step does not overwrite the current step. The follow-up confirm button follows the phase of the action that owns the token.

### Code anchors

- `backend/app/api/chat.py:526-529` stops the bound generation on disconnect. `talk` limits that to the interaction turn.
- `backend/app/tasks/celery_app.py:21-28` sets `task_acks_late` and `task_reject_on_worker_lost`. `visibility_timeout` is `max(3600, celery_ingest_time_limit + 300)`. This unit does not change that number.
- `backend/app/api/chat.py:641-679` can flush `reason_buf.finish()` and `answer_buf.finish()` when `stream_chat` returns on `is_stopped` while `leave_stream` is still false. `691-696` can still emit an artifact, and `830` always emits `done`.
- `frontend/src/services/chatStream.ts:24-26` aborts the fetch. `272-277` reloads messages unless the session is already hydrated and busy.
- `frontend/src/components/FollowupCard.tsx:192-197` shows "确认加到日历" when the card root `phase` is `draft`. The plug evidence README says an action token can be confirmed while that root stays `draft`. The server rejects a second insert. The button can still look live.
- `loop` stores `TaskStep`. `watch` stores the notice stamp.

### Modules, types, and state

`GET /api/sessions/{id}/jobs` locks `session_cursors` for that session, then reads `published_seq` and the job rows before it releases the lock. `cursor` is `published_seq`. It is not `max(session_events.id)`. The browser uses this on load and when the document becomes visible. No poll interval is added.

`GET /api/sessions/{id}/events?after={cursor}` streams `session_events` for that session with a greater id. The event kinds and the snapshot rule are in `contracts.md`. A confirm or a status change is an event even when no `task_steps` row is added. Disconnect closes that response and does not call `task_run.stop`. A second window opens its own subscription. The last window to disconnect leaves the job running. The client applies each event id once.

`run_job` adopts the `dispatch_token` in its message. Zero rows means it returns without work. It does not reserve. A `results_ready` step calls the model once and does not invoke saved calls again. A `final_answer` step completes the job and does not call the model. A `calls_saved` step executes calls that have no terminal result and does not call the model first. An armed write is looked up and is not sent again. `waiting_person` without `continuation_due` returns without a model call. `stopped` and `completed` return. A write whose `exec_epoch` is older is discarded.

The interaction stream's stop path sets `leave_stream` when `stream_chat` returns because the turn is stopped, so the buffers are not flushed as a continuation. `done` is still sent so the client can close. The event is a close of the interaction stream, not a statement that the job stopped. The jobs GET is the authority.

`FollowupCard` enables the calendar button only when the action that owns `confirm_token` has phase `draft`. A root phase of `draft` with an action phase of `confirmed`, `unknown`, or `cancelled` does not show the button. The server mismatch rejection stays.

### Interfaces

The jobs GET returns JSON from `TaskRun` and `ScheduleConfirmClaim`, plus `cursor` equal to `published_seq`. The events GET emits `session_events` with `seq` greater than `after`, ordered by `seq`. `after` is the last published seq the client applied, not a per-job step seq and not an autoincrement id. No new provider field is involved.

### Persistence and migration

This unit adds `session_cursors` and `session_events.seq`. Dispatch columns and `continuation_due` already come from `talk`. Tool rows come from `loop`. Notice columns come from `watch`. The sweep reserves with the updates in `contracts.md` and only then publishes `{task_run_id, dispatch_token, dispatch_epoch, intent_version}`. It never sets `exec_token`. A future `hold_until` is not reserved again. A `waiting_person` row is reserved only when `continuation_due` is true. An armed write is delivered onto lookup, not onto send.

### Concurrency, disconnect, and restart

Two subscriptions read the same published seq. Two workers can adopt one reservation. One update matches. The other does nothing. A late write with an older `exec_epoch` updates zero rows. Restart does not mark the job stopped and does not mark it completed.

The concrete restart sequence follows the traces in `contracts.md`.

1. The worker process comes back and the existing beat runs.
2. The sweep reserves a `running` row only when `exec_token` is null and the reservation is missing or expired, or it retires an executor whose `hold_until` has passed. A future `hold_until` publishes nothing.
3. A `waiting_person` row is reserved only when `continuation_due` is true. That update does not clear the flag.
4. The sweep publishes the new token. If it dies before publish, the next beat after `dispatch_until` publishes a new epoch. The job stays `running`.
5. The worker adopts that message. Adoption sets `exec_token` and clears `continuation_due`.
6. `calls_saved` runs calls with no terminal result and does not call the model first.
7. `results_ready` calls the model once. `final_answer` completes the job.
8. An armed write is looked up. Desktop with no finish stays `unknown` and does not call `perform`.
9. The worker commits a new step only when `exec_epoch` and `intent_version` still match.

"The row is in the database" is not a step. A reservation is not an execution hold. An autoincrement id is not the session cursor.

### Confirm, cancel, and unknown results

An open claim survives disconnect. An expired claim is still rejected by `consume_schedule_claim`. A second window cannot consume a claim the first window already wrote. Unknown is looked up. Cancel of the interaction stream is not cancel of the job. Text cancel from `talk` still stops the job, and a late step is dropped.

### Reference version and what may be copied

Hermes attach and detach, same pin as `talk`, are the subscriber idea. Closing the session when the last subscriber leaves is not copied. The generation fence recorded in the OSS report is the local `seq` check. No upstream file was re-opened.

### Explicit non-goals

Do not add a poll interval. Do not raise `visibility_timeout`. Do not recover by replaying the user message from the start. Do not flush stopped interaction tokens as a continued answer. Do not deploy.

### Acceptance

Unit assertion: aborting the session fetch leaves `kind=job` `running`. The sweep stores a dispatch token, publishes that token, and the worker adoption sets `exec_token` and then runs. A crash after the token is stored and before publish leaves `exec_token` null. After `dispatch_until`, a new token is published and adopted, and the job is not `stopped`. A message for the first token updates zero rows. A sweep while `hold_until` is in the future publishes nothing. Two deliveries of one token produce one adoption. A crash after tool calls are saved runs those calls on the next adopted worker and does not call the model first. A crash after arm looks up and does not send. An old `exec_epoch` updates zero rows. A publisher that has allocated seq 100 and not committed blocks the publisher of seq 101. A snapshot that returns cursor 101 has already committed seq 100. A client whose cursor is 100 receives 101. A confirm card gets a seq even when `task_steps` does not grow.

Formal property: `DisconnectDoesNotStopJob`, `ReservationThenAdopt`, `PublishCrashRecovers`, `LiveHolderNotReclaimed`, `CallsSavedBeforeModel`, `ArmedWriteNotSent`, `StaleEpochDropped`, `CommitOrderedCursor`. The model is two jobs, two subscribers, one reserve-before-publish crash, and one delayed seq. It does not cover the provider network, a stolen confirm token, or the follow-up button's clipboard.

Live scenario: start two jobs, close the tab, and open the session in a second window. Pass predicate: neither job is `stopped`, the second window shows the same waiting card, confirming in the second window writes once, and an event for the second job is not dropped because the first job has a higher step seq.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Waits for `talk`, `loop`, and `watch`.

### Risks

Celery can still redeliver one message after `visibility_timeout`. Adoption of the same token is what makes the second delivery a no-op. A bug that invokes before adoption would defeat the contract. The acceptance test reserves, publishes, and adopts, in that order. An autoincrement id is not used as `cursor`.

### Open points for this unit

None.

## desktop

### User-visible behavior

When a job is about to open a file or run a program on an enrolled package, the card names the device, the file or program, and the action. Nothing runs before confirm. Confirm runs that action once on that device. The result returns to the same job, which continues. A card whose path or arguments changed does not run. The two probe buttons remain available as diagnostics. The web page still does not execute a local tool. There is no always-allow.

### Code anchors

- `desktop/preload.js:3-6` offers "打开测试文件" and "运行测试程序". `desktop/confirm_local.py:12-13` maps those to `/tmp/ev-desktop-probe.txt` and `/usr/bin/true`.
- `desktop/executor.py:6-23` runs only a license whose `kind` is `armed`, and only `desktop.open` or `desktop.run`.
- `backend/app/services/local_action.py:90-237` proposes a claim, arms a request id, and finishes only when the request id matches. A second finish does not arm again. `finish_local` does not enqueue a job.
- `backend/app/services/schedule_confirm.py:97-101` fingerprints `local_target` and `local_args` and does not include a device id. `write_scope` at `164-165` is `desktop:user:{id}` whenever `local_target` is present.
- `frontend/src/components/ChatWindow.tsx:238` skips `mode === "desktop"` in the web bubble.
- `backend/app/agents/harness/gateway.py:161-178` denies a local tool on the web surface before execute.

### Modules, types, and state

`write_parts` for `desktop.open` and `desktop.run` includes `device_id`. The fingerprint changes when the device, path, or arguments change, so the old token mismatches. The card stores the device label, the target, and the action. The license returned by `commit_local` carries the same three. `perform` returns false when the license device id does not match the package's enrolled device.

`finish_local`, after `finish_confirm`, saves a `tool_result` step on the linked job and calls `continue_job` then enqueues `run_job`. The job generation is the one on the claim. A different job is not continued. The web build still does not render a control that calls `perform`. The package renders the card from the job message and keeps the probe buttons as a separate diagnostic that still goes through propose, confirm, and perform.

`local_tool_denial` stays. `desktop.open` and `desktop.run` stay off the web gateway. The package is the executor, after the server has armed the license.

### Interfaces

`POST /api/desktop/propose`, `/commit`, and `/finish` keep their paths. Propose and commit require a `PackageProof` whose device id equals the action's `device_id`. Finish requires the same proof. The license `kind` values stay `armed`, `unknown`, and `finished` as `commit_local` already returns.

### Persistence and migration

No new table beyond `devices` and `TaskStep`. The claim fingerprint is the binding. A probe action that has no job uses the existing `insert_card_action` path and does not call `continue_job`, because there is no job to continue. A job-originated action always has a job id on the claim.

### Concurrency, disconnect, and restart

Confirm on one window consumes the claim. The other window sees the claim is no longer `open`. A crash after arm and before a successful open returns `kind=unknown` from the existing commit branch (`local_action.py:178-185`). The package may still enter `perform`. `perform` returns false unless `kind` is `armed` (`desktop/executor.py` lines 6-8), so `open_file` does not run. Restart does not open the file. The job stays `waiting_person` until a real finish. Unknown local finish does not continue the job into another open.

### Confirm, cancel, and unknown results

One confirmation, one `perform`. A mismatch does not perform. Expired and stopped claims fail in `consume_schedule_claim`. Cancel of the job before confirm does not perform. After `perform` has started, cancel does not undo the open. The result step is written only if the job was not stopped before finish. If it was stopped, finish records the license result on the step and does not call `continue_job`.

### Reference version and what may be copied

The same OpenClaw, FullDots, and Rowboat pins as `loop`. What is used is one visible action, one consumption, and continuation of the same job. Allow-always, remembered rules, and unattended allow are not copied. OpenMausBot is the device hash from `devices`, not a license to skip the card.

### Explicit non-goals

Do not remove the probe buttons. Do not execute local tools in the web gateway. Do not add screen-click automation. Do not share a token. Do not continue a different job. Do not auto-allow the next file because the last one was confirmed.

### Acceptance

Unit assertion: a job proposes `desktop.open` for device 7 and path `/tmp/fixture.txt`. The license matches those values. `perform` runs once. `finish_local` leaves that same generation `running` and then the worker can complete it. A commit with a different path returns mismatch and `perform` is not called. A web `invoke` of a local tool is still denied.

Formal property: `SameActionOnce`, `ContinueSameJob`, `WebStillDenied`. The model is one job, one device, and one card. It does not cover a real window manager, a second machine, or a token copied out of the package.

Live scenario: an enrolled package receives a job card to open a fixture file. Pass predicate: the open callback runs once after the confirm click, the job generation is unchanged, and a second confirm does not call open again.

The parent reviews this unit before merge. This pull request does not merge it. No deploy.

### Dependencies

Waits for `talk`, `loop`, and `devices`.

### Risks

Continuing the job before `finish_local` would let the model describe a file open that did not happen. The enqueue sits after `finish_confirm`. A package that performs and never calls finish leaves the job in `waiting_person`. The card shows that state. The sweep does not invent a success.

### Open points for this unit

None.
