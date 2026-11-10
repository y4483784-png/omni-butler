---- MODULE WorkLoop ----
\* One generation of the work loop.
\* Assign records the tool and does not act.
\* Admit reads stop, then calls the tool only when the generation is still running.
\* A calendar step shows a card and does not insert.
\* An unknown result is settled by reconcile or by refuse. Neither inserts.
\* A lookup runs only after reconcile.

EXTENDS Integers

CONSTANT breakStop, breakUnknown, breakCalendar, breakDispatch, breakLookup

VARIABLES
  phase, stopped, tool,
  invokes, invokedAfterStop,
  insertKind, lookups, choice,
  card, reply,
  dispatchInvoked, dispatchClaimed, dispatchWrote

vars == <<phase, stopped, tool, invokes, invokedAfterStop, insertKind, lookups,
          choice, card, reply, dispatchInvoked, dispatchClaimed, dispatchWrote>>

Init ==
  /\ phase = "start"
  /\ stopped = FALSE
  /\ tool = "none"
  /\ invokes = 0
  /\ invokedAfterStop = FALSE
  /\ insertKind = "none"
  /\ lookups = 0
  /\ choice = "none"
  /\ card = FALSE
  /\ reply = "none"
  /\ dispatchInvoked = FALSE
  /\ dispatchClaimed = FALSE
  /\ dispatchWrote = FALSE

Assign ==
  /\ phase = "start"
  /\ phase' = "assigned"
  /\ tool' \in {"none", "read", "calendar"}
  /\ dispatchInvoked' = breakDispatch
  /\ dispatchClaimed' = breakDispatch
  /\ dispatchWrote' = breakDispatch
  /\ UNCHANGED <<stopped, invokes, invokedAfterStop, insertKind, lookups, choice, card, reply>>

Stop ==
  /\ ~stopped
  /\ phase \in {"start", "assigned", "working"}
  /\ stopped' = TRUE
  /\ UNCHANGED <<phase, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 card, reply, dispatchInvoked, dispatchClaimed, dispatchWrote>>

Enter ==
  /\ phase = "assigned"
  /\ phase' = IF stopped THEN "halted" ELSE "working"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 card, reply, dispatchInvoked, dispatchClaimed, dispatchWrote>>

Halt ==
  /\ phase = "working"
  /\ stopped
  /\ ~breakStop
  /\ phase' = "halted"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 card, reply, dispatchInvoked, dispatchClaimed, dispatchWrote>>

AnswerNone ==
  /\ phase = "working"
  /\ tool = "none"
  /\ phase' = IF stopped THEN "halted" ELSE "done"
  /\ reply' = IF stopped THEN reply ELSE "work"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 card, dispatchInvoked, dispatchClaimed, dispatchWrote>>

Call ==
  /\ phase = "working"
  /\ tool \in {"read", "calendar"}
  /\ invokes = 0
  /\ (~stopped \/ breakStop)
  /\ invokes' = 1
  /\ invokedAfterStop' = stopped
  /\ phase' = IF tool = "calendar" THEN "drafted" ELSE "readback"
  /\ UNCHANGED <<stopped, tool, insertKind, lookups, choice, card, reply,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

ReadAnswer ==
  /\ phase = "readback"
  /\ phase' = "done"
  /\ reply' = "work"
  /\ card' = FALSE
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

ShowCard ==
  /\ phase = "drafted"
  /\ ~breakCalendar
  /\ card' = TRUE
  /\ reply' = "work"
  /\ phase' = "done"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

WriteFromLoop ==
  /\ breakCalendar
  /\ phase = "drafted"
  /\ insertKind = "none"
  /\ insertKind' = "draft"
  /\ card' = TRUE
  /\ reply' = "work"
  /\ phase' = "done"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, lookups, choice,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

ReportUnknown ==
  /\ phase = "drafted"
  /\ card' = FALSE
  /\ phase' = "unknown"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, lookups, choice,
                 reply, dispatchInvoked, dispatchClaimed, dispatchWrote>>

SettleReconcile ==
  /\ phase = "unknown"
  /\ ~breakUnknown
  /\ ~breakLookup
  /\ choice' = "reconcile"
  /\ lookups' = 1
  /\ reply' = "work"
  /\ phase' = "done"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, card,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

SettleSend ==
  /\ phase = "unknown"
  /\ ~breakUnknown
  /\ ~breakLookup
  /\ choice' = "send"
  /\ lookups' = 0
  /\ reply' = "work"
  /\ phase' = "done"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, card,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

SecondInsert ==
  /\ breakUnknown
  /\ phase = "unknown"
  /\ insertKind = "none"
  /\ insertKind' = "second"
  /\ invokes' = invokes + 1
  /\ phase' = "done"
  /\ UNCHANGED <<stopped, tool, invokedAfterStop, lookups, choice, card, reply,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

LookupOnSend ==
  /\ breakLookup
  /\ phase = "unknown"
  /\ choice' = "send"
  /\ lookups' = 1
  /\ reply' = "work"
  /\ phase' = "done"
  /\ UNCHANGED <<stopped, tool, invokes, invokedAfterStop, insertKind, card,
                 dispatchInvoked, dispatchClaimed, dispatchWrote>>

Done ==
  /\ phase \in {"halted", "done"}
  /\ UNCHANGED vars

Next ==
  \/ Assign
  \/ Stop
  \/ Enter
  \/ Halt
  \/ AnswerNone
  \/ Call
  \/ ReadAnswer
  \/ ShowCard
  \/ WriteFromLoop
  \/ ReportUnknown
  \/ SettleReconcile
  \/ SettleSend
  \/ SecondInsert
  \/ LookupOnSend
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in {"start", "assigned", "working", "halted", "drafted", "readback", "unknown", "done"}
  /\ stopped \in BOOLEAN
  /\ tool \in {"none", "read", "calendar"}
  /\ invokes \in 0..2
  /\ invokedAfterStop \in BOOLEAN
  /\ insertKind \in {"none", "second", "draft"}
  /\ lookups \in 0..1
  /\ choice \in {"none", "reconcile", "send"}
  /\ card \in BOOLEAN
  /\ reply \in {"none", "work"}
  /\ dispatchInvoked \in BOOLEAN
  /\ dispatchClaimed \in BOOLEAN
  /\ dispatchWrote \in BOOLEAN

StopBlocksInvoke == invokedAfterStop = FALSE

NoSecondInsert == insertKind # "second"

CalendarStaysUnwritten == insertKind # "draft"

LookupFollowsChoice == (lookups = 1) => (choice = "reconcile")

DispatchStaysQuiet ==
  /\ dispatchInvoked = FALSE
  /\ dispatchClaimed = FALSE
  /\ dispatchWrote = FALSE

====
