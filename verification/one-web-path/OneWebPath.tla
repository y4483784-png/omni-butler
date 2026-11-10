---- MODULE OneWebPath ----
\* One chat turn.
\* The router may queue web. The chat body does not gain a networking field.
\* A calendar step shows a card and does not write the event.
\* Stop blocks the next model send.

CONSTANT breakBoth, breakField, breakStop, breakCalendar

VARIABLES
  phase, token, needsWeb,
  netField, zhipu,
  stopped, sendsAfterStop,
  card, calendarWritten

vars == <<phase, token, needsWeb, netField, zhipu, stopped, sendsAfterStop, card, calendarWritten>>

Init ==
  /\ phase = "start"
  /\ token \in BOOLEAN
  /\ needsWeb \in BOOLEAN
  /\ netField = FALSE
  /\ zhipu = FALSE
  /\ stopped = FALSE
  /\ sendsAfterStop = FALSE
  /\ card = FALSE
  /\ calendarWritten = FALSE

Queue ==
  /\ phase = "start"
  /\ phase' = "queued"
  /\ UNCHANGED <<token, needsWeb, netField, zhipu, stopped, sendsAfterStop, card, calendarWritten>>

Build ==
  /\ phase = "queued"
  /\ phase' = "built"
  /\ netField' = (breakField \/ breakBoth)
  /\ UNCHANGED <<token, needsWeb, zhipu, stopped, sendsAfterStop, card, calendarWritten>>

Search ==
  /\ phase = "built"
  /\ needsWeb
  /\ phase' = "searched"
  /\ zhipu' = TRUE
  /\ UNCHANGED <<token, needsWeb, netField, stopped, sendsAfterStop, card, calendarWritten>>

SkipSearch ==
  /\ phase = "built"
  /\ ~needsWeb
  /\ phase' = "searched"
  /\ UNCHANGED <<token, needsWeb, netField, zhipu, stopped, sendsAfterStop, card, calendarWritten>>

ShowCard ==
  /\ phase = "searched"
  /\ phase' = "card"
  /\ card' = TRUE
  /\ calendarWritten' = breakCalendar
  /\ UNCHANGED <<token, needsWeb, netField, zhipu, stopped, sendsAfterStop>>

Stop ==
  /\ ~stopped
  /\ phase \in {"built", "searched", "card"}
  /\ stopped' = TRUE
  /\ UNCHANGED <<phase, token, needsWeb, netField, zhipu, sendsAfterStop, card, calendarWritten>>

LaterSend ==
  /\ phase = "card"
  /\ phase' = "done"
  /\ sendsAfterStop' = (stopped /\ breakStop)
  /\ UNCHANGED <<token, needsWeb, netField, zhipu, stopped, card, calendarWritten>>

Done ==
  /\ phase = "done"
  /\ UNCHANGED vars

Next ==
  \/ Queue
  \/ Build
  \/ Search
  \/ SkipSearch
  \/ ShowCard
  \/ Stop
  \/ LaterSend
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in {"start", "queued", "built", "searched", "card", "done"}
  /\ token \in BOOLEAN
  /\ needsWeb \in BOOLEAN
  /\ netField \in BOOLEAN
  /\ zhipu \in BOOLEAN
  /\ stopped \in BOOLEAN
  /\ sendsAfterStop \in BOOLEAN
  /\ card \in BOOLEAN
  /\ calendarWritten \in BOOLEAN

NoBoth == ~(netField /\ zhipu)

NoGuessedField == ~netField

StopBlocksLaterSend == ~sendsAfterStop

CalendarStaysUnwritten == ~calendarWritten

FieldWhenAllowed ==
  ~token \/ netField \/ phase \in {"start", "queued"}

AbsentSkipsZhipu == token \/ ~zhipu

====
