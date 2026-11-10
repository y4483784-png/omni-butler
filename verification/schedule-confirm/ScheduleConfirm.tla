------------------------------ MODULE ScheduleConfirm ------------------------------

EXTENDS Integers

CONSTANTS SUBMIT_GATE, CONFIRMED_LOCK, TOKEN_REQUIRED

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
  writesAtRetire,
  token

vars == <<
  phase, mode, target, pastExpiry, streaming, window,
  busy, pending, armed, unacked, writes, entered, restored, retired, writesAtRetire,
  token
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
  /\ token \in {"absent", "bound"}

NoWriteBeforeConfirm == writes = 0 \/ entered > 0
NoWriteWithoutToken == writes = 0 \/ token = "bound"
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

ready(p, m, t, tok) ==
  /\ base
  /\ phase = p
  /\ mode = m
  /\ target = t
  /\ token = tok

Init ==
  \/ ready("draft", "create", "none", "bound")
  \/ ready("draft", "create", "none", "absent")
  \/ ready("draft", "update", "local", "bound")
  \/ ready("draft", "cancel", "local", "bound")
  \/ ready("draft", "update", "google", "bound")
  \/ ready("draft", "cancel", "google", "bound")
  \/ ready("draft", "choose", "none", "bound")
  \/ ready("", "", "local", "bound")
  \/ ready("draft", "update", "none", "bound")

tokenOk == ~TOKEN_REQUIRED \/ token = "bound"

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
       writes, entered, restored, retired, writesAtRetire, token
     >>

applySlot ==
  /\ ~locked
  /\ window = "busy"
  /\ window' = "free"
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, busy, pending, armed, unacked,
       writes, entered, restored, retired, writesAtRetire, token
     >>

Confirm ==
  /\ writableDispatch
  /\ canEnter
  /\ busy' = TRUE
  /\ entered' = entered + 1
  /\ pending' = pending + 1
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, armed, unacked,
       writes, restored, retired, writesAtRetire, token
     >>

MissingEvent ==
  /\ dispatch \in {"reject", "choose"}
  /\ canEnter
  /\ entered' = entered + 1
  /\ busy' = FALSE
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, pending, armed, unacked,
       writes, restored, retired, writesAtRetire, token
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
       writes, entered, restored, retired, writesAtRetire, token
     >>

CreateCalendarEvent ==
  /\ armed > 0
  /\ writes < 2
  /\ ~retired
  /\ tokenOk
  /\ armed' = armed - 1
  /\ writes' = writes + 1
  /\ unacked' = unacked + 1
  /\ window' = "busy"
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, busy, pending,
       entered, restored, retired, writesAtRetire, token
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
       writes, entered, restored, retired, writesAtRetire, token
     >>

countWrite ==
  /\ ~retired
  /\ tokenOk
  /\ pending' = pending - 1
  /\ writes' = writes + 1
  /\ unacked' = unacked + 1
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, streaming, window, busy, armed,
       entered, restored, retired, writesAtRetire, token
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
       writes, entered, restored, retired, writesAtRetire, token
     >>

ApplySuccess ==
  /\ unacked > 0
  /\ unacked' = unacked - 1
  /\ busy' = FALSE
  /\ phase' = IF CONFIRMED_LOCK THEN "confirmed" ELSE phase
  /\ UNCHANGED <<
       mode, target, pastExpiry, streaming, window, pending, armed,
       writes, entered, restored, retired, writesAtRetire, token
     >>

UpdateLastAssistant ==
  /\ streaming
  /\ \/ /\ phase \in {"draft", ""}
        /\ ~retired
        /\ writes = 0
        /\ phase' = "draft"
        /\ restored' = TRUE
        /\ pastExpiry' = FALSE
        /\ UNCHANGED <<
             mode, target, streaming, window, busy, pending, armed, unacked,
             writes, entered, retired, writesAtRetire, token
           >>
     \/ /\ phase \in {"confirmed", "cancelled"} \/ retired \/ writes >= 1
        /\ restored' = TRUE
        /\ UNCHANGED <<
             phase, pastExpiry, mode, target, streaming, window, busy, pending,
             armed, unacked, writes, entered, retired, writesAtRetire, token
           >>

FinishRunStreamLoop ==
  /\ streaming
  /\ streaming' = FALSE
  /\ UNCHANGED <<
       phase, mode, target, pastExpiry, window, busy, pending, armed, unacked,
       writes, entered, restored, retired, writesAtRetire, token
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
       writes, entered, restored, token
     >>

DeclineCancel ==
  /\ mode = "cancel"
  /\ ~locked
  /\ ~busy
  /\ phase' = "cancelled"
  /\ retireNow
  /\ UNCHANGED <<
       mode, target, pastExpiry, streaming, window, busy, pending, armed, unacked,
       writes, entered, restored, token
     >>

ExpireDraft ==
  /\ phase = "draft"
  /\ ~pastExpiry
  /\ pastExpiry' = TRUE
  /\ retireNow
  /\ UNCHANGED <<
       phase, mode, target, streaming, window, busy, pending, armed, unacked,
       writes, entered, restored, token
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
