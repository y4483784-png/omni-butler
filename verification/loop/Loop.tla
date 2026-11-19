---- MODULE Loop ----
EXTENDS Integers

CONSTANT breakSave, breakResult, breakWrite, breakForbid, breakStop, breakContinuation

VARIABLES
    constraint, step, stopped, saved, invoked, web, terminal,
    nextModel, opened, write, confirms, due, crashed, enqueued

vars == <<
    constraint, step, stopped, saved, invoked, web, terminal,
    nextModel, opened, write, confirms, due, crashed, enqueued
>>

Init ==
    /\ constraint \in {"allow", "forbid"}
    /\ step = 0
    /\ stopped = FALSE
    /\ saved = 0
    /\ invoked = 0
    /\ web = 0
    /\ terminal = 0
    /\ nextModel = 0
    /\ opened = FALSE
    /\ write = 0
    /\ confirms = 0
    /\ due = FALSE
    /\ crashed = FALSE
    /\ enqueued = FALSE

Save ==
    \/ /\ step = 0 /\ ~breakSave /\ ~breakStop
       /\ step' = 1
       /\ saved' = 2
       /\ UNCHANGED <<constraint, stopped, invoked, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step = 0 /\ ~breakSave /\ ~breakStop
       /\ step' = 8
       /\ stopped' = TRUE
       /\ UNCHANGED <<constraint, saved, invoked, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step = 0 /\ breakSave /\ ~breakStop
       /\ step' = 8
       /\ invoked' = 1
       /\ UNCHANGED <<constraint, stopped, saved, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step = 0 /\ breakStop
       /\ step' = 8
       /\ stopped' = TRUE
       /\ saved' = 2
       /\ invoked' = 1
       /\ UNCHANGED <<constraint, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step # 0
       /\ UNCHANGED vars

webEffect ==
    IF constraint = "allow" THEN 1 ELSE IF breakForbid THEN 1 ELSE 0

invokedEffect ==
    IF constraint = "allow" THEN 1 ELSE 0

Execute ==
    \/ /\ step = 1 /\ ~breakResult
       /\ step' = 2
       /\ terminal' = 1
       /\ invoked' = invokedEffect
       /\ web' = webEffect
       /\ UNCHANGED <<constraint, stopped, saved, nextModel, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step = 1 /\ breakResult
       /\ step' = 8
       /\ terminal' = 1
       /\ nextModel' = 1
       /\ invoked' = invokedEffect
       /\ web' = webEffect
       /\ UNCHANGED <<constraint, stopped, saved, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step # 1
       /\ UNCHANGED vars

Open ==
    \/ /\ step = 2 /\ ~breakWrite
       /\ step' = 3
       /\ opened' = TRUE
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, write, confirms, due, crashed, enqueued>>
    \/ /\ step = 2 /\ breakWrite
       /\ step' = 8
       /\ opened' = TRUE
       /\ write' = 1
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, confirms, due, crashed, enqueued>>
    \/ /\ step # 2
       /\ UNCHANGED vars

Confirm ==
    \/ /\ step = 3
       /\ step' = 4
       /\ confirms' = 1
       /\ write' = 1
       /\ terminal' = 2
       /\ due' = TRUE
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, nextModel, opened, crashed, enqueued>>
    \/ /\ step # 3
       /\ UNCHANGED vars

ConfirmAgain ==
    \/ /\ step = 4
       /\ step' = 5
       /\ confirms' = 2
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, opened, write, due, crashed, enqueued>>
    \/ /\ step # 4
       /\ UNCHANGED vars

RequestAfterTools ==
    \/ /\ step = 5 /\ terminal = 2
       /\ step' = 6
       /\ nextModel' = 1
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, opened, write, confirms, due, crashed, enqueued>>
    \/ /\ step # 5
       /\ UNCHANGED vars

Crash ==
    \/ /\ step = 6 /\ ~breakContinuation
       /\ step' = 7
       /\ crashed' = TRUE
       /\ due' = TRUE
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, opened, write, confirms, enqueued>>
    \/ /\ step = 6 /\ breakContinuation
       /\ step' = 7
       /\ crashed' = TRUE
       /\ due' = FALSE
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, opened, write, confirms, enqueued>>
    \/ /\ step # 6
       /\ UNCHANGED vars

Next ==
    \/ Save
    \/ Execute
    \/ Open
    \/ Confirm
    \/ ConfirmAgain
    \/ RequestAfterTools
    \/ Crash

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ constraint \in {"allow", "forbid"}
    /\ step \in 0..8
    /\ stopped \in BOOLEAN
    /\ saved \in 0..2
    /\ invoked \in 0..1
    /\ web \in 0..1
    /\ terminal \in 0..2
    /\ nextModel \in 0..1
    /\ opened \in BOOLEAN
    /\ write \in 0..1
    /\ confirms \in 0..2
    /\ due \in BOOLEAN
    /\ crashed \in BOOLEAN
    /\ enqueued = FALSE

SaveBeforeExecute == invoked = 0 \/ saved = 2

ResultBeforeNextModel == nextModel = 0 \/ terminal = 2

WriteWaitsForClaim ==
    /\ write <= confirms
    /\ (opened /\ confirms = 0) => write = 0

ForbidWebBlocksSearch == constraint = "forbid" => web = 0

StoppedJobDoesNotExecute == stopped => invoked = 0 /\ nextModel = 0

ContinuationDueSurvivesCrash == crashed => due /\ ~enqueued

====
