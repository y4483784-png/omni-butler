---- MODULE ErrandPlace ----
\* One errand order.
\* An empty token does not place. A missing pay secret does not place.
\* The remote id is armed before the one external call.
\* A second click after an unknown result does not place again.
\* A page that says the order was placed, but does not name this id, is not saved.
\* An app wall does not click pay. Stop does not delete an accepted row.

EXTENDS Integers

CONSTANT
  breakBare, breakUnpaid, breakSuccess, breakAgain,
  breakSeen, breakPage, breakWall, breakStop

VARIABLES phase, places, claim, record, clicks

vars == <<phase, places, claim, record, clicks>>

Phases == {
  "start", "offered", "bare", "unpaid", "armed", "placed",
  "unknown", "retried", "adopted", "unnamed", "wall", "stopped"
}
Claims == {"none", "open", "unknown", "written"}

Init ==
  /\ phase = "start"
  /\ places = 0
  /\ claim = "none"
  /\ record = FALSE
  /\ clicks = 0

\* propose_errand. The card is shown. Nothing is sent.
Offer ==
  /\ phase = "start"
  /\ phase' = "offered"
  /\ claim' = "open"
  /\ record' = FALSE
  /\ places' = 0
  /\ clicks' = 0

\* commit_errand with an empty token. breakBare places anyway.
Bare ==
  /\ phase = "offered"
  /\ phase' = "bare"
  /\ places' = IF breakBare THEN 1 ELSE 0
  /\ record' = FALSE
  /\ clicks' = 0
  /\ UNCHANGED claim

\* A missing pay secret rolls back before arm. breakUnpaid places anyway.
Unpaid ==
  /\ phase = "offered"
  /\ phase' = "unpaid"
  /\ places' = IF breakUnpaid THEN 1 ELSE 0
  /\ record' = FALSE
  /\ claim' = "open"
  /\ clicks' = 0

\* arm_remote_claim commits the id before the external call.
Arm ==
  /\ phase = "offered"
  /\ phase' = "armed"
  /\ claim' = "unknown"
  /\ places' = 0
  /\ record' = FALSE
  /\ clicks' = 0

\* The provider accepts. breakSuccess marks the claim written without the row.
Place ==
  /\ phase = "armed"
  /\ phase' = "placed"
  /\ places' = 1
  /\ record' = ~breakSuccess
  /\ claim' = "written"
  /\ clicks' = 0

\* The first call times out. One attempt is already counted.
Miss ==
  /\ phase = "armed"
  /\ phase' = "unknown"
  /\ places' = 1
  /\ claim' = "unknown"
  /\ record' = FALSE
  /\ clicks' = 0

\* The same confirm is clicked again. breakAgain places a second time.
Again ==
  /\ phase = "unknown"
  /\ phase' = "retried"
  /\ places' = IF breakAgain THEN 2 ELSE places
  /\ record' = FALSE
  /\ UNCHANGED <<claim, clicks>>

\* Lookup finds this armed id. breakSeen places again instead of saving the found row.
Seen ==
  /\ phase = "unknown"
  /\ phase' = "adopted"
  /\ places' = IF breakSeen THEN 2 ELSE places
  /\ record' = TRUE
  /\ claim' = "written"
  /\ clicks' = 0

\* The page says the order was placed, and the armed id is absent.
\* breakPage saves that page as this order.
BarePage ==
  /\ phase = "unknown"
  /\ phase' = "unnamed"
  /\ places' = places
  /\ record' = breakPage
  /\ UNCHANGED <<claim, clicks>>

\* The page is an app wall before any pay click. breakWall clicks anyway.
Wall ==
  /\ phase = "armed"
  /\ phase' = "wall"
  /\ places' = 0
  /\ clicks' = IF breakWall THEN 1 ELSE 0
  /\ record' = FALSE
  /\ claim' = "open"

\* Stop after acceptance. breakStop deletes the row.
Stop ==
  /\ phase = "placed"
  /\ record
  /\ phase' = "stopped"
  /\ record' = ~breakStop
  /\ claim' = "written"
  /\ UNCHANGED <<places, clicks>>

Done ==
  /\ phase \in {"bare", "unpaid", "retried", "adopted", "unnamed", "wall", "stopped"}
  /\ UNCHANGED vars

Next ==
  \/ Offer
  \/ Bare
  \/ Unpaid
  \/ Arm
  \/ Place
  \/ Miss
  \/ Again
  \/ Seen
  \/ BarePage
  \/ Wall
  \/ Stop
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in Phases
  /\ places \in 0..2
  /\ claim \in Claims
  /\ record \in BOOLEAN
  /\ clicks \in {0, 1}

AtMostOnePlace == places <= 1

BareWritesNothing == phase = "bare" => places = 0 /\ ~record

UnpaidWritesNothing == phase = "unpaid" => places = 0 /\ ~record

SuccessAfterRecord == claim = "written" => record

UnnamedIsNotOrder == phase = "unnamed" => ~record /\ places <= 1

WallDoesNotClick == phase = "wall" => clicks = 0 /\ places = 0

StopKeepsRecord == phase = "stopped" => record

====
