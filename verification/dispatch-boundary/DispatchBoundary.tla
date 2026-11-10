---- MODULE DispatchBoundary ----
\* One generation. Dispatch records an assignment. Only the work path may call
\* the gateway, publish a schedule claim, or write a calendar row.

EXTENDS Integers

CONSTANT breakInvoke, breakClaim, breakCalendar, breakStop

VARIABLES
  phase, stopped, tool,
  dispatchInvoked, dispatchClaimed, dispatchWrote,
  workInvoked, invokedAfterStop, claim, calendar, reply

vars == <<phase, stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
          workInvoked, invokedAfterStop, claim, calendar, reply>>

Init ==
  /\ phase = "start"
  /\ stopped = FALSE
  /\ tool = "none"
  /\ dispatchInvoked = FALSE
  /\ dispatchClaimed = FALSE
  /\ dispatchWrote = FALSE
  /\ workInvoked = FALSE
  /\ invokedAfterStop = FALSE
  /\ claim = FALSE
  /\ calendar = 0
  /\ reply = "none"

Assign ==
  /\ phase = "start"
  /\ phase' = "assigned"
  /\ tool' \in {"none", "read", "calendar"}
  /\ dispatchInvoked' = breakInvoke
  /\ dispatchClaimed' = breakClaim
  /\ dispatchWrote' = breakCalendar
  /\ calendar' = IF breakCalendar THEN 1 ELSE calendar
  /\ UNCHANGED <<stopped, workInvoked, invokedAfterStop, claim, reply>>

Stop ==
  /\ ~stopped
  /\ phase \in {"start", "assigned", "working"}
  /\ stopped' = TRUE
  /\ UNCHANGED <<phase, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 workInvoked, invokedAfterStop, claim, calendar, reply>>

EnterWork ==
  /\ phase = "assigned"
  /\ phase' = IF stopped THEN "halted" ELSE "working"
  /\ UNCHANGED <<stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 workInvoked, invokedAfterStop, claim, calendar, reply>>

WorkCutoff ==
  /\ phase = "working"
  /\ tool \in {"read", "calendar"}
  /\ ~workInvoked
  /\ stopped
  /\ ~breakStop
  /\ phase' = "halted"
  /\ UNCHANGED <<stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 workInvoked, invokedAfterStop, claim, calendar, reply>>

WorkCall ==
  /\ phase = "working"
  /\ tool \in {"read", "calendar"}
  /\ ~workInvoked
  /\ (~stopped \/ breakStop)
  /\ workInvoked' = TRUE
  /\ invokedAfterStop' = stopped
  /\ phase' = IF tool = "calendar" THEN "card" ELSE "replied"
  /\ reply' = "work"
  /\ UNCHANGED <<stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 claim, calendar>>

WorkAnswer ==
  /\ phase = "working"
  /\ tool = "none"
  /\ phase' = IF stopped THEN "halted" ELSE "replied"
  /\ reply' = IF stopped THEN reply ELSE "work"
  /\ UNCHANGED <<stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 workInvoked, invokedAfterStop, claim, calendar>>

Publish ==
  /\ phase = "card"
  /\ ~claim
  /\ claim' = TRUE
  /\ UNCHANGED <<phase, stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 workInvoked, invokedAfterStop, calendar, reply>>

Confirm ==
  /\ phase = "card"
  /\ claim
  /\ calendar = 0
  /\ ~dispatchWrote
  /\ calendar' = 1
  /\ phase' = "replied"
  /\ UNCHANGED <<stopped, tool, dispatchInvoked, dispatchClaimed, dispatchWrote,
                 workInvoked, invokedAfterStop, claim, reply>>

Done ==
  /\ phase \in {"halted", "replied"}
  /\ UNCHANGED vars

Next ==
  \/ Assign
  \/ Stop
  \/ EnterWork
  \/ WorkCutoff
  \/ WorkCall
  \/ WorkAnswer
  \/ Publish
  \/ Confirm
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ phase \in {"start", "assigned", "working", "halted", "card", "replied"}
  /\ stopped \in BOOLEAN
  /\ tool \in {"none", "read", "calendar"}
  /\ dispatchInvoked \in BOOLEAN
  /\ dispatchClaimed \in BOOLEAN
  /\ dispatchWrote \in BOOLEAN
  /\ workInvoked \in BOOLEAN
  /\ invokedAfterStop \in BOOLEAN
  /\ claim \in BOOLEAN
  /\ calendar \in 0..1
  /\ reply \in {"none", "work"}

DispatchDoesNotInvoke == dispatchInvoked = FALSE

DispatchDoesNotClaim == dispatchClaimed = FALSE

DispatchDoesNotWrite == dispatchWrote = FALSE /\ (calendar = 1 => claim)

StopBlocksGateway == invokedAfterStop = FALSE
====
