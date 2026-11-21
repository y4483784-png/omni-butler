---- MODULE Watch ----
EXTENDS Integers

CONSTANT breakStop, breakDouble, breakWait, breakAge, createdOld

VARIABLES status, stamp, observed, told, notices, sweeps, stable, hits1, hits2, left, snap

vars == <<status, stamp, observed, told, notices, sweeps, stable, hits1, hits2, left, snap>>

Init ==
    /\ status = "running"
    /\ stamp = 0
    /\ observed = 0
    /\ told = 0
    /\ notices = 0
    /\ sweeps = 0
    /\ stable = 0
    /\ hits1 = 0
    /\ hits2 = 0
    /\ left = FALSE
    /\ snap = <<0, 0, 0, 0, 0, 0, 0>>

Progress ==
    \/ /\ status = "running" /\ stamp < 2 /\ ~left
       /\ stamp' = stamp + 1
       /\ stable' = 0
       /\ UNCHANGED <<status, observed, told, notices, sweeps, hits1, hits2, left, snap>>
    \/ /\ ~(status = "running" /\ stamp < 2 /\ ~left)
       /\ UNCHANGED vars

Renew ==
    UNCHANGED vars

WaitPerson ==
    \/ /\ status = "running" /\ ~left
       /\ status' = "waiting_person"
       /\ left' = TRUE
       /\ snap' = <<stamp, observed, told, notices, stable, hits1, hits2>>
       /\ UNCHANGED <<stamp, observed, told, notices, sweeps, stable, hits1, hits2>>
    \/ /\ ~(status = "running" /\ ~left)
       /\ UNCHANGED vars

WaitResource ==
    \/ /\ status = "running" /\ ~left
       /\ status' = "waiting_resource"
       /\ left' = TRUE
       /\ snap' = <<stamp, observed, told, notices, stable, hits1, hits2>>
       /\ UNCHANGED <<stamp, observed, told, notices, sweeps, stable, hits1, hits2>>
    \/ /\ ~(status = "running" /\ ~left)
       /\ UNCHANGED vars

RecordedEarlier ==
    \/ /\ sweeps = 0 /\ stamp = 0 /\ stable = 0 /\ status = "running" /\ ~left
       /\ stamp' = 1
       /\ observed' = 1
       /\ stable' = 1
       /\ UNCHANGED <<status, told, notices, sweeps, hits1, hits2, left, snap>>
    \/ /\ ~(sweeps = 0 /\ stamp = 0 /\ stable = 0 /\ status = "running" /\ ~left)
       /\ UNCHANGED vars

shouldTell ==
    status = "running" /\ stamp > 0 /\ (
        breakDouble \/ (stamp = observed /\ told /= stamp /\ stable >= 1)
    )

stops ==
    breakStop \/ (breakAge /\ createdOld)

Sweep ==
    \/ /\ sweeps < 3
       /\ sweeps' = sweeps + 1
       /\ IF status = "running" THEN
            /\ IF (shouldTell /\ ~stops) THEN
                 /\ notices' = notices + 1
                 /\ told' = stamp
                 /\ hits1' = IF stamp = 1 THEN hits1 + 1 ELSE hits1
                 /\ hits2' = IF stamp = 2 THEN hits2 + 1 ELSE hits2
               ELSE
                 UNCHANGED <<notices, told, hits1, hits2>>
            /\ IF stops THEN
                 /\ status' = "stopped"
                 /\ UNCHANGED <<observed, stable>>
               ELSE
                 /\ status' = status
                 /\ observed' = stamp
                 /\ stable' = IF stamp = observed THEN stable + 1 ELSE 1
            /\ UNCHANGED <<stamp, left, snap>>
          ELSE
            /\ IF breakWait THEN
                 observed' = IF observed = 0 THEN 1 ELSE 0
               ELSE
                 UNCHANGED observed
            /\ UNCHANGED <<status, stamp, told, notices, stable, hits1, hits2, left, snap>>
    \/ /\ sweeps >= 3
       /\ UNCHANGED vars

Next ==
    \/ Progress
    \/ Renew
    \/ WaitPerson
    \/ WaitResource
    \/ RecordedEarlier
    \/ Sweep

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ status \in {"running", "waiting_person", "waiting_resource", "stopped"}
    /\ stamp \in 0..2
    /\ observed \in 0..2
    /\ told \in 0..2
    /\ notices \in 0..3
    /\ sweeps \in 0..3
    /\ stable \in 0..4
    /\ hits1 \in 0..3
    /\ hits2 \in 0..3
    /\ left \in BOOLEAN
    /\ snap[1] \in 0..2
    /\ snap[2] \in 0..2
    /\ snap[3] \in 0..2
    /\ snap[4] \in 0..3
    /\ snap[5] \in 0..4
    /\ snap[6] \in 0..3
    /\ snap[7] \in 0..3
    /\ createdOld \in BOOLEAN

CreationAgeDoesNotStop ==
    status \in {"running", "waiting_person", "waiting_resource"}

NullSampleStaysQuiet ==
    stamp = 0 => notices = 0

WitnessOldNull ==
    ~(createdOld /\ stamp = 0 /\ status = "running" /\ sweeps >= 1 /\ notices = 0)

WitnessYoungNull ==
    ~(~createdOld /\ stamp = 0 /\ status = "running" /\ sweeps >= 1 /\ notices = 0)

WitnessOldSample ==
    ~(createdOld /\ stamp > 0 /\ status = "running" /\ sweeps >= 1)

WitnessYoungSample ==
    ~(~createdOld /\ stamp > 0 /\ status = "running" /\ sweeps >= 1)

OneNoticePerProgressStamp ==
    /\ hits1 <= 1
    /\ hits2 <= 1
    /\ notices = hits1 + hits2
    /\ (hits1 = 1 /\ stamp = 1 => stable >= 2 /\ told = 1)
    /\ (hits2 = 1 /\ stamp = 2 => stable >= 2 /\ told = 2)

WaitingLeftAlone ==
    left =>
        /\ status \in {"waiting_person", "waiting_resource"}
        /\ <<stamp, observed, told, notices, stable, hits1, hits2>> = snap

====
