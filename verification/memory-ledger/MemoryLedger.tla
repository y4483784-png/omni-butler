---- MODULE MemoryLedger ----
\* One generation and one schedule fingerprint.
\* A turn that is not completed does not call the model and does not append a ledger row.
\* A low-confidence extract does not open a confirm card.
\* A second pulse of the same fingerprint does not add a claim.
\* A later turn does not replace a value a person already applied.

EXTENDS Integers

CONSTANT breakStop, breakQuiet, breakClaim, breakErase

VARIABLES status, called, turnRows, personRows, pending, live, pulseClaims, memoryClaims, pulses, closed
vars == <<status, called, turnRows, personRows, pending, live, pulseClaims, memoryClaims, pulses, closed>>

Allowed == IF breakStop THEN {"completed", "stopped", "waiting"} ELSE {"completed"}

Init ==
    /\ status = "open"
    /\ called = FALSE
    /\ turnRows = 0
    /\ personRows = 0
    /\ pending = FALSE
    /\ live = "empty"
    /\ pulseClaims = 0
    /\ memoryClaims = 0
    /\ pulses = 0
    /\ closed = FALSE

TypeOK ==
    /\ status \in {"open", "completed", "stopped", "waiting"}
    /\ called \in BOOLEAN
    /\ turnRows \in 0..1
    /\ personRows \in 0..1
    /\ pending \in BOOLEAN
    /\ live \in {"empty", "person", "turn"}
    /\ pulseClaims \in 0..2
    /\ memoryClaims \in 0..1
    /\ pulses \in 0..2
    /\ closed \in BOOLEAN

PersonWrites ==
    /\ ~closed
    /\ personRows = 0
    /\ personRows' = 1
    /\ live' = "person"
    /\ UNCHANGED <<status, called, turnRows, pending, pulseClaims, memoryClaims, pulses, closed>>

Complete ==
    /\ ~closed
    /\ status = "open"
    /\ status' = "completed"
    /\ UNCHANGED <<called, turnRows, personRows, pending, live, pulseClaims, memoryClaims, pulses, closed>>

Stop ==
    /\ ~closed
    /\ status = "open"
    /\ status' = "stopped"
    /\ UNCHANGED <<called, turnRows, personRows, pending, live, pulseClaims, memoryClaims, pulses, closed>>

Wait ==
    /\ ~closed
    /\ status = "open"
    /\ status' = "waiting"
    /\ UNCHANGED <<called, turnRows, personRows, pending, live, pulseClaims, memoryClaims, pulses, closed>>

LowRemember ==
    /\ ~closed
    /\ ~called
    /\ status \in Allowed
    /\ called' = TRUE
    /\ turnRows' = 1
    /\ pending' = TRUE
    /\ memoryClaims' = IF breakQuiet THEN 1 ELSE memoryClaims
    /\ UNCHANGED <<status, personRows, live, pulseClaims, pulses, closed>>

HighRemember ==
    /\ ~closed
    /\ ~called
    /\ status \in Allowed
    /\ called' = TRUE
    /\ turnRows' = 1
    /\ IF live = "person" /\ ~breakErase THEN
         /\ pending' = TRUE
         /\ UNCHANGED live
       ELSE
         /\ pending' = FALSE
         /\ live' = "turn"
    /\ UNCHANGED <<status, personRows, pulseClaims, memoryClaims, pulses, closed>>

Pulse ==
    /\ ~closed
    /\ live = "person"
    /\ pulses < 2
    /\ pulses' = pulses + 1
    /\ IF pulses = 0 THEN
         pulseClaims' = 1
       ELSE IF breakClaim THEN
         pulseClaims' = pulseClaims + 1
       ELSE
         UNCHANGED pulseClaims
    /\ UNCHANGED <<status, called, turnRows, personRows, pending, live, memoryClaims, closed>>

Close ==
    /\ ~closed
    /\ status \in {"completed", "stopped", "waiting"}
    /\ \/ called
       \/ status \in {"stopped", "waiting"}
    /\ closed' = TRUE
    /\ UNCHANGED <<status, called, turnRows, personRows, pending, live, pulseClaims, memoryClaims, pulses>>

Done ==
    /\ closed
    /\ UNCHANGED vars

Next ==
    \/ PersonWrites
    \/ Complete
    \/ Stop
    \/ Wait
    \/ LowRemember
    \/ HighRemember
    \/ Pulse
    \/ Close
    \/ Done

Spec == Init /\ [][Next]_vars

NotCompletedWritesNothing ==
    status \in {"stopped", "waiting"} => ~called /\ turnRows = 0

LowConfidenceNoCard ==
    memoryClaims = 0

OneClaim ==
    pulseClaims <= 1

PersonNotUndone ==
    ~(personRows = 1 /\ called /\ live = "turn" /\ ~pending /\ turnRows = 1)

====
