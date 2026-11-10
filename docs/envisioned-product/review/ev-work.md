# ev-work review

The work loop now reads the stop bit and the allow list before it calls a tool. A calendar step still returns a confirm card. The click still creates the event. An unknown write is settled with `decide_replay` and `lookup_inserted_event`, and the loop does not insert. Dispatch still only records the assignment.

This note is not a merge approval. The live lanes and the review screenshot and video are deferred. They are not passed.

## What the code does

`retrieve_node` returns before `gateway.invoke` when `is_stopped()` is true. `admit` reads that bit and then `execution_denial`. A calendar name is a confirm step and still invokes the existing calendar tool, which drafts a card. A read name invokes the tool and does not publish a write card. `reflect_node` settles `UnknownWrite` before it pops another tool. `settle_unknown` calls `decide_replay`. It calls `lookup_inserted_event` only when the choice is `reconcile`. A `send` choice does not look up and does not insert. The spoken sentence is `CONFIRM_DETAIL["unknown"]`. The loop does not call `save_found_event` or `create_event`.

`chat.py` calls `stream_chat` only when `llm_messages` is non-empty. `assign_turn` still does not call the gateway, publish a claim, or write the reply.

`ToolResult.unknown_write` is the one new field. The loop cannot see an unknown write without it. `WorkflowResult` keeps its old keys.

## Model check

The checker anchors were confirmed at `8d59f5f46d0e4a9a5d97b273982bfbeeb62ea139`. `python verification/work-loop/check.py` exits 0. The rules that hold are `TypeOK`, `StopBlocksInvoke`, `NoSecondInsert`, `CalendarStaysUnwritten`, `LookupFollowsChoice`, and `DispatchStaysQuiet`. TLC reports 25 distinct states. The pinned jar is SHA256 `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`, 4,510,215 bytes, manifest `2.0 2026-11-06`, revision `d8fb231de4e4b5d02c1b2ed1ac759c086f694021`.

`--break-stop` exits 1 and violates `StopBlocksInvoke`. `--break-unknown` exits 1 and violates `NoSecondInsert`. `--break-calendar` exits 1 and violates `CalendarStaysUnwritten`. `--break-dispatch` exits 1 and violates `DispatchStaysQuiet`. `--break-lookup` exits 1 and violates `LookupFollowsChoice`.

Removing the `if is_stopped():` that sits before `gateway.invoke` in `retrieve_node` makes the checker exit 2 with `retrieve calls the gateway before it reads stop`. That line was put back. The held command then exits 0.

`python verification/dispatch-boundary/check.py` still exits 0 on this tree.

The model is one generation. It does not model a second user, `verify_state`, the confirm click, or an empty 403. A stop that flips during `gateway.invoke` can still enter that call. The chart is `ev-work-evidence/chart.mmd`. It hides steps that stay inside one label, including `Assign`, `Stop`, and `Done`.

## Tests

From `backend`, with `DATABASE_URL=sqlite+pysqlite:///./ci-eval.db`, `REDIS_ENABLED=false`, `CELERY_TASK_ALWAYS_EAGER=true`, `SANDBOX_RUNNER_URL` empty, `SECRET_KEY=test-secret-key`, and `ADMIN_PASSWORD=test-admin-pass`. This machine has Python 3.12. The commands used `python3`.

`python3 -m pytest tests/test_work_loop.py tests/test_workflow.py tests/test_dispatch_boundary.py -q` exits 0. The new file covers a stop before the graph, a stop after the graph has started, an unknown write with a second calendar still queued, a `send` choice that does not look up, dispatch call counts of 0, and a knowledge-base chat through `plain_client` with no confirm card.

## Perf

The gate is wall-clock seconds around one `pytest -q` of one file. Fresh sqlite in each directory. Same boot-recipe env. The machine has 4 cores. Load at the start of the timed probe was 0.02. Trunk is release `1d96a240304da93a512aea2dd3b5cb9fb094e260` in `/tmp/ev-work-trunk`. Head is this tree. An earlier trunk pytest on that worktree finished, and the timer script then crashed, so that pass has no recorded seconds. The timed order below is still trunk, then head, then trunk. A `--durations` run is a profile, not the gate.

- Trunk `tests/test_workflow.py`, first timed run: 2.172 seconds, exit 0, 29 passed.
- Head `tests/test_workflow.py`: 2.196 seconds, exit 0, 29 passed.
- Trunk `tests/test_workflow.py`, second timed run: 2.143 seconds, exit 0, 29 passed.
- Head `tests/test_work_loop.py`: 2.376 seconds, exit 0, 6 passed.

The gate is head of `tests/test_workflow.py` under 60 seconds, and the new file under 30 seconds. Both hold. The workflow profile's slowest setup is 0.49 seconds and the slowest call is 0.03 seconds. The loop profile's slowest setup is 0.48 seconds and the slowest call is 0.07 seconds. The new settlement is not the limiter. Trunk alone moved from 2.172 to 2.143, which is wider than the head gap of 0.024 seconds, so this probe does not claim a speedup. The runs are one each.

## Flowchart

`ev-work-flowchart.png` is the operator chart. The main path goes down. Side boxes do not rejoin. Phrases stay whole. The font on this machine is WenQuanYi Micro Hei. Noto Sans CJK SC is not installed. The drawer is `ev-work-evidence/draw_flowchart.py`.

## Carried open issues

These are not closed and not passed.

From ev-plug:

1. An empty 403 is still a definite rejection. If that response had stored an event, a retry could insert a second one. Wait for a captured 403 body with an event id.
2. `FollowupCard` still reads the root phase. After a refresh the button can look clickable. The server rejects a second insert.
3. A crash after the arm commit and before the request leaves stays unknown. Another click cannot resend.
4. Update, delete, and RSVP after unknown do not reconcile and seal the way create does.
5. There is still no GitHub Actions workflow for UnknownWrite.

From ev-stop:

1. `verification/stop-before-send/check.py` is not in GitHub Actions yet.
2. The formal `CalendarStays` model is weak. The calendar starts at 1, and the source anchor only checks that `chat.py` has no `delete_event`.
3. On a planning-stage disconnect, `wf_task.cancel()` does not wait for the worker thread. Later prevention relies on the committed stop bit. Unretrieved exception logs are possible.
4. After stop, `chat.py` may still infer and yield an artifact from text already received, and `finally` always sends `done`.
5. If `stream_chat` returns on stop without raising `GenerationStopped`, `leave_stream` may stay false and flush fragments already accepted into the desensitization buffer.

From ev-dispatch:

1. `verification/dispatch-boundary/check.py` is not in GitHub Actions yet.
2. The formal violations depend on break constants. The anchor starts after `def assign_turn(`, so it does not see `from_plan`, and it does not anchor `run_task`.
3. A low-confidence queue can still enter the work graph. That is the existing router behavior.
4. `test_workflow.py` still has three pre-existing `object()` session arguments, at the kb-then-web test, the sandbox artifact test, and the empty-kb test.

From this knife:

1. `verification/work-loop/check.py` is not in GitHub Actions.
2. The loop does not save a lookup hit. The sentence stays the unknown sentence either way.
3. A stop that becomes true during `gateway.invoke` can still enter that call.
4. AnythingLLM `server/utils/agents/aibitat/index.js` and `server/utils/chats/stream.js` were read from `Mintplex-Labs/anything-llm` commit `ace9dad3348b4d8a237b09978517ced2bd18e198`. That loop was not copied. Leon `develop` `7926e9771d811b80eb4409c1d83122482b5656e0` does not contain `llm-duties/react-llm-duty.ts` or `core/turn-lifecycle`. Those paths were not invented.

## Deferred live lanes

Not run. Not passed.

1. A knowledge-base question on trunk and on the head answers in the bubble and creates no calendar event.
2. Asking for a calendar event and clicking the card creates one event only after the click.
3. Stop during tool planning exits the loop and logs no later tool call for that generation.
4. An unknown insert does not send a second insert on the next step.
5. A second user stores the tool audit only under that user.
6. Two read steps both finish, the answer cites both, and no confirm card appears.
7. A long history does not change the allow list.
8. A visible tool that is not allowed is denied, and the answer says it did not run.
9. Reload after the answer does not start another tool.
10. The diff does not replace LangGraph and does not add Khoj.

`ev-work-review-loop.png` and `ev-work-review.mp4` were not recorded. They are not passed.
