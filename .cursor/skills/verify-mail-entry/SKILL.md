---
name: verify-mail-entry
description: "Check that a new mail delivery stays one received receipt, that a send without a confirm claim does not accept, and that external success follows a provider id from the selected transport. Use when editing the mail entry, the mail confirm claim, or the follow-up mailto control."
---

# Verify the mail entry

A new message is filed with source `mail` and a delivery id. The same delivery id does not open a second receipt. `propose_send` shows a confirm card and does not insert an acceptance row. `commit_send` refuses an empty token before it inserts that row, and before it checks the transport. An empty `mail_transport` selects no transport. `bind_mail_transport` stores the object and the setting's value at that moment. The object is returned only when the setting is still that same non-empty string. An empty setting ignores the object. A different non-empty string is not registered and selects nothing. The claim stays open, and no `MailAcceptance` row is written. That empty setting is a scaffold. It is not acceptance against a real mailbox. The mailbox is not chosen here, so this check does not describe the target as complete.

With a transport bound under the selected name, `commit_send` arms `attempt:{uuid}` and calls `send` once. The response state is `sent` only after that transport returns a provider id, the acceptance row stores that id, and `note_external_success` returns. An attempt id is not a provider id. A rejection reopens the claim. A timeout, a dropped response, or a raised transport error stays unknown and is not sent again. `note_external_success` runs only after the row exists and the claim is written. Stop does not delete the row. The mailto anchor has no click handler. `search_meeting_mail` stays a read, and the calendar confirm handlers do not call `note_external_success`.

The question is an order of steps, so the check is TLC on two small models. `MailEntry.tla` is one delivery and one draft. `MailSend.tla` is one claim. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/mail_entry.py` (`receive`, `propose_send`, `commit_send`, `bind_mail_transport`)
- `backend/app/services/event_inbox.py` (the source `mail` branch in `_open_task`, and `note_external_success`)
- `backend/app/services/schedule_confirm.py` (`mail.send`, `write_parts`, `write_scope`, `finish_confirm`)
- `backend/app/core/config.py` (`mail_transport`)
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
python verification/mail-entry/check.py --break-no-provider
python verification/mail-entry/check.py --break-two-sends
python verification/mail-entry/check.py --break-resend
```

The held command checks both models and exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`OneReceipt` says one delivery id stays at one receipt. The harm is a repeated id opening a second card. In the trace, `Again` sets `receipts` to 2 when `breakDedupe` is true. In the code, `submit_event` returns the stored row when `user_id`, source, and `delivery_id` already match.

`BareWritesNothing` says a refused commit does not set success and does not accept. The harm is a send with no token being marked sent. In the trace, `Bare` sets `success` when `breakBare` is true. In the code, `commit_send` raises `缺少确认，未写入。` before `_accept` and before `_selected_transport`.

`SuccessAfterAccept` says success implies the acceptance row. The harm is `external_success` without the provider row. In the trace, `Seal` sets `success` and leaves `accepted` false when `breakSuccess` is true. In the code, `_write_acceptance` calls `_accept` before `_seal`. A row that is already stored can be sealed again without a second insert.

`StopKeepsAccepted` says a stopped send still has the acceptance row. The harm is stop retracting a message the provider already accepted. In the trace, `Stop` clears `accepted` when `breakStop` is true. In the code, `task_run.stop` does not delete `MailAcceptance`.

`LinkIsNotSend` says the link phase is not success. The harm is opening the mailto link counting as a send. In the trace, `Link` sets `success` when `breakLink` is true. In the code, the anchor uses `mailtoHref` and has no `onClick`.

`NoAcceptWithoutProvider` says acceptance implies the stored id is the provider id. The harm is a row, or a sent card, while the claim still holds `attempt:`. In the trace, `Accept` sets `accepted` and leaves `remote` at `attempt` when `breakNoProvider` is true. In the code, `_usable_provider_id` rejects an empty id, the attempt string, the bare uuid, and any id that starts with `attempt:`.

`OneSendPerClaim` says `sends <= 1`. The harm is one attempt calling the transport twice. In the trace, `Send` sets `sends` to 2 when `breakTwoSends` is true. A rejection reopens the claim and the model resets that attempt, so a later open send is a new attempt at `sends = 1`.

`UnknownDoesNotResend` says that while `claim` is `unknown`, `sends` stays 1, `remote` stays `attempt`, and `accepted` and `success` stay false. The harm is a second send after a dropped response. In the trace, `Resend` increments `sends` when `breakResend` is true. In the code, a stored value that starts with `attempt:` does not call `send` or `lookup`.

`TypeOK` says the variables stay in the sets above. TLC also checks deadlock. `Done` stutters after a settled phase.

If the `MailEntry` trace cannot be replayed as `receive`, `propose_send`, and `commit_send`, the model is wrong. If the `MailSend` trace cannot be replayed as one claim's arm, accept, reject, or unknown, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

`MailEntry` is one delivery id and one draft. `MailSend` is one claim. Neither model covers a second user, the recipient inbox, or a transport that accepts and returns no id. That last case stays `unknown` in the code and is not sent again. Live lanes are not part of this check. An empty `mail_transport` proves the product does not say sent. It is not acceptance against a real mailbox, and it does not make the mail target complete.

## Keep the check with the code

The anchors were rechecked against the transport scaffold. Move the confirmation forward only after reading `receive`, `commit_send`, `_write_acceptance`, `_seal`, the mail branch in `_open_task`, `write_scope`, the mailto anchor, and `calendar.py`, and seeing that both models still describe them.

The empty-token refusal stays before any `_accept`. `_selected_transport` stays before `consume_schedule_claim`. `_seal` still calls `note_external_success`. The calendar sentence `结果未知，未再次写入。请先核对日历。` stays in `schedule_confirm.py`. Mail uses `结果未知，未再次发送。` and does not read `CONFIRM_DETAIL["unknown"]`.

If the empty-token refusal moves below `_accept`, if the transport check moves below `consume_schedule_claim`, if `gmail.py` imports `event_inbox`, or if `calendar.py` calls `note_external_success`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
