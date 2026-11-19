# Citations

Line numbers are from the working tree at release `d7710d687d147aeeda502d412454ab8a3829af66` plus the untracked `docs/envisioned-product/2026-11-19/` copies, which were not used as the design. Upstream repositories were not opened. Pins and "what the file does" sentences for Hermes, Harness, OpenCode, DS-Bot, Agent Zero, OpenClaw, OpenMausBot, FullDots, Rowboat, and Open Super Agent come from `/home/ubuntu/.cursor/projects/agent-repos-omni-butler/uploads/oss-issue-references_8ae0.md` and `/tmp/arena-plan/GROUNDING.md`. Those sentences are report claims, not a fresh reading of the upstream file.

## Current code

- `backend/app/services/task_run.py:12-70` — events, move table, `_validate_moves` rejects `target == "running"`.
- `backend/app/services/task_run.py:187-219` — `_insert_running` stops other open rows except a card-action marker.
- `backend/app/services/task_run.py:253-279` — `open_alongside` does not stop others; `begin_turn` does.
- `backend/app/tasks/watchdog.py:16-19` — `CHECK_INTERVAL` 60s, `ORDINARY_STALL` 20 minutes, `STALL_FLOOR` 60s.
- `backend/app/tasks/watchdog.py:99-164` — silence from `created_at`, `load_runs` queries all `TaskRun` rows, process-local `_ledger`.
- `backend/app/tasks/celery_app.py:21-28` — `task_acks_late`, `task_reject_on_worker_lost`, `visibility_timeout` from the ingest limit.
- `backend/app/tasks/celery_app.py:50-54` — `watch-task-runs` schedule `60`.
- `backend/app/core/llm.py:244-269` — `_refuse_outbound`, `_wire_messages` forces string content.
- `backend/app/core/llm.py:356-376` and `459-474` — usage reads `prompt_tokens` and `completion_tokens`; the counter uses `or 0`.
- `backend/app/core/llm.py:489-494` — `stream_chat` returns when `is_stopped()` is true.
- `backend/app/core/config.py:23` — `llm_timeout` default 120.
- `backend/app/core/config.py:25` — `chat_model` default `deepseek-flash`.
- `backend/app/core/config.py:36` — `embedding_model` default `embedding-3`.
- `backend/app/core/config.py:39` — `vision_model` default `glm-4.6v`.
- `backend/app/core/config.py:128` — `max_upload_mb` default 20.
- `backend/app/core/config.py:138` — `web_search_count` default 5.
- `.env.example:25` — `EMBEDDING_MODEL=embedding-3-pro`.
- `.env.example:31` — `VISION_MODEL=glm-4v-flash`.
- `.env.example:109` — concurrency example uses `embedding-3-pro` and `glm-4v-flash`.
- `backend/app/api/chat.py:641-696` — `leave_stream` stays false when the stream ends from inside `stream_chat`; buffers flush; an artifact can still be inferred.
- `backend/app/api/chat.py:699-707` — `CancelledError` stops the bound generation.
- `backend/app/api/chat.py:830` — the stream always yields `done`.
- `backend/app/agents/workflow.py:877` — `route_intent` is defined. A search for a call site found this definition and no caller in application code.
- `backend/app/services/memory.py:586-587` — empty user id becomes 1.
- `backend/app/services/memory.py:641-663` — a set generation that is not `completed` returns 0; an omitted generation skips that guard.
- `backend/app/services/memory.py:862-886` — `discarded` updates the same ledger row.
- `backend/app/services/event_inbox.py:164-198` — `note_external_success` entry.
- `backend/app/services/event_inbox.py:239-248` — a completed target calls `_open_task`.
- `backend/app/services/event_inbox.py:420-458` — mail stores `received`/`filed`; other sources call `open_alongside`.
- `backend/app/services/event_inbox.py:562-581` — success marker must equal `remote_request_id` on a `written` claim.
- `backend/app/services/mail_entry.py:226-273` — consume, `uuid4()`, arm, local accept, finish, seal.
- `backend/app/services/calendar_secrets.py:41` — Gmail scope is `gmail.readonly`.
- `backend/app/services/errands.py:348-377` — `outcome == "placed"` with an empty or equal echo returns `named=True`.
- `services/errand-compose/server.py:15-27` — `_public` drops sealed keys and does not return `idempotency_key`.
- `backend/app/services/calendar_google.py:1234-1266` — lookup by `privateExtendedProperty=requestId=`, no insert.
- `backend/app/services/calendar_google.py:1321-1380` — insert arms `extendedProperties.private.requestId`.
- `backend/app/services/web_search.py:58-62` — Zhipu `/web_search` URL.
- `backend/app/agents/tools/web.py:10-36` — `execute_web` calls `search_planned`.
- `backend/app/rag/vision_ocr.py:17-35` — ingest OCR posts to the vision endpoint.
- `frontend/src/components/ChatWindow.tsx:49` and `:140` — send is refused while `busy`; the stop control replaces the send button.
- `frontend/src/components/ChatWindow.tsx:238` — web bubbles skip `mode === "desktop"`.
- `frontend/src/components/FollowupCard.tsx:183-197` — mailto and propose-send; the calendar button is shown when root `phase` is `draft`.
- `frontend/src/services/chatStream.ts:293` — a busy session does not start another stream.
- `desktop/preload.js` — the two probe button labels, confirmed by the desktop-open check and the package file.
- `desktop/executor.py` — `perform` requires an armed license. The function body was read in the prior pass of this design.

`ChatRequest` fields, `begin_turn` at `chat.py` around 429, disconnect stop around 526, `dispatch.py` assignment, `execution_denial`, `surface.py` `ServerSurface.WEB`, `desktop_channel.py:30-41`, `gateway.py` local denial before execute, `schedule_confirm.py` consume and arm, `local_action.py` propose/commit/finish, constitution allowed tools, and `calendar.py` create sealing with `finish_confirm` only, were read in the prior pass of this design. The exact windows are the ones named in `plan.md`. Where this file gives a function and a line, that line was checked again in this pass.

## Docs and reviews opened

- `/tmp/arena-plan/GROUNDING.md` — the contract.
- `/home/ubuntu/.cursor/plugins/cache/cursor-public/9717366/e58dd043600587dbaf58ba315e7f2b38373eeb43/skills/poteto-mode/worker.md` — read first.
- `/home/ubuntu/.cursor/projects/agent-repos-omni-butler/uploads/current-plan-page_814a.txt` — M1–M6, T1–T5, X1–X5, R-B3, R1–R7.
- `/home/ubuntu/.cursor/projects/agent-repos-omni-butler/uploads/oss-issue-references_8ae0.md` — reference pins and the boundaries on what was not covered.
- `/home/ubuntu/.cursor/projects/agent-repos-omni-butler/assets/Omni-Butler-______-20261119.png` and the SVG upload — the target diagram.
- `docs/envisioned-product/README.md` and `decision-trail.md`.
- `docs/envisioned-product/envisioned-product-plan.md` lines 1–80 only.
- Review openings or full "what the code does" for ev-task, ev-stop, ev-dispatch, ev-work, ev-net, ev-webblock, and the status sections of ev-mail, ev-errands, ev-events, ev-desktop, ev-watch, ev-memory. There is no top-level `ev-gate.md` or `ev-plug.md`. `ev-gate-evidence/README.md` and `ev-plug-evidence/README.md` were opened for the confirm callers and the follow-up root-phase note.
- `/tmp/arena-plan/historical-gemini-20261119/citations.md` and `open.md` were opened as a reading list. Lines used from them were re-opened in this repository before being relied on. Their unit names were not adopted.

## Official pages opened

- `https://api-docs.deepseek.com/guides/search` — Search and Fetch paths, body fields, result fields, retry statuses, account QPS, and the empty-result billing note.
- `https://api-docs.deepseek.com/guides/thinking_mode` — `thinking.type`, `reasoning_effort`, the 400 rule for `reasoning_content` when `tools` is present, and the tool-result message shape.
- `https://api-docs.deepseek.com/quick_start/pricing` — `deepseek-flash` Vision, `deepseek-v4-pro` without Vision, legacy aliases.
- `https://api-docs.deepseek.com/guides/vision` — content array, `image_url`, formats, and the size limits cited in `picture`.

The Responses API guide and the create-chat-completion reference were not opened. No field from those pages is specified.

## Not opened, or not finished

These were not read to the end, or were not opened. Claims that would require them are marked unread in the plan or are absent.

- `docs/envisioned-product/envisioned-product-plan.md` after line 80.
- `/tmp/arena-plan/historical-gemini-20261119/mapping.md`.
- `/tmp/arena-plan/historical-gemini-20261119/rationale.md`.
- `/tmp/arena-plan/candidate-grok/` and `/tmp/arena-plan/candidate-fable/`. Any other `candidate-*` directory.
- DeepSeek create-chat-completion reference, the Responses API guide, and any provider-cancel API.
- Hermes, DeepSeek Harness, OpenCode, DS-Bot, Agent Zero, OpenClaw, OpenMausBot, FullDots, Rowboat, Open Super Agent, nanoMuse, and Grokked Bot source trees.
- `backend/app/core/llm.py` after `complete_text` starts (about line 504 through the end of the file).
- `backend/app/core/config.py` after the settings block that holds the web-search and desktop-token fields, including the Zhipu endpoint helpers.
- `backend/app/agents/router.py` `_tier0_forced` and the rest of `route` beyond `_decision_to_plan` and `plan_tools`.
- `backend/app/agents/tools/calendar_tool.py` except `register` near lines 965–973.
- `backend/app/agents/harness/gateway.py` after the local-tool denial.
- `backend/app/services/schedule_confirm.py` after the consume and arm functions (helpers past that point).
- `backend/app/services/event_inbox.py` between `_target` and `_written_claim`, and after `_written_claim`.
- `backend/app/services/errands.py` after `interpret`.
- `services/errand-compose/driver.py` and `decide.py`.
- `backend/app/api/calendar.py` delete, RSVP, and `confirm_google_title_change` bodies.
- `backend/app/services/calendar_google.py` outside the insert, lookup, adopt, and the patch-arm window. `_save_local_row` and `_write_result_unknown` were not opened.
- `calendar_local.py`, `calendar_factory.py`, the calendar service module, `meeting_followup.py`, `meeting_prep.py`.
- `frontend/src/components/FollowupCard.tsx` `confirm()` and the file after the phase button. `ScheduleCard.tsx`, `MailSendCard.tsx`, `ErrandCard.tsx`, and `MessageActions.tsx` were not read as files.
- `desktop/actions.js`. `desktop/main.js` beyond the token handoff. `desktop/confirm_local.py` beyond the probe targets.
- `constitution.yaml` body, `harness/verify.py`, `critique.py`, `types.py` beyond the `UnknownWrite` import, `context.py`, `prompts.py`.
- `memory.py` outside the three slices above. `tasks/pulse.py` and `tasks/ingestion.py` bodies.
- `rag/` except `vision_ocr.py` lines 17–35. `gmail.py` after the read-only search function.
- Alembic revision bodies `0001` through `0017`. Only the head name `0017_errand` is used.
- `verification/**` checkers and TLA sources. Tests. `.github/workflows`. `docker-compose.yml`. `backend/app/main.py`. `backend/app/core/db.py`.
- The later formal, performance, and live sections of the reviews whose openings were read.
- `RedactBuffer` class.

`config.py` defines the tabular settings twice. That duplication was seen and is not a unit.
