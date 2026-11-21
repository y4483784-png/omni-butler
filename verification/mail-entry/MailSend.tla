---- MODULE MailSend ----
EXTENDS Integers

CONSTANT
  breakNoProvider,
  breakTwoSends,
  breakResend,
  breakAttemptRepair,
  breakLookupRepair,
  breakReconcileSend,
  breakSplitWrite,
  breakDropSeal,
  breakUnseal

VARIABLES
  claim,
  remote,
  sends,
  row,
  receipt,
  lookup,
  reconcileSends,
  everSealed,
  resealed,
  refused,
  seen

vars == <<
  claim, remote, sends, row, receipt, lookup,
  reconcileSends, everSealed, resealed, refused, seen
>>

Claims == {"open", "unknown", "written"}
Remotes == {"none", "attempt", "provider"}
Rows == {"none", "legacy", "committed"}
Receipts == {"received", "sealed"}
Lookups == {"none", "skipped", "accepted", "other"}
Witness == {"refuse", "attemptRefuse", "noTransport", "lookup", "legacy", "reseal"}

accepted == row \in {"legacy", "committed"}

Init ==
  /\ sends = 0
  /\ lookup = "none"
  /\ reconcileSends = 0
  /\ everSealed = FALSE
  /\ resealed = FALSE
  /\ refused = FALSE
  /\ seen = {}
  /\ receipt = "received"
  /\ \/ /\ claim = "open"
        /\ remote = "none"
        /\ row = "none"
     \/ /\ claim = "unknown"
        /\ remote = "none"
        /\ row = "none"
     \/ /\ claim = "unknown"
        /\ remote = "provider"
        /\ row = "none"
     \/ /\ claim = "unknown"
        /\ remote = "provider"
        /\ row = "legacy"

Send ==
  /\ claim = "open"
  /\ remote = "none"
  /\ row = "none"
  /\ receipt = "received"
  /\ sends' = IF breakTwoSends THEN 2 ELSE 1
  /\ claim' = "unknown"
  /\ remote' = "attempt"
  /\ row' = "none"
  /\ receipt' = "received"
  /\ lookup' = "none"
  /\ reconcileSends' = reconcileSends
  /\ everSealed' = everSealed
  /\ resealed' = resealed
  /\ refused' = FALSE
  /\ seen' = seen

Accept ==
  /\ claim = "unknown"
  /\ remote = "attempt"
  /\ sends = 1
  /\ row = "none"
  /\ receipt = "received"
  /\ sends' = sends
  /\ lookup' = "none"
  /\ reconcileSends' = reconcileSends
  /\ resealed' = FALSE
  /\ refused' = FALSE
  /\ seen' = seen
  /\ IF breakNoProvider THEN
       /\ claim' = "written"
       /\ remote' = "attempt"
       /\ row' = "committed"
       /\ receipt' = "received"
       /\ everSealed' = FALSE
     ELSE IF breakSplitWrite THEN
       /\ claim' = "written"
       /\ remote' = "provider"
       /\ row' = "committed"
       /\ receipt' = "received"
       /\ everSealed' = FALSE
     ELSE
       /\ claim' = "written"
       /\ remote' = "provider"
       /\ row' = "committed"
       /\ receipt' = "sealed"
       /\ everSealed' = TRUE

Reject ==
  /\ claim = "unknown"
  /\ remote = "attempt"
  /\ row = "none"
  /\ receipt = "received"
  /\ claim' = "open"
  /\ remote' = "none"
  /\ sends' = 0
  /\ row' = "none"
  /\ receipt' = "received"
  /\ lookup' = "none"
  /\ reconcileSends' = reconcileSends
  /\ everSealed' = everSealed
  /\ resealed' = resealed
  /\ refused' = FALSE
  /\ seen' = seen

Resend ==
  /\ breakResend
  /\ claim = "unknown"
  /\ remote = "attempt"
  /\ sends = 1
  /\ row = "none"
  /\ receipt = "received"
  /\ sends' = 2
  /\ UNCHANGED <<
       claim, remote, row, receipt, lookup,
       reconcileSends, everSealed, resealed, refused, seen
     >>

RepeatUnstored ==
  /\ claim = "unknown"
  /\ remote \in {"none", "attempt"}
  /\ row = "none"
  /\ receipt = "received"
  /\ ~refused
  /\ sends' = sends
  /\ lookup' = lookup
  /\ reconcileSends' = reconcileSends
  /\ resealed' = resealed
  /\ refused' = TRUE
  /\ seen' = seen \union (IF remote = "attempt" THEN {"refuse", "attemptRefuse"} ELSE {"refuse"})
  /\ IF breakAttemptRepair /\ remote = "attempt" THEN
       /\ claim' = "written"
       /\ remote' = "attempt"
       /\ row' = "committed"
       /\ receipt' = "sealed"
       /\ everSealed' = TRUE
     ELSE
       /\ claim' = claim
       /\ remote' = remote
       /\ row' = "none"
       /\ receipt' = "received"
       /\ everSealed' = everSealed

RepairNoTransport ==
  /\ claim = "unknown"
  /\ remote = "provider"
  /\ row = "none"
  /\ receipt = "received"
  /\ claim' = "written"
  /\ remote' = "provider"
  /\ row' = "committed"
  /\ receipt' = "sealed"
  /\ lookup' = "skipped"
  /\ sends' = sends
  /\ reconcileSends' = IF breakReconcileSend THEN 1 ELSE reconcileSends
  /\ everSealed' = TRUE
  /\ resealed' = FALSE
  /\ refused' = FALSE
  /\ seen' = seen \union {"noTransport"}

LookupAccepted ==
  /\ claim = "unknown"
  /\ remote = "provider"
  /\ row = "none"
  /\ receipt = "received"
  /\ claim' = "written"
  /\ remote' = "provider"
  /\ row' = "committed"
  /\ receipt' = "sealed"
  /\ lookup' = "accepted"
  /\ sends' = sends
  /\ reconcileSends' = reconcileSends
  /\ everSealed' = TRUE
  /\ resealed' = FALSE
  /\ refused' = FALSE
  /\ seen' = seen \union {"lookup"}

LookupOther ==
  /\ claim = "unknown"
  /\ remote = "provider"
  /\ row = "none"
  /\ receipt = "received"
  /\ lookup = "none"
  /\ sends' = sends
  /\ reconcileSends' = reconcileSends
  /\ resealed' = resealed
  /\ refused' = refused
  /\ seen' = seen
  /\ lookup' = "other"
  /\ IF breakLookupRepair THEN
       /\ claim' = "written"
       /\ remote' = "provider"
       /\ row' = "committed"
       /\ receipt' = "sealed"
       /\ everSealed' = TRUE
     ELSE
       /\ claim' = claim
       /\ remote' = remote
       /\ row' = "none"
       /\ receipt' = "received"
       /\ everSealed' = everSealed

RepairLegacy ==
  /\ claim = "unknown"
  /\ remote = "provider"
  /\ row = "legacy"
  /\ receipt = "received"
  /\ claim' = "written"
  /\ remote' = "provider"
  /\ row' = "committed"
  /\ lookup' = "none"
  /\ sends' = sends
  /\ reconcileSends' = reconcileSends
  /\ resealed' = FALSE
  /\ refused' = FALSE
  /\ seen' = seen \union {"legacy"}
  /\ IF breakDropSeal THEN
       /\ receipt' = "received"
       /\ everSealed' = FALSE
     ELSE
       /\ receipt' = "sealed"
       /\ everSealed' = TRUE

Reseal ==
  /\ receipt = "sealed"
  /\ row = "committed"
  /\ claim = "written"
  /\ remote = "provider"
  /\ ~resealed
  /\ resealed' = TRUE
  /\ everSealed' = TRUE
  /\ seen' = seen \union {"reseal"}
  /\ UNCHANGED <<claim, remote, sends, row, lookup, reconcileSends, refused>>
  /\ receipt' = IF breakUnseal THEN "received" ELSE "sealed"

Done ==
  /\ \/ /\ claim = "written"
        /\ resealed \/ receipt # "sealed"
     \/ /\ claim = "unknown"
        /\ remote = "none"
        /\ refused
        /\ row = "none"
  /\ UNCHANGED vars

Next ==
  \/ Send
  \/ Accept
  \/ Reject
  \/ Resend
  \/ RepeatUnstored
  \/ RepairNoTransport
  \/ LookupAccepted
  \/ LookupOther
  \/ RepairLegacy
  \/ Reseal
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ claim \in Claims
  /\ remote \in Remotes
  /\ sends \in 0..2
  /\ row \in Rows
  /\ receipt \in Receipts
  /\ lookup \in Lookups
  /\ reconcileSends \in 0..1
  /\ everSealed \in BOOLEAN
  /\ resealed \in BOOLEAN
  /\ refused \in BOOLEAN
  /\ seen \subseteq Witness

NoAcceptWithoutProvider == accepted => remote = "provider"

OneSendPerClaim == sends <= 1

UnknownDoesNotResend ==
  (claim = "unknown" /\ remote = "attempt") =>
    (sends = 1 /\ row = "none" /\ receipt = "received" /\ reconcileSends = 0)

AttemptDoesNotRepair ==
  remote \in {"none", "attempt"} =>
    (row = "none" /\ receipt = "received" /\ reconcileSends = 0)

LookupMissDoesNotRepair ==
  lookup = "other" =>
    (row = "none" /\ receipt = "received" /\ claim = "unknown" /\ reconcileSends = 0)

ReconcileDoesNotSend == reconcileSends = 0

CommittedIsAtomic ==
  row = "committed" =>
    (remote = "provider" /\ claim = "written" /\ receipt = "sealed")

SealedReceiptStays == everSealed => receipt = "sealed"

NoRefusePath == "refuse" \notin seen
NoAttemptRefusePath == "attemptRefuse" \notin seen
NoTransportPath == "noTransport" \notin seen
NoLookupPath == "lookup" \notin seen
NoLegacyPath == "legacy" \notin seen
NoResealPath == "reseal" \notin seen
NoMissPath == lookup # "other"

====
