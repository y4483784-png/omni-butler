---- MODULE MailEntry ----
\* One delivery id and one outbound draft.
\* Filing stores one receipt and does not mark external success.
\* The same delivery id does not open a second receipt.
\* A confirm card is not acceptance. A missing token does not accept.
\* External success follows the acceptance row.
\* Stop after acceptance leaves that row. Opening the link does not send.

EXTENDS Integers

CONSTANT breakDedupe, breakBare, breakSuccess, breakStop, breakLink

VARIABLES phase, receipts, claim, accepted, success

vars == <<phase, receipts, claim, accepted, success>>

Phases == {
  "start", "filed", "repeated", "offered", "refused",
  "armed", "sealed", "stopped", "linked"
}
Claims == {"none", "open", "unknown", "written"}

Init ==
  /\ phase = "start"
  /\ receipts = 0
  /\ claim = "none"
  /\ accepted = FALSE
  /\ success = FALSE

\* receive. The first delivery is filed. Success stays false.
File ==
  /\ phase = "start"
  /\ phase' = "filed"
  /\ receipts' = 1
  /\ claim' = "none"
  /\ accepted' = FALSE
  /\ success' = FALSE

\* The same delivery id. breakDedupe opens a second receipt.
Again ==
  /\ phase = "filed"
  /\ phase' = "repeated"
  /\ receipts' = IF breakDedupe THEN 2 ELSE 1
  /\ UNCHANGED <<claim, accepted, success>>

\* propose_send. The card is shown. The acceptance row is not inserted.
Offer ==
  /\ phase \in {"filed", "repeated"}
  /\ phase' = "offered"
  /\ claim' = "open"
  /\ accepted' = FALSE
  /\ success' = FALSE
  /\ UNCHANGED receipts

\* commit_send with an empty token. breakBare marks success anyway.
Bare ==
  /\ phase = "offered"
  /\ phase' = "refused"
  /\ accepted' = FALSE
  /\ success' = breakBare
  /\ UNCHANGED <<receipts, claim>>

\* arm_remote_claim commits before the acceptance row exists.
Arm ==
  /\ phase = "offered"
  /\ phase' = "armed"
  /\ claim' = "unknown"
  /\ accepted' = FALSE
  /\ success' = FALSE
  /\ UNCHANGED receipts

\* The acceptance insert, then finish_confirm, then note_external_success.
\* breakSuccess marks success without the row.
Seal ==
  /\ phase = "armed"
  /\ phase' = "sealed"
  /\ claim' = "written"
  /\ accepted' = ~breakSuccess
  /\ success' = TRUE
  /\ UNCHANGED receipts

\* Stop after the row exists. breakStop deletes the row.
Stop ==
  /\ phase = "sealed"
  /\ phase' = "stopped"
  /\ claim' = "written"
  /\ accepted' = ~breakStop
  /\ success' = TRUE
  /\ UNCHANGED receipts

\* The mailto anchor. breakLink treats the click as a send.
Link ==
  /\ phase \in {"filed", "offered"}
  /\ phase' = "linked"
  /\ accepted' = FALSE
  /\ success' = breakLink
  /\ UNCHANGED <<receipts, claim>>

Done ==
  /\ phase \in {"repeated", "refused", "sealed", "stopped", "linked"}
  /\ UNCHANGED vars

Next ==
  \/ File
  \/ Again
  \/ Offer
  \/ Bare
  \/ Arm
  \/ Seal
  \/ Stop
  \/ Link
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in Phases
  /\ receipts \in {0, 1, 2}
  /\ claim \in Claims
  /\ accepted \in BOOLEAN
  /\ success \in BOOLEAN

OneReceipt == receipts <= 1

BareWritesNothing == phase = "refused" => ~success /\ ~accepted

SuccessAfterAccept == success => accepted

StopKeepsAccepted == phase = "stopped" => accepted

LinkIsNotSend == phase = "linked" => ~success

====
