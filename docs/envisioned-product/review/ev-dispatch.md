# ev-dispatch review

A chat turn now records its tool assignment before the work graph runs. `assign_turn` calls `plan_tools` and returns a `TurnAssignment`. `run_agent_workflow` requires that value and does not call `plan_tools`. The reply is still written by `answer_node` or by `stream_chat` on the messages the graph returns. A schedule claim is still published in `chat.py` after the graph returns a card.

The same sentence still reaches a confirm card only when the work path runs the calendar tool. Dispatch does not insert a calendar row.

## What the code does

`TurnAssignment` holds `next_tool`, the tail in `pending_tools`, the five need flags, `forced`, and `intent`. It has no confidence and no card. `from_plan` splits the queue the way `plan_node` used to, and it drops confidence and reasoning.

`_run_workflow_sync` and `run_task` call `assign_turn` and then `run_agent_workflow`. The graph starts at retrieve when `next_tool` is set, and at answer when it is empty. `verify_state` can still choose a later tool inside `reflect_node`. That later choice is the work loop.

`route` and `plan_tools` are unchanged. They already return the router decision and do not call the gateway. Routing scores still read confidence from that decision. The work assignment does not carry it, so a low score cannot open a card by itself.

Stop is the check already at the start of `run_agent_workflow`, and again in `retrieve_node` before `gateway.invoke`. Dispatch may still record the assignment after stop. The model path inside `plan_tools` still raises `GenerationStopped` when the generation is bound and stopped. Tier shortcuts and a Redis hit do not read the stop flag.

## Model check

`python verification/dispatch-boundary/check.py` exits 0. The rules that hold are `TypeOK`, `DispatchDoesNotInvoke`, `DispatchDoesNotClaim`, `DispatchDoesNotWrite`, and `StopBlocksGateway`. TLC v1.8.0, revision `d8fb231de4e4b5d02c1b2ed1ac759c086f694021`, the pinned jar. SHA256 `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`.

`--break-invoke` exits 1 and violates `DispatchDoesNotInvoke`. `--break-claim` exits 1 and violates `DispatchDoesNotClaim`. `--break-calendar` exits 1 and violates `DispatchDoesNotWrite`. `--break-stop` exits 1 and violates `StopBlocksGateway`.

A scratch line `gateway.invoke` inside `assign_turn` makes the checker exit 2 with `assign_turn calls gateway.invoke`. That line was removed. The held command then exits 0.

The model is one generation. It does not model a second user, a router model call, or `verify_state`. A claim after the graph returns is the chat path. The click that writes the calendar is outside this knife.

The audit chart is `ev-dispatch-evidence/chart.mmd`. It is the formal graph. The operator chart is `ev-dispatch-flowchart.png`.

## Tests

From `backend`, with `DATABASE_URL=sqlite+pysqlite:///./ci-eval.db`, `REDIS_ENABLED=false`, `CELERY_TASK_ALWAYS_EAGER=true`, `SANDBOX_RUNNER_URL` empty, `SECRET_KEY=test-secret-key`, and `ADMIN_PASSWORD=test-admin-pass`. This machine has Python 3.12 and no `python` on `PATH`, so the commands used `python3`. Pytest is 8.4.2.

`python3 -m pytest tests/test_dispatch_boundary.py tests/test_router.py tests/test_workflow.py tests/test_calendar_card.py tests/test_calendar_dialogue.py tests/test_agent_e2e.py -q` exits 0.

The dispatch file checks that `assign_turn` calls the gateway double and the claim double 0 times, that the work graph runs one web search from a written assignment while `plan_tools` raises if called, that a stopped entry returns the halted result with no gateway call, that a `plain_client` chat streams the work-path token, that router history is the history `plan_tools` receives, and that confidence does not change the assignment.

## Perf

The number is wall-clock seconds from `date +%s.%N` around one `pytest -q` of one file. Fresh sqlite in each directory. Same boot-recipe env. The machine has 4 cores. Load at the start of the probe was 0.09. Trunk is release `8fc5dddee311b04416661002b32d4274cd6b6edf` in `/tmp/ev-dispatch-trunk`. Head is this tree. Trunk again is that same worktree. A `--durations` run is a profile, not the gate. A combined suite had already run on the head before this probe, so the first trunk run is not a cold process start. The probe order is still trunk, then head, then trunk.

- Trunk `tests/test_router.py`, first run: 1.846 seconds, exit 0, 17 passed.
- Head `tests/test_router.py`: 1.464 seconds, exit 0, 17 passed.
- Trunk `tests/test_router.py`, second run: 1.437 seconds, exit 0, 17 passed.
- Head `tests/test_dispatch_boundary.py`: 2.084 seconds, exit 0, 6 passed.

The gate is head of `tests/test_router.py` under 60 seconds, and the new file under 30 seconds. Both hold. The router profile's slowest setup is 0.31 seconds and the other calls are under 0.005 seconds, so that number is pytest startup. The dispatch profile's slowest setups are 0.31 and 0.24 seconds, and the slowest call is 0.05 seconds. The assignment function is not the limiter. Trunk alone moved from 1.846 to 1.437, which is wider than the head gap, so this probe does not claim a speedup. The runs are one each.

## Flowchart

`ev-dispatch-flowchart.png` is the operator chart. The main path goes down. Side boxes do not rejoin. Phrases break at a full-width comma or stay whole. The font on this machine is WenQuanYi Micro Hei. Noto Sans CJK SC is not installed. The drawer is `ev-dispatch-evidence/draw_flowchart.py`.

## Carried open issues

These are not closed and not passed.

From ev-plug:

1. An empty 403 is still a definite rejection. If that response had stored an event, a retry could insert a second one. Wait for a captured 403 body with an event id.
2. `FollowupCard` still reads the root phase. After a refresh the button can look clickable. The server rejects a second insert.
3. A crash after the arm commit and before the request leaves stays unknown. Another click cannot resend.
4. Update, delete, and RSVP after unknown do not reconcile and seal the way create does.
5. There is still no GitHub Actions workflow for UnknownWrite.
6. Live lanes stay deferred.

From ev-stop:

1. `verification/stop-before-send/check.py` is not in GitHub Actions yet.
2. The formal `CalendarStays` model is weak. The calendar starts at 1, and the source anchor only checks that `chat.py` has no `delete_event`.
3. On a planning-stage disconnect, `wf_task.cancel()` does not wait for the worker thread. Later prevention relies on the committed stop bit. Unretrieved exception logs are possible.
4. After stop, `chat.py` may still infer and yield an artifact from text already received, and `finally` always sends `done`.
5. If `stream_chat` returns on stop without raising `GenerationStopped`, `leave_stream` may stay false and flush fragments already accepted into the desensitization buffer.

## Deferred live lanes

Not run. Not passed.

1. Asking for a calendar event still shows a confirm card and writes nothing before the click.
2. An ordinary question shows one answer bubble and no confirm card.
3. Stop may record the assignment and must not call the gateway after the stop.
4. A calendar change without a click keeps the card, and the dispatch log has no `publish_schedule_claim`.
5. A second user gets a separate task and cannot see the first user's card.
6. The same prompt twice after finish is two generations, and each dispatch call is free of tool calls.
7. A low-confidence router still does not open a card by itself.
8. The answer still streams in the existing bubble.
9. Reload keeps the transcript and does not run a second tool.
10. The diff stays in this repo, with no copied DSH tree and no Khoj.

This knife is not merge consent. ev-work is not started.
