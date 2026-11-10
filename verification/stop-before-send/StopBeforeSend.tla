---- MODULE StopBeforeSend ----
\* One chat generation.
\*
\* Queue then Create is a model request that has not left.
\* Fly is a request already inside chat.completions.create. Stop does not unsend it.
\* A chunk that comes back after Stop is not written into the bubble.
\* Remember runs only when the generation is not stopped.
\* The calendar count does not change.

EXTENDS Integers

CONSTANT breakSend, breakLate, breakMemory, breakCalendar

VARIABLES stopped, request, startedBeforeStop, bubble, memory, calendar, closed
vars == <<stopped, request, startedBeforeStop, bubble, memory, calendar, closed>>

Init ==
    /\ stopped = FALSE
    /\ request = "none"
    /\ startedBeforeStop = FALSE
    /\ bubble = "empty"
    /\ memory = FALSE
    /\ calendar = 1
    /\ closed = FALSE

TypeOK ==
    /\ stopped \in BOOLEAN
    /\ request \in {"none", "queued", "flying", "back"}
    /\ startedBeforeStop \in BOOLEAN
    /\ bubble \in {"empty", "kept", "late"}
    /\ memory \in BOOLEAN
    /\ calendar \in 0..1
    /\ closed \in BOOLEAN

Queue ==
    /\ ~closed
    /\ ~stopped
    /\ request = "none"
    /\ request' = "queued"
    /\ UNCHANGED <<stopped, startedBeforeStop, bubble, memory, calendar, closed>>

Create ==
    /\ ~closed
    /\ ~stopped
    /\ request = "queued"
    /\ request' = "flying"
    /\ startedBeforeStop' = TRUE
    /\ UNCHANGED <<stopped, bubble, memory, calendar, closed>>

Stop ==
    /\ ~closed
    /\ ~stopped
    /\ stopped' = TRUE
    /\ request' = IF request = "queued" /\ ~breakSend THEN "none" ELSE request
    /\ UNCHANGED <<startedBeforeStop, bubble, memory, calendar, closed>>

SendWhileStopped ==
    /\ breakSend
    /\ ~closed
    /\ stopped
    /\ request = "queued"
    /\ request' = "flying"
    /\ UNCHANGED <<stopped, startedBeforeStop, bubble, memory, calendar, closed>>

Return ==
    /\ ~closed
    /\ request = "flying"
    /\ request' = "back"
    /\ UNCHANGED <<stopped, startedBeforeStop, bubble, memory, calendar, closed>>

Keep ==
    /\ ~closed
    /\ ~stopped
    /\ request = "back"
    /\ bubble = "empty"
    /\ bubble' = "kept"
    /\ UNCHANGED <<stopped, request, startedBeforeStop, memory, calendar, closed>>

AppendLate ==
    /\ breakLate
    /\ ~closed
    /\ stopped
    /\ request = "back"
    /\ bubble = "empty"
    /\ bubble' = "late"
    /\ UNCHANGED <<stopped, request, startedBeforeStop, memory, calendar, closed>>

Remember ==
    /\ ~closed
    /\ ~stopped
    /\ ~memory
    /\ memory' = TRUE
    /\ closed' = TRUE
    /\ UNCHANGED <<stopped, request, startedBeforeStop, bubble, calendar>>

CloseStopped ==
    /\ ~closed
    /\ stopped
    /\ ~breakMemory
    /\ closed' = TRUE
    /\ UNCHANGED <<stopped, request, startedBeforeStop, bubble, memory, calendar>>

RememberStopped ==
    /\ breakMemory
    /\ ~closed
    /\ stopped
    /\ memory' = TRUE
    /\ closed' = TRUE
    /\ UNCHANGED <<stopped, request, startedBeforeStop, bubble, calendar>>

DropCalendar ==
    /\ breakCalendar
    /\ calendar = 1
    /\ calendar' = 0
    /\ UNCHANGED <<stopped, request, startedBeforeStop, bubble, memory, closed>>

Done ==
    /\ closed
    /\ UNCHANGED vars

NoSendAfterStop ==
    (request \in {"flying", "back"}) => startedBeforeStop

NoLateChunk ==
    bubble /= "late"

NoMemoryWhenStopped ==
    ~(stopped /\ memory)

CalendarStays ==
    calendar = 1

Next ==
    \/ Queue
    \/ Create
    \/ Stop
    \/ SendWhileStopped
    \/ Return
    \/ Keep
    \/ AppendLate
    \/ Remember
    \/ CloseStopped
    \/ RememberStopped
    \/ DropCalendar
    \/ Done

Spec == Init /\ [][Next]_vars
====
