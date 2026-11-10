# ev-plug evidence

Recorded on 2026-11-10. The branch is `cursor/ev-plug-side-effect-dca2`, cut from release `457cfe97c8ad42f7f111076256905fc29e90d1b7` (`feat(calendar): bind each confirm claim to the write (#80)`).

This knife adds the side-effect contract and the unknown-result claim. It does not deploy, and it does not start ev-stop.

Live product lanes are not run. They stay open until the whole envisioned plan is implemented. A unit result, a model result, or a chart is not one of those lanes.

## Deferred live lanes

Not run:

1. trunk and head confirm regression on a running product (`ev-plug-regression.png`)
2. one successful Google insert (`ev-plug-write.png`)
3. failure after a committed token refresh (`ev-plug-unknown.png`)
4. a second click after an unknown result (`ev-plug-second.png`)
5. a stopped generation denied at the gateway, with the audit row (`ev-plug-stop.png`)
6. another user's token (`ev-plug-identity.png`)
7. a repeated read (`ev-plug-read.png`)
8. a tool name that is visible but removed from the turn (`ev-plug-visible.png`)
9. reload while the result is unknown (`ev-plug-reload.png`)
10. the scope diff against Khoj and Compose on a running product (`ev-plug-scope.png`)

Not produced, because they need a live click: `ev-plug-review-unknown.png` and `ev-plug-review.mp4`.

## Deferred display issue

`FollowupCard` shows the confirm button when the card root `phase` is `draft`. A follow-up token can sit on an action item. The server stamps that action, and it still refuses a second insert. If the root phase stays `draft`, a reload can show a clickable button. The click does not insert again. Revisit this when `FollowupCard` reads the action phase or the claim status. This is not a passed lane.

## What the code does

A registered tool is `read`. The calendar tool prepares a draft. The web tool searches. The sandbox tool computes without a network. None of them insert a calendar event. `idempotent_write` may send again only with the same external id. `non_replayable_write` is looked up, not sent again.

`invoke` reads the `TaskRun` before `execute` when `task_id` is set. A stopped generation is denied, the audit reason contains `generation=<n>`, and the row stays `stopped`. A name missing from that turn's list is denied even if the constitution still lists it. A card-action marker is not a tool list, so it is denied. `completed` is not rewritten.

`consume_schedule_claim` moves `open` to `unknown` and does not commit. `arm_remote_claim` stores `remote_request_id` and commits before the Google call. A network failure or a 5xx looks the id up with a GET. A found create is saved and not inserted again. A miss stays `unknown`, and the next click does not insert. A 4xx reopens the claim. A refresh that fails before that commit rolls the claim back to `open` and does not insert.

The plan's file list did not name `schedule_confirm.py`, `models.py`, or the alembic revision. The unknown result is false without them: a fresh token used to roll the claim back to `open`, and a committed refresh used to stamp the card confirmed before Google answered. Those files are in this knife for that reason.

## Upstream

Reread at the pinned commits. Copied: a non-repeatable network or 5xx failure becomes unknown and is not sent again. A visible name is not execution permission. A stopped or removed name is refused at execution. Not copied: a shell permission catalog, Khoj, or a new Compose service. The upstream branch is a repeatable boolean. It does not prove the three-way contract, and it does not look up a request id.

## Checks

Boot recipe, from `backend`:

`DATABASE_URL=sqlite+pysqlite:///./ci-eval.db REDIS_ENABLED=false CELERY_TASK_ALWAYS_EAGER=true SANDBOX_RUNNER_URL= SECRET_KEY=test-secret-key ADMIN_PASSWORD=test-admin-pass`

`python -m pytest tests/test_tool_contract.py tests/test_calendar_google.py tests/test_harness_gateway.py tests/test_claim_fingerprint.py -q`

Exit 0. The progress lines are in `acceptance-progress.txt`. Frontend `npm test` passed 30 tests.

Gateway wall clock, fresh sqlite, same env, `date +%s.%N` around one file. Trunk is release `457cfe9`. Head is this tree. Trunk again is the same release. The durations file is a profile. It is not a gate number.

- trunk 1.818s, exit 0
- head 1.695s, exit 0
- trunk 1.733s, exit 0
- `tests/test_tool_contract.py` head 4.473s, exit 0

The gate is head gateway under 60 seconds and the new file under 30 seconds. Both hold. The slowest gateway setup is 0.49s and the calls are about 0.01s, so the number is pytest startup plus sqlite. That is why a few new checks do not approach the gate.

`python verification/unknown-write/check.py` exits 0. `--break-blind` exits 1 and violates `NoBlindPost`. `--break-stopped-execute` exits 1 and violates `NoStoppedExecute`. The jar is the pinned `verification/tlc/tla2tools.jar`. Statechart used that jar through `TLA2TOOLS` and did not install another TLC. It reached 8 states. The chart hides `RunTool` and `Stutter` because they do not change what the reader sees. Schedule-confirm, task-run, follow-up, and prep-readonly checks exit 0.

The model is one claim and one tool call. It does not model the network, token refresh, patch, or delete.

## Flowchart

The readable chart for this knife is `../ev-plug-flowchart.png`. One path downward. Exception boxes sit to the side. The formal state diagram is `statechart.png` in this folder. They are different pictures.
