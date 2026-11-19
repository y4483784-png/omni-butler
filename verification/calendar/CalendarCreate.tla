---- MODULE CalendarCreate ----
EXTENDS Integers

CONSTANT breakSuccess, breakSecondInsert, breakLookupInsert, breakWrittenComplete

VARIABLES claim, armed, event, receipt, posts, gets, success, phase

vars == <<claim, armed, event, receipt, posts, gets, success, phase>>

Init ==
  /\ claim = "open"
  /\ armed = FALSE
  /\ event = "none"
  /\ receipt = "none"
  /\ posts = 0
  /\ gets = 0
  /\ success = FALSE
  /\ phase = "idle"

Post ==
  /\ phase = "idle"
  /\ claim = "open"
  /\ posts' = 1
  /\ claim' = "unknown"
  /\ armed' = TRUE
  /\ phase' = "posted"
  /\ UNCHANGED <<event, receipt, gets, success>>

Store ==
  /\ phase = "posted"
  /\ event' = "evt"
  /\ phase' = "stored"
  /\ UNCHANGED <<claim, armed, receipt, posts, gets, success>>

Lose ==
  /\ phase = "posted"
  /\ phase' = "lost"
  /\ UNCHANGED <<claim, armed, event, receipt, posts, gets, success>>

LookupMiss ==
  /\ phase = "lost"
  /\ gets = 0
  /\ gets' = 1
  /\ posts' = IF breakLookupInsert THEN posts + 1 ELSE posts
  /\ UNCHANGED <<claim, armed, event, receipt, success, phase>>

LookupHit ==
  /\ phase = "lost"
  /\ gets = 0
  /\ gets' = 1
  /\ event' = "evt"
  /\ phase' = "stored"
  /\ posts' = IF breakLookupInsert THEN posts + 1 ELSE posts
  /\ UNCHANGED <<claim, armed, receipt, success>>

WriteClaim ==
  /\ phase = "stored"
  /\ event = "evt"
  /\ claim' = "written"
  /\ phase' = "written"
  /\ success' = IF breakWrittenComplete THEN TRUE ELSE FALSE
  /\ UNCHANGED <<armed, event, receipt, posts, gets>>

File ==
  /\ phase = "written"
  /\ receipt' = "filed"
  /\ phase' = "filed"
  /\ UNCHANGED <<claim, armed, event, posts, gets, success>>

Seal ==
  /\ phase = "filed"
  /\ receipt' = "success"
  /\ phase' = "sealed"
  /\ success' = TRUE
  /\ UNCHANGED <<claim, armed, event, posts, gets>>

FalseSuccess ==
  /\ breakSuccess
  /\ event = "none"
  /\ success' = TRUE
  /\ UNCHANGED <<claim, armed, event, receipt, posts, gets, phase>>

SecondPost ==
  /\ breakSecondInsert
  /\ posts = 1
  /\ posts' = 2
  /\ UNCHANGED <<claim, armed, event, receipt, gets, success, phase>>

Crash ==
  /\ phase \in {"stored", "written", "filed"}
  /\ phase' = "crashed"
  /\ UNCHANGED <<claim, armed, event, receipt, posts, gets, success>>

Repair ==
  /\ phase = "crashed"
  /\ event = "evt"
  /\ armed
  /\ claim' = "written"
  /\ receipt' = "success"
  /\ success' = TRUE
  /\ phase' = "sealed"
  /\ UNCHANGED <<armed, event, posts, gets>>

Done ==
  /\ phase \in {"sealed", "lost", "crashed"}
  /\ UNCHANGED vars

Next ==
  \/ Post
  \/ Store
  \/ Lose
  \/ LookupMiss
  \/ LookupHit
  \/ WriteClaim
  \/ File
  \/ Seal
  \/ FalseSuccess
  \/ SecondPost
  \/ Crash
  \/ Repair
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ claim \in {"open", "unknown", "written"}
  /\ armed \in BOOLEAN
  /\ event \in {"none", "evt"}
  /\ receipt \in {"none", "filed", "success"}
  /\ posts \in 0..2
  /\ gets \in 0..1
  /\ success \in BOOLEAN
  /\ phase \in {"idle", "posted", "stored", "lost", "written", "filed", "sealed", "crashed"}

SuccessAfterEventId ==
  success => (event = "evt" /\ receipt = "success" /\ claim = "written" /\ armed)

NoSecondInsert == posts <= 1

LookupNotInsert ==
  (phase = "lost" /\ gets = 1) => posts = 1

WrittenWithoutReceiptIsIncomplete ==
  (claim = "written" /\ receipt # "success") => ~success

====
