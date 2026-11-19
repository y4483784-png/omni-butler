---- MODULE OneWebPath ----
\* One chat turn after native web search moved onto the Responses request.
\* webAllowed is the existing allow-list name "web".
\* The answer request carries the server tool when that name is present.
\* The client search does not run. needsWeb does not start it.
\* A completed server search is not itself a citation.
\* Stop blocks the next model send.
\* A calendar card does not write the event.

CONSTANT
  breakBoth, breakOmit, breakZhipu, breakStop, breakCalendar, breakCite

VARIABLES
  phase, webAllowed, needsWeb, hasUrl,
  netField, zhipu,
  stopped, sendsAfterStop,
  card, calendarWritten,
  searchCompleted, citation

vars == <<
  phase, webAllowed, needsWeb, hasUrl,
  netField, zhipu,
  stopped, sendsAfterStop,
  card, calendarWritten,
  searchCompleted, citation
>>

Init ==
  /\ phase = "start"
  /\ webAllowed \in BOOLEAN
  /\ needsWeb \in BOOLEAN
  /\ hasUrl \in BOOLEAN
  /\ netField = FALSE
  /\ zhipu = FALSE
  /\ stopped = FALSE
  /\ sendsAfterStop = FALSE
  /\ card = FALSE
  /\ calendarWritten = FALSE
  /\ searchCompleted = FALSE
  /\ citation = FALSE

Queue ==
  /\ phase = "start"
  /\ phase' = "queued"
  /\ UNCHANGED <<webAllowed, needsWeb, hasUrl, netField, zhipu, stopped, sendsAfterStop, card, calendarWritten, searchCompleted, citation>>

Build ==
  /\ phase = "queued"
  /\ phase' = "built"
  /\ netField' = (IF breakBoth THEN TRUE ELSE IF breakOmit THEN FALSE ELSE webAllowed)
  /\ UNCHANGED <<webAllowed, needsWeb, hasUrl, zhipu, stopped, sendsAfterStop, card, calendarWritten, searchCompleted, citation>>

ClientSearch ==
  /\ phase = "built"
  /\ phase' = "searched"
  /\ zhipu' = (breakZhipu \/ breakBoth)
  /\ UNCHANGED <<webAllowed, needsWeb, hasUrl, netField, stopped, sendsAfterStop, card, calendarWritten, searchCompleted, citation>>

NoteResult ==
  /\ phase = "searched"
  /\ phase' = "noted"
  /\ searchCompleted' = TRUE
  /\ citation' = (IF breakCite THEN TRUE ELSE hasUrl)
  /\ UNCHANGED <<webAllowed, needsWeb, hasUrl, netField, zhipu, stopped, sendsAfterStop, card, calendarWritten>>

ShowCard ==
  /\ phase = "noted"
  /\ phase' = "card"
  /\ card' = TRUE
  /\ calendarWritten' = breakCalendar
  /\ UNCHANGED <<webAllowed, needsWeb, hasUrl, netField, zhipu, stopped, sendsAfterStop, searchCompleted, citation>>

Stop ==
  /\ ~stopped
  /\ phase \in {"built", "searched", "noted", "card"}
  /\ stopped' = TRUE
  /\ UNCHANGED <<phase, webAllowed, needsWeb, hasUrl, netField, zhipu, sendsAfterStop, card, calendarWritten, searchCompleted, citation>>

LaterSend ==
  /\ phase = "card"
  /\ phase' = "done"
  /\ sendsAfterStop' = (stopped /\ breakStop)
  /\ UNCHANGED <<webAllowed, needsWeb, hasUrl, netField, zhipu, stopped, card, calendarWritten, searchCompleted, citation>>

Done ==
  /\ phase = "done"
  /\ UNCHANGED vars

Next ==
  \/ Queue
  \/ Build
  \/ ClientSearch
  \/ NoteResult
  \/ ShowCard
  \/ Stop
  \/ LaterSend
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in {"start", "queued", "built", "searched", "noted", "card", "done"}
  /\ webAllowed \in BOOLEAN
  /\ needsWeb \in BOOLEAN
  /\ hasUrl \in BOOLEAN
  /\ netField \in BOOLEAN
  /\ zhipu \in BOOLEAN
  /\ stopped \in BOOLEAN
  /\ sendsAfterStop \in BOOLEAN
  /\ card \in BOOLEAN
  /\ calendarWritten \in BOOLEAN
  /\ searchCompleted \in BOOLEAN
  /\ citation \in BOOLEAN

NoZhipu == ~zhipu

NoBoth == ~(netField /\ zhipu)

DefaultOffers ==
  ~webAllowed \/ netField \/ phase \in {"start", "queued"}

ExplicitOmits ==
  webAllowed \/ (~netField /\ ~zhipu)

StopBlocksLaterSend == ~sendsAfterStop

CalendarStaysUnwritten == ~calendarWritten

CitationNeedsSource == citation => hasUrl

====
