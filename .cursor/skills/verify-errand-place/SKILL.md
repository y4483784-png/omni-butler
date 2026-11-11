---
name: verify-errand-place
description: "Check that one errand places once, that a missing claim or pay secret does not pay, and that an unknown result or a bare 已下单 page does not place again. Use when editing the errand confirm path, the vault, or the compose browser service."
---

# Verify the errand place path

`propose_errand` shows a confirm card and does not insert an `ErrandRecord`. `commit_errand` refuses an empty token before it reads the vault or calls a shop. A missing pay secret rolls the session back before `arm_remote_claim`. After the claim is armed, one call goes to the connector or to the compose service. A second click looks up by that armed id and does not place again. A page that says `已下单` without this armed id does not become a record. An app wall before a pay click does not click. Stop does not delete a record that already exists. The calendar confirm path does not call `note_external_success`.

The question is an order of steps, so the check is TLC on a small model of those steps. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/errands.py` (`propose_errand`, `commit_errand`, `_reconcile`, `public_start_url`)
- `backend/app/services/errand_order.py`
- `backend/app/services/errand_vault.py`
- `backend/app/api/errand.py`
- `backend/app/services/schedule_confirm.py` (`errand.place`, `write_parts`, `write_scope`)
- `services/errand-compose/`
- `frontend/src/components/ErrandCard.tsx`

## Run

From the repo root, with Java on PATH. `python` is not on PATH. Use `python3`.

```sh
python3 verification/errand-place/check.py
python3 verification/errand-place/check.py --break-bare
python3 verification/errand-place/check.py --break-unpaid
python3 verification/errand-place/check.py --break-success
python3 verification/errand-place/check.py --break-again
python3 verification/errand-place/check.py --break-seen
python3 verification/errand-place/check.py --break-page
python3 verification/errand-place/check.py --break-wall
python3 verification/errand-place/check.py --break-stop
```

The held command exits 0. Each break command exits 1 and names its invariant. Two break flags exit 2. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`AtMostOnePlace` says `places` stays at most 1. The harm is a second order. In the trace, `Again` or `Seen` sets `places` to 2 when `breakAgain` or `breakSeen` is true. In the code, a claim that is already unknown goes to `_reconcile`, which calls `decide_replay` and looks up. It does not call `_place`.

`BareWritesNothing` says an empty token does not place. The harm is a commit with no claim paying the shop. In the trace, `Bare` sets `places` to 1 when `breakBare` is true. In the code, `commit_errand` calls `consume_schedule_claim` with an empty token and raises `缺少确认，未写入。` before the vault read and before `_place`.

`UnpaidWritesNothing` says a missing pay secret does not place. The harm is a charge with no credential. In the trace, `Unpaid` sets `places` to 1 when `breakUnpaid` is true. In the code, `commit_errand` calls `db.rollback()` and raises `没有可用的支付凭证，未支付。` before `arm_remote_claim`.

`SuccessAfterRecord` says a written claim has the acceptance row. The harm is sealing the card when the shop acceptance was not stored. In the trace, `Place` sets `record` false when `breakSuccess` is true. In the code, `_save` inserts `ErrandRecord` and then calls `finish_confirm`.

`UnnamedIsNotOrder` says a page that says `已下单` without this armed id does not become the record. The harm is a check page turning into a new order. In the trace, `BarePage` sets `record` when `breakPage` is true. In the code, `interpret` treats that page as already only when the armed id is present.

`WallDoesNotClick` says an app wall does not click. The harm is an automatic click inside an app. In the trace, `Wall` sets `clicks` to 1 when `breakWall` is true. In the code, `decide` returns `app_wall` before a buy click when `pay_clicked` is false, and the driver returns before `_click_buy`.

`StopKeepsRecord` says stop after acceptance still has the row. The harm is stop deleting an order the shop already accepted. In the trace, `Stop` clears `record` when `breakStop` is true. In the code, `task_run.stop` does not delete `ErrandRecord`.

`TypeOK` says the variables stay in the sets above. TLC also checks deadlock. `Done` stutters after a settled phase.

If the trace cannot be replayed as `propose_errand` and `commit_errand`, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one order. It does not model a second user, a real Chromium launch, a network shop, or the calendar confirm HTTP path. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at `54989e70edb0962c4219629df647b140f35bc477`, including a scratch deletion of the empty-token refusal that made `check.py` exit 2 with `modeling error: commit without a token is not refused`. The restored file then exited 0. Move the confirmation forward only after reading `commit_errand`, `_reconcile`, `_save`, `decide`, `driver.run_task`, `write_parts`, and `calendar.py`, and seeing that the model still describes them. If the empty-token refusal moves below `arm_remote_claim`, if `errands.py` calls `note_external_success` or `settle_unknown`, or if `decide.py` imports Playwright, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
