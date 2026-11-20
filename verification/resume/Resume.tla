---- MODULE Resume ----
EXTENDS Integers

Jobs == 1..2
Subs == 1..2

VARIABLES
    status, msg, publishedMsg, hold, phase, model, invoked, sent,
    epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick

vars == <<
    status, msg, publishedMsg, hold, phase, model, invoked, sent,
    epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick
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
    /\ seen = [j \in Jobs |-> FALSE]
    /\ cancel = [j \in Jobs |-> FALSE]
    /\ crash = 0
    /\ disconnected = FALSE
    /\ published = 0
    /\ inflight = 0
    /\ cursor = [s \in Subs |-> 0]
    /\ tick = 0

Disconnect ==
    /\ ~disconnected
    /\ disconnected' = TRUE
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, published, inflight, cursor, tick>>

Reserve(j) ==
    /\ status[j] = "run"
    /\ ~hold[j]
    /\ msg[j] = 0
    /\ ~(crash = 1 /\ j = 1)
    /\ msg' = [msg EXCEPT ![j] = 1]
    /\ UNCHANGED <<status, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Publish(j) ==
    /\ msg[j] # 0
    /\ publishedMsg[j] # msg[j]
    /\ ~(crash = 1 /\ j = 1)
    /\ publishedMsg' = [publishedMsg EXCEPT ![j] = msg[j]]
    /\ UNCHANGED <<status, msg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Crash ==
    /\ crash = 0
    /\ msg[1] = 1
    /\ publishedMsg[1] = 0
    /\ ~hold[1]
    /\ crash' = 1
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, disconnected, published, inflight, cursor, tick>>

Recover ==
    /\ crash = 1
    /\ status[1] = "run"
    /\ crash' = 2
    /\ msg' = [msg EXCEPT ![1] = 2]
    /\ hold' = [hold EXCEPT ![1] = FALSE]
    /\ UNCHANGED <<status, publishedMsg, phase, model, invoked, sent, epoch, result, seen, cancel, disconnected, published, inflight, cursor, tick>>

Adopt(j) ==
    /\ status[j] = "run"
    /\ ~hold[j]
    /\ msg[j] # 0
    /\ publishedMsg[j] = msg[j]
    /\ hold' = [hold EXCEPT ![j] = TRUE]
    /\ epoch' = [epoch EXCEPT ![j] = epoch[j] + 1]
    /\ msg' = [msg EXCEPT ![j] = 0]
    /\ publishedMsg' = [publishedMsg EXCEPT ![j] = 0]
    /\ UNCHANGED <<status, phase, model, invoked, sent, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Cancel(j) ==
    /\ status[j] = "run"
    /\ ~cancel[j]
    /\ cancel' = [cancel EXCEPT ![j] = TRUE]
    /\ status' = [status EXCEPT ![j] = "stop"]
    /\ hold' = [hold EXCEPT ![j] = FALSE]
    /\ UNCHANGED <<msg, publishedMsg, phase, model, invoked, sent, epoch, result, seen, crash, disconnected, published, inflight, cursor, tick>>

Calls ==
    /\ hold[1]
    /\ phase[1] = "none"
    /\ phase' = [phase EXCEPT ![1] = "calls"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Invoke ==
    /\ phase[1] = "calls"
    /\ invoked[1] = 0
    /\ invoked' = [invoked EXCEPT ![1] = 1]
    /\ phase' = [phase EXCEPT ![1] = "ready"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, model, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Model ==
    /\ phase[1] = "ready"
    /\ invoked[1] = 1
    /\ model[1] = 0
    /\ model' = [model EXCEPT ![1] = 1]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Arm ==
    /\ hold[1]
    /\ phase[1] \in {"none", "calls"}
    /\ phase' = [phase EXCEPT ![1] = "armed"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Stale(j) ==
    /\ epoch[j] > 0
    /\ ~seen[j]
    /\ seen' = [seen EXCEPT ![j] = TRUE]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, cancel, crash, disconnected, published, inflight, cursor, tick>>

Write(j) ==
    /\ hold[j]
    /\ epoch[j] > 0
    /\ result[j] = ""
    /\ result' = [result EXCEPT ![j] = "kept"]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, seen, cancel, crash, disconnected, published, inflight, cursor, tick>>

Allocate ==
    /\ inflight = 0
    /\ published < 2
    /\ inflight' = 1
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, cursor, tick>>

Commit ==
    /\ inflight = 1
    /\ published' = published + 1
    /\ inflight' = 0
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, cursor, tick>>

Deliver(s) ==
    /\ cursor[s] < published
    /\ cursor' = [cursor EXCEPT ![s] = cursor[s] + 1]
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, tick>>

Spin ==
    /\ tick' = IF tick = 0 THEN 1 ELSE 0
    /\ UNCHANGED <<status, msg, publishedMsg, hold, phase, model, invoked, sent, epoch, result, seen, cancel, crash, disconnected, published, inflight, cursor>>

Next ==
    \/ Disconnect
    \/ \E j \in Jobs: Reserve(j) \/ Publish(j) \/ Adopt(j) \/ Cancel(j) \/ Stale(j) \/ Write(j)
    \/ Crash
    \/ Recover
    \/ Calls
    \/ Invoke
    \/ Model
    \/ Arm
    \/ Allocate
    \/ Commit
    \/ \E s \in Subs: Deliver(s)
    \/ Spin

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ \A j \in Jobs: status[j] \in {"run", "stop", "done"}
    /\ \A j \in Jobs: msg[j] \in 0..2
    /\ \A j \in Jobs: publishedMsg[j] \in 0..2
    /\ \A j \in Jobs: hold[j] \in BOOLEAN
    /\ \A j \in Jobs: phase[j] \in {"none", "calls", "ready", "armed"}
    /\ \A j \in Jobs: model[j] \in 0..1
    /\ \A j \in Jobs: invoked[j] \in 0..1
    /\ \A j \in Jobs: sent[j] \in 0..1
    /\ \A j \in Jobs: epoch[j] \in 0..2
    /\ \A j \in Jobs: result[j] \in {"", "kept"}
    /\ \A j \in Jobs: seen[j] \in BOOLEAN
    /\ \A j \in Jobs: cancel[j] \in BOOLEAN
    /\ crash \in 0..2
    /\ disconnected \in BOOLEAN
    /\ published \in 0..2
    /\ inflight \in 0..1
    /\ \A s \in Subs: cursor[s] \in 0..2
    /\ tick \in 0..1

DisconnectDoesNotStopJob ==
    \A j \in Jobs: status[j] = "stop" => cancel[j]

ReservationThenAdopt ==
    \A j \in Jobs: hold[j] => epoch[j] > 0 /\ msg[j] = 0 /\ publishedMsg[j] = 0

PublishCrashRecovers ==
    /\ crash = 1 => ~hold[1] /\ publishedMsg[1] = 0 /\ (status[1] = "run" \/ cancel[1])
    /\ crash = 2 /\ ~cancel[1] => status[1] = "run"

LiveHolderNotReclaimed ==
    \A j \in Jobs: hold[j] => msg[j] = 0

CallsSavedBeforeModel ==
    /\ phase[1] = "calls" => model[1] = 0 /\ invoked[1] = 0
    /\ model[1] = 1 => invoked[1] = 1 /\ phase[1] = "ready"

ArmedWriteNotSent ==
    phase[1] = "armed" => sent[1] = 0

StaleEpochDropped ==
    \A j \in Jobs: seen[j] => result[j] \in {"", "kept"}

CommitOrderedCursor ==
    /\ published \in 0..2
    /\ inflight \in 0..1
    /\ \A s \in Subs: cursor[s] <= published

====
