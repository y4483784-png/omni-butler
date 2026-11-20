# Residual closure, 2026-11-20

Base commit `1f28d4fd52af71245e8549797548ed948bf7726f`. That commit is `origin/release`. This file lists residuals from the eleven units merged as pull requests 95 through 105. Current code decides. A parent note that called an item nonblocking does not close it. A plan that omits a check does not make the behavior acceptable.

The parent decisions in the 2026-11-20 review packet are historical. Resume lease and lock order were accepted on the merged tree. Desktop recovery was left without a client protocol.

## How to read an item

Each item has one status.

- Reproduced defect. The current code does the wrong thing on a path a caller can reach.
- Verification gap. The behavior may hold, and the check that should fail when it breaks is missing or only matches a string.
- Demonstrably already fixed. The current code or an executed counterexample closes the old claim.
- Genuinely needs live acceptance. The automated seam cannot see a real provider, mailbox, or enrolled package.
- Architectural decision requiring parent design review. The next change needs a product or lifecycle choice. This round does not implement it. The write-up is a proposal. It is not an accepted limitation.
- Fixed in this branch, pending parent acceptance. The change is on this pull request. It is not closed on `release` until the parent accepts it.

Evidence is named separately.

- Bounded-model proof. A TLC run, or a break flag that TLC rejected.
- Source anchor. A line that was read. A checker that only requires a substring is a source anchor.
- Behavior test. An executed function, request, or test whose assertion a user of that function can observe.
- Deferred live. A real external system was not called.

## Execution order

This pull request implements CAL-01. That item is fixed in this branch, pending parent acceptance. It is not closed on `release`. Later units branch from the release after the parent merges. They do not stack on this branch, and this pull request does not start them.

| Order | Ids | Why this order |
| --- | --- | --- |
| 1 | CAL-01 | A failed arm still calls Google. This is the first code change. |
| 2 | LOOP-03 | A second holder can invoke a call that is already `running`. |
| 3 | TALK-04 | A supplement sets `continuation_due` while a call is still open, which starts that second holder. |
| 4 | ERR-01, ERR-02 | A whitespace order id is stored as placed. The checker should call the same functions. |
| 5 | DESK-01, DESK-02 | The package fills a missing device id. Add the HTTP omission test with that change. |
| 6 | PIC-01 | Bind image bytes when a message becomes a job. |
| 7 | PIC-04 | Stop tombstoning a non-terminal staged upload at 10 minutes. |
| 8 | TALK-03 | Make the composer void use the server keep rule. |
| 9 | SEARCH-02 | Drop an invalid `ori_question` and still post the query. |
| 10 | RES-02 | Post only a draft follow-up action. |
| 11 | DEV-05 | Reject a blank device label. |
| 12 | PIC-02 | Write one usage row from JSON completions. |
| 13 | WATCH-01, PIC-07 | Put the existing break flags on the workflows. |
| 14 | MAIL-01, CAL-03, LOOP-02 | Point checkers at the branch that can regress. |

Parent review before any code. Each item below is a proposal, not an accepted limitation:

- DESK-06. Local execution recovery. The revised design is below. It is not approved.
- DEV-06. Whether every package 403 deletes the token file.
- LOOP-05. What a keyless job tells the person.
- SEARCH-01. How a chat web query longer than 128 characters is shortened or refused.
- SEARCH-03. Whether the job answer uses the chat model or `EXECUTOR_MODEL`.
- PIC-03. Whether a tombstone row stays after the blob is gone. The current contract says it stays.
- CAL-02. The read that proves a patch, delete, or RSVP before a success receipt is filed.

Live acceptance, after the code units. PIC-08, MAIL-02, DESK-07.

## First unit

CAL-01 is the first change. On `1f28d4fd`, a confirm token was present, `arm_remote_claim` returned false, and insert, patch, and delete still called Google. Mail already returns before the send when that boolean is false. `contracts.md` says an arm that matches zero rows does not send the provider call. This branch stops those three writes. Acceptance is still with the parent.

The data shape stays the claim row. No new column and no new protocol.

The change:

- `_arm_user_write` returns false when a confirm token is set and the arm does not commit. It rolls the session back so the earlier consume does not remain `unknown`.
- A request with no confirm token still writes. That is the connected calendar path without a claim.
- Insert, patch, and the required delete raise a closed write error and do not call Google.
- The compensate delete and the compensate patch do not arm. `_restore_patch` patches with arming turned off, so a local commit failure can still undo a patch that already succeeded.
- The HTTP handler answers that closed error with 409 and the existing closed sentence. It does not mark the result unknown, and it does not reopen a claim the arm already rolled back.

The regressions post create, patch, and delete. Each claim is linked to a job in `running` with an exec token, so `arm_remote_claim` matches zero rows. The handler is the public HTTP route. The test expects 409 with the closed sentence, no Google insert, patch, or delete, no local change, and a claim that stays `open` with an empty remote id. A focused private-method stub is not the proof.

A local commit that fails after Google has already accepted a patch still sends the restore patch. A local commit that fails after an insert still sends the compensate delete. A connected update with a claim the arm does commit still patches Google.

LOOP-03 is the next safety item. It is not implemented here. A missing holder or an expired lease does not prove the earlier call has stopped. The next unit needs its own mini-plan and a failing behavior reproduction. DESK-06 stays a design proposal. The design below is not approved, and this pull request does not implement it.

## Desktop recovery design

This section is a proposal for parent review. It is not approved. This pull request does not implement it. It does not close the gap between the operating-system call and the durable executed marker, and it does not prove that an external side effect happens exactly once.

What the code does now. `confirm_local` calls `perform`, then posts finish. If finish fails, a second commit of the same claim returns `unknown` and does not call `perform` again. The job stays `waiting_person` until the claim is `written`. The package stores the device token and the device id. It does not store whether this claim was armed or executed. A crash after the operating-system call and before a durable marker leaves no local way to tell those two moments apart.

The record. One durable record per confirm token, on the enrolled device. The write is atomic. Write a temporary file in the same directory, fsync it, then rename it over the record. A reader sees the previous record or the new record, never a partial one. The record binds the confirm token, the device id, the server request id when the server has one, the path, the args, the job generation, and the intent version. The state is `armed`, `executed`, or left absent when this device has not started the claim.

Crash boundaries.

- No record yet. This device has not started `perform` for that token. The first confirm may create the `armed` record and then call `perform` once. A second process that loses the exclusive create does not call `perform`.
- The record is `armed`. The operating-system call may or may not have happened. Restart, a new click, and confirm on that same claim do not call `perform`. The package shows an explicit unknown outcome for that claim. It does not show success, and it does not show failure.
- The record is `executed`. `perform` returned to this process. Reconnect and process restart call finish only, with the stored device, request, path, and args. They do not call `perform`. If finish fails, the record stays `executed` and the next restart calls finish again.

Same claim. While the result is ambiguous, the same confirm token is not a second chance to run the action. Confirm, click, and restart all read the record first. An `armed` record stops them before `perform`. An `executed` record stops them before `perform` and sends them to finish.

Visible unknown. An `armed` record is shown as unknown. The sentence has to say that the action may already have happened. Safe resolution does not invent a result. It does not call `finish_local`, because that continues the job as a success. It does not mark the action failed, because that invents a failure. It does not call `perform` again. Until a later protocol has a server state for a local outcome that stays unknown, the job remains `waiting_person` and the package keeps showing unknown. That later protocol is not this pull request.

A new action. If the person authorizes a separate action, that action gets a new confirm token and its own record. The package says the earlier claim's outcome is unknown. The new action is not labeled a retry, and it is not described as running the original claim again. The new claim is bound to its own device, path, and args. Performing it is a new side effect the person accepted with that warning.

Concurrent confirms. Creating the record uses an exclusive create. One confirm wins and may call `perform` once from the no-record state. The other confirm sees `armed` or `executed` and does not call `perform`. Two confirms cannot both pass the create.

Binding. Finish sends the device id, path, and args stored in the record. A record whose device id is not this enrollment is not finished and is not performed. The package shows unknown, because the action may have run on the enrolled device. A confirm whose path or args differ from the stored record is a different request. It does not reuse the record, and it does not perform under the old token.

Finish. Finish is idempotent. A second finish does not enqueue the job again. A mismatched marker is rejected. A failed finish leaves the record `executed`.

Stopped or superseded generation. A stopped generation, or a generation that a newer one has replaced, is not a reason to call `perform`. An `armed` record on that generation stays unknown and is not performed. An `executed` record may still call finish. If the server rejects finish for that generation, the record stays `executed`, the package says finish was not accepted, and `perform` is not called.

Tests and model counterexamples for the unit that implements this design, after the parent approves it.

- An `armed` record, then process restart. `perform` is not called. The visible outcome is unknown.
- An `armed` record, then another click or confirm of the same token. `perform` is not called.
- An `executed` record and a failing finish, then restart. Finish is called again. `perform` is not called.
- Finish succeeds twice. The job is enqueued once.
- Two concurrent confirms of a token with no record. `perform` is called once.
- A new token while an older token is `armed`. The new token can perform only as its own claim, and the package has already said the older outcome is unknown.
- The model disables `perform` from `armed` and from `executed`. `executed` enables finish only. The model does not claim the operating-system call happened once, because `armed` covers both "not called yet" and "called, marker not stored".

Keep the current denials. The web surface does not gain a local tool. There is no always-allow. The device, path, and args stay bound to the claim. Continuation stays on the same job generation when finish is accepted.

## Picture

### PIC-01. Chat never stores a job image

- Origin. Pull request 95, and `contracts.md` lines 276 and 282.
- Status. Reproduced defect.
- Source. `bind_images` is defined at `backend/app/services/task_images.py` line 43. Production callers under `backend/app` do not call it. `backend/app/api/chat.py` lines 685 and 810 pass the bytes into the in-flight completion. `backend/app/tasks/job.py` line 399 reloads through `load_image_blocks`.
- Impact. A restarted job has no `task_images` row, so the model request has no image bytes.
- Reproduction. Source search. `test_chat_request_sends_png_to_the_chat_role_and_skips_ocr` expects the image count to stay unchanged. Not re-run in this pass.
- Evidence. Source anchor.
- Close. A message that binds to a job leaves a `ready` row, and a later `run_job` sends those bytes. A chat image that never binds to a job is still dropped.
- Unit. Call `bind_images` at the job bind. Order 6.

### PIC-02. JSON completions write no usage row

- Origin. Pull request 95 tradeoff.
- Status. Reproduced defect.
- Source. `complete_json` at `backend/app/core/llm.py` line 929 and `complete_json_object` at line 1060 return without `record_response_usage`. `complete_text` writes a row at line 891. `stream_chat` writes a row at line 758.
- Impact. A planner, router, or judge response leaves `llm_calls` unchanged.
- Reproduction. The function bodies were read. Not executed.
- Evidence. Source anchor.
- Close. One `LlmCall` after a stop finish. A missing count stays null. A provider error before a response writes no row.
- Unit. Call the existing writer from both JSON functions. Order 12.

### PIC-03. Tombstone rows stay after the blob is gone

- Origin. Pull request 95, and `contracts.md` line 284.
- Status. Architectural decision requiring parent design review.
- Source. `backend/app/services/task_images.py` lines 430-431 keep a row whose state is not `pending_delete`. The contract says a tombstone stays so a later put of that key is deleted.
- Impact. Every abandoned or terminal staged image leaves a permanent row.
- Reproduction. The contract sentence and the purge branch were read. Not executed.
- Evidence. Source anchor. The contract states this retention.
- Close. The parent confirms that sentence, or replaces it with a delete after the blob is gone.
- Unit. No code until that choice.

### PIC-04. A running staged upload is tombstoned after 10 minutes

- Origin. Pull request 95 tradeoff. The contract tombstone is the terminal transaction at `contracts.md` line 284.
- Status. Reproduced defect.
- Source. `STAGED_HOLD` is 10 minutes at `backend/app/services/task_images.py` line 20. `reconcile_task_images` calls abandon when the blob is absent after that hold, at lines 372-373. Abandon tombstones the row with no running check, at lines 313-314.
- Impact. A put that has not created the blob within 10 minutes loses the row while the task is still running. A late put is deleted on the next sweep.
- Reproduction. `test_late_put_after_terminal_or_expiry_is_deleted_by_the_next_sweep` encodes the current expiry. Not re-run.
- Evidence. Source anchor.
- Close. A non-terminal staged row with no blob is still `staged` after 10 minutes. A late put can become `ready`. The terminal transaction still tombstones a staged row.
- Unit. Use the terminal fence for a missing blob. Order 7.

### PIC-05. Alembic creates the tombstone check

- Origin. Hypothesis that only `init_db` relaxes the constraint.
- Status. Demonstrably already fixed.
- Source. `backend/alembic/versions/0018_picture.py` lines 19 and 72. `TASK_IMAGE_PURGE` includes `tombstone` at `backend/app/models/models.py` line 545.
- Impact. A database created by `alembic upgrade head` accepts `tombstone`.
- Reproduction. The revision was read. Not executed.
- Evidence. Source anchor.
- Close. None for a new upgrade.
- Unit. None.

### PIC-06. The picture model has no tombstone

- Origin. Pull request 95 verification note.
- Status. Verification gap.
- Source. `verification/picture/Picture.tla` line 28 limits the row to `none`, `ready`, `pending`, and `deleted`. `verification/picture/check.py` line 63 only requires the string `"tombstone"`.
- Impact. TLC can stay green while PIC-03 or PIC-04 changes.
- Reproduction. The state set was read. TLC was not re-run for the held model.
- Evidence. Source anchor.
- Close. The model includes `staged` and `tombstone`, and a break flag violates the fence.
- Unit. Extend `Picture.tla` after PIC-04.

### PIC-07. Picture CI exercises one break flag

- Origin. `.github/workflows/picture-tla.yml` lines 24-31.
- Status. Verification gap.
- Source. The workflow runs the held checker and `--break-zero` only.
- Impact. A regression of another picture invariant can pass this workflow.
- Reproduction. Executed `python3 verification/picture/check.py --break-ocr`. It exited 1 with `Invariant ImageStaysOnChatModel is violated.`
- Evidence. Bounded-model proof for that flag. The workflow omission is a source anchor.
- Close. The workflow requires each other break flag to exit 1.
- Unit. Order 13.

### PIC-08. No live provider call

- Origin. Pull request 95 verification.
- Status. Genuinely needs live acceptance.
- Source. Picture tests install a fake client, for example `backend/tests/test_picture.py` line 1027.
- Impact. The fake stream does not show a configured vision endpoint accepting the data URL.
- Reproduction. Not run. It needs a provider.
- Evidence. Deferred live.
- Close. One real chat image returns a model reply and one `LlmCall` row.
- Unit. A live script after PIC-01 and PIC-02. No code choice.

## Talk

### TALK-01. Talk.tla is on CI

- Origin. Hypothesis that `Talk.tla` was unwired.
- Status. Demonstrably already fixed.
- Source. `.github/workflows/talk-tla.yml` line 24.
- Impact. None at this head.
- Reproduction. Executed `python3 verification/talk/check.py`. It printed the four invariants and exited 0.
- Evidence. Bounded-model proof of those invariants. The checker anchors are still source strings.
- Close. None.
- Unit. None.

### TALK-02. run_job is no longer the pre-loop stub

- Origin. Pull request 96 said `run_job` stored the goal and returned.
- Status. Demonstrably already fixed.
- Source. `backend/app/tasks/job.py` lines 166-176 adopt, and lines 206-215 call `_drive` when the loop is enabled.
- Impact. An enabled loop runs tool steps. With no API key and no test executor, the function still stops after the start message. That is the keyless case in LOOP-05.
- Reproduction. The function was read. Not executed.
- Evidence. Source anchor.
- Close. None for the stub.
- Unit. None.

### TALK-03. The composer void and the server void use different keep rules

- Origin. Hypothesis, rechecked after resume.
- Status. Reproduced defect.
- Source. `frontend/src/services/chatStream.ts` lines 69-74 cancel a draft unless `mode` is `suggest` or `task_kind` is `job`. `backend/app/services/calendar.py` lines 1416-1417 cancel a draft unless `mode` is `suggest` or the card tokens belong to the kept job or a `waiting_person` job. The chat SSE card at `backend/app/api/chat.py` lines 708-709 adds `task_generation` and does not add `task_kind`.
- Impact. The next send can cancel a stream-delivered job draft in the client while the server keeps it. A hydrated job card can stay locally when the server voids it.
- Reproduction. The two functions were read. No frontend test mentions `voidOpenDrafts`.
- Evidence. Source anchor.
- Close. After a send, the client phase matches `void_draft_schedule_cards` for a stream card with no `task_kind`, a waiting job card, and a running job card that is not the keep target.
- Unit. Put `task_kind` on the SSE card and void with the server token rule. Order 8.

### TALK-04. apply_intent always sets continuation_due

- Origin. Hypothesis, rechecked against `contracts.md` lines 267-268.
- Status. Reproduced defect.
- Source. `backend/app/services/task_run.py` lines 548-559. A `waiting_resource` job returns to `running`. Every open job sets `continuation_due` true. There is no read of the latest step and no `results_ready` write.
- Impact. A supplement during an open tool call marks the job due and clears the holder. The idle reserve can publish another `run_job`. That is the trigger for LOOP-03.
- Reproduction. The assignment was read. `backend/tests/test_talk.py` line 344 expects the flag for a job with no tool step, which matches the contract. The open-call case has no test.
- Evidence. Source anchor.
- Close. A latest step with a non-terminal call leaves `continuation_due` false. A step with no non-terminal call sets `continuation_due`, and sets `results_ready` when that step exists.
- Unit. That branch inside `apply_intent`. Order 3, after LOOP-03.

### TALK-05. Disconnect coverage

- Origin. Hypothesis that disconnect tests were missing. Reverified after resume.
- Status. Demonstrably already fixed for the events route. The frontend half is a verification gap.
- Source. `backend/tests/test_resume.py` lines 823, 897, and 943 expect the job to stay `running` after `http.disconnect` on the events stream. Chat disconnect still calls `task_run.stop` at `backend/app/api/chat.py` line 690. `frontend/tests` has no `voidOpenDrafts` test.
- Impact. The events behavior has a test. The composer abort and the local void do not.
- Reproduction. The tests were read. Not executed.
- Evidence. Source anchor.
- Close. A frontend test covers TALK-03. The events tests stay.
- Unit. Fold the frontend test into the TALK-03 unit.

## Watch

### WATCH-01. CI runs one of the three watch breaks

- Origin. Pull request 97 said all three flags exited 1 locally. `.github/workflows/watch-tla.yml` lines 24-31 run `--break-stop` only.
- Status. Verification gap.
- Source. `verification/watch/check.py` defines `break_double` and `break_wait`.
- Impact. A double notice or a waiting-row mutation can pass this workflow.
- Reproduction. Executed. `--break-double` exited 1 with `Invariant OneNoticePerProgressStamp is violated.` `--break-wait` exited 1 with `Invariant WaitingLeftAlone is violated.`
- Evidence. Bounded-model proof that those flags still fail.
- Close. The workflow requires both flags to exit 1.
- Unit. Order 13.

### WATCH-02. createdOld is a constant true

- Origin. Hypothesis.
- Status. Verification gap.
- Source. `verification/watch/check.py` line 97 writes `createdOld = TRUE`. `verification/watch/Watch.tla` line 122 puts that constant in `TypeOK`. `backend/app/tasks/watchdog.py` does not read `created_at`. `contracts.md` line 313 says a null sample is not filled from `created_at`.
- Impact. The model has no young job, so a sweep that stopped only old jobs cannot be distinguished from a sweep that stops every job. The implementation leaves the old job running.
- Reproduction. Executed a temp config with `createdOld = FALSE`. TLC exited 12 because `TypeOK` failed on the initial state. The watchdog test was not executed.
- Evidence. Bounded-model proof that the false constant is rejected. The product behavior is a source anchor.
- Close. `createdOld` can be false in a typed state, and the age invariant fails only for a broken age rule.
- Unit. A model state for age. The implementation stays as it is.

## Loop

### LOOP-01. The model has no crash after a saved call

- Origin. `verification/loop/Loop.tla`.
- Status. Verification gap.
- Source. `verification/loop/Loop.tla` lines 116-128. The `Crash` action is not a crash after the tool rows commit. `save_turn` commits pending calls at `backend/app/services/loop_round.py` lines 290-344.
- Impact. A green TLC run does not show that a crash after the tool rows commit still runs that saved call once.
- Reproduction. Executed for the implementation. A pending row invoked once, then the model ran. The assertion was `order[0] == "invoke"` and `order.count("invoke") == 1`.
- Evidence. Behavior test for the saved pending call. Source anchor for the spec.
- Close. The model has a crash after the save, and the saved call is invoked once before the next model.
- Unit. The TLA change. The resume path already invokes a saved pending call through the compare-and-swap.

### LOOP-02. The loop checker anchors substrings

- Origin. `verification/loop/check.py` lines 60-71.
- Status. Verification gap.
- Source. The checker slices source text. `open_cards` contains `continuation_due` because it sets the flag false. The confirm path sets it true later.
- Impact. The workflow can stay green when the save-before-execute order changes.
- Reproduction. The checker and the assignments were read. TLC was not re-run.
- Evidence. Source anchor.
- Close. The checker fails when the confirm assignment or the save-before-execute order breaks, or the substring gate is removed and the behavior tests remain.
- Unit. Order 14.

### LOOP-03. A running call can be invoked again

- Origin. Hypothesis that concurrent `_drive` with the same token could invoke twice. Rechecked after resume.
- Status. Reproduced defect.
- Source. `begin_read` returns `invoke` for `CallState.RUNNING` with no compare-and-swap at `backend/app/services/loop_round.py` lines 362-365. `_cas_running` matches only `pending` at lines 1191-1200. `adopt` requires a null `exec_token` in `backend/app/services/resume.py`. `run_job` returns before `_drive` when adopt matches zero rows, at `backend/app/tasks/job.py` lines 166-176.
- Impact. A second `run_job` does not enter `_drive` while the first holder is still adopted. A later holder can. That happens when the hold expires during `_invoke`, or when TALK-04 clears the token. `begin_read` then returns `invoke` again, so a web call can POST twice. The first holder may already be inside the provider call.
- Reproduction. Executed two `_one_pass` calls with the same token and state `running`. The result was `["call-a", "call-a"]`. Two sequential `begin_read` calls both returned `invoke`. Two `_one_pass` calls on one pending row invoked once. The adopt SQL was read and not re-run.
- Evidence. Behavior test of `begin_read` and `_one_pass`. The adopt fence is a source anchor.
- Close. The next unit has to prove current execution ownership with a behavior test. A second driver must not enter the provider call while the first call can still be executing. A missing holder, or an expired lease, does not prove the earlier call has stopped. The unit has to prevent that overlap and still recover a call whose process died. A compare-and-swap that only sets `running` back to `pending` is not a demonstrated solution. A repeated read and a repeated write are different, and the unit has to name which tool semantics it covers. A pending call still invokes once.
- Unit. Not this pull request. Order 2. The next unit starts with a mini-plan and a failing behavior reproduction. It keeps the crash recovery in LOOP-01.

### LOOP-04. A missing key writes 已开始 without owning the token

- Origin. Hypothesis.
- Status. Demonstrably already fixed.
- Source. `backend/app/tasks/job.py` lines 64-77 and 127-143. The update matches `exec_token`.
- Impact. A worker that does not own the token leaves no start line.
- Reproduction. Executed. `_start` with token `nobody` returned `None` and the note list stayed empty. After the owner token was cleared, `_start` with the old token returned `None` and the notes stayed `["已开始：alpha"]`.
- Evidence. Behavior test.
- Close. None.
- Unit. None.

### LOOP-05. A missing key still says 已开始 and leaves the job running

- Origin. Pull request 98 tradeoff.
- Status. Architectural decision requiring parent design review.
- Source. `backend/app/tasks/job.py` lines 113-114 and 193-207.
- Impact. With no key and no test executor, the person sees `已开始：alpha` and the job stays `running` with a live token. No tool runs.
- Reproduction. Executed. The note list was `["已开始：alpha"]`, status was `running`, and `exec_token` was set.
- Evidence. Behavior test.
- Close. The parent chooses whether a keyless job waits without that line, or stops.
- Unit. The branch after `_start`. No code until that choice.

## Search

### SEARCH-01. A chat web turn posts the raw user message

- Origin. Pull request 99 blast radius.
- Status. Architectural decision requiring parent design review.
- Source. `backend/app/agents/workflow.py` line 440 sets `tool_args` to `{"query": msg}`. `execute_web` passes that query to `deepseek_search`. The tool schema limit is 128 characters at `backend/app/agents/tools/web.py` lines 9-15.
- Impact. The router web turn searches the whole user message. Past 128 characters the person gets a tool error and no sources.
- Reproduction. Executed. `run_agent_workflow` with the web tool posted `query` `today weather`. A 129-character message returned the 128-character tool error and did not post.
- Evidence. Behavior test.
- Close. The chat path hands `execute_web` a bounded query. The parent chooses truncation or the refusal sentence.
- Unit. The `tool_args` assignment, after that choice.

### SEARCH-02. An invalid ori_question cancels a valid query

- Origin. Pull request 99. `ori_question` is sent only when the model provides a valid one.
- Status. Reproduced defect.
- Source. `backend/app/services/deepseek_search.py` lines 113-114 return a tool error before the post when `ori_question` is not bounded.
- Impact. A valid query with a 129-character `ori_question` does not search.
- Reproduction. Executed. `deepseek_search("weather", ori_question="x" * 129)` returned a `ToolError` whose message contains `原问题`. `httpx.Client` was not constructed.
- Evidence. Behavior test.
- Close. Omit the invalid field and POST the query.
- Unit. Those two lines. Order 9.

### SEARCH-03. The executor model can answer the search round

- Origin. Pull request 99. The description says the answer uses the chat role's model.
- Status. Architectural decision requiring parent design review.
- Source. `resolve_executor` at `backend/app/core/llm.py` lines 260-271 uses `EXECUTOR_MODEL` when it is set, and otherwise copies the chat model. `verification/search/check.py` does not read `resolve_executor`.
- Impact. With `EXECUTOR_MODEL` set, the round's completion uses that model.
- Reproduction. Executed. `resolve_executor().model` was `executor-only`. `resolve(Role.CHAT).model` was `deepseek-flash`.
- Evidence. Behavior test.
- Close. The parent decides whether the answer is `Role.CHAT` or the executor.
- Unit. The `job` branch of `stream_chat` and `complete_text`, after that choice.

### SEARCH-04. The search proof is one step plus string anchors

- Origin. `verification/search/Search.tla` and `verification/search/check.py`.
- Status. Verification gap.
- Source. The checker requires `deepseek_search(` inside `execute_web`. It does not cover the chat query, `ori_question`, the executor model, or a second post. `search_api` still exists in `backend/app/services/web_search.py`. A search of `backend/app` found no caller of `search_planned` or `search_api` outside that file.
- Impact. The workflow can stay green while SEARCH-01 or SEARCH-02 regresses.
- Reproduction. The spec and the checker were read. No Zhipu call was made.
- Evidence. Source anchor.
- Close. The checker fails when the executed search cases break.
- Unit. The checker, after SEARCH-02. Deleting `search_api` waits on the tests that import it.

## Mail

The acceptance repair and the seal order are closed on this tree. `mail_entry.py` lines 435-454 repair a stored provider id. A bound transport looks up first. No transport calls `_write_acceptance` directly. Lines 419-426 call `_seal` and then `db.rollback()` only when the receipt is already `external_success`. `commit_send` returns before the provider call when `arm_remote_claim` returns false, at lines 337-339. Those branches were read. The named tests were not re-run.

### MAIL-01. The send checker misses reconcile

- Origin. Pull request 100. `verification/mail-entry/check.py` lines 88-104.
- Status. Verification gap.
- Source. The anchors take the first `_seal(` between `commit_send` and `_active_mail_claim`. The reconcile `_seal` at `mail_entry.py` line 419 is a later occurrence. `MailSend.tla` has no lookup, acceptance row, or receipt state.
- Impact. `mail-tla.yml` can stay green while reconcile skips a repair.
- Reproduction. The anchors and the reconcile function were read. Not executed.
- Evidence. Source anchor.
- Close. The checker fails if the no-transport branch repairs without a stored provider id, or if the bound-transport branch writes an acceptance when lookup is not `accepted`.
- Unit. Order 14. Leave the boolean model for a later unit.

### MAIL-02. No mailbox has accepted a send

- Origin. Pull request 100, and `contracts.md` line 305.
- Status. Genuinely needs live acceptance.
- Source. `mail_entry.py` lines 326-329 refuse an empty transport before consume. The checker forbids `gmail` inside `mail_entry.py`.
- Impact. Automated runs prove the empty-transport seam and a fake transport. They do not prove a provider id against a mailbox.
- Reproduction. Not run. A real send is not authorized.
- Evidence. Deferred live.
- Close. One chosen transport, one accepted send, one lost response that does not send again, and one repair that reads the stored provider id.
- Unit. Selecting that transport is an architectural choice. No send from this work.

## Calendar

Create receipt tests exist for a stored event id and for a lookup miss. They were read and not re-run. They do not cover a failed arm.

### CAL-01. A failed arm still calls Google

- Origin. `contracts.md` line 270. Mail honors the same boolean. Calendar did not, on `1f28d4fd`.
- Status. Fixed in this branch, pending parent acceptance.
- Source. `_arm_user_write` at `backend/app/services/calendar_google.py` line 1225 returns false when a confirm token is set and `arm_remote_claim` does not commit, and it rolls the session back. `_refuse_unarmed` at line 1240 raises the closed write error. Insert, `_patch_google_event` at line 1187, and `_delete_google_event_required` at line 1458 stop before the Google call. `_restore_patch` at line 1214 patches with arming turned off. The handler answers that closed error with 409 and does not reopen the claim. A job-linked mismatch still rolls back and returns false at `backend/app/services/schedule_confirm.py` lines 631-633.
- Impact. On the unfixed tree, the provider call is issued after the arm commit is rolled back. The response can be the event card with the claim still `open` and no receipt. A later confirm can write again.
- Reproduction. With `backend/app/api/calendar.py` and `backend/app/services/calendar_google.py` restored from `1f28d4fd`, the create, patch, and delete tests returned 200. The patch card title was the new title. The delete card status was `cancelled`. The create card `external_id` was `evt-should-not-exist`. `test_local_patch_commit_failure_restores_the_google_event` passed on that tree. After those two files were restored to this branch, `tests/test_calendar_create_receipt.py` plus `test_local_insert_failure_deletes_google_event`, `test_duplicate_external_id_deletes_google_event_and_fails_confirm`, and `test_update_patches_google_before_local_and_failure_keeps_row` passed, 14 tests.
- Evidence. Behavior test. The patch and delete cases go through `PATCH /api/calendar/{id}` and `DELETE /api/calendar/{id}` with a real job-linked claim. The arm helper is the production function. The test records its false result so a fingerprint rejection cannot pass in its place.
- Close. When `arm_remote_claim` returns false, insert, patch, and delete make no provider call and the handler returns 409 with the closed sentence. The claim stays `open` with an empty remote id, and the local row does not change. A connected update whose arm commits still patches. Compensate patch and compensate delete still run after a local commit failure.
- Unit. This pull request. See First unit. Not closed on `release` until the parent accepts it.

### CAL-02. Patch, delete, and RSVP have no receipt repair

- Origin. `contracts.md` line 303.
- Status. Architectural decision requiring parent design review for the success receipt. The safe no-repeat behavior is a later test, not a new write.
- Source. Only create passes `adopt`, at `backend/app/api/calendar.py` line 200. Patch, delete, and RSVP call `_consume_schedule_claim` without it. An `unknown` claim rolls back and raises at `schedule_confirm.py` lines 513-515. Success on those routes calls `_seal_schedule_write`, which is `finish_confirm` and no `cal:{token}` receipt.
- Impact. A second confirm of an unknown patch, delete, or RSVP returns 409 and does not read the provider. The claim stays unknown. Nothing here shows the provider write succeeded.
- Reproduction. The handlers were read. Not executed.
- Evidence. Source anchor.
- Close. A second confirm does not repeat the provider write. A success receipt is filed only after a read that shows the intended state. Until that read exists, the claim stays unknown.
- Unit. The read needs a parent choice. Do not invent provider success.

### CAL-03. The create checker ignores the other writes

- Origin. `verification/calendar/check.py` and `CalendarCreate.tla`.
- Status. Verification gap.
- Source. The anchors inspect `record_create_success`. The model has no patch, delete, RSVP, or failed-arm state.
- Impact. Create invariants can hold while CAL-01 or CAL-02 remains.
- Reproduction. The checker was read. Not executed.
- Evidence. Source anchor.
- Close. The checker fails if patch, delete, or RSVP call `record_create_success`, and fails if they stop calling `_seal_schedule_write` without a named replacement.
- Unit. Order 14, after CAL-01.

## Errand

### ERR-01. A blank order id counts as placed

- Origin. Pull request 102 tradeoff. The tradeoff text is still the code.
- Status. Reproduced defect.
- Source. `interpret` treats any non-empty string as present at `backend/app/services/errands.py` lines 360 and 371. `decide` uses `if order_id` at `services/errand-compose/decide.py` line 41.
- Impact. A whitespace order id with a matching echo is `placed` and can be stored. A pay click and a blank order id become `placed` because the public payload supplies the armed key.
- Reproduction. Executed.

```text
spaces kind=placed order_id=' ' named=True truthy=True
empty kind=unknown order_id='' named=False truthy=False
DECIDE outcome=placed order_id=' '
PUBLIC idempotency_key=armed-1 order_id=' ' pay absent login absent
CHAIN kind=placed order_id=' ' named=True
already-spaces kind=already order_id=' \t ' named=True truthy=True
```

- Evidence. Behavior test. The save gate was not executed.
- Close. `interpret` and `decide` return unknown for an order id of spaces or tabs. A matching echo does not change that. The save gate does not insert that id.
- Unit. Strip the order id in both functions. Order 4.

### ERR-02. The errand checker never runs those rules

- Origin. `verification/errand-place/check.py` and `ErrandEcho.tla`.
- Status. Verification gap.
- Source. The anchors do not name `interpret` or `_public`. `ErrandEcho.tla` stores `order` as a boolean. A whitespace id satisfies `order = TRUE`. `test_empty_echo_is_not_placed` covers `""` only.
- Impact. `errand-tla.yml` stays green for the blank-id result above.
- Reproduction. The blank-id run above, plus the checker source. TLC was not run.
- Evidence. Behavior test for the miss. Source anchor for the checker.
- Close. `check.py` fails when `interpret` returns placed for a whitespace order id, and fails when `_public` leaks `pay` or `login`.
- Unit. Call those functions from the checker in the same unit as ERR-01, so the checker fails before the strip and passes after it.

## Devices

### DEV-01. The server token setting is unread

- Origin. Pull request 103. `backend/app/core/config.py` line 186.
- Status. Demonstrably already fixed for authentication.
- Source. `recognize_package` reads the device hash. The settings field is unused. The same environment name is the package header at `desktop/main.js` line 139.
- Impact. None on accept or reject.
- Reproduction. `test_package_word_is_not_a_proof` is the counterexample. Not re-run.
- Evidence. Source anchor.
- Close. Delete the unread field, or leave it. Deletion is optional cleanup and is not in the safety order.
- Unit. None required.

### DEV-02. The hash is compared with itself

- Origin. Pull request 103. `backend/app/agents/harness/desktop_channel.py` lines 47 and 64.
- Status. Demonstrably already fixed for authentication. The compare is dead code.
- Source. The query keeps `token_hash == digest`. `hmac.compare_digest` then compares that stored hash with the same digest.
- Impact. None on who is accepted. The devices checker only requires the string `hmac.compare_digest`, so the tautology stays green.
- Reproduction. The comparison was executed. A selected hash is not rejected.
- Evidence. Source anchor.
- Close. Delete the compare, or compare a candidate the query has not already matched. Optional cleanup.
- Unit. None required.

### DEV-03. The device model has no revoked token

- Origin. `verification/devices/DesktopDevices.tla`.
- Status. Verification gap.
- Source. `Enroll` requires `stored = "absent"`. The model has no step for a revoked token that is still on disk.
- Impact. TLC does not cover DEV-06.
- Reproduction. The spec was read. TLC was not run.
- Evidence. Bounded-model proof is the held model. This gap is a source anchor.
- Close. Keep the behavior tests as the auth proof.
- Unit. None until DEV-06 is chosen.

### DEV-04. A website session can enroll

- Origin. Pull request 103. `backend/app/api/desktop.py` enroll route.
- Status. Demonstrably already fixed as the enrollment path. It is not a defect.
- Source. The route uses the logged-in user. The package enrolls with that session cookie at `desktop/main.js` line 93.
- Impact. Denying that session would remove enrollment.
- Reproduction. The route and the client were read. HTTP was not executed.
- Evidence. Source anchor.
- Close. None. Do not add a second authenticator.
- Unit. None.

### DEV-05. A blank device label is stored

- Origin. Pull request 103.
- Status. Reproduced defect.
- Source. `DeviceEnroll.label` is a bare `str` at `backend/app/api/desktop.py` line 30. `enroll` stores that string at `backend/app/services/desktop_devices.py` line 23.
- Impact. An empty name appears in the device list and on a card.
- Reproduction. An AST check showed no length constraint. FastAPI was not booted.
- Evidence. Source anchor.
- Close. An empty or whitespace label is rejected. The package can still send `os.hostname()`.
- Unit. One validation constraint. Order 11.

### DEV-06. A revoked package keeps the old token

- Origin. Pull request 103.
- Status. Architectural decision requiring parent design review.
- Source. A revoked hash returns no proof at `desktop_channel.py` lines 47-50, and the route answers 403. `enrollIfNeeded` returns when a token file exists, at `desktop/main.js` lines 120-121. Nothing deletes that file.
- Impact. Propose keeps failing until someone removes the token file by hand.
- Reproduction. Executed the `enrollIfNeeded` source with token `revoked-secret`. Fetch count was 0, and the token stayed. Electron was not run.
- Evidence. Behavior test of that function.
- Close. The parent chooses whether every 403 drops the token. The 403 body is the same denial for every bad proof. After that choice, a package 403 can drop the stored token and call the existing enroll route once.
- Unit. One client change after that choice. Do not broaden auth.

## Resume

The lease fix holds. `reopen_known_rejection` clears `confirm_until` at `backend/app/services/schedule_confirm.py` line 670. Both arm paths write `confirm_until=None` at lines 629 and 642. `_owner_gone` at `backend/app/services/loop_round.py` lines 854-861 treats a null lease as still live. `reconcile_parked_writes` skips a desktop claim until it is `written`, and skips an `unknown` claim whose lease is still in the future, at lines 907-910. These lines were read. The Postgres deadlock tests were not re-run.

Claim, then job, then cursor still holds on `open_cards`. `lock_open_claims` runs before the job lock. The cursor is published in the job commit. That path was read.

### RES-01. The resume model is coarse

- Origin. Pull request 104. `verification/resume/Resume.tla`.
- Status. Verification gap.
- Source. Jobs are `1..2` and `tick` is `0..1`. The spec has no `confirm_until`, no pulse, and no desktop finish. `verification/resume/check.py` anchors SQL text.
- Impact. TLC does not reopen the lease or the lock order, and it also does not prove them.
- Reproduction. The spec was read. TLC was not run.
- Evidence. Bounded-model proof is bounded. The lease proof remains the Python tests.
- Close. None for the lease. A richer model is optional.
- Unit. None.

### RES-02. A mixed-phase follow-up card posts settled rows

- Origin. Pull request 104. `frontend/src/components/FollowupCard.tsx`.
- Status. Reproduced defect.
- Source. The confirm filter at line 125 is `ticked.has(action.id) && action.start_at`. It ignores `phase`. `claimFollowRows` at `frontend/src/utils/followMail.ts` lines 21-30 also ignores phase. `consume_schedule_claim` rejects a claim that is not open, at `schedule_confirm.py` lines 513-515.
- Impact. One click posts the settled row as well. The server rejects that second consume. The visible result is `有待办没有加进日历。`
- Reproduction. Executed the filter. One draft action and one confirmed action produced `draft,done`. The calendar HTTP call was not executed.
- Evidence. Behavior test of the filter. The reject is a source anchor.
- Close. The click posts only a draft action, and a settled checkbox is disabled.
- Unit. One component change. Order 10.

## Desktop

These contracts are still in the code and are not residuals. Web login denies a local tool. `perform` runs only for `kind == "armed"` on the enrolled device. The fingerprint binds device, path, and args. A second commit returns `unknown` and does not run again. `finish_local` continues that generation, and a second finish does not enqueue again. The preserving tests are `backend/tests/test_desktop_job.py`.

### DESK-01. The client fills a missing offer id

- Origin. Pull request 105. `desktop/confirm_local.py` lines 24-28.
- Status. Reproduced defect.
- Source. `_device_on` returns the enrolled id unless `device_id` is an int. `parse_local_action` rejects a non-int at `backend/app/services/local_action.py` lines 99-100 with `缺少设备`. `_require_proof` rejects a mismatched int at line 115.
- Impact. An offer with no id is sent as the enrolled device, so the server never sees the omission. A wrong int is sent as itself and does not run.
- Reproduction. Executed `_device_on({}, 7) == 7` and `_device_on({"device_id": "7"}, 7) == 7`. `parse_local_action(..., None)` raised `LocalClosed` 422.
- Evidence. Behavior test.
- Close. The offer value is sent unchanged. A missing id is rejected and `perform` is not called.
- Unit. One client edit. Order 5.

### DESK-02. No HTTP test for a missing device id

- Origin. Pull request 105.
- Status. Verification gap.
- Source. `test_spoofed_desktop_header_does_not_arm` omits `device_id` and gets 403 because the header is absent. The worker rejects a tool call with no device before HTTP.
- Impact. A package request that omits `device_id` and presents a real package header is not asserted.
- Reproduction. FastAPI was not installed during the inventory pass.
- Evidence. Source anchor.
- Close. One authenticated post that omits `device_id` expects 422 and creates no claim.
- Unit. With DESK-01.

### DESK-03. write_parts raises ValueError for a bad device

- Origin. Pull request 105. `schedule_confirm.py` line 1113.
- Status. Demonstrably already fixed on the worker and HTTP paths.
- Source. `_device_id(None)` raises `ValueError`. The worker rejects earlier at `job.py`. `open_cards` catches `ValueError`.
- Impact. The helper error is not the response a caller sees.
- Reproduction. Executed `_device_id(None)`. It raised `ValueError` with `device_id must be an int`.
- Evidence. Behavior test of the helper.
- Close. None.
- Unit. None.

### DESK-04. The desktop checker is coarse

- Origin. Pull request 105. `verification/desktop/check.py` and `Desktop.tla`.
- Status. Verification gap.
- Source. The checker looks for substrings. `verification/desktop-open/check.py` is not named by a workflow.
- Impact. TLC does not replace `backend/tests/test_desktop_job.py`.
- Reproduction. TLC was not run.
- Evidence. Source anchor.
- Close. Keep the behavior tests as the contract proof.
- Unit. None.

### DESK-05. Lose does not start from armed

- Origin. Pull request 105. `verification/desktop/Desktop.tla` line 53.
- Status. Verification gap.
- Source. `Lose` is enabled from `start`. The model never reaches a performed-then-lost state.
- Impact. The model does not state DESK-06.
- Reproduction. The spec was read. TLC was not run.
- Evidence. Bounded-model proof is the held model. This gap is a source anchor.
- Close. None by itself. DESK-06 is the product consequence.
- Unit. None until the recovery design is approved.

### DESK-06. A package can run the action and never finish

- Origin. Pull request 105, also named on the pull request 104 decision.
- Status. Architectural decision requiring parent design review.
- Source. `confirm_local` runs `perform` and then posts finish, at `desktop/confirm_local.py` lines 47-60. `lookup_desktop_write` leaves `waiting_person` while the claim is not `written`, at `loop_round.py` line 684. Reconcile skips that desktop claim at line 907. `main.js` persists only the package token and the device id.
- Impact. A completed local action leaves the job waiting. The next click does not report it and does not run it again.
- Reproduction. Executed a fake transport. The first finish raised. The second commit returned `kind` `unknown`. `opened` stayed `["/tmp/fixture.txt"]`, the second call returned false, and finish was posted once. `xdg-open` was not run.
- Evidence. Behavior test.
- Close. The parent accepts a design, then a later unit implements it. The proposal in Desktop recovery design replaces the earlier step that let the person confirm the same claim again. An `armed` record is unknown. The same claim does not call `perform` again. An `executed` record retries finish only. A new action uses a new claim and says the original outcome is unknown. That design does not close the operating-system call gap and does not prove exactly-once side effects.
- Unit. Parent review. No code in this pull request.

### DESK-07. No live enrolled package

- Origin. The pull request 105 decision.
- Status. Genuinely needs live acceptance.
- Source. No enrolled package was run.
- Impact. The automated package tests do not show a real enroll, confirm, finish, and revoke.
- Reproduction. Not run.
- Evidence. Deferred live.
- Close. One real enroll, confirm, finish, and revoke. That run does not close DESK-06.
- Unit. None in this repository change.
