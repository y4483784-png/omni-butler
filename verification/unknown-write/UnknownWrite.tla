---- MODULE UnknownWrite ----
\* One confirm and one tool call.
\*
\* Send, Reject, and Unknown are the insert after arm_remote_claim commits.
\* Lookup is the later click. It does not insert.
\* Blind is the scratch break: that click inserts again.
\* RunTool is a tool invoke. BrokenRun is the scratch break that runs it
\* after the generation is stopped.

EXTENDS Integers

CONSTANT breakBlindRetry, breakStoppedExecute

VARIABLES claim, armed, remoteCount, blindPosts, stopped, badExec
vars == <<claim, armed, remoteCount, blindPosts, stopped, badExec>>

Init ==
    /\ claim = "open"
    /\ armed = FALSE
    /\ remoteCount = 0
    /\ blindPosts = 0
    /\ stopped = FALSE
    /\ badExec = FALSE

TypeOK ==
    /\ claim \in {"open", "unknown", "written"}
    /\ armed \in BOOLEAN
    /\ remoteCount \in 0..2
    /\ blindPosts \in 0..1
    /\ stopped \in BOOLEAN
    /\ badExec \in BOOLEAN

Send ==
    /\ claim = "open"
    /\ ~stopped
    /\ remoteCount = 0
    /\ claim' = "written"
    /\ armed' = TRUE
    /\ remoteCount' = 1
    /\ UNCHANGED <<blindPosts, stopped, badExec>>

Reject ==
    /\ claim = "open"
    /\ ~stopped
    /\ claim' = "open"
    /\ armed' = FALSE
    /\ UNCHANGED <<remoteCount, blindPosts, stopped, badExec>>

Unknown ==
    /\ claim = "open"
    /\ ~stopped
    /\ remoteCount = 0
    /\ claim' = "unknown"
    /\ armed' = TRUE
    /\ remoteCount' \in {0, 1}
    /\ UNCHANGED <<blindPosts, stopped, badExec>>

Lookup ==
    /\ claim = "unknown"
    /\ armed
    /\ IF remoteCount = 1 THEN claim' = "written" ELSE claim' = "unknown"
    /\ UNCHANGED <<armed, remoteCount, blindPosts, stopped, badExec>>

Blind ==
    /\ breakBlindRetry
    /\ claim = "unknown"
    /\ armed
    /\ blindPosts = 0
    /\ remoteCount <= 1
    /\ blindPosts' = 1
    /\ remoteCount' = remoteCount + 1
    /\ UNCHANGED <<claim, armed, stopped, badExec>>

Stop ==
    /\ ~stopped
    /\ stopped' = TRUE
    /\ UNCHANGED <<claim, armed, remoteCount, blindPosts, badExec>>

RunTool ==
    /\ ~stopped
    /\ UNCHANGED vars

BrokenRun ==
    /\ breakStoppedExecute
    /\ stopped
    /\ ~badExec
    /\ badExec' = TRUE
    /\ UNCHANGED <<claim, armed, remoteCount, blindPosts, stopped>>

Stutter == UNCHANGED vars

Next ==
    \/ Send
    \/ Reject
    \/ Unknown
    \/ Lookup
    \/ Blind
    \/ Stop
    \/ RunTool
    \/ BrokenRun
    \/ Stutter

Spec == Init /\ [][Next]_vars

NoBlindPost == blindPosts = 0
AtMostOneRemote == remoteCount <= 1
NoStoppedExecute == ~badExec

====
