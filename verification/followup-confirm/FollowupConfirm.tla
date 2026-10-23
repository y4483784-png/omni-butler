------------------------------ MODULE FollowupConfirm ------------------------------

EXTENDS Integers

CONSTANTS ATOMIC_CLAIM, OVERLAP_CHECK

VARIABLES
  phase,
  expired,
  ticked,
  done,
  inflight,
  loops,
  toSend,
  checking,
  checked,
  unacked,
  events,
  confirms

vars == <<phase, expired, ticked, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

TypeOK ==
  /\ phase \in {"none", "info", "draft", "cancelled"}
  /\ expired \in BOOLEAN
  /\ ticked \in BOOLEAN
  /\ done \in BOOLEAN
  /\ inflight \in BOOLEAN
  /\ loops \in 0..2
  /\ toSend \in 0..2
  /\ checking \in 0..2
  /\ checked \in 0..2
  /\ unacked \in 0..2
  /\ events \in 0..2
  /\ confirms \in 0..2

OneRequestPerLoop == toSend + checking + checked + unacked = loops

AtMostOneEvent == events <= 1

NoWriteBeforeConfirm == (events = 0) \/ (confirms > 0)

NeedsPasteWritesNothing == (phase = "info") => (events = 0 /\ confirms = 0)

Init ==
  /\ phase = "none"
  /\ expired = FALSE
  /\ ticked = FALSE
  /\ done = FALSE
  /\ inflight = FALSE
  /\ loops = 0
  /\ toSend = 0
  /\ checking = 0
  /\ checked = 0
  /\ unacked = 0
  /\ events = 0
  /\ confirms = 0

FollowUpDraft ==
  /\ phase = "none"
  /\ phase' = "draft"
  /\ UNCHANGED <<expired, ticked, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

FollowUpNeedsPaste ==
  /\ phase = "none"
  /\ phase' = "info"
  /\ UNCHANGED <<expired, ticked, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

Tick ==
  /\ phase = "draft"
  /\ ~inflight
  /\ ~ticked
  /\ ticked' = TRUE
  /\ UNCHANGED <<phase, expired, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

Untick ==
  /\ phase = "draft"
  /\ ~inflight
  /\ ticked
  /\ ticked' = FALSE
  /\ UNCHANGED <<phase, expired, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

Confirm ==
  /\ phase = "draft"
  /\ ~expired
  /\ ticked
  /\ ~done
  /\ confirms < 2
  /\ loops < 2
  /\ IF ATOMIC_CLAIM THEN ~inflight ELSE TRUE
  /\ inflight' = TRUE
  /\ loops' = loops + 1
  /\ toSend' = toSend + 1
  /\ confirms' = confirms + 1
  /\ UNCHANGED <<phase, expired, ticked, done, checking, checked, unacked, events>>

PostFollowAction ==
  /\ toSend > 0
  /\ toSend' = toSend - 1
  /\ checking' = checking + 1
  /\ UNCHANGED <<phase, expired, ticked, done, inflight, loops, checked, unacked, events, confirms>>

ConflictCheck ==
  /\ checking > 0
  /\ \/ ~OVERLAP_CHECK
     \/ events = 0
  /\ checking' = checking - 1
  /\ checked' = checked + 1
  /\ UNCHANGED <<phase, expired, ticked, done, inflight, loops, toSend, unacked, events, confirms>>

RejectOverlap ==
  /\ OVERLAP_CHECK
  /\ checking > 0
  /\ events >= 1
  /\ checking' = checking - 1
  /\ loops' = loops - 1
  /\ inflight' = FALSE
  /\ UNCHANGED <<phase, expired, ticked, done, toSend, checked, unacked, events, confirms>>

InsertEvent ==
  /\ checked > 0
  /\ events < 2
  /\ checked' = checked - 1
  /\ events' = events + 1
  /\ unacked' = unacked + 1
  /\ UNCHANGED <<phase, expired, ticked, done, inflight, loops, toSend, checking, confirms>>

ObserveSuccess ==
  /\ unacked > 0
  /\ unacked' = unacked - 1
  /\ loops' = loops - 1
  /\ done' = TRUE
  /\ inflight' = FALSE
  /\ UNCHANGED <<phase, expired, ticked, toSend, checking, checked, events, confirms>>

ObserveLostResponse ==
  /\ unacked > 0
  /\ unacked' = unacked - 1
  /\ loops' = loops - 1
  /\ inflight' = FALSE
  /\ UNCHANGED <<phase, expired, ticked, done, toSend, checking, checked, events, confirms>>

ExpireDraft ==
  /\ phase = "draft"
  /\ ~expired
  /\ expired' = TRUE
  /\ UNCHANGED <<phase, ticked, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

VoidDraft ==
  /\ phase = "draft"
  /\ phase' = "cancelled"
  /\ UNCHANGED <<expired, ticked, done, inflight, loops, toSend, checking, checked, unacked, events, confirms>>

Idle == UNCHANGED vars

Next ==
  \/ FollowUpDraft
  \/ FollowUpNeedsPaste
  \/ Tick
  \/ Untick
  \/ Confirm
  \/ PostFollowAction
  \/ ConflictCheck
  \/ RejectOverlap
  \/ InsertEvent
  \/ ObserveSuccess
  \/ ObserveLostResponse
  \/ ExpireDraft
  \/ VoidDraft
  \/ Idle

Spec == Init /\ [][Next]_vars

================================================================================
