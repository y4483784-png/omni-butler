# Shortest MailSend traces

Held run. 20 distinct states, depth 5, exit 0. The chart is `statechart/chart.mmd`. It draws 10 states and 12 arrows and hides 6 `Done` steps.

Each block below is the shortest TLC trace for that named invariant. The checker exits 1 only when TLC exits 12 and the log contains `Invariant <name> is violated.`

## Repairs the held model reaches

`NoTransportPath`. Stored provider id, no acceptance. `RepairNoTransport` writes the committed row, the written claim, and the sealed receipt. `lookup` is `skipped`. `reconcileSends` stays 0.

`NoLookupPath`. The same stored id. `LookupAccepted` seals the receipt. `lookup` is `accepted`. `reconcileSends` stays 0.

`NoMissPath`. The same stored id. `LookupOther` sets `lookup` to `other` and leaves the row absent, the claim `unknown`, and the receipt `received`. The model name `other` covers production `absent`, production `unknown`, and a lookup that `_lookup_result` normalized to `unknown`.

`NoLegacyPath`. Stored acceptance, receipt still `received`. `RepairLegacy` seals the receipt and does not set `lookup` to `accepted` or `other`.

`NoResealPath`. `RepairNoTransport`, then `Reseal`. The receipt stays `sealed`.

`NoRefusePath`. Empty stored id. `RepeatUnstored` leaves the row absent and the receipt `received`.

`NoAttemptRefusePath`. Open claim, `Send`, then `RepeatUnstored`. The remote id stays `attempt`, the row stays absent, and `sends` stays 1.

## Mutations

`AttemptDoesNotRepair`. Open claim, `Send`, `RepeatUnstored`. The break writes a committed row while `remote` stays `attempt`.

`LookupMissDoesNotRepair`. Stored provider id, `LookupOther`. The break writes a committed row while `lookup` stays `other`.

`ReconcileDoesNotSend`. Stored provider id, `RepairNoTransport`. The break sets `reconcileSends` to 1 on that repair.

`CommittedIsAtomic` with `--break-split-write`. Open claim, `Send`, `Accept`. The row is committed and the claim is written, and the receipt stays `received`.

`CommittedIsAtomic` with `--break-drop-seal`. Stored acceptance, `RepairLegacy`. The row becomes committed and the receipt stays `received`.

`SealedReceiptStays`. `RepairNoTransport`, then `Reseal`. `everSealed` stays true and the break sets the receipt back to `received`.

`NoAcceptWithoutProvider`. Open claim, `Send`, `Accept`. The break leaves `remote` at `attempt` with a committed row.

`UnknownDoesNotResend`. Open claim, `Send`, `Resend`. `sends` becomes 2 while the claim is `unknown` and the id is an attempt.
