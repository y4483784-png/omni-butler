---- MODULE EventInbox ----
\* One user, one session, one aimed generation.
\* Submit records a receipt and does not write the calendar.
\* A repeated delivery id does not open another receipt.
\* Stopped does not write and does not become external success.
\* Completed opens a new generation.
\* Waiting keeps one writable card: one reissue, then later events queue.
\* External success happens only after the other side has accepted.

EXTENDS Integers

CONSTANT breakDedupe, breakStopped, breakReuse, breakSecond, breakSuccess, breakPass

VARIABLES
  task, aimed, gen, newGen, seen, route, cards, reissued, extra, repeats,
  success, written, settled

vars == <<task, aimed, gen, newGen, seen, route, cards, reissued, extra, repeats,
          success, written, settled>>

Init ==
  /\ task = "none"
  /\ aimed = "none"
  /\ gen = 0
  /\ newGen = 0
  /\ seen = 0
  /\ route = "none"
  /\ cards = 0
  /\ reissued = FALSE
  /\ extra = 0
  /\ repeats = 0
  /\ success = FALSE
  /\ written = FALSE
  /\ settled = FALSE

Prepare(kind) ==
  /\ ~settled
  /\ task = "none"
  /\ seen = 0
  /\ kind \in {"running", "waiting", "completed", "stopped"}
  /\ task' = kind
  /\ aimed' = kind
  /\ gen' = 1
  /\ cards' = IF kind = "waiting" THEN 1 ELSE 0
  /\ UNCHANGED <<newGen, seen, route, reissued, extra, repeats, success, written, settled>>

Receive(kind) ==
  /\ ~settled
  /\ seen = 0
  /\ kind \in {"noise", "held"}
  /\ seen' = 1
  /\ route' = kind
  /\ success' = breakSuccess
  /\ UNCHANGED <<task, aimed, gen, newGen, cards, reissued, extra, repeats, written, settled>>

OpenFresh ==
  /\ ~settled
  /\ seen = 0
  /\ task = "none"
  /\ seen' = 1
  /\ route' = "opened"
  /\ task' = "running"
  /\ newGen' = 2
  /\ success' = breakSuccess
  /\ UNCHANGED <<aimed, gen, cards, reissued, extra, repeats, written, settled>>

Passthrough ==
  /\ ~settled
  /\ seen = 0
  /\ task = "running"
  /\ seen' = 1
  /\ route' = "passthrough"
  /\ newGen' = IF breakPass THEN 2 ELSE 0
  /\ success' = breakSuccess
  /\ UNCHANGED <<task, aimed, gen, cards, reissued, extra, repeats, written, settled>>

Keep ==
  /\ ~settled
  /\ seen = 0
  /\ task = "waiting"
  /\ seen' = 1
  /\ route' = "kept"
  /\ cards' = 1
  /\ success' = breakSuccess
  /\ UNCHANGED <<task, aimed, gen, newGen, reissued, extra, repeats, written, settled>>

Reissue ==
  /\ ~settled
  /\ seen = 0
  /\ task = "waiting"
  /\ seen' = 1
  /\ route' = "reissued"
  /\ reissued' = TRUE
  /\ cards' = 1
  /\ success' = breakSuccess
  /\ UNCHANGED <<task, aimed, gen, newGen, extra, repeats, written, settled>>

QueueFirst ==
  /\ ~settled
  /\ seen = 0
  /\ task = "waiting"
  /\ seen' = 1
  /\ route' = "queued"
  /\ cards' = 1
  /\ success' = breakSuccess
  /\ UNCHANGED <<task, aimed, gen, newGen, reissued, extra, repeats, written, settled>>

OpenAfterCompleted ==
  /\ ~settled
  /\ seen = 0
  /\ task = "completed"
  /\ seen' = 1
  /\ route' = "opened"
  /\ task' = "running"
  /\ newGen' = IF breakReuse THEN gen ELSE 2
  /\ success' = breakSuccess
  /\ UNCHANGED <<aimed, gen, cards, reissued, extra, repeats, written, settled>>

RefuseStopped ==
  /\ ~settled
  /\ seen = 0
  /\ task = "stopped"
  /\ seen' = 1
  /\ route' = "stopped"
  /\ cards' = IF breakStopped THEN 1 ELSE 0
  /\ newGen' = IF breakStopped THEN 2 ELSE 0
  /\ success' = breakStopped \/ breakSuccess
  /\ UNCHANGED <<task, aimed, gen, reissued, extra, repeats, written, settled>>

Later ==
  /\ ~settled
  /\ seen = 1
  /\ aimed = "waiting"
  /\ extra < 2
  /\ route \in {"kept", "reissued", "queued"}
  /\ extra' = extra + 1
  /\ \/ /\ ~reissued
        /\ route' = "reissued"
        /\ reissued' = TRUE
        /\ cards' = 1
        /\ success' = breakSuccess
        /\ UNCHANGED <<task, aimed, gen, newGen, seen, repeats, written, settled>>
     \/ /\ reissued
        /\ route' = "queued"
        /\ cards' = IF breakSecond THEN 2 ELSE 1
        /\ success' = breakSuccess
        /\ UNCHANGED <<task, aimed, gen, newGen, seen, reissued, repeats, written, settled>>

Repeat ==
  /\ ~settled
  /\ seen >= 1
  /\ repeats = 0
  /\ repeats' = 1
  /\ seen' = IF breakDedupe THEN seen + 1 ELSE seen
  /\ cards' = IF breakDedupe /\ aimed = "waiting" THEN cards + 1 ELSE cards
  /\ UNCHANGED <<task, aimed, gen, newGen, route, reissued, extra, success, written, settled>>

MarkWritten ==
  /\ ~settled
  /\ ~written
  /\ route \in {"kept", "reissued"}
  /\ written' = TRUE
  /\ UNCHANGED <<task, aimed, gen, newGen, seen, route, cards, reissued, extra, repeats,
                 success, settled>>

Note ==
  /\ ~settled
  /\ written
  /\ ~success
  /\ route \in {"kept", "reissued"}
  /\ success' = TRUE
  /\ UNCHANGED <<task, aimed, gen, newGen, seen, route, cards, reissued, extra, repeats,
                 written, settled>>

Settle ==
  /\ ~settled
  /\ seen >= 1
  /\ repeats = 1
  /\ \/ route \notin {"kept", "reissued", "queued"}
     \/ route = "queued" /\ (extra >= 2 \/ ~reissued)
     \/ success
  /\ settled' = TRUE
  /\ UNCHANGED <<task, aimed, gen, newGen, seen, route, cards, reissued, extra, repeats,
                 success, written>>

Idle ==
  /\ settled
  /\ UNCHANGED vars

Next ==
  \/ \E kind \in {"running", "waiting", "completed", "stopped"} : Prepare(kind)
  \/ \E kind \in {"noise", "held"} : Receive(kind)
  \/ OpenFresh
  \/ Passthrough
  \/ Keep
  \/ Reissue
  \/ QueueFirst
  \/ OpenAfterCompleted
  \/ RefuseStopped
  \/ Later
  \/ Repeat
  \/ MarkWritten
  \/ Note
  \/ Settle
  \/ Idle

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ task \in {"none", "running", "waiting", "completed", "stopped"}
  /\ aimed \in {"none", "running", "waiting", "completed", "stopped"}
  /\ gen \in 0..1
  /\ newGen \in 0..2
  /\ seen \in 0..2
  /\ route \in {"none", "noise", "held", "opened", "passthrough", "kept", "reissued",
                "queued", "stopped"}
  /\ cards \in 0..2
  /\ extra \in 0..2
  /\ repeats \in 0..1
  /\ reissued \in BOOLEAN
  /\ success \in BOOLEAN
  /\ written \in BOOLEAN
  /\ settled \in BOOLEAN

OneReceipt == seen <= 1

ReceiveIsNotSuccess == success => written

StoppedWritesNothing ==
  route = "stopped" => cards = 0 /\ ~success /\ newGen = 0

CompletedNotReused ==
  aimed = "completed" /\ route = "opened" => newGen = 2 /\ newGen # gen

OneCard == cards <= 1

QueueStaysOne == route = "queued" => cards = 1 /\ ~success

PassthroughStays == route = "passthrough" => newGen = 0

====
