---- MODULE ErrandPlace ----
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

Offer ==
  /\ phase = "start"
  /\ phase' = "offered"
  /\ claim' = "open"
  /\ record' = FALSE
  /\ places' = 0
  /\ clicks' = 0

Bare ==
  /\ phase = "offered"
  /\ phase' = "bare"
  /\ places' = IF breakBare THEN 1 ELSE 0
  /\ record' = FALSE
  /\ clicks' = 0
  /\ UNCHANGED claim

Unpaid ==
  /\ phase = "offered"
  /\ phase' = "unpaid"
  /\ places' = IF breakUnpaid THEN 1 ELSE 0
  /\ record' = FALSE
  /\ claim' = "open"
  /\ clicks' = 0

Arm ==
  /\ phase = "offered"
  /\ phase' = "armed"
  /\ claim' = "unknown"
  /\ places' = 0
  /\ record' = FALSE
  /\ clicks' = 0

Place ==
  /\ phase = "armed"
  /\ phase' = "placed"
  /\ places' = 1
  /\ record' = ~breakSuccess
  /\ claim' = "written"
  /\ clicks' = 0

Miss ==
  /\ phase = "armed"
  /\ phase' = "unknown"
  /\ places' = 1
  /\ claim' = "unknown"
  /\ record' = FALSE
  /\ clicks' = 0

Again ==
  /\ phase = "unknown"
  /\ phase' = "retried"
  /\ places' = IF breakAgain THEN 2 ELSE places
  /\ record' = FALSE
  /\ UNCHANGED <<claim, clicks>>

Seen ==
  /\ phase = "unknown"
  /\ phase' = "adopted"
  /\ places' = IF breakSeen THEN 2 ELSE places
  /\ record' = TRUE
  /\ claim' = "written"
  /\ clicks' = 0

BarePage ==
  /\ phase = "unknown"
  /\ phase' = "unnamed"
  /\ places' = places
  /\ record' = breakPage
  /\ UNCHANGED <<claim, clicks>>

Wall ==
  /\ phase = "armed"
  /\ phase' = "wall"
  /\ places' = 0
  /\ clicks' = IF breakWall THEN 1 ELSE 0
  /\ record' = FALSE
  /\ claim' = "open"

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
