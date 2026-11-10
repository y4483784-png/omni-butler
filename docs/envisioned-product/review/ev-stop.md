# ev-stop review

Stopping a chat generation now means the `TaskRun` row is `stopped`. A model request that has not left yet is not sent. A chunk that comes back after that is not written into the current bubble. An event that was already confirmed stays. A stopped generation does not call the model to write long-term memory.

The button still aborts the browser fetch. The server treats that drop, and a cancelled handler, as `task_run.stop`. Cancelling the outer asyncio task does not stop the worker thread. The row does.

## What the code does

`generation_stop.bind` stores the user and the generation for the request task. `asyncio.to_thread` and the later memory threads see that same key. `is_stopped` reads the row in a new session every time. It does not cache the bool. Unbound callers, including eval scripts that never bind, still send.

`stream_chat` and the sync completions read the flag before the mock and before every `chat.completions.create`, including the `stream_options` retry. A stopped call raises `GenerationStopped`. That type extends `BaseException`, so an `except Exception` in advice or repair does not turn it into an empty answer or a second model call.

`answer_node` checks again after `complete_text` returns and before `ground_and_repair_answer`. `execution_denial` loads the row with `task_run.load_turn` because the workflow session can still show the row as running after stop has committed.

In `chat.py`, a stopped generation skips `task_run.finish`, `_remember_turn_isolated`, the context refresh, and the title call. Text already in the accumulator can stay in the session transcript. A turn that finishes as `completed` still uses the existing remember path. Building the memory ledger for that turn is ev-memory.

Stop does not delete a calendar event, unsend mail, or refund money. A tool that has already entered `execute` can still commit its row.

## Upstream

Reread at the pinned commits. Copied from DS-Bot `71d344bf193f3f18185f25de3d8e5c483046910a`: D-CANCEL drops a queued completion that has not started, and D-LATE (`turns.js` 695-708) suppresses a late result so it is not applied. Copied from OpenMausBot `083fd7b51405e6d0c01e189b2f65ab0322509b5a`: O-CANCEL (`client.ts` 259-265) rechecks the activity before continuing, and O-HTTP (`client.ts` 335-401) checks immediately before the outbound call. Not copied: a shell permission catalog, full pause or redirect, an AbortSignal as the product stop, group chat, or Khoj. G-SHELL says a full streaming interrupt is not proven. This knife only stops a request that has not left, and it drops a late chunk. It does not implement async ReAct, and it does not move confirm back into the shell.

## Model check

`python verification/stop-before-send/check.py` exits 0. The rules that hold are `TypeOK`, `NoSendAfterStop`, `NoLateChunk`, `NoMemoryWhenStopped`, and `CalendarStays`. TLC v1.8.0, the pinned jar.

`--break-send` exits 1 and violates `NoSendAfterStop`. `--break-late` exits 1 and violates `NoLateChunk`. `--break-memory` exits 1 and violates `NoMemoryWhenStopped`. `--break-calendar` exits 1 and violates `CalendarStays`.

The model is one generation and one request. A request that is already `flying` may stay flying after stop. That is the in-flight HTTP call, which this knife cannot unsend. The model does not include the provider network.

A unit mutation that appends the late chunk before the stop break fails `test_late_chunk_stays_out_of_the_reply` at the SSE assertion. Exit 1. The restored code passes that test.

## Tests

From `backend`, with `DATABASE_URL=sqlite+pysqlite:///./ci-eval.db`, `REDIS_ENABLED=false`, `CELERY_TASK_ALWAYS_EAGER=true`, `SANDBOX_RUNNER_URL` empty, `SECRET_KEY=test-secret-key`, and `ADMIN_PASSWORD=test-admin-pass`. The numbers below are this machine, Python 3.12.3, pytest 8.4.2. The eval workflow also runs `python -m pytest tests -q` on Python 3.11 after a push. That job is not the evidence here. The paths are the pytest file paths. `BindingClient` is not used for `/api/chat`.

`python -m pytest tests/test_stop_before_send.py tests/test_chat_sse_routing.py -q` exits 0. 16 tests. The stop file covers a stop that lands after the slot wait and before `create`, an unbound send, a stopped mock, a late chunk kept out of the SSE body and the saved message, an unchanged calendar count, no memory and no memory model call, and a gateway deny after stop commits on another session.

`python -m pytest tests -q` exits 0. 796 passed, 1 xfailed, 6 skipped.

Frontend `npm test` exits 0. 30 passed.

`verification/task-run/check.py`, `verification/schedule-confirm/check.py` including the continuation, `verification/followup-confirm/check.py`, `verification/prep-readonly/check.py`, and `verification/unknown-write/check.py` exit 0. The unknown-write anchor now requires `turn.status == "stopped"` and `task_run.load_turn`. The TLC invariants are unchanged.

## Perf

The number is wall-clock seconds from `date +%s.%N` around one `pytest -q` of one file. Fresh sqlite each run. Same boot-recipe env. The machine has 4 cores. Trunk is release `1e89297976ef66883202503d99bca3166d61005e`. Head is this tree. Trunk again is that same release, in a separate worktree. A `--durations` run is a profile, not the gate.

Trunk load at the first run was 0.07. Head and the second trunk ran later at load 0.04.

- Trunk `tests/test_chat_sse_routing.py`, first run: 5.237 seconds, exit 0, 9 passed.
- Head `tests/test_chat_sse_routing.py`: 4.338 seconds, exit 0, 9 passed.
- Trunk `tests/test_chat_sse_routing.py`, second run: 4.444 seconds, exit 0, 9 passed.
- Head `tests/test_stop_before_send.py`: 2.865 seconds, exit 0, 7 passed.

The gate is head of the routing file under 60 seconds, and the new file under 30 seconds. Both hold. The slowest routing setup is about 1.04 seconds and the calls are under a tenth of a second, so the number is pytest startup plus sqlite. The runs are one each. The fail line is 60 seconds.

## Flowchart

The readable chart is `ev-stop-flowchart.png` beside this file. The main path goes down. Exception boxes sit to the right and do not rejoin. The blank node in the source is a rank spacer so the last two exceptions stay on the right. It is not a product step. The formal state diagram is `ev-stop-evidence/statechart.png`. They are different pictures.

## Carried from ev-plug

These are not closed and not passed. None of them blocks the stop checks in this knife.

1. An empty 403 is still a definite rejection. If that response had stored an event, a retry could insert a second one. Wait for a captured 403 body with an event id. Stop does not retry a calendar insert.
2. `FollowupCard` still reads the root phase. After a refresh the button can look clickable. The server rejects a second insert.
3. A crash after the arm commit and before the request leaves stays `unknown`. Another click cannot resend. That is the conservative choice.
4. Update, delete, and RSVP after `unknown` do not reconcile and seal the way create does.
5. There is still no GitHub Actions workflow for UnknownWrite.
6. Live lanes and live screenshots stay deferred until the implementation knives are done.

## Deferred live lanes

Not run. Not passed.

1. Trunk and head finish with no stop, on a running product.
2. Stop during planning sends no model request and no memory request.
3. Stop after a confirmed calendar event keeps the event.
4. A late chunk is not appended.
5. A new message after stop gets a new generation.
6. A second user cannot stop the first user's turn.
7. A second stop stays stopped.
8. The mock stream also stops.
9. Reload after stop does not resume the turn.
10. No WeChat, Telegram, Slack, or Khoj route on a running product.

Not produced, because they need a live click: `ev-stop-review-stop.png` and `ev-stop-review.mp4`. An offline diff can show those routes are absent. That is not lane 10.

## Limits that stay open

A `create` already inside the HTTP client is not unsent. Later chunks are dropped. The planning disconnect records stop and returns without waiting for the worker. If that thread later raises, the cancelled task may log an unretrieved exception. That log was not seen in the unit run. A non-list allow payload is treated as an empty list and denied as absent, which is a different sentence from the old invalid-list denial. Vision OCR and embeddings are not on this chat path and are not gated here.
