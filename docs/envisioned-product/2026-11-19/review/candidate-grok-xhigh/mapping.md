# Mapping from the 2026-11-19 items to this design

Release is `d7710d687d147aeeda502d412454ab8a3829af66`. The fourteen merged knives are the code on that release. They are not a work list. PR 93 (`b41e74dcc0ca8c4800d4fd5861957e7d9445fcaf`, branch `cursor/ev-net-responses-5880`) is open and is not current code and not the target. A directory README that says yes, and the Poke and Vesta case pages, are not wired into this repository.

The units are `picture`, `talk`, `watch`, `loop`, `search`, `mail`, `calendar`, `errand`, `devices`, `resume`, and `desktop`.

## M1 — Flash answers, DeepSeek Search and Fetch

Current: chat completions use `Role.CHAT`, default model `deepseek-flash` (`backend/app/core/config.py:25`, `backend/app/core/llm.py`). Web search is a separate POST to `{base}/web_search` (`backend/app/services/web_search.py:58-62`, `213-323`). `execute_web` calls `search_planned` (`backend/app/agents/tools/web.py:10-36`). The outbound chat request refuses `tools` (`backend/app/core/llm.py:244-256`).

Gap: the model does not choose search, and the provider is Zhipu.

Change: `loop` gives the job a tool list. `search` posts to `{llm_base_url}/v1/tools/search` and `/v1/tools/fetch` with the fields from the opened Search guide. The completion model stays `Role.CHAT`. A round with no search tool call does not hit either search URL. PR 93's Pro Responses path is not used.

Already on release, do not redo: the chat role, the DeepSeek chat client, and the fact that search is not inside the chat completion.

## M2 — Tool round, then the model continues, with reasoning when tools are present

Current: the server builds a tool queue and then asks the chat model to write (`backend/app/agents/workflow.py:411-495` and `582-794`). `_refuse_outbound` rejects tools, `reasoning_content`, `role == "tool"`, and `tool_calls` (`llm.py:244-256`). Ordinary turns that omit previous reasoning are the current no-tools path. The opened Thinking Mode guide requires previous `reasoning_content` only when the request carries `tools`.

Gap: there is no model tool loop, no saved tool step, and no resume of the same job after confirm.

Change: `loop` saves the assistant tool call on `TaskStep` before `gateway.invoke`, returns `role: tool` results, and sends `reasoning_content` back on the tool-bearing job path. Ordinary chat turns stay on the no-tools path. Confirm stays in the executor. `resume` continues from the saved step after a crash. Hermes's blank-space padding of empty reasoning is not copied. `tool_choice` is omitted because the opened sample does not set it. The create-chat-completion page was not opened, so `tool_choice: required` is not specified.

## M3 — Per-task model settings, original chat images to Flash, usage unknown when missing

Current: four roles exist (`llm.py` roles, `config.py:25` and the planner, router, and judge settings). A request omits temperature except the JSON non-thinking path. `max_context_turns` is 10 and `context_max_tokens` is 32000 (`config.py`, the soft-budget comment). `_record_call` uses `prompt_tokens or 0` (`llm.py` usage helper). `ChatRequest` has no image (`backend/app/api/chat.py` `ChatRequest`). Ingest OCR is `ocr_image_bytes` (`backend/app/rag/vision_ocr.py:17-35`). The Models page says `deepseek-flash` supports Vision and `deepseek-v4-pro` does not.

Gap: chat cannot send the original image, and a missing usage object is stored as zero in the process counter.

Change: `picture` sends a user content array with `image_url` to `Role.CHAT` and writes `LlmCall` with null token columns when the response has no number. The 32000-token and ten-turn limits stay application clipping. No temperature, top-p, or output limit is added. The executor model falls back to the existing chat model and `chat_thinking` when its own environment values are empty. That is a fallback to an existing setting, not a new number.

Already on release, do not redo: the four roles and the omitted sampling fields on ordinary chat.

## M4 — Replace only Zhipu web search

Current: embeddings default to `embedding-3`, vision to `glm-4.6v` (`config.py:36` and `39`). Rerank is the Zhipu provider setting. Ingest OCR stays on the vision client.

Change: `search` removes the chat and job call to `_web_search_url`. Embedding, rerank, and ingest OCR stay. Agent Zero's embedding slot is future material and is not started. `picture` must not send chat images through `ocr_image_bytes`.

## M5 — Example file versus code defaults

Current: `.env.example:25` says `embedding-3-pro`. `.env.example:31` says `glm-4v-flash`. `.env.example:109` repeats those names in `LLM_MODEL_CONCURRENCY_LIMITS`. Code defaults are `embedding-3` and `glm-4.6v`. Production environment variables were not read.

Change: `picture` rewrites the example comments so an unset variable means the code default. `config.py` defaults are not changed. This is maintenance beside the image work, not a separate product decision.

## M6 — A clear "do not search" is a hard constraint, not a switch

Current: `plan_tools` turns router booleans into a tool list (`backend/app/agents/router.py:82-101` and `323-350`). There is no per-job forbid flag. `route_intent` is defined at `workflow.py:877` and this search found no caller.

Change: `talk` stores `web_constraint` from the router JSON for that job. `loop` omits `web` and `execution_denial` refuses it. The next message defaults to allow. There is no resident network switch and no keyword table for all messages.

## T1 — Keep talking, and a new sentence does not kill the job

Current: the composer returns when `busy` (`frontend/src/components/ChatWindow.tsx:49` and the stop control at `140`). `begin_turn` stops other open rows in the session except a card-action marker (`backend/app/services/task_run.py:187-219` and `262-279`).

Change: `talk` splits `kind=turn` and `kind=job`. `begin_turn` stops other turns and skips jobs. The resident stop control is removed. The job is a Celery task on the existing app, not a new service.

Already on release, do not redo: `open_alongside` (`task_run.py:253-259`) and the card-action exemption. They are reused.

## T2 — Supplement, redirect, new task, cancel

Current: dispatch records a tool assignment (`backend/app/agents/dispatch.py`). It does not classify a new sentence against an open job. Text "停止" is not a job cancel. The stop button and SSE disconnect call `task_run.stop` on the bound generation (`chat.py:645-647` and `699-707`).

Change: `talk` asks `Role.ROUTER` for `relation` of `supplement`, `redirect`, `new`, `cancel`, or `ask`, plus `target_generation` and `web`. Ambiguous cancel or redirect asks and changes nothing. A supplement does not stop the job. Cancel stops the named job only. Confirm stays on the job. OpenCode's default steer and Hermes's default interrupt are not copied.

The plan page left "which `waiting_person` rows the card-action exemption covers" as a wiring check. `talk` answers the part this design needs: a new interaction turn must not void a claim whose row is a `kind=job` in `waiting_person`. Other card-action callers stay as they are.

## T3 — Server state, disconnect does not cancel, reconnect reads it

Current: disconnect stops the bound generation (`chat.py:526-529`). There is no job that outlives the HTTP request. The workflow runs inside the request. No provider-cancel API was found in the pages opened here. `stream_chat` returns on `is_stopped()` (`llm.py:489-494`) without raising. `chat.py:670-679` then flushes the buffers when `leave_stream` is still false. `691-696` can still emit an artifact. `830` always emits `done`.

Change: `talk` stops only the interaction turn. `loop` persists steps. `picture` stores usage as returned or null. `resume` adds the jobs read, the event subscription, the redelivery rules, and sets `leave_stream` when the interaction stream returns because the turn is stopped, so those buffers are not presented as a continued answer. `done` still closes the interaction stream. A provider cancel is not promised.

## T4 — Watch the latest progress, not creation time

Current: `_silence` is `now - created_at - credit` (`watchdog.py:99-119`). `ORDINARY_STALL` is 20 minutes (`watchdog.py:19`). Only `running` is stopped. The beat is schedule `60` (`celery_app.py:50-54`). `waiting_person` and `waiting_resource` are left.

Change: `talk` adds `last_progress_at`. `watch` removes the creation-time stop, writes one notice per unchanged stamp on the row, and does not invent a replacement duration. The existing 60-second beat stays. Waiting rows stay left. Stop remains user cancel or a definite failure the executor already recorded.

## T5 — Two windows share server state

Current: `busy` is tab memory (`frontend/src/store/useStore.ts` and `chatStream.ts:293`). A later `begin_turn` stops the earlier open turn.

Change: `resume` serves jobs and steps from the server. The second window's message goes through `talk`'s classifier. Disconnect of one subscription does not stop the job. An expired claim is still rejected by `consume_schedule_claim`. One write stays one write.

## X1 — Sent only after the mailbox accepts

Current: `commit_send` arms `str(uuid4())`, inserts `MailAcceptance`, finishes the claim, and calls `note_external_success` (`mail_entry.py:226-273`). No Gmail or SMTP send is in that function. `gmail.py` search is read-only. `GOOGLE_MAIL_SCOPES` is `gmail.readonly` (`calendar_secrets.py:41`). The mailto control is not a send (`FollowupCard.tsx:183-187`).

Change: `mail` refuses to consume or accept when no transport is configured. A provider id must come from the transport. Unknown does not resend. If the response is lost before any provider id, the claim stays unknown. That gap is stated in the unit. The operator chooses the transport.

Already on release, do not redo: the claim, `arm_remote_claim`, and the mailto draft.

## X2 — Calendar success receipt, and one confirm does not insert twice

Current: create consumes, inserts, and `finish_confirm` (`backend/app/api/calendar.py` create path). It does not call `note_external_success`. Google insert already sets `extendedProperties.private.requestId`, arms that id, and on a lost response GETs `privateExtendedProperty=requestId=` (`calendar_google.py:1234-1266` and `1321-1380`). `_written_claim` requires status `written` and a marker equal to `remote_request_id` (`event_inbox.py:562-581`).

Change: `calendar` calls `note_external_success` with the armed request id after a non-empty event id is stored, and puts the event id in the receipt body. Lookup does not insert. The request id is not stored as the event id. Delete and RSVP bodies were not read and are not edited.

Already on release, do not redo: the requestId arm and the lookup GET.

## X3 — Order correlation, lookup only

Current: `commit_errand` arms a uuid and sends `Idempotency-Key` (`errands.py:160`). `_public` returns `outcome`, `order_id`, `told`, and `clicks_after_wall` and drops `idempotency_key` (`services/errand-compose/server.py:15-27`). `interpret` returns `placed` with `named=True` for `outcome == "placed"` when the echo is empty or equal (`errands.py:367-368`). A differing echo is not placed. `_reconcile` does not call `_place`. `ErrandRecord` already has `idempotency_key` and `order_id`.

Change: `errand` echoes the armed key and requires that echo plus a non-empty `order_id` before `placed`. Secrets stay dropped. No browser dependency is added. The existing compose service stays.

The review sentence that a bare "已下单" page never places is narrower than this `outcome == "placed"` branch. The code is the source.

## X4 — One card, one action, then the same job continues

Current: the package offers two probes (`desktop/preload.js`, "打开测试文件" and "运行测试程序"). `confirm_local.py` maps them to `/tmp/ev-desktop-probe.txt` and `/usr/bin/true`. `perform` runs only an armed license (`desktop/executor.py`). `finish_local` seals the claim and does not continue a job (`local_action.py`). The web bubble skips `mode === "desktop"` (`ChatWindow.tsx:238`). `write_parts` for desktop has `local_target` and `local_args` and no device id (`schedule_confirm.py`).

Change: `desktop` adds `device_id` to the fingerprint, runs `perform` once, and then `continue_job` on that generation. Probe buttons stay diagnostic. Allow-always is not copied. Web denial stays.

Already on release, do not redo: the claim, the armed license, and the web-surface denial.

## X5 — One credential per device

Current: `recognize_package` compares one shared `desktop_package_token` (`desktop_channel.py:30-41`, `config.py` `desktop_package_token`). The words `desktop` and `web` fail. `ChatRequest` has no desktop field. `ServerSurface` is `WEB`.

Change: `devices` stores SHA-256, compares with `hmac.compare_digest`, lists without the hash, and revokes one id. Enrollment is the signed-in user. The token is shown once. The shared token stops authenticating. The credential does not skip the card.

## R-B3 — Stopped stream still flushes buffers, an artifact, and done

Re-read this round: `stream_chat` returns on `is_stopped()` (`llm.py:489-494`). `chat.py:641-679` flushes `reason_buf.finish()` and `answer_buf.finish()` when `leave_stream` is still false. `691-696` can emit an artifact from the accumulated text. `830` always yields `done`. `RedactBuffer` itself was not opened.

Status: historical behavior, now re-read on this path. `resume` sets `leave_stream` when the interaction turn is stopped so the flush is not presented as a continued answer. `done` remains the close of that stream. This is not closed as a defect of the whole product, and it is not left unread.

## R1 — Formal models, Actions, regressions, evidence files

`verification/` checkers and the TLA files were not opened this round. The old claim that coverage is narrow stays historical. It is not a merge blocker for this design and it is not promoted to a current defect. Each new unit names a new small property. This design does not claim TLC was run.

## R2 — A completed generation can open another running row

Re-read: `_route_act` on `target.status == "completed"` calls `_open_task` (`event_inbox.py:239-248`). For a non-mail source, `_open_task` calls `open_alongside` (`420-458`). A mail source stores `received` / `filed` and does not open a turn (`430-441`).

Status: matches the code. The plan page says this is a product choice, not a decided defect. This design does not change it and does not ask again.

## R3 — Memory default user, discard, generation

Re-read: `list_memories` uses `int(user_id or 1)` (`memory.py:586-587`). `remember_from_turn` returns 0 when `generation is not None` and status is not `completed` (`657-658`). When `generation` is omitted, that guard does not run. `settle_pending` with `discarded` sets `pending = 0` and `decision = "discarded"` on the same row (`881-886`). It does not append a row.

Status: re-read, not a work item. Not asked again.

## R4 — Full-table sweep and the in-process ledger

Re-read: `load_runs` uses `db.query(TaskRun).all()` (`watchdog.py:122-137`). `_ledger` lives in the process (`158-164`). The creation-time clock is T4 and is changed by `watch`.

Change: `watch` filters to open statuses and stores the notice stamp on the row. `resume` does not use the in-process ledger as recovery. The object lifetime of every credit entry was not traced past this module. The sweep change does not depend on that trace.

## R5 — Execution surface and `route_intent`

Re-read: `route_intent` is defined at `workflow.py:877`. A search for a call found the definition and no caller in application code. `ServerSurface` is only `WEB`. Direct constructors and the full boundary tests were not re-read. This design does not claim every router is dead. `devices` and `desktop` keep the web denial. `loop` does not call `route_intent`.

## R6 — Diagram branches, fonts, export

The 2026-11-19 diagram is the target picture. It is not an audited implementation drawing. No code change is assigned. The older audited flow folder was not re-checked for font or export differences.

## R7 — Unknown writes and a leaked confirm sentence

`ToolResult.unknown_write` is used by `workflow.py` and by tests. This round did not find a production tool that constructs it. `calendar_google.py` `_write_result_unknown` was not opened, so the empty-403 claim stays unverified. Arm-before-send is on the claim helpers and is reused by `mail`, `calendar`, `errand`, and `loop`: unknown looks up or stays unknown, and does not send again. A confirm sentence leaking from a failed publish was not re-read. `resume` changes the follow-up button to follow the action phase that owns the token (`FollowupCard.tsx:192-197` shows the button from the root `phase`). The server rejection of a second insert stays.

## What is already on release and is reused

`TaskRun` and its five statuses, `ScheduleConfirmClaim`, `arm_remote_claim`, `EventReceipt`, `consume_schedule_claim`, the web-surface denial, stop-before-send inside the current workflow, and dispatch that does not itself invoke a tool. `continue_job` is the one new edge, from `waiting_person` or `waiting_resource` to `running`. `completed` and `stopped` still cannot take it. `_validate_moves` today rejects any target of `running` (`task_run.py:56-57`). The new validator is part of `talk`, not a rewrite of the five statuses.
