# ev-gate evidence

Recorded on 2026-11-10. The branch is `cursor/ev-gate-claim-fingerprint-470a`, cut from release `5a6e0f847a238061fa719778689705bcccf0f01d` (`feat(task): record each chat turn as TaskRun (#79)`).

Live product lanes are not run. They stay open until the whole envisioned plan is implemented. Do not read a unit, model, or chart result as one of those lanes.

## Deferred lanes

Not run:

- trunk and head confirm regression on a running product
- single click
- double click
- edit the time, then use the old token
- stop the owning generation, then click
- cross-user token
- two generations, one row and one card
- absent token on the running product
- refresh after confirm, no second event
- no second approval table and no Khoj on a running product

Not produced, because they need a live click: `ev-gate-review-card.png` and `ev-gate-review.mp4`. Planned names if those lanes are ever run: `ev-gate-regression.png`, `ev-gate-click.png`, `ev-gate-twice.png`, `ev-gate-edit.png`, `ev-gate-stop.png`, `ev-gate-identity.png`, `ev-gate-idempotent.png`, `ev-gate-missing.png`, `ev-gate-reload.png`, `ev-gate-scope.png`.

## Callers

These HTTP writes now go through `consume_schedule_claim`. A missing or invalid token is HTTP 409 and creates no event.

- `POST /api/calendar`
- `PATCH /api/calendar/{id}`
- `DELETE /api/calendar/{id}`
- `POST /api/calendar/google/confirm`
- `POST /api/calendar/google/rsvp` (this route did not consume a token before)

`POST /api/calendar/confirm-claims` calls `issue_schedule_claim`. The browser does not send generation ids. The server uses the live turns in that session. No live turn returns 409 `没有进行中的任务，未签发确认。`

Chat calls `publish_schedule_claim` and `publish_followup_claims`. Both live in `schedule_confirm.py`. Chat does not sign a token.

`backend/scripts/run_calendar_eval.py` `_confirm` issues a claim before create, patch, delete, and Google confirm. `do_not_confirm` still returns early.

`auth_client` is a `BindingClient` that issues a real claim before a calendar write when the test did not pass `confirm_token`. Gate tests use `plain_client`, so a missing token stays missing. This is a test migration, not a product bypass.

Not product bypasses: `create_event`, `update_event`, and `cancel_event` inside the calendar services, called only after consume or by a provider unit test. `prepare_meeting` does not write. The follow-up write is the client `createCalendarEvent`, gated as `calendar.create`. The description includes `follow_action: {id}`, so that fingerprint differs from an ordinary create. Google connection delete is not bound.

A list or suggest turn still finishes as `completed`. Slot selection and RSVP on that card cannot issue a claim unless some other turn in the same session is still live. The calendar route does not call `begin_turn`.

## What was copied

Reread at the pinned commits. The local checkouts were not used.

OpenMausBot `083fd7b51405e6d0c01e189b2f65ab0322509b5a`:

- https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/routines.ts#L1487-L1536
- `matchingRoutineRequestReceipt` returns null when there is no receipt and throws when the action or fingerprint does not match.
- `commitMutation` snapshots state and restores it when `save()` throws.
- Copied: reject a mismatched action or fingerprint, and roll the claim update back when the calendar write does not commit. `consume_schedule_claim` does not commit on success. The provider commit is on the same session.
- Not copied: `messageId`, `botId`, `threadId`, and `fingerprintVersion`.

OpenMausBot live approval, same commit:

- https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/host/live-approval.ts#L12-L17
- The file is 17 lines. It reads the current project bot at decision time.
- Copied the analogue: consume reads `TaskRun.status` at consume time.
- Not copied: an approval-mode enum. This product does not have one.

DS-Bot `71d344bf193f3f18185f25de3d8e5c483046910a`:

- https://github.com/Fei2411/DS-Bot/blob/71d344bf193f3f18185f25de3d8e5c483046910a/ds-bot/src/host/approvals.js
- Cancel answers the pending entry, and a missing entry is rejected as stale.
- Copied: a cancelled or stale claim is rejected.
- Not copied: the in-memory `Map`. The durable row stays `ScheduleConfirmClaim`.

Scope, stop invalidation, and keeping generation out of the write key are this product's own rules. The upstream routine receipt does not give exactly-once external side effects. Do not describe those as upstream behavior.

## Model

`ScheduleConfirm.tla` is the existing card model plus `token`. A write requires `token = "bound"` unless `--break-tokenless` turns that off. `NoWriteWithoutToken` is `writes = 0 \/ token = "bound"`. The held run is 1759 states generated, 500 distinct.

`ClaimGate.tla` is one user, one action, and one fingerprint, with two generations. Each generation is `idle`, `live`, `stopped`, or `completed`. Stopped and completed are different states. Neither can authorize a write. An open claim needs at least one linked live generation. Stopping one of two leaves the claim open. Stopping or completing the last one cancels it and does not issue a replacement. A later `Issue` after cancel is a new explicit publish. Consume and the calendar write are one step, because a failed write rolls the claim update back with the request session.

Kept out of `ClaimGate.tla`:

- the card button, busy, and stream restore (those stay in `ScheduleConfirm.tla`)
- expiry, scope string format, and the sha256 canonicalizer
- SQLite `FOR UPDATE`, which is a no-op here. Postgres row locks are real. The model does not claim the SQLite race is locked.
- a second fingerprint. An edited time is `match = FALSE` on the old claim, not a second row.
- republish after a successful write. The product allows a new open row for the same fingerprint after the previous row is `written`. The model stops at one successful consume of the open row.
- page stop. The model action is `task_run.stop`. The page button is still unwired.

`python3 verification/schedule-confirm/check.py` exits 0. `--break-claim token` exits 1 and TLC reports `NoWriteWithoutToken` violated: Consume from `claim = "none"` reaches `writes = 1`. The other break flags also exit 1. See `break-exits.txt`.

Removing `raise ScheduleConfirmClosed("missing")` from `consume_schedule_claim` makes the default command exit 2 with `consume_schedule_claim no longer rejects a missing token.` The line was restored. `scratch-missing.exit` is that run.

Jar: `verification/tlc/tla2tools.jar`, sha256 `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`, manifest `2.0 2026-11-06`, revision `d8fb231de4e4b5d02c1b2ed1ac759c086f694021`. No second pin and no rolling download.

Follow-up confirm, TaskRun, and prep-readonly checks also exited 0 on this tree. Their pass lines are `followup.out`, `task-run.out`, and `prep.out`.

The statechart command was the pstack `statechart` script at the plugin cache `skills/statechart/scripts/statechart`, with `TLA2TOOLS` set to the vendored jar and `JAVA=java`. It exited 0. TLC reached 115 states and 344 steps. The chart draws 9 states and 23 arrows. It hides steps that stay on one chart label, and it hides the idle stutter. The view is `verification/schedule-confirm/statechart/claim-view.mjs`. Raw Mermaid is `chart.mmd`. The review copy replaces the script's ` or ` joiner with `或` and adds `classDef` plus `style` so the legend swatches use the same fill as the states: `#c3e0ff`, `#f0d3bf`, `#bfe3df`, `#e5d9ce`.

Review chart:

- `docs/envisioned-product/review/ev-gate-statechart.mmd`
- `docs/envisioned-product/review/ev-gate-statechart.svg`
- `docs/envisioned-product/review/ev-gate-statechart.png`

`pele.svg` is the same Mermaid rendered with `pele@1.9.0`. Inline `style` fills on the legend nodes match the state nodes. The PNG and SVG next to the review Mermaid were rendered with `@mermaid-js/mermaid-cli` 11.4.2 and `/usr/local/bin/google-chrome`. The title `确认凭证` is visible.

This bounded model is not the whole product.

## Perf

Same boot env. Wall clock is `date +%s.%N` before and after pytest. `/usr/bin/time` is not installed. Python is 3.12.3. CI is 3.11. Pytest is 8.4.2. The venv is `/tmp/ev-gate-venv`.

`tests/test_schedule_confirm_claims.py`:

- release baseline, before any head edit: 4.022 seconds, exit 0. Load at the rerun that produced the number was 0.37. The meta file says `11:28:01 up 15 min, load 0.05, 0.07, 0.02`, 4 cores.
- head: 3.203 seconds, exit 0. Load 0.22.
- release again, worktree at `5a6e0f8`: 3.604 seconds, exit 0. Load 0.21.

`tests/test_claim_fingerprint.py` on head: 4.851 seconds, exit 0. The 60 second and 30 second gates both hold. These are one timed run each, not a median.

## Tests

From `backend`, with the boot env:

`python -m pytest tests/test_schedule_confirm_claims.py tests/test_claim_fingerprint.py tests/test_task_generation.py -q`

34 passed.

`python -m pytest tests -q` on a fresh `ci-eval-suite.db` exited 0. A later run on the already-used `ci-eval.db` returned 409 from writes whose open claims were left behind by earlier runs. That file is not the CI database. The two named-file failures on the first shared-db run were the same kind of leftover row. Those tests now use a unique fingerprint and get-or-create the other user.

`npm test` in `frontend`: 29 passed.

The old assertion that a create without a token still writes is gone.
