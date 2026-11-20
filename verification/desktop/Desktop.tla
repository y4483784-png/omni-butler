---- MODULE Desktop ----
EXTENDS Integers

CONSTANT
  breakAgain, breakPath, breakUnknown,
  breakOther, breakProbe, breakStopped, breakWeb

VARIABLES phase, performed, continued

vars == <<phase, performed, continued>>

Phases == {
  "start", "armed", "opened", "again", "finished",
  "mismatch", "unknown", "stopped", "probe", "web"
}
Continued == {"none", "same", "other"}

Init ==
  /\ phase = "start"
  /\ performed = 0
  /\ continued = "none"

Arm ==
  /\ phase = "start"
  /\ phase' = "armed"
  /\ performed' = 0
  /\ continued' = "none"

Open ==
  /\ phase = "armed"
  /\ phase' = "opened"
  /\ performed' = 1
  /\ UNCHANGED continued

Again ==
  /\ phase = "opened"
  /\ phase' = "again"
  /\ performed' = IF breakAgain THEN performed + 1 ELSE performed
  /\ UNCHANGED continued

Finish ==
  /\ phase = "opened"
  /\ phase' = "finished"
  /\ performed' = performed
  /\ continued' = IF breakOther THEN "other" ELSE "same"

Mismatch ==
  /\ phase = "start"
  /\ phase' = "mismatch"
  /\ performed' = IF breakPath THEN 1 ELSE 0
  /\ continued' = "none"

Lose ==
  /\ phase = "start"
  /\ phase' = "unknown"
  /\ performed' = IF breakUnknown THEN 1 ELSE 0
  /\ continued' = "none"

Stop ==
  /\ phase = "opened"
  /\ phase' = "stopped"
  /\ performed' = performed
  /\ continued' = IF breakStopped THEN "same" ELSE "none"

Probe ==
  /\ phase = "start"
  /\ phase' = "probe"
  /\ performed' = 0
  /\ continued' = IF breakProbe THEN "same" ELSE "none"

Web ==
  /\ phase = "start"
  /\ phase' = "web"
  /\ performed' = IF breakWeb THEN 1 ELSE 0
  /\ continued' = "none"

Done ==
  /\ phase \in {"again", "finished", "mismatch", "unknown", "stopped", "probe", "web"}
  /\ UNCHANGED vars

Next ==
  \/ Arm
  \/ Open
  \/ Again
  \/ Finish
  \/ Mismatch
  \/ Lose
  \/ Stop
  \/ Probe
  \/ Web
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in Phases
  /\ performed \in 0..2
  /\ continued \in Continued

SameActionOnce ==
  /\ performed <= 1
  /\ phase = "mismatch" => performed = 0
  /\ phase = "unknown" => performed = 0
  /\ phase = "again" => performed = 1

ContinueSameJob ==
  /\ continued \in {"none", "same"}
  /\ phase = "finished" => continued = "same"
  /\ phase = "probe" => continued = "none"
  /\ phase = "unknown" => continued = "none"
  /\ phase = "stopped" => continued = "none"

WebStillDenied == phase = "web" => performed = 0

====
