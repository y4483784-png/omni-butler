---- MODULE TaskWatch ----
\* One older generation, one newer generation of the same user, and one other user's running generation.
\* The sweep stops only a running generation whose wall age is due and whose sleep credit does not cover it.
\* Waiting, completed, and an already quiet slept row are not stops.
\* Stopping the older generation does not stop the newer one or the other user.
\* The sweep does not publish a confirm card.

EXTENDS Integers

CONSTANT breakWait, breakDone, breakSleep, breakNewer, breakOther, breakCard

VARIABLES oldStatus, oldDue, credit, watchStops, parked, finished, newer, other, claims, swept
vars == <<oldStatus, oldDue, credit, watchStops, parked, finished, newer, other, claims, swept>>

Init ==
    /\ oldStatus = "running"
    /\ oldDue = FALSE
    /\ credit = 0
    /\ watchStops = 0
    /\ parked = FALSE
    /\ finished = FALSE
    /\ newer = "running"
    /\ other = "running"
    /\ claims = 0
    /\ swept = 0

MarkDue ==
    /\ oldStatus = "running"
    /\ ~oldDue
    /\ oldDue' = TRUE
    /\ UNCHANGED <<oldStatus, credit, watchStops, parked, finished, newer, other, claims, swept>>

Sleep ==
    /\ oldStatus = "running"
    /\ credit = 0
    /\ credit' = 1
    /\ UNCHANGED <<oldStatus, oldDue, watchStops, parked, finished, newer, other, claims, swept>>

Wait ==
    /\ oldStatus = "running"
    /\ ~parked
    /\ oldStatus' = "waiting"
    /\ parked' = TRUE
    /\ UNCHANGED <<oldDue, credit, watchStops, finished, newer, other, claims, swept>>

Finish ==
    /\ oldStatus = "running"
    /\ ~finished
    /\ oldStatus' = "completed"
    /\ finished' = TRUE
    /\ UNCHANGED <<oldDue, credit, watchStops, parked, newer, other, claims, swept>>

Sweep ==
    /\ swept < 2
    /\ swept' = swept + 1
    /\ IF oldStatus = "running" /\ oldDue /\ (credit = 0 \/ breakSleep) THEN
         /\ oldStatus' = "stopped"
         /\ watchStops' = watchStops + 1
         /\ newer' = IF breakNewer THEN "stopped" ELSE newer
         /\ other' = IF breakOther THEN "stopped" ELSE other
       ELSE IF breakWait /\ oldStatus = "waiting" THEN
         /\ oldStatus' = "stopped"
         /\ UNCHANGED <<watchStops, newer, other>>
       ELSE IF breakDone /\ oldStatus = "completed" THEN
         /\ oldStatus' = "stopped"
         /\ UNCHANGED <<watchStops, newer, other>>
       ELSE
         UNCHANGED <<oldStatus, watchStops, newer, other>>
    /\ claims' = IF breakCard THEN 1 ELSE claims
    /\ UNCHANGED <<oldDue, credit, parked, finished>>

Done ==
    /\ swept >= 1
    /\ UNCHANGED vars

Next ==
    \/ MarkDue
    \/ Sleep
    \/ Wait
    \/ Finish
    \/ Sweep
    \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ oldStatus \in {"running", "waiting", "completed", "stopped"}
    /\ oldDue \in BOOLEAN
    /\ credit \in 0..1
    /\ watchStops \in 0..1
    /\ parked \in BOOLEAN
    /\ finished \in BOOLEAN
    /\ newer \in {"running", "stopped"}
    /\ other \in {"running", "stopped"}
    /\ claims \in 0..1
    /\ swept \in 0..2

WaitStays == parked => oldStatus = "waiting"

CompletedStays == finished => oldStatus = "completed"

SleepNotSilence == watchStops = 0 \/ credit = 0

NewerStays == newer = "running"

OtherStays == other = "running"

NoCard == claims = 0

====
