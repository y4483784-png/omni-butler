# Mail send reconcile map

Release base `9e336b42b26edd008eea75b1bd259a73fc59b719`. The production function is `_reconcile_send` in `backend/app/services/mail_entry.py`. The model is `verification/mail-entry/MailSend.tla`. This file is the source anchor for that model.

## Boolean gap

`MailSend` at that release had `claim`, `remote`, `sends`, `accepted`, and `success`. `Accept` set `remote` to `provider` and `accepted` to true in one step. A stored provider id with no acceptance was not a state.

`verification/mail-entry/gap/MailSendGap.tla` keeps that model and asks TLC for `<>(remote = "provider" /\ ~accepted)`. TLC exits 13. The trace is the open claim, `Send`, then `Accept` with `accepted = TRUE`, then a stutter. The property name in the log is `RepairStateReachable`.

## Confirm again

`commit_send` calls `_reconcile_send` when the claim status is `unknown` or `written`, at lines 314-322. That call does not reach `_invoke_send`.

`_reconcile_send` lines 412-415 keep `remote` only when it is non-empty and does not start with `_ATTEMPT_PREFIX`. Otherwise the function rolls back and raises the unknown mail error. An empty id and an attempt id take that branch.

When `_delivery_acceptance` finds a row, lines 416-434 run before the transport check.

- Lines 418-426, the receipt state is `external_success`. The function calls `_seal` and then `db.rollback()`.
- Lines 427-434, the receipt is not `external_success`. The function calls `_write_acceptance`.

When no acceptance row exists and `_selected_transport()` is `None`, lines 435-444 call `_write_acceptance`. They do not call `_lookup_result`.

When a transport is bound, lines 445-454 roll back, then call `_lookup_result`. Line 446 continues only when the result is `accepted`. Any other result raises the unknown mail error.

`_lookup_result` lines 471-478 return `accepted`, `absent`, or `unknown`. An exception, and any other string, becomes `unknown`. The model value `other` is that whole set. `absent` and `unknown` are not separate repairs.

`_reconcile_send` does not call `transport.send`.

## One commit

`_write_acceptance` lines 379-399 assign `remote_request_id`, call `_accept`, call `finish_confirm` with `commit=False`, call `_seal` with `commit=False`, and then `db.commit()`. An exception calls `db.rollback()`.

`note_external_success` lines 186-188 return the current view when the receipt is already `external_success` and the external id matches. They do not commit on that path. Lines 200-202 roll back when the function raises `EventRejected`. `_written_claim` lines 588-591 require claim status `written` and the same remote id before a receipt becomes `external_success`.

A seal that fails inside this transaction does not leave the new acceptance row, the new provider id, or the written claim. `arm_remote_claim` lines 635-646 already committed the attempt id before send. That earlier row remains. The model does not add a state for the rolled-back write. `Accept` is the successful commit. `breakSplitWrite` is the mutation that commits the row without the sealed receipt.

`RepairLegacy` is the same transaction when the acceptance row was already committed. The new writes are the written claim and the sealed receipt. `breakDropSeal` leaves the receipt unsealed. The honest action does not call lookup. The test `test_existing_acceptance_seals_without_send` records `lookups == 0` with a transport bound.

## Seeded states

No honest action creates these states. Splitting `_write_acceptance` would.

| Initial state | Why it is in `Init` |
| --- | --- |
| `claim=unknown`, `remote=none`, `row=none` | Lines 412-415 treat an empty id like an attempt id. The executed tests cover the attempt prefix. The empty id is the same branch. |
| `claim=unknown`, `remote=provider`, `row=none` | `test_stored_provider_repairs_without_a_transport`, `test_lookup_accepted_repairs_without_send`, and `test_lookup_miss_stays_unknown` call `_set_remote` before confirm. |
| `claim=unknown`, `remote=provider`, `row=legacy`, `receipt=received` | `test_existing_acceptance_seals_without_send` inserts `MailAcceptance` and sets the remote id. The receipt is still `received`. |

`RepeatUnstored` on an attempt id is also reached from `Send`. That path is the dropped response in `test_transport_error_stays_unknown` and `test_lost_response_does_not_resend`.

## Model actions

| Action | Production |
| --- | --- |
| `Send` | `commit_send` arms an attempt and calls `_invoke_send` once. |
| `Accept` | `_write_acceptance` after a provider id comes back. One commit. |
| `Reject` | `reopen_known_rejection`. |
| `RepeatUnstored` | Lines 412-415. No acceptance write and no send. |
| `RepairNoTransport` | Lines 435-444. |
| `LookupAccepted` | Lines 446-454 when lookup is `accepted`. |
| `LookupOther` | Lines 446-447 for every other lookup result. |
| `RepairLegacy` | Lines 416-434. Lookup is not called. |
| `Reseal` | Lines 418-426. The receipt stays `external_success`. |

## Held rules

`NoAcceptWithoutProvider`. An acceptance row implies the stored id is a provider id.

`OneSendPerClaim`. One attempt calls send at most once. A rejection resets the counter so a later open claim can send once.

`UnknownDoesNotResend`. An unknown claim whose id is an attempt has `sends = 1`, no acceptance row, and a receipt that is still `received`.

`AttemptDoesNotRepair`. An empty id or an attempt id does not gain an acceptance row or a sealed receipt.

`LookupMissDoesNotRepair`. Lookup `other` leaves the row absent, the receipt `received`, and the claim `unknown`.

`ReconcileDoesNotSend`. A reconcile step does not count a send.

`CommittedIsAtomic`. A row this model commits has a provider id, a written claim, and a sealed receipt.

`SealedReceiptStays`. Once a receipt has been sealed, it stays sealed.

## Bounds

One claim. `sends` is 0, 1, or 2. The held run has 4 initial states, 20 distinct states, and depth 5. Transport is which repair action is enabled, not a stored field. A mismatched id on an already sealed receipt is not a state. A second user, a real mailbox, and provider selection are out of the model. The open-claim refusal for a missing transport stays outside this send model. `commit_send` lines 326-329 still raise `not_connected` before consume, and the checker anchor for that order is unchanged.
