---- MODULE Loop ----
EXTENDS Integers

CONSTANT
    breakSave, breakResult, breakWrite, breakForbid, breakStop, breakContinuation,
    breakDuplicate, breakEarlyModel

VARIABLES
    constraint, step, stopped, saved, invoked, web, terminal,
    nextModel, opened, write, confirms, due, crashed, enqueued,
    callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed,
    hlen, h1, h2, h3, h4, h5, h6, h7, h8

vars == <<
    constraint, step, stopped, saved, invoked, web, terminal,
    nextModel, opened, write, confirms, due, crashed, enqueued,
    callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed,
    hlen, h1, h2, h3, h4, h5, h6, h7, h8
>>

Stamp(event, id, args, phase, vol, dueBit) ==
    [event |-> event, id |-> id, args |-> args, phase |-> phase, volatile |-> vol, due |-> dueBit]

EmptyMark == Stamp("none", "", "", "none", 0, FALSE)

MarkOK(mark) ==
    /\ mark.event \in {"none", "Save", "PreinvokeCrash", "Resume", "Invoke", "Deny", "Persist", "Model", "EarlyModel", "PostConfirmCrash"}
    /\ mark.id \in {"", "read"}
    /\ mark.args \in {"", "kb"}
    /\ mark.phase \in {"none", "pending", "running", "result"}
    /\ mark.volatile \in 0..1
    /\ mark.due \in BOOLEAN

Push(stamp) ==
    /\ hlen' = hlen + 1
    /\ h1' = IF hlen = 0 THEN stamp ELSE h1
    /\ h2' = IF hlen = 1 THEN stamp ELSE h2
    /\ h3' = IF hlen = 2 THEN stamp ELSE h3
    /\ h4' = IF hlen = 3 THEN stamp ELSE h4
    /\ h5' = IF hlen = 4 THEN stamp ELSE h5
    /\ h6' = IF hlen = 5 THEN stamp ELSE h6
    /\ h7' = IF hlen = 6 THEN stamp ELSE h7
    /\ h8' = IF hlen = 7 THEN stamp ELSE h8

preinvokeReady ==
    /\ step = 1
    /\ callPhase = "pending"
    /\ ~preCrashed
    /\ invoked = 0

invokeReady ==
    /\ step = 1
    /\ callPhase = "pending"
    /\ (~preCrashed \/ resumed)
    /\ ~breakEarlyModel

invokeOnce == invokeReady /\ ~breakDuplicate

invokeTwice ==
    /\ invokeReady
    /\ breakDuplicate
    /\ resumed
    /\ constraint = "allow"

webEffect ==
    IF constraint = "allow" THEN 1 ELSE IF breakForbid THEN 1 ELSE 0

invokedEffect ==
    IF constraint = "allow" THEN 1 ELSE 0

Init ==
    /\ constraint \in {"allow", "forbid"}
    /\ step = 0
    /\ stopped = FALSE
    /\ saved = 0
    /\ invoked = 0
    /\ web = 0
    /\ terminal = 0
    /\ nextModel = 0
    /\ opened = FALSE
    /\ write = 0
    /\ confirms = 0
    /\ due = FALSE
    /\ crashed = FALSE
    /\ enqueued = FALSE
    /\ callId = ""
    /\ callArgs = ""
    /\ callPhase = "none"
    /\ volatile = 1
    /\ readResult = 0
    /\ preCrashed = FALSE
    /\ resumed = FALSE
    /\ hlen = 0
    /\ h1 = EmptyMark
    /\ h2 = EmptyMark
    /\ h3 = EmptyMark
    /\ h4 = EmptyMark
    /\ h5 = EmptyMark
    /\ h6 = EmptyMark
    /\ h7 = EmptyMark
    /\ h8 = EmptyMark

Save ==
    \/ /\ step = 0 /\ ~breakSave /\ ~breakStop
       /\ step' = 1
       /\ saved' = 2
       /\ callId' = "read"
       /\ callArgs' = "kb"
       /\ callPhase' = "pending"
       /\ Push(Stamp("Save", "read", "kb", "pending", volatile, due))
       /\ UNCHANGED <<constraint, stopped, invoked, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, volatile, readResult, preCrashed, resumed>>
    \/ /\ step = 0 /\ ~breakSave /\ ~breakStop
       /\ step' = 8
       /\ stopped' = TRUE
       /\ UNCHANGED <<constraint, saved, invoked, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step = 0 /\ breakSave /\ ~breakStop
       /\ step' = 8
       /\ invoked' = 1
       /\ UNCHANGED <<constraint, stopped, saved, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step = 0 /\ breakStop
       /\ step' = 8
       /\ stopped' = TRUE
       /\ saved' = 2
       /\ invoked' = 1
       /\ UNCHANGED <<constraint, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step # 0
       /\ UNCHANGED vars

CrashBeforeInvoke ==
    \/ /\ preinvokeReady
       /\ preCrashed' = TRUE
       /\ volatile' = 0
       /\ Push(Stamp("PreinvokeCrash", callId, callArgs, "pending", 0, due))
       /\ UNCHANGED <<constraint, step, stopped, saved, invoked, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, readResult, resumed>>
    \/ /\ ~preinvokeReady
       /\ UNCHANGED vars

Resume ==
    \/ /\ preCrashed /\ ~resumed /\ step = 1 /\ callPhase = "pending" /\ volatile = 0
       /\ resumed' = TRUE
       /\ volatile' = 1
       /\ Push(Stamp("Resume", callId, callArgs, "pending", 1, due))
       /\ UNCHANGED <<constraint, step, stopped, saved, invoked, web, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, readResult, preCrashed>>
    \/ /\ ~(preCrashed /\ ~resumed /\ step = 1 /\ callPhase = "pending" /\ volatile = 0)
       /\ UNCHANGED vars

Invoke ==
    \/ /\ invokeOnce
       /\ invoked' = invokedEffect
       /\ web' = webEffect
       /\ callPhase' = "running"
       /\ Push(Stamp(IF invokedEffect = 1 THEN "Invoke" ELSE "Deny", callId, callArgs, "running", volatile, due))
       /\ UNCHANGED <<constraint, step, stopped, saved, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, volatile, readResult, preCrashed, resumed>>
    \/ /\ invokeTwice
       /\ invoked' = 2
       /\ web' = 1
       /\ callPhase' = "running"
       /\ Push(Stamp("Invoke", callId, callArgs, "running", volatile, due))
       /\ UNCHANGED <<constraint, step, stopped, saved, terminal, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, volatile, readResult, preCrashed, resumed>>
    \/ /\ ~invokeOnce /\ ~invokeTwice
       /\ UNCHANGED vars

Persist ==
    \/ /\ step = 1 /\ callPhase = "running" /\ ~breakResult
       /\ step' = 2
       /\ terminal' = 1
       /\ readResult' = 1
       /\ callPhase' = "result"
       /\ Push(Stamp("Persist", callId, callArgs, "result", volatile, due))
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, nextModel, opened, write, confirms, due, crashed, enqueued, callId, callArgs, volatile, preCrashed, resumed>>
    \/ /\ step = 1 /\ callPhase = "running" /\ breakResult
       /\ step' = 8
       /\ terminal' = 1
       /\ nextModel' = 1
       /\ readResult' = 1
       /\ callPhase' = "result"
       /\ Push(Stamp("Persist", callId, callArgs, "result", volatile, due))
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, opened, write, confirms, due, crashed, enqueued, callId, callArgs, volatile, preCrashed, resumed>>
    \/ /\ ~(step = 1 /\ callPhase = "running")
       /\ UNCHANGED vars

RequestBeforeSavedResult ==
    \/ /\ breakEarlyModel /\ preCrashed /\ resumed /\ step = 1 /\ callPhase = "pending" /\ readResult = 0
       /\ step' = 8
       /\ nextModel' = 1
       /\ terminal' = 2
       /\ Push(Stamp("EarlyModel", callId, callArgs, callPhase, volatile, due))
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed>>
    \/ /\ ~(breakEarlyModel /\ preCrashed /\ resumed /\ step = 1 /\ callPhase = "pending" /\ readResult = 0)
       /\ UNCHANGED vars

Open ==
    \/ /\ step = 2 /\ ~breakWrite
       /\ step' = 3
       /\ opened' = TRUE
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step = 2 /\ breakWrite
       /\ step' = 8
       /\ opened' = TRUE
       /\ write' = 1
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step # 2
       /\ UNCHANGED vars

Confirm ==
    \/ /\ step = 3
       /\ step' = 4
       /\ confirms' = 1
       /\ write' = 1
       /\ terminal' = 2
       /\ due' = TRUE
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, nextModel, opened, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step # 3
       /\ UNCHANGED vars

ConfirmAgain ==
    \/ /\ step = 4
       /\ step' = 5
       /\ confirms' = 2
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, opened, write, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed, hlen, h1, h2, h3, h4, h5, h6, h7, h8>>
    \/ /\ step # 4
       /\ UNCHANGED vars

RequestAfterTools ==
    \/ /\ step = 5 /\ terminal = 2
       /\ step' = 6
       /\ nextModel' = 1
       /\ Push(Stamp("Model", callId, callArgs, callPhase, volatile, due))
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, opened, write, confirms, due, crashed, enqueued, callId, callArgs, callPhase, volatile, readResult, preCrashed, resumed>>
    \/ /\ ~(step = 5 /\ terminal = 2)
       /\ UNCHANGED vars

Crash ==
    \/ /\ step = 6 /\ ~breakContinuation
       /\ step' = 7
       /\ crashed' = TRUE
       /\ due' = TRUE
       /\ volatile' = 0
       /\ Push(Stamp("PostConfirmCrash", callId, callArgs, callPhase, 0, TRUE))
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, opened, write, confirms, enqueued, callId, callArgs, callPhase, readResult, preCrashed, resumed>>
    \/ /\ step = 6 /\ breakContinuation
       /\ step' = 7
       /\ crashed' = TRUE
       /\ due' = FALSE
       /\ volatile' = 0
       /\ Push(Stamp("PostConfirmCrash", callId, callArgs, callPhase, 0, FALSE))
       /\ UNCHANGED <<constraint, stopped, saved, invoked, web, terminal, nextModel, opened, write, confirms, enqueued, callId, callArgs, callPhase, readResult, preCrashed, resumed>>
    \/ /\ step # 6
       /\ UNCHANGED vars

Next ==
    \/ Save
    \/ CrashBeforeInvoke
    \/ Resume
    \/ Invoke
    \/ Persist
    \/ RequestBeforeSavedResult
    \/ Open
    \/ Confirm
    \/ ConfirmAgain
    \/ RequestAfterTools
    \/ Crash

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ constraint \in {"allow", "forbid"}
    /\ step \in 0..8
    /\ stopped \in BOOLEAN
    /\ saved \in 0..2
    /\ invoked \in 0..2
    /\ web \in 0..1
    /\ terminal \in 0..2
    /\ nextModel \in 0..1
    /\ opened \in BOOLEAN
    /\ write \in 0..1
    /\ confirms \in 0..2
    /\ due \in BOOLEAN
    /\ crashed \in BOOLEAN
    /\ enqueued = FALSE
    /\ callId \in {"", "read"}
    /\ callArgs \in {"", "kb"}
    /\ callPhase \in {"none", "pending", "running", "result"}
    /\ volatile \in 0..1
    /\ readResult \in 0..1
    /\ preCrashed \in BOOLEAN
    /\ resumed \in BOOLEAN
    /\ hlen \in 0..8
    /\ MarkOK(h1) /\ MarkOK(h2) /\ MarkOK(h3) /\ MarkOK(h4)
    /\ MarkOK(h5) /\ MarkOK(h6) /\ MarkOK(h7) /\ MarkOK(h8)

SaveBeforeExecute == invoked = 0 \/ saved = 2

ResultBeforeNextModel == nextModel = 0 \/ terminal = 2

WriteWaitsForClaim ==
    /\ write <= confirms
    /\ (opened /\ confirms = 0) => write = 0

ForbidWebBlocksSearch == constraint = "forbid" => web = 0

StoppedJobDoesNotExecute == stopped => invoked = 0 /\ nextModel = 0

ContinuationDueSurvivesCrash == crashed => due /\ ~enqueued

OneProviderInvoke == invoked <= 1

SavedResultBeforeModel == nextModel = 0 \/ readResult = 1

OrderedPair(earlier, later) ==
    /\ earlier.event = "Model"
    /\ later.event = "PostConfirmCrash"
    /\ later.due = TRUE
    /\ later.volatile = 0
    /\ earlier.id = later.id
    /\ earlier.args = later.args

WitnessOrderedResume ==
    ~(
        /\ h1.event = "Save"
        /\ h2.event = "PreinvokeCrash"
        /\ h3.event = "Resume"
        /\ h4.event = "Invoke"
        /\ h5.event = "Persist"
        /\ h6.event = "Model"
        /\ h1.id = "read"
        /\ h1.args = "kb"
        /\ h1.phase = "pending"
        /\ h2.id = h1.id
        /\ h2.args = h1.args
        /\ h2.phase = "pending"
        /\ h2.volatile = 0
        /\ h3.id = h2.id
        /\ h3.args = h2.args
        /\ h3.phase = "pending"
        /\ h3.volatile = 1
        /\ h4.id = h1.id
        /\ h4.args = h1.args
        /\ h4.phase = "running"
        /\ h5.id = h1.id
        /\ h5.phase = "result"
        /\ h6.id = h1.id
    )

WitnessPostConfirmCrash == ~(OrderedPair(h4, h5) \/ OrderedPair(h6, h7))

====
