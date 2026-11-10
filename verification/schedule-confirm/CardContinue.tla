------------------------------ MODULE CardContinue ------------------------------

EXTENDS Integers

CONSTANT BREAK

VARIABLES unrelated, click, claim, borrowed, writes, rows

vars == <<unrelated, click, claim, borrowed, writes, rows>>

TypeOK ==
  /\ unrelated \in {"idle", "live", "stopped"}
  /\ click \in {"absent", "waiting", "stopped"}
  /\ claim \in {"none", "open", "written", "cancelled"}
  /\ borrowed \in BOOLEAN
  /\ writes \in 0..1
  /\ rows \in 0..2
  /\ BREAK \in {"none", "borrow"}

NoWriteWithoutToken == writes = 0 \/ claim = "written"
AtMostOneSuccessfulWrite == writes <= 1
AtMostOneOpenRow == rows <= 1
NoBorrow == ~borrowed
UnrelatedStopKeepsClick == ~(click = "waiting" /\ claim = "cancelled")

Init ==
  /\ unrelated = "idle"
  /\ click = "absent"
  /\ claim = "none"
  /\ borrowed = FALSE
  /\ writes = 0
  /\ rows = 0

StartUnrelated ==
  /\ unrelated = "idle"
  /\ unrelated' = "live"
  /\ UNCHANGED <<click, claim, borrowed, writes, rows>>

StopUnrelated ==
  /\ unrelated = "live"
  /\ unrelated' = "stopped"
  /\ UNCHANGED <<click, claim, borrowed, writes, rows>>

Continue ==
  /\ click = "absent"
  /\ click' = "waiting"
  /\ \/ /\ claim \in {"none", "cancelled"}
        /\ rows = 0
        /\ claim' = "open"
        /\ rows' = 1
        /\ borrowed' = (BREAK = "borrow" /\ unrelated = "live")
     \/ /\ claim = "open"
        /\ rows' = rows
        /\ borrowed' = borrowed \/ (BREAK = "borrow" /\ unrelated = "live")
  /\ UNCHANGED <<unrelated, writes>>

StopClick ==
  /\ click = "waiting"
  /\ click' = "stopped"
  /\ claim' = IF claim = "open" /\ ~borrowed THEN "cancelled" ELSE claim
  /\ rows' = IF claim' = "open" THEN rows ELSE 0
  /\ UNCHANGED <<unrelated, borrowed, writes>>

Consume ==
  /\ writes = 0
  /\ claim = "open"
  /\ click = "waiting"
  /\ ~borrowed
  /\ writes' = 1
  /\ claim' = "written"
  /\ rows' = 0
  /\ UNCHANGED <<unrelated, click, borrowed>>

Idle == UNCHANGED vars

Next ==
  \/ StartUnrelated
  \/ StopUnrelated
  \/ Continue
  \/ StopClick
  \/ Consume
  \/ Idle

Spec == Init /\ [][Next]_vars

================================================================================
