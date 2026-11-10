---- MODULE TaskRun ----
\* One session, at most two turns. This is the move table in
\* backend/app/services/task_run.py, not a second lifecycle.
\*
\* begin_turn inserts running and applies stop to an open earlier turn.
\* finish writes completed only from running. A waiting turn stays waiting.
\* stop writes stopped from an open turn and leaves completed completed.
\* A stopped turn does not return to running or completed.
\* breakCompletedStop is the scratch break: stop then rewrites a finished turn.

CONSTANT breakCompletedStop

Statuses == {"running", "waiting_person", "waiting_resource", "completed", "stopped"}
Open == {"running", "waiting_person", "waiting_resource"}

VARIABLES s1, s2, done1, done2, halted1, halted2, left1, left2, waited1, waited2
vars == <<s1, s2, done1, done2, halted1, halted2, left1, left2, waited1, waited2>>

Init ==
    /\ s1 = "absent"
    /\ s2 = "absent"
    /\ done1 = FALSE /\ done2 = FALSE
    /\ halted1 = FALSE /\ halted2 = FALSE
    /\ left1 = FALSE /\ left2 = FALSE
    /\ waited1 = FALSE /\ waited2 = FALSE

TypeOK ==
    /\ s1 \in ({"absent"} \cup Statuses)
    /\ s2 \in ({"absent"} \cup Statuses)
    /\ (s2 /= "absent") => (s1 \in Statuses)
    /\ done1 \in BOOLEAN /\ done2 \in BOOLEAN
    /\ halted1 \in BOOLEAN /\ halted2 \in BOOLEAN
    /\ left1 \in BOOLEAN /\ left2 \in BOOLEAN
    /\ waited1 \in BOOLEAN /\ waited2 \in BOOLEAN

CompletedStays == (done1 => s1 = "completed") /\ (done2 => s2 = "completed")
StoppedStays == (halted1 => s1 = "stopped") /\ (halted2 => s2 = "stopped")
NotRunningAgain == (left1 => s1 /= "running") /\ (left2 => s2 /= "running")
WaitingNeverCompletes ==
    /\ waited1 => s1 /= "completed"
    /\ waited2 => s2 /= "completed"

Touch1(next) ==
    /\ s1' = next
    /\ done1' = (done1 \/ (next = "completed"))
    /\ halted1' = (halted1 \/ (next = "stopped"))
    /\ left1' = (left1 \/ (next /= "running"))
    /\ waited1' = (waited1 \/ (next \in {"waiting_person", "waiting_resource"}))
    /\ UNCHANGED <<s2, done2, halted2, left2, waited2>>

Touch2(next) ==
    /\ s2' = next
    /\ done2' = (done2 \/ (next = "completed"))
    /\ halted2' = (halted2 \/ (next = "stopped"))
    /\ left2' = (left2 \/ (next /= "running"))
    /\ waited2' = (waited2 \/ (next \in {"waiting_person", "waiting_resource"}))
    /\ UNCHANGED <<s1, done1, halted1, left1, waited1>>

\* a new message arrives
Begin ==
    \/ /\ s1 = "absent"
       /\ Touch1("running")
    \/ /\ s1 \in Open
       /\ s2 = "absent"
       /\ s1' = "stopped"
       /\ done1' = done1
       /\ halted1' = TRUE
       /\ left1' = TRUE
       /\ waited1' = (waited1 \/ (s1 \in {"waiting_person", "waiting_resource"}))
       /\ s2' = "running"
       /\ done2' = FALSE
       /\ halted2' = FALSE
       /\ left2' = FALSE
       /\ waited2' = FALSE
    \/ /\ s1 \in {"completed", "stopped"}
       /\ s2 = "absent"
       /\ s2' = "running"
       /\ done2' = FALSE
       /\ halted2' = FALSE
       /\ left2' = FALSE
       /\ waited2' = FALSE
       /\ UNCHANGED <<s1, done1, halted1, left1, waited1>>

\* the answer is stored
Finish1 ==
    \/ /\ s1 = "running"
       /\ Touch1("completed")
    \/ /\ s1 \in {"waiting_person", "waiting_resource", "completed", "stopped"}
       /\ UNCHANGED vars

Finish2 ==
    \/ /\ s2 = "running"
       /\ Touch2("completed")
    \/ /\ s2 \in {"waiting_person", "waiting_resource", "completed", "stopped"}
       /\ UNCHANGED vars

\* stop is called
Stop1 ==
    \/ /\ s1 \in Open
       /\ Touch1("stopped")
    \/ /\ s1 = "completed"
       /\ IF breakCompletedStop THEN Touch1("stopped") ELSE UNCHANGED vars
    \/ /\ s1 = "stopped"
       /\ UNCHANGED vars

Stop2 ==
    \/ /\ s2 \in Open
       /\ Touch2("stopped")
    \/ /\ s2 = "completed"
       /\ IF breakCompletedStop THEN Touch2("stopped") ELSE UNCHANGED vars
    \/ /\ s2 = "stopped"
       /\ UNCHANGED vars

\* a confirm card is about to show
WaitPerson1 ==
    \/ /\ s1 = "running"
       /\ Touch1("waiting_person")
    \/ /\ s1 \in {"waiting_person", "waiting_resource", "completed", "stopped"}
       /\ UNCHANGED vars

WaitPerson2 ==
    \/ /\ s2 = "running"
       /\ Touch2("waiting_person")
    \/ /\ s2 \in {"waiting_person", "waiting_resource", "completed", "stopped"}
       /\ UNCHANGED vars

\* the turn waits on a resource
WaitResource1 ==
    \/ /\ s1 = "running"
       /\ Touch1("waiting_resource")
    \/ /\ s1 \in {"waiting_person", "waiting_resource", "completed", "stopped"}
       /\ UNCHANGED vars

WaitResource2 ==
    \/ /\ s2 = "running"
       /\ Touch2("waiting_resource")
    \/ /\ s2 \in {"waiting_person", "waiting_resource", "completed", "stopped"}
       /\ UNCHANGED vars

\* both turns have ended
Done ==
    /\ s1 \in {"completed", "stopped"}
    /\ s2 \in {"completed", "stopped"}
    /\ UNCHANGED vars

Next ==
    \/ Begin
    \/ Finish1
    \/ Finish2
    \/ Stop1
    \/ Stop2
    \/ WaitPerson1
    \/ WaitPerson2
    \/ WaitResource1
    \/ WaitResource2
    \/ Done

Spec == Init /\ [][Next]_vars

====
