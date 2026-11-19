---- MODULE Talk ----

VARIABLES job1, job2, turn, asked, sawPlain, sawDisconnect, sawNamed, sawAmbiguous
vars == <<job1, job2, turn, asked, sawPlain, sawDisconnect, sawNamed, sawAmbiguous>>

Init ==
    /\ job1 = "running"
    /\ job2 = "running"
    /\ turn = "absent"
    /\ asked = FALSE
    /\ sawPlain = FALSE
    /\ sawDisconnect = FALSE
    /\ sawNamed = FALSE
    /\ sawAmbiguous = FALSE

OpenChoice == ~sawPlain /\ ~sawNamed /\ ~sawAmbiguous /\ turn = "absent"

BeginTurn ==
    \/ /\ OpenChoice
       /\ turn' = "running"
       /\ sawPlain' = TRUE
       /\ UNCHANGED <<job1, job2, asked, sawDisconnect, sawNamed, sawAmbiguous>>
    \/ /\ ~OpenChoice
       /\ UNCHANGED vars

Disconnect ==
    \/ /\ sawPlain /\ turn = "running" /\ ~sawDisconnect
       /\ turn' = "stopped"
       /\ sawDisconnect' = TRUE
       /\ UNCHANGED <<job1, job2, asked, sawPlain, sawNamed, sawAmbiguous>>
    \/ /\ ~(sawPlain /\ turn = "running" /\ ~sawDisconnect)
       /\ UNCHANGED vars

FinishTurn ==
    \/ /\ sawPlain /\ turn = "running"
       /\ turn' = "completed"
       /\ UNCHANGED <<job1, job2, asked, sawPlain, sawDisconnect, sawNamed, sawAmbiguous>>
    \/ /\ ~(sawPlain /\ turn = "running")
       /\ UNCHANGED vars

NamedCancel ==
    \/ /\ OpenChoice
       /\ job1' = "stopped"
       /\ turn' = "running"
       /\ sawNamed' = TRUE
       /\ UNCHANGED <<job2, asked, sawPlain, sawDisconnect, sawAmbiguous>>
    \/ /\ ~OpenChoice
       /\ UNCHANGED vars

AmbiguousCancel ==
    \/ /\ OpenChoice
       /\ asked' = TRUE
       /\ turn' = "running"
       /\ sawAmbiguous' = TRUE
       /\ UNCHANGED <<job1, job2, sawPlain, sawDisconnect, sawNamed>>
    \/ /\ ~OpenChoice
       /\ UNCHANGED vars

Next ==
    \/ BeginTurn
    \/ Disconnect
    \/ FinishTurn
    \/ NamedCancel
    \/ AmbiguousCancel

Spec == Init /\ [][Next]_vars

JobSurvivesNewTurn == sawPlain => (job1 = "running" /\ job2 = "running")
JobSurvivesDisconnect == sawDisconnect => (job1 = "running" /\ job2 = "running" /\ turn = "stopped")
CancelStopsNamedJob == sawNamed => (job1 = "stopped" /\ job2 = "running")
AmbiguousCancelAsks == sawAmbiguous => (asked /\ job1 = "running" /\ job2 = "running")

====
