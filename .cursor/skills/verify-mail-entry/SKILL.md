---
name: verify-mail-entry
description: "Check that a new mail delivery stays one received receipt, that a send without a confirm claim does not accept, that external success follows a provider id, and that a repeat confirm repairs only a stored provider id. Use when editing the mail entry, the mail confirm claim, reconcile, or the follow-up mailto control."
---

# Verify the mail entry

A new message is filed with source `mail` and a delivery id. The same delivery id does not open a second receipt. `propose_send` shows a confirm card and does not insert an acceptance row. `commit_send` refuses an empty token before it inserts that row, and before it checks the transport. An empty `mail_transport` selects no transport. `bind_mail_transport` stores the object and the setting's value at that moment. The object is returned only when the setting is still that same non-empty string. An empty setting ignores the object. A different non-empty string is not registered and selects nothing. The claim stays open, and no `MailAcceptance` row is written. That empty setting is a scaffold. It is not acceptance against a real mailbox. The mailbox is not chosen here, so this check does not describe the target as complete.

With a transport bound under the selected name, `commit_send` arms `attempt:{uuid}` and calls `send` once. The response state is `sent` only after that transport returns a provider id, the acceptance row stores that id, and `note_external_success` returns. An attempt id is not a provider id. A rejection reopens the claim. A timeout, a dropped response, or a raised transport error stays unknown and is not sent again. `note_external_success` runs only after the row exists and the claim is written. Stop does not delete the row. The mailto anchor has no click handler. `search_meeting_mail` stays a read, and the calendar confirm handlers do not call `note_external_success`.

A later confirm of an unknown or written claim calls `_reconcile_send`. An empty id or an attempt id does not repair and does not send. A stored provider id with no transport repairs the acceptance and the receipt. With a bound transport and no acceptance row, only lookup `accepted` repairs. An acceptance that is already stored seals its receipt without lookup. A receipt that is already `external_success` stays sealed. Reconcile does not call `send`. `_write_acceptance` commits the provider id, the acceptance, the written claim, and the receipt together, and rolls them back together.

The question is an order of steps, so the check is TLC on two small models. `MailEntry.tla` is one delivery and one draft. `MailSend.tla` is one claim. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/mail_entry.py` (`receive`, `propose_send`, `commit_send`, `_reconcile_send`, `_write_acceptance`, `_lookup_result`, `bind_mail_transport`)
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
python verification/mail-entry/check.py --break-attempt-repair
python verification/mail-entry/check.py --break-lookup-repair
python verification/mail-entry/check.py --break-reconcile-send
python verification/mail-entry/check.py --break-split-write
python verification/mail-entry/check.py --break-drop-seal
python verification/mail-entry/check.py --break-unseal
python verification/mail-entry/check.py --reach refuse
python verification/mail-entry/check.py --reach attempt
python verification/mail-entry/check.py --reach no-transport
python verification/mail-entry/check.py --reach lookup-accept
python verification/mail-entry/check.py --reach lookup-miss
python verification/mail-entry/check.py --reach legacy
python verification/mail-entry/check.py --reach reseal
```

The held command checks both models and exits 0. Each break command and each `--reach` command exits 1 and names its invariant. TLC must exit 12 and the log must contain `Invariant <name> is violated.` Exit 2 means the checker no longer matches the code, TLC could not run, or the named invariant did not fail. A model error is exit 2.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`OneReceipt` says one delivery id stays at one receipt. The harm is a repeated id opening a second card. In the trace, `Again` sets `receipts` to 2 when `breakDedupe` is true. In the code, `submit_event` returns the stored row when `user_id`, source, and `delivery_id` already match.

`BareWritesNothing` says a refused commit does not set success and does not accept. The harm is a send with no token being marked sent. In the trace, `Bare` sets `success` when `breakBare` is true. In the code, `commit_send` raises `缺少确认，未写入。` before `_accept` and before `_selected_transport`.

`SuccessAfterAccept` says success implies the acceptance row. The harm is `external_success` without the provider row. In the trace, `Seal` sets `success` and leaves `accepted` false when `breakSuccess` is true. In the code, `_write_acceptance` calls `_accept` before `_seal`. A row that is already stored can be sealed again without a second insert.

`StopKeepsAccepted` says a stopped send still has the acceptance row. The harm is stop retracting a message the provider already accepted. In the trace, `Stop` clears `accepted` when `breakStop` is true. In the code, `task_run.stop` does not delete `MailAcceptance`.

`LinkIsNotSend` says the link phase is not success. The harm is opening the mailto link counting as a send. In the trace, `Link` sets `success` when `breakLink` is true. In the code, the anchor uses `mailtoHref` and has no `onClick`.

`NoAcceptWithoutProvider` says acceptance implies the stored id is the provider id. The harm is a row, or a sent card, while the claim still holds `attempt:`. In the trace, `Accept` sets `accepted` and leaves `remote` at `attempt` when `breakNoProvider` is true. In the code, `_usable_provider_id` rejects an empty id, the attempt string, the bare uuid, and any id that starts with `attempt:`.

`OneSendPerClaim` says `sends <= 1`. The harm is one attempt calling the transport twice. In the trace, `Send` sets `sends` to 2 when `breakTwoSends` is true. A rejection reopens the claim and the model resets that attempt, so a later open send is a new attempt at `sends = 1`.

`UnknownDoesNotResend` says that an unknown claim whose stored id is an attempt has `sends = 1`, no acceptance row, and a receipt that is still `received`. The harm is a second send after a dropped response. In the trace, `Resend` sets `sends` to 2 when `breakResend` is true. In the code, a stored value that starts with `attempt:` does not call `send` or `lookup`. An unknown claim that already stores a provider id is a different state, and `RepeatUnstored` does not run for it.

`AttemptDoesNotRepair` says an empty id or an attempt id does not gain an acceptance row or a sealed receipt. The harm is a repeat confirm repairing a send that has no provider id. In the trace, `RepeatUnstored` commits a row while `remote` stays `attempt` when `breakAttemptRepair` is true. In the code, `_reconcile_send` raises unknown before any acceptance write when the id is empty or starts with `attempt:`.

`LookupMissDoesNotRepair` says lookup `other` leaves the acceptance absent, the receipt `received`, and the claim `unknown`. The harm is a repair after lookup returned something other than `accepted`. In the trace, `LookupOther` commits a row while `lookup` stays `other` when `breakLookupRepair` is true. In the code, `_lookup_result` returns `absent` and `unknown` unchanged. An exception, and any string outside `accepted`, `absent`, and `unknown`, becomes `unknown`. Only `accepted` continues. The model value `other` is the collapse of every non-accepted result. Production does not use that string.

`ReconcileDoesNotSend` says a reconcile step leaves `reconcileSends` at 0. The harm is a repeat confirm calling the transport. In the trace, `RepairNoTransport` sets `reconcileSends` to 1 when `breakReconcileSend` is true. In the code, `_reconcile_send` does not call `_invoke_send`.

`CommittedIsAtomic` says a committed acceptance has a provider id, a written claim, and a sealed receipt. The harm is a partial commit of `_write_acceptance`. `--break-split-write` seals nothing after `Accept`. `--break-drop-seal` leaves the receipt `received` after `RepairLegacy`. In the code those writes share one `db.commit()`, and an exception rolls them back.

`SealedReceiptStays` says a receipt that has been sealed stays sealed. The harm is a second confirm clearing `external_success`. In the trace, `Reseal` sets the receipt back to `received` when `breakUnseal` is true. In the code, a receipt that is already `external_success` returns the current view and the following rollback does not clear it.

`--reach` runs the negation of a path witness. Exit 1 means that repair or refusal is reachable. Exit 2 means the path disappeared or TLC failed. The witnesses are `NoRefusePath`, `NoAttemptRefusePath`, `NoTransportPath`, `NoLookupPath`, `NoMissPath`, `NoLegacyPath`, and `NoResealPath`. They are not held invariants.

`TypeOK` says the variables stay in the sets above. TLC also checks deadlock. `Done` stutters after a settled phase.

If the `MailEntry` trace cannot be replayed as `receive`, `propose_send`, and `commit_send`, the model is wrong. If the `MailSend` trace cannot be replayed as one claim's arm, accept, reject, or unknown, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

`MailEntry` is one delivery id and one draft. `MailSend` is one claim. `sends` is 0, 1, or 2. The held send run has 4 initial states, 20 distinct states, and depth 5. Neither model covers a second user, the recipient inbox, or a transport that accepts and returns no id. That last case stays `unknown` in the code and is not sent again. The model value `other` covers production `absent`, production `unknown`, a raised lookup, and an unrecognized string. Production keeps the `absent` and `unknown` strings. The provider-id and legacy acceptance seeds check the guards when those rows are already present. They do not prove a production crash window. The initial state `claim=unknown` with `remote=none` is not a row `arm_remote_claim` commits, and no cited mail test builds it. `NoRefusePath` assumes that row. The boolean gap formula `RepairStateReachable` is liveness. Its exit 13 trace is one counterexample. The unreachability argument is that old `Accept` assigns the provider id and the acceptance in one step. `verification/mail-entry/reconcile-map.md` records both limits. A mismatched id on an already sealed receipt is not a state. The chart is `verification/mail-entry/statechart/`. The source map is `verification/mail-entry/reconcile-map.md`. Live lanes are not part of this check. An empty `mail_transport` on an open claim does not send. A stored provider id with no transport can still repair. That repair is not acceptance against a real mailbox, and it does not make the mail target complete.

## Keep the check with the code

The anchors were rechecked against `_reconcile_send` and `_write_acceptance`. The send reconcile confirmation commit is `fbe83c2b81af81c2eb8b84a9cdffde0cc5771a32`. Move that commit forward only after reading `receive`, `commit_send`, `_reconcile_send`, `_write_acceptance`, `_lookup_result`, `_seal`, the mail branch in `_open_task`, `write_scope`, the mailto anchor, and `calendar.py`, and seeing that both models still describe them.

The empty-token refusal stays before any `_accept`. `_selected_transport` stays before `consume_schedule_claim`. `_seal` still calls `note_external_success`. The calendar sentence `结果未知，未再次写入。请先核对日历。` stays in `schedule_confirm.py`. Mail uses `结果未知，未再次发送。` and does not read `CONFIRM_DETAIL["unknown"]`.

If the empty-token refusal moves below `_accept`, if the transport check moves below `consume_schedule_claim`, if `gmail.py` imports `event_inbox`, or if `calendar.py` calls `note_external_success`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
