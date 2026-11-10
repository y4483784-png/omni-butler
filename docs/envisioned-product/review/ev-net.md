# ev-net

This knife keeps one web path. The 2026-11-10 DeepSeek chat-completions document names no networking field, so the request body stays as it was, and the existing Zhipu search stays as it was. The cutover the plan describes is a recorded gap, not a code change.

The plan file and its checkboxes are untouched. Live lanes are not passed. This note does not merge or deploy, and it does not start ev-events.

## The document

Read on 2026-11-10:

- https://api-docs.deepseek.com/api/create-chat-completion
- https://api-docs.deepseek.com/api/create-chat-completion.md
- https://api-docs.deepseek.com/zh-cn/api/create-chat-completion

`CreateChatCompletionRequest` lists `messages`, `model`, `thinking`, `reasoning_effort`, `max_tokens`, `response_format`, `stop`, `stream`, `stream_options`, `temperature`, `top_p`, `tools`, `tool_choice`, `logprobs`, `top_logprobs`, `user_id`, and the deprecated `frequency_penalty` and `presence_penalty`. `tools` is function type only. The page has no networking property.

Web search on that same site is a different API. The Messages API has a server tool `web_search_20250305`. The Responses API has its own web-search tool. `POST /v1/tools/search` is a standalone search call. None of those is a field of `POST /chat/completions`.

## What the running turn does

`_decision_to_plan` appends `"web"` when `needs_web` is true. It does not read the token 「上网」. `execute_web` calls `search_planned`, and `_web_search_url` still posts to Zhipu `/web_search` when the base URL is empty. `_build_request` still writes `model`, `messages`, `stream`, `extra_body` with the thinking switch, and `reasoning_effort`. A JSON call also writes `response_format` and `temperature`. `_refuse_outbound` still raises `ToolsRefused` before the client exists. `_create_sync` still calls `is_stopped()` before `chat.completions.create`. `workflow.py` still does not call `create_event`.

## Formal check

The question is whether one turn can take two web paths, send after stop, or write the calendar from the card. That is an order of steps, so the check is TLC on a small model of those steps. The jar is the vendored `verification/tlc/tla2tools.jar`, SHA256 `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`, TLC 2.0 from tag `v1.8.0`.

Held run, 40 distinct states, no error:

- `NoBoth`. The body field and the Zhipu call are not both true.
- `NoGuessedField`. The body field stays false.
- `StopBlocksLaterSend`. A later send after stop stays false.
- `CalendarStaysUnwritten`. The card does not write the event.

Broken variants, each exit 1:

- `--break-both` violates `NoBoth`. `Build` sets the field and `Search` calls Zhipu.
- `--break-field` violates `NoGuessedField`. `Build` sets the field.
- `--break-stop` violates `StopBlocksLaterSend`. `LaterSend` sends after stop.
- `--break-calendar` violates `CalendarStaysUnwritten`. `ShowCard` writes the event.

`--cutover` is the finding, not a pass. It exits 1.

`FieldWhenAllowed` says a turn that has the token gets a networking field once the body is built. The shortest path is: the turn starts, the router records its choice, `Build` leaves the field false. That is `_build_request` as it stands. The document names no field to set, so the code was not changed to satisfy this rule.

`AbsentSkipsZhipu` says a turn without the token does not call search. The shortest path adds one step: `Search` runs because `needsWeb` is true. That is `_decision_to_plan` appending `"web"` and `execute_web` calling `search_planned`. The token is not an input of those functions.

A scratch edit that put `"web_search": True` into `_build_request` made `check.py` exit 2 with `modeling error: _build_request names web_search`. The edit was reverted, and the held run exited 0 again.

The model is one turn. It does not cover a second user, `verify_state` queueing web again, the click that writes the calendar, or a request that is already on the wire when stop arrives.

Run from the repo root:

```sh
python verification/one-web-path/check.py
python verification/one-web-path/check.py --cutover
```

`/statechart` drew the held model with `TLA2TOOLS` pointed at the vendored jar. TLC reached 40 states. The chart has 12 states and 16 arrows. Eight steps that stay inside one chart state are not drawn. Those are the end stutter. Stop is drawn, because a stopped turn has its own label. The files are `docs/envisioned-product/review/ev-net-evidence/statechart-held/`. The cutover chart, with the two failing paths marked, is `docs/envisioned-product/review/ev-net-evidence/statechart-cutover/`.

Regenerate into a new directory:

```sh
TLA2TOOLS=verification/tlc/tla2tools.jar JAVA=java \
  node /path/to/statechart/scripts/statechart.ts \
  verification/one-web-path/OneWebPath.tla \
  --config verification/one-web-path/OneWebPath.cfg \
  --view verification/one-web-path/view.mjs \
  --out /tmp/ev-net-chart-held
```

## Operator flowchart

![这一轮的上网](ev-net-flowchart.png)

The main column is one turn. Side boxes are the exceptions, and the lines do not cross. The font is Noto Sans CJK SC. Each line is a whole phrase. The drawing script is `docs/envisioned-product/review/ev-net-evidence/draw_flowchart.py`.

## Tests

`python -m pytest tests/test_one_web_path.py tests/test_web_thinking.py tests/test_zhipu_endpoints.py tests/test_deepseek_roles.py -q` exited 0. The run collected 73 tests: 4, 1, 44, and 24. Output is `docs/envisioned-product/review/ev-net-evidence/unit.txt`.

`tests/test_one_web_path.py` locks the hold. It checks the body keys, `ToolsRefused` before any client call, the router queue, and the Zhipu POST. It does not assert the cutover the plan first described, because that cutover has no field to turn on.

## Perf

One run each. Numbers are in `docs/envisioned-product/review/ev-net-evidence/perf.txt`.

| run | seconds | exit |
| --- | ---: | ---: |
| trunk `test_web_thinking.py`, before the head | 2.564 | 0 |
| head `test_web_thinking.py` | 1.831 | 0 |
| trunk `test_web_thinking.py`, after the head | 1.763 | 0 |
| head `test_one_web_path.py` | 1.778 | 0 |

The head thinking run is under 60 seconds. The head one-web-path run is under 30 seconds. The two trunk runs differ by 0.801 seconds, and the head run is 0.068 seconds above the second trunk run. A profile of the first trunk run, kept out of the gate, spent about 0.50 seconds in setup and under 0.005 seconds in the test call. The wall clock is process startup.

## Live lanes

Lanes 1 through 10 were not run. `ev-net-review-web.png` and `ev-net-review.mp4` were not produced. Do not read this note as a live pass.

## Carried open items

From ev-plug, still open:

- Freshness does not change the search count or the recency filter.
- `verify_state` can queue `web` again. It passes `iteration=1` into `execute_web`, and the query-rewrite cooldown does not key on that iteration.
- The scheduler tick bypasses prep, the router, and dispatch.
- The sandbox still runs in-process when no runner URL is set.
- Browser paths, the live stack, and the 300-second cold-start ceiling were not run.

From ev-stop, still open:

- An in-flight model request is not cancelled.
- A stop during `gateway.invoke` can still enter that call. The post-invoke `is_stopped()` check then replaces the graph output.
- Dispatch may still record the assignment after stop.

From ev-dispatch, still open:

- `publish_schedule_claim` has no consumer.
- `assign_turn` failure still publishes before the graph is discarded.
- No live check of dispatch, stop, or the calendar card.

From ev-work, still open:

- `verify_state` can queue another read.
- The click on the card writes the calendar. The loop does not.
- An empty 403 from the lookup is still an open product choice.
- Live lanes for that knife were not run.

## Decision log

`.audit/ev-net.tsv` records the six choices above.
