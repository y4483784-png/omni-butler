------------------------------ MODULE ScheduleConfirm ------------------------------

EXTENDS Integers

CONSTANTS SUBMIT_GATE, CONFIRMED_LOCK

VARIABLES
  phase,
  mode,
  target,
  pastExpiry,
  streaming,
  window,
  busy,
  pending,
  armed,
  unacked,
  writes,
  entered,
  restored,
  retired,
  writesAtRetire

vars == <<
  phase, mode, target, pastExpiry, streaming, window,
  busy, pending, armed, unacked, writes, entered, restored, retired, writesAtRetire
>>

legacy == phase = "" /\ mode = ""
expired == phase = "draft" /\ pastExpiry
writable == phase = "draft" \/ legacy
locked == ~writable \/ expired \/ phase = "cancelled"
buttonOff == locked \/ busy \/ mode = "choose"
open == pending + armed + unacked

dispatch ==
  IF mode = "create" THEN "create"
  ELSE IF mode = "choose" THEN "choose"
  ELSE IF legacy THEN "patch"
  ELSE IF target = "google" /\ mode = "update" THEN "google-update"
  ELSE IF target = "google" /\ mode = "cancel" THEN "google-cancel"
  ELSE IF target = "local" /\ mode = "update" THEN "patch"
  ELSE IF target = "local" /\ mode = "cancel" THEN "delete"
  ELSE "reject"

TypeOK ==
  /\ phase \in {"", "draft", "confirmed", "cancelled"}
  /\ mode \in {"", "create", "update", "cancel", "choose"}
  /\ target \in {"none", "local", "google"}
  /\ pastExpiry \in BOOLEAN
  /\ streaming \in BOOLEAN
  /\ window \in {"free", "busy"}
  /\ busy \in BOOLEAN
  /\ pending \in 0..2
  /\ armed \in 0..2
  /\ unacked \in 0..2
  /\ writes \in 0..2
  /\ entered \in 0..2
  /\ restored \in BOOLEAN
  /\ retired \in BOOLEAN
  /\ writesAtRetire \in 0..2

NoWriteBeforeConfirm == writes = 0 \/ entered > 0
AtMostOneSuccessfulWrite == writes <= 1
NoWriteAfterRetire == ~retired \/ writes <= writesAtRetire
NoReentryWhileBusy == open <= 1
SecondWriteImpliesRestore == writes <= 1 \/ restored
WritesFollowConfirm == writes <= entered /\ open <= entered

base ==
  /\ pastExpiry = FALSE
  /\ streaming = TRUE
  /\ window = "free"
  /\ busy = FALSE
  /\ pending = 0
  /\ armed = 0
  /\ unacked = 0
  /\ writes = 0
  /\ entered = 0
  /\ restored = FALSE
  /\ retired = FALSE
  /\ writesAtRetire = 0

Init ==
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "create"
     /\ target = "none"
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "update"
     /\ target = "local"
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "cancel"
     /\ target = "local"
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "update"
     /\ target = "google"
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "cancel"
     /\ target = "google"
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "choose"
     /\ target = "none"
  \/ /\ base
     /\ phase = ""
     /\ mode = ""
     /\ target = "local"
  \/ /\ base
     /\ phase = "draft"
     /\ mode = "update"
     /\ target = "none"

canEnter ==
  /\ entered < 2
  /\ open < 2
  /\ ~SUBMIT_GATE \/ ~buttonOff

writableDispatch ==
  dispatch \in {"create", "patch", "delete", "google-update", "google-cancel"}

ChooseCandidate ==
  /\ mode = "choose"
  /\ phase = "draft"
  /\ mode' \in {"update", "cancel"}
  /\ target' \in {"local", "google"}
  /\ UNCHANGED <<
       phase, pastExpiry, streaming, window, busy, pending, armed, unacked,
       writes, entered, restored, retired, writesAtRetire
     >>

applySlot ==
  /\ ~locked
  /\ window = "busy"
  /\ window' = "free"
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, busy, pending, armed, unacked,
       writes, entered, restored, retired, writesAtRetire
     >>

Confirm ==
  /\ writableDispatch
  /\ canEnter
  /\ busy' = TRUE
  /\ entered' = entered + 1
  /\ pending' = pending + 1
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, armed, unacked,
       writes, restored, retired, writesAtRetire
     >>

MissingEvent ==
  /\ dispatch \in {"reject", "choose"}
  /\ canEnter
  /\ entered' = entered + 1
  /\ busy' = FALSE
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, pending, armed, unacked,
       writes, restored, retired, writesAtRetire
     >>

CheckConflict ==
  /\ pending > 0
  /\ dispatch = "create"
  /\ window = "free"
  /\ armed < 2
  /\ pending' = pending - 1
  /\ armed' = armed + 1
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, busy, unacked,
       writes, entered, restored, retired, writesAtRetire
     >>

CreateCalendarEvent ==
  /\ armed > 0
  /\ writes < 2
  /\ armed' = armed - 1
  /\ writes' = writes + 1
  /\ unacked' = unacked + 1
  /\ window' = "busy"
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, busy, pending,
       entered, restored, retired, writesAtRetire
     >>

RejectCreateConflict ==
  /\ pending > 0
  /\ dispatch = "create"
  /\ window = "busy"
  /\ writes >= 1
  /\ pending' = pending - 1
  /\ busy' = FALSE
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, armed, unacked,
       writes, entered, restored, retired, writesAtRetire
     >>

countWrite ==
  /\ pending' = pending - 1
  /\ writes' = writes + 1
  /\ unacked' = unacked + 1
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, busy, armed,
       entered, restored, retired, writesAtRetire
     >>

PatchEvent ==
  /\ pending > 0
  /\ dispatch = "patch"
  /\ writes < 2
  /\ countWrite

DeleteEvent ==
  /\ pending > 0
  /\ dispatch = "delete"
  /\ writes < 2
  /\ countWrite

ConfirmGoogleTitleChange ==
  /\ pending > 0
  /\ writes < 2
  /\ \/ dispatch = "google-update"
     \/ /\ dispatch = "google-cancel"
        /\ writes = 0
  /\ countWrite

CalendarTitleUnmatched ==
  /\ pending > 0
  /\ dispatch = "google-cancel"
  /\ writes >= 1
  /\ pending' = pending - 1
  /\ busy' = FALSE
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, armed, unacked,
       writes, entered, restored, retired, writesAtRetire
     >>

ApplySuccess ==
  /\ unacked > 0
  /\ unacked' = unacked - 1
  /\ busy' = FALSE
  /\ phase' = IF CONFIRMED_LOCK THEN "confirmed" ELSE phase
  /\ UNCHANGED <<
       mode, target, pastExpiry, streaming, window, pending, armed,
       writes, entered, restored, retired, writesAtRetire
     >>

UpdateLastAssistant ==
  /\ streaming
  /\ phase' = "draft"
  /\ restored' = TRUE
  /\ pastExpiry' = FALSE
  /\ UNCHANGED <<
       mode, target, streaming, window, busy, pending, armed, unacked,
       writes, entered, retired, writesAtRetire
     >>

FinishRunStreamLoop ==
  /\ streaming
  /\ streaming' = FALSE
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, window, busy, pending, armed, unacked,
       writes, entered, restored, retired, writesAtRetire
     >>

retireNow ==
  /\ retired' = TRUE
  /\ writesAtRetire' = IF retired THEN writesAtRetire ELSE writes

VoidOpenDrafts ==
  /\ phase = "draft"
  /\ phase' = "cancelled"
  /\ retireNow
  /\ UNCHANGED <<
       mode, target, pastExpiry, streaming, window, busy, pending, armed, unacked,
       writes, entered, restored
     >>

DeclineCancel ==
  /\ mode = "cancel"
  /\ ~locked
  /\ ~busy
  /\ phase' = "cancelled"
  /\ retireNow
  /\ UNCHANGED <<
       mode, target, pastExpiry, streaming, window, busy, pending, armed, unacked,
       writes, entered, restored
     >>

ExpireDraft ==
  /\ phase = "draft"
  /\ ~pastExpiry
  /\ pastExpiry' = TRUE
  /\ retireNow
  /\ UNCHANGED <<
       phase, mode, target, streaming, window, busy, pending, armed, unacked,
       writes, entered, restored
     >>

Idle == UNCHANGED vars

Next ==
  \/ ChooseCandidate
  \/ applySlot
  \/ Confirm
  \/ MissingEvent
  \/ CheckConflict
  \/ CreateCalendarEvent
  \/ RejectCreateConflict
  \/ PatchEvent
  \/ DeleteEvent
  \/ ConfirmGoogleTitleChange
  \/ CalendarTitleUnmatched
  \/ ApplySuccess
  \/ UpdateLastAssistant
  \/ FinishRunStreamLoop
  \/ VoidOpenDrafts
  \/ DeclineCancel
  \/ ExpireDraft
  \/ Idle

Spec == Init /\ [][Next]_vars

================================================================================
