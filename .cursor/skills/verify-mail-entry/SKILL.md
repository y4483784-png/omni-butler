---
name: verify-mail-entry
description: "Check that a new mail delivery stays one received receipt, that a send without a confirm claim does not accept, and that external success follows provider acceptance. Use when editing the mail entry, the mail confirm claim, or the follow-up mailto control."
---

# Verify the mail entry

A new message is filed with source `mail` and a delivery id. The same delivery id does not open a second receipt. `propose_send` shows a confirm card and does not insert an acceptance row. `commit_send` refuses an empty token before it inserts that row. `note_external_success` runs only after the row exists and the claim is written. Stop does not delete the row. The mailto anchor has no click handler. `search_meeting_mail` stays a read, and the calendar confirm handlers do not call `note_external_success`.

The question is an order of steps, so the check is TLC on a small model of those steps. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/mail_entry.py` (`receive`, `propose_send`, `commit_send`)
- `backend/app/services/event_inbox.py` (the source `mail` branch in `_open_task`)
- `backend/app/services/schedule_confirm.py` (`mail.send`, `write_parts`, `write_scope`)
- `backend/app/api/events.py` (source `mail` calls `receive`)
- `backend/app/api/mail.py`
- `frontend/src/components/FollowupCard.tsx` (the mailto anchor and the send button)
- `frontend/src/components/MailSendCard.tsx`

## Run

From the repo root, with Java on PATH:

```sh
python verification/mail-entry/check.py
python verification/mail-entry/check.py --break-dedupe
python verification/mail-entry/check.py --break-bare
python verification/mail-entry/check.py --break-success
python verification/mail-entry/check.py --break-stop
python verification/mail-entry/check.py --break-link
```

The held command exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`OneReceipt` says one delivery id stays at one receipt. The harm is a repeated id opening a second card. In the trace, `Again` sets `receipts` to 2 when `breakDedupe` is true. In the code, `submit_event` returns the stored row when `user_id`, source, and `delivery_id` already match.

`BareWritesNothing` says a refused commit does not set success and does not accept. The harm is a send with no token being marked sent. In the trace, `Bare` sets `success` when `breakBare` is true. In the code, `commit_send` raises `缺少确认，未写入。` before `_accept`.

`SuccessAfterAccept` says success implies the acceptance row. The harm is `external_success` without the provider row. In the trace, `Seal` sets `success` and leaves `accepted` false when `breakSuccess` is true. In the code, `_seal` calls `note_external_success` only after `_accept`.

`StopKeepsAccepted` says a stopped send still has the acceptance row. The harm is stop retracting a message the provider already accepted. In the trace, `Stop` clears `accepted` when `breakStop` is true. In the code, `task_run.stop` does not delete `MailAcceptance`.

`LinkIsNotSend` says the link phase is not success. The harm is opening the mailto link counting as a send. In the trace, `Link` sets `success` when `breakLink` is true. In the code, the anchor uses `mailtoHref` and has no `onClick`.

`TypeOK` says the variables stay in the sets above. TLC also checks deadlock. `Done` stutters after a settled phase.

If the trace cannot be replayed as `receive`, `propose_send`, and `commit_send`, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one delivery id and one draft. It does not model a second user, a waiting-person reissue, a Gmail network send, or the calendar confirm HTTP path. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at the commit that added this skill. Move the confirmation forward only after reading `receive`, `commit_send`, `_seal`, the mail branch in `_open_task`, `write_scope`, the mailto anchor, and `calendar.py`, and seeing that the model still describes them. If the empty-token refusal moves below `_accept`, if `gmail.py` imports `event_inbox`, or if `calendar.py` calls `note_external_success`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
