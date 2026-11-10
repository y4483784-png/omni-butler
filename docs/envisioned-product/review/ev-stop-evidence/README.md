# ev-stop evidence

Recorded on 2026-11-10. The branch is `cursor/ev-stop-before-send-0a3c`, cut from release `1e89297976ef66883202503d99bca3166d61005e` (`feat(calendar): keep an unknown write from being inserted again (#81)`).

This knife stops a model request that has not left, and it drops a late chunk. It does not deploy, and it does not start ev-dispatch.

Live product lanes are not run. They stay open until the whole envisioned plan is implemented. A unit result, a model result, or a chart is not one of those lanes.

## Commands

Boot recipe, from `backend`, using `/tmp/ev-stop-venv/bin/python` (Python 3.12.3, pytest 8.4.2):

`DATABASE_URL=sqlite+pysqlite:///./ci-eval.db REDIS_ENABLED=false CELERY_TASK_ALWAYS_EAGER=true SANDBOX_RUNNER_URL= SECRET_KEY=test-secret-key ADMIN_PASSWORD=test-admin-pass`

`python -m pytest tests/test_stop_before_send.py tests/test_chat_sse_routing.py -q`

Exit 0. 7 stop tests and 9 routing tests. Frontend `npm test` exits 0, 30 passed (`npm.out`).

`python -m pytest tests -q` exits 0. `full-summary.txt` counts 796 passed, 1 xfailed, 6 skipped.

Wall clock is `date +%s.%N` around one file, fresh sqlite, same env. Trunk is release `1e89297`. Head is this tree. Trunk again is that release in `/tmp/ev-stop-trunk`. The durations file is a profile.

- trunk 5.237s, exit 0, 9 passed (`perf-trunk-1.seconds`)
- head routing 4.338s, exit 0, 9 passed (`perf-head-sse.seconds`)
- trunk again 4.444s, exit 0, 9 passed (`perf-trunk-2.seconds`)
- head stop file 2.865s, exit 0, 7 passed (`perf-head-stop.seconds`)

The gate is head routing under 60 seconds and the new file under 30 seconds. Both hold. The slowest routing setup is 1.04s. The number is pytest startup plus sqlite.

`python verification/stop-before-send/check.py` exits 0 (`tlc-held.out`). `--break-send`, `--break-late`, `--break-memory`, and `--break-calendar` each exit 1 and name `NoSendAfterStop`, `NoLateChunk`, `NoMemoryWhenStopped`, and `CalendarStays`. The jar sha256 is `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f` (`jar.sha256`). Statechart used that jar through `TLA2TOOLS`. `statechart-report.md` says 18 states and 28 steps, and the chart draws 10 states and 18 arrows. `Done` is hidden because it does not change what the reader sees.

A temporary edit that wrote the late chunk before the stop break failed `test_late_chunk_stays_out_of_the_reply` (`mutation-late.out`, exit 1). The file was restored. The head stop run after that restore passed.

Task-run, schedule-confirm including continuation, follow-up, prep-readonly, and unknown-write checks exit 0. Their logs are the `prior-*.out` files.

## Flowchart

The readable chart is `../ev-stop-flowchart.png`. One path downward. Exception boxes sit to the right and do not rejoin. The formal state diagram is `statechart.png` in this folder.
