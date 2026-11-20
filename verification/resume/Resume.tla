---- MODULE Resume ----
EXTENDS Integers

CONSTANTS
    allowDisconnectStop, allowHoldEarly, allowPublishCrash, allowReclaim,
    allowEarlyModel, allowResend, allowStale, allowDirty, allowRecover

Jobs == 1..2
Subs == 1..2

VARIABLES
    status, msg, publishedMsg, hold, phase, model, invoked, sent,
    epoch, result, cancel, crash, disconnected, published, inflight,
    pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick

vars == <<
    status, msg, publishedMsg, hold, phase, model, invoked, sent,
    epoch, result, cancel, crash, disconnected, published, inflight,
    pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick
>>

Init ==
    /\ status = [j \in Jobs |-> "run"]
    /\ msg = [j \in Jobs |-> 0]
    /\ publishedMsg = [j \in Jobs |-> 0]
    /\ hold = [j \in Jobs |-> FALSE]
    /\ phase = [j \in Jobs |-> "none"]
    /\ model = [j \in Jobs |-> 0]
    /\ invoked = [j \in Jobs |-> 0]
    /\ sent = [j \in Jobs |-> 0]
    /\ epoch = [j \in Jobs |-> 0]
    /\ result = [j \in Jobs |-> ""]
    /\ cancel = [j \in Jobs |-> FALSE]
    /\ crash = 0
    /\ disconnected = FALSE
    /\ published = 0
    /\ inflight = 0
    /\ pending = "old"
    /\ committed = "old"
    /\ snapshot = 0
    /\ saw = "old"
    /\ cursor = [s \in Subs |-> 0]
    /\ sends = 0
    /\ stales = 0
    /\ snaps = 0
    /\ tick = 0

Disconnect ==
    /\ ~disconnected
    /\ disconnected' = TRUE
    /\ status' = IF allowDisconnectStop THEN [status EXCEPT ![1] = "stop"] ELSE status
    /\ UNCHANGED <<msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Reserve(j) ==
    /\ status[j] = "run"
    /\ ~hold[j]
    /\ msg[j] = 0
    /\ ~(crash = 1 /\ j = 1)
    /\ msg' = [msg EXCEPT ![j] = 1]
    /\ UNCHANGED <<status, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Publish(j) ==
    /\ msg[j] # 0
    /\ publishedMsg[j] # msg[j]
    /\ ~(crash = 1 /\ j = 1)
    /\ publishedMsg' = [publishedMsg EXCEPT ![j] = msg[j]]
    /\ UNCHANGED <<status, msg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Crash ==
    /\ crash = 0
    /\ msg[1] = 1
    /\ publishedMsg[1] = 0
    /\ ~hold[1]
    /\ crash' = 1
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Recover ==
    /\ allowRecover
    /\ crash = 1
    /\ ~cancel[1]
    /\ status[1] = "run"
    /\ crash' = 2
    /\ msg' = [msg EXCEPT ![1] = 2]
    /\ publishedMsg' = [publishedMsg EXCEPT ![1] = 0]
    /\ UNCHANGED <<status, hold, phase, model, invoked, sent, epoch, result, cancel, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Adopt(j) ==
    /\ status[j] = "run"
    /\ ~hold[j]
    /\ msg[j] # 0
    /\ publishedMsg[j] = msg[j]
    /\ ~(crash = 1 /\ j = 1)
    /\ hold' = [hold EXCEPT ![j] = TRUE]
    /\ epoch' = [epoch EXCEPT ![j] = epoch[j] + 1]
    /\ msg' = [msg EXCEPT ![j] = 0]
    /\ publishedMsg' = [publishedMsg EXCEPT ![j] = 0]
    /\ UNCHANGED <<status, phase, model, invoked, sent, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Cancel(j) ==
    /\ crash # 1
    /\ status[j] = "run"
    /\ ~cancel[j]
    /\ cancel' = [cancel EXCEPT ![j] = TRUE]
    /\ status' = [status EXCEPT ![j] = "stop"]
    /\ hold' = [hold EXCEPT ![j] = FALSE]
    /\ UNCHANGED <<msg, publishedMsg, phase, model, invoked, sent, epoch, result, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Calls ==
    /\ hold[1]
    /\ phase[1] = "none"
    /\ phase' = [phase EXCEPT ![1] = "calls"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Invoke ==
    /\ phase[1] = "calls"
    /\ invoked[1] = 0
    /\ invoked' = [invoked EXCEPT ![1] = 1]
    /\ phase' = [phase EXCEPT ![1] = "ready"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, model, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Model ==
    /\ phase[1] = "ready"
    /\ invoked[1] = 1
    /\ model[1] = 0
    /\ model' = [model EXCEPT ![1] = 1]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Arm ==
    /\ hold[1]
    /\ phase[1] \in {"none", "calls"}
    /\ phase' = [phase EXCEPT ![1] = "armed"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Send ==
    /\ phase[1] = "armed"
    /\ sends = 0
    /\ sends' = 1
    /\ sent' = IF allowResend THEN [sent EXCEPT ![1] = 1] ELSE sent
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, stales, snaps, tick>>

StaleWrite(j) ==
    /\ epoch[j] > 0
    /\ stales < 2
    /\ stales' = stales + 1
    /\ result' = IF allowStale THEN [result EXCEPT ![j] = "stale"] ELSE result
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, snaps, tick>>

Write(j) ==
    /\ hold[j]
    /\ epoch[j] > 0
    /\ result[j] = ""
    /\ result' = [result EXCEPT ![j] = "kept"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Allocate ==
    /\ inflight = 0
    /\ published < 2
    /\ inflight' = 1
    /\ pending' = "new"
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Commit ==
    /\ inflight = 1
    /\ published' = published + 1
    /\ inflight' = 0
    /\ committed' = pending
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, pending, snapshot, saw, cursor, sends, stales, snaps, tick>>

Snapshot ==
    /\ snaps < 2
    /\ snaps' = snaps + 1
    /\ snapshot' = IF allowDirty /\ inflight = 1 THEN published + 1 ELSE published
    /\ saw' = IF allowDirty /\ inflight = 1 THEN pending ELSE committed
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, cursor, sends, stales, tick>>

Deliver(s) ==
    /\ cursor[s] < published
    /\ cursor' = [cursor EXCEPT ![s] = cursor[s] + 1]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, sends, stales, snaps, tick>>

EarlyHold ==
    /\ allowHoldEarly
    /\ ~hold[1]
    /\ hold' = [hold EXCEPT ![1] = TRUE]
    /\ UNCHANGED <<status, msg, publishedMsg, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

PublishDuringCrash ==
    /\ allowPublishCrash
    /\ crash = 1
    /\ publishedMsg[1] = 0
    /\ publishedMsg' = [publishedMsg EXCEPT ![1] = msg[1]]
    /\ UNCHANGED <<status, msg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Reclaim ==
    /\ allowReclaim
    /\ hold[1]
    /\ msg[1] = 0
    /\ msg' = [msg EXCEPT ![1] = 1]
    /\ UNCHANGED <<status, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

EarlyModel ==
    /\ allowEarlyModel
    /\ phase[1] # "ready"
    /\ model[1] = 0
    /\ phase' = [phase EXCEPT ![1] = "calls"]
    /\ model' = [model EXCEPT ![1] = 1]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps, tick>>

Tick ==
    /\ tick' = IF tick = 0 THEN 1 ELSE 0
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, pending, committed, snapshot, saw, cursor, sends, stales, snaps>>

Next ==
    \/ Disconnect
    \/ \E j \in Jobs: Reserve(j) \/ Publish(j) \/ Adopt(j) \/ Cancel(j) \/ StaleWrite(j) \/ Write(j)
    \/ Crash
    \/ Recover
    \/ Calls
    \/ Invoke
    \/ Model
    \/ Arm
    \/ Send
    \/ Allocate
    \/ Commit
    \/ Snapshot
    \/ \E s \in Subs: Deliver(s)
    \/ EarlyHold
    \/ PublishDuringCrash
    \/ Reclaim
    \/ EarlyModel
    \/ Tick

Spec == Init /\ [][Next]_vars /\ WF_vars(Recover)

TypeOK ==
    /\ \A j \in Jobs: status[j] \in {"run", "stop"}
    /\ \A j \in Jobs: msg[j] \in 0..2
    /\ \A j \in Jobs: publishedMsg[j] \in 0..2
    /\ \A j \in Jobs: hold[j] \in BOOLEAN
    /\ \A j \in Jobs: phase[j] \in {"none", "calls", "ready", "armed"}
    /\ \A j \in Jobs: model[j] \in 0..1
    /\ \A j \in Jobs: invoked[j] \in 0..1
    /\ \A j \in Jobs: sent[j] \in 0..1
    /\ \A j \in Jobs: epoch[j] \in 0..2
    /\ \A j \in Jobs: result[j] \in {"", "kept", "stale"}
    /\ \A j \in Jobs: cancel[j] \in BOOLEAN
    /\ crash \in 0..2
    /\ disconnected \in BOOLEAN
    /\ published \in 0..2
    /\ inflight \in 0..1
    /\ pending \in {"old", "new"}
    /\ committed \in {"old", "new"}
    /\ snapshot \in 0..3
    /\ saw \in {"old", "new"}
    /\ \A s \in Subs: cursor[s] \in 0..2
    /\ sends \in 0..1
    /\ stales \in 0..2
    /\ snaps \in 0..2
    /\ tick \in 0..1

DisconnectDoesNotStopJob ==
    \A j \in Jobs: status[j] = "stop" => cancel[j]

ReservationThenAdopt ==
    \A j \in Jobs: hold[j] => epoch[j] > 0 /\ publishedMsg[j] = 0

PublishCrashRecovers ==
    /\ crash = 1 => ~hold[1] /\ publishedMsg[1] = 0 /\ (status[1] = "run" \/ cancel[1])
    /\ crash = 2 /\ ~cancel[1] => status[1] = "run" /\ (msg[1] = 2 \/ hold[1])

LiveHolderNotReclaimed ==
    \A j \in Jobs: hold[j] => msg[j] = 0

CallsSavedBeforeModel ==
    /\ phase[1] = "calls" => model[1] = 0 /\ invoked[1] = 0
    /\ model[1] = 1 => invoked[1] = 1 /\ phase[1] = "ready"

ArmedWriteNotSent ==
    phase[1] = "armed" => sent[1] = 0

StaleEpochDropped ==
    \A j \in Jobs: result[j] # "stale"

CommitOrderedCursor ==
    /\ snapshot <= published
    /\ (snapshot = published => saw = committed)
    /\ \A s \in Subs: cursor[s] <= published

LiveRecovery ==
    (crash = 1 /\ ~cancel[1]) ~> (crash = 2)

====
