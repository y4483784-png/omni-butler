---- MODULE DesktopOpen ----
EXTENDS Integers

CONSTANT breakLie, breakStop, breakEarly, breakAgain, breakKeep

VARIABLES phase, opens, claim

vars == <<phase, opens, claim>>

Phases == {
  "start", "lied", "offered", "stopped", "armed",
  "opened", "again", "kept", "retried"
}
Claims == {"none", "open", "cancelled", "unknown", "written"}

Init ==
  /\ phase = "start"
  /\ opens = 0
  /\ claim = "none"

Lie ==
  /\ phase = "start"
  /\ phase' = "lied"
  /\ opens' = IF breakLie THEN 1 ELSE 0
  /\ claim' = "none"

Offer ==
  /\ phase = "start"
  /\ phase' = "offered"
  /\ opens' = 0
  /\ claim' = "open"

StopBefore ==
  /\ phase = "offered"
  /\ phase' = "stopped"
  /\ opens' = IF breakStop THEN 1 ELSE 0
  /\ claim' = "cancelled"

Arm ==
  /\ phase = "offered"
  /\ phase' = "armed"
  /\ opens' = 0
  /\ claim' = "unknown"

Open ==
  /\ \/ /\ ~breakEarly /\ phase = "armed"
     \/ /\ breakEarly /\ phase = "offered"
  /\ phase' = "opened"
  /\ opens' = 1
  /\ claim' = IF breakEarly THEN "open" ELSE "written"

Again ==
  /\ phase = "opened"
  /\ phase' = "again"
  /\ opens' = IF breakAgain THEN 2 ELSE opens
  /\ UNCHANGED claim

Retry ==
  /\ phase = "armed"
  /\ phase' = "retried"
  /\ opens' = opens
  /\ UNCHANGED claim

StopAfter ==
  /\ phase = "opened"
  /\ phase' = "kept"
  /\ opens' = IF breakKeep THEN 0 ELSE opens
  /\ UNCHANGED claim

Done ==
  /\ phase \in {"lied", "stopped", "again", "kept", "retried"}
  /\ UNCHANGED vars

Next ==
  \/ Lie
  \/ Offer
  \/ StopBefore
  \/ Arm
  \/ Open
  \/ Again
  \/ Retry
  \/ StopAfter
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in Phases
  /\ opens \in 0..2
  /\ claim \in Claims

LieOpensNothing == phase = "lied" => opens = 0 /\ claim = "none"

StopBeforeOpensNothing == phase = "stopped" => opens = 0 /\ claim = "cancelled"

AtMostOneOpen == opens <= 1

OpenAfterArm == phase \in {"opened", "again", "kept"} => opens = 1 /\ claim = "written"

RetryOpensNothing == phase = "retried" => opens = 0 /\ claim = "unknown"

StopKeepsOpen == phase = "kept" => opens = 1

====
