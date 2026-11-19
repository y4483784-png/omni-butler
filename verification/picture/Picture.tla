---- MODULE Picture ----
EXTENDS Integers

CONSTANT breakOcr, breakZero, breakEarlyBlob, breakRevert, breakLeak

VARIABLES picture, ocr, usage, stored, job, blob, imageRow, cut, stranger, closed
vars == <<picture, ocr, usage, stored, job, blob, imageRow, cut, stranger, closed>>

Init ==
    /\ picture = "none"
    /\ ocr = FALSE
    /\ usage = "pending"
    /\ stored = "none"
    /\ job = "none"
    /\ blob = "out"
    /\ imageRow = "none"
    /\ cut = "none"
    /\ stranger = "hidden"
    /\ closed = FALSE

TypeOK ==
    /\ picture \in {"none", "accepted", "rejected", "sent"}
    /\ ocr \in BOOLEAN
    /\ usage \in {"pending", "missing", "counted", "failed"}
    /\ stored \in {"none", "null", "numbers", "zero"}
    /\ job \in {"none", "running", "terminal"}
    /\ blob \in {"out", "held", "gone"}
    /\ imageRow \in {"none", "ready", "pending", "deleted"}
    /\ cut \in {"none", "afterPending", "early"}
    /\ stranger \in {"hidden", "seen"}
    /\ closed \in BOOLEAN

Accept ==
    /\ picture = "none"
    /\ ~closed
    /\ picture' = "accepted"
    /\ job' = "running"
    /\ blob' = "held"
    /\ imageRow' = "ready"
    /\ UNCHANGED <<ocr, usage, stored, cut, stranger, closed>>

Reject ==
    /\ picture = "none"
    /\ ~closed
    /\ picture' = "rejected"
    /\ closed' = TRUE
    /\ UNCHANGED <<ocr, usage, stored, job, blob, imageRow, cut, stranger>>

Send ==
    /\ picture = "accepted"
    /\ ~closed
    /\ picture' = "sent"
    /\ ocr' = IF breakOcr THEN TRUE ELSE FALSE
    /\ UNCHANGED <<usage, stored, job, blob, imageRow, cut, stranger, closed>>

NoteMissing ==
    /\ picture = "sent"
    /\ usage = "pending"
    /\ ~closed
    /\ usage' = "missing"
    /\ stored' = IF breakZero THEN "zero" ELSE "null"
    /\ UNCHANGED <<picture, ocr, job, blob, imageRow, cut, stranger, closed>>

NoteNumbers ==
    /\ picture = "sent"
    /\ usage = "pending"
    /\ ~closed
    /\ usage' = "counted"
    /\ stored' = "numbers"
    /\ UNCHANGED <<picture, ocr, job, blob, imageRow, cut, stranger, closed>>

NoteFailed ==
    /\ picture = "sent"
    /\ usage = "pending"
    /\ ~closed
    /\ usage' = "failed"
    /\ stored' = "none"
    /\ UNCHANGED <<picture, ocr, job, blob, imageRow, cut, stranger, closed>>

Finish ==
    /\ ~breakEarlyBlob
    /\ picture = "sent"
    /\ job = "running"
    /\ imageRow = "ready"
    /\ usage /= "pending"
    /\ ~closed
    /\ job' = "terminal"
    /\ imageRow' = "pending"
    /\ closed' = TRUE
    /\ UNCHANGED <<picture, ocr, usage, stored, blob, cut, stranger>>

FinishEarly ==
    /\ breakEarlyBlob
    /\ picture = "sent"
    /\ job = "running"
    /\ imageRow = "ready"
    /\ usage /= "pending"
    /\ ~closed
    /\ job' = "terminal"
    /\ blob' = "gone"
    /\ cut' = "early"
    /\ closed' = TRUE
    /\ UNCHANGED <<picture, ocr, usage, stored, imageRow, stranger>>

Purge ==
    /\ ~breakRevert
    /\ closed
    /\ imageRow = "pending"
    /\ blob = "held"
    /\ blob' = "gone"
    /\ imageRow' = "deleted"
    /\ cut' = "afterPending"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, stranger, closed>>

CrashAfterBlob ==
    /\ ~breakRevert
    /\ ~breakEarlyBlob
    /\ closed
    /\ imageRow = "pending"
    /\ blob = "held"
    /\ blob' = "gone"
    /\ cut' = "afterPending"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, imageRow, stranger, closed>>

AlreadyGone ==
    /\ closed
    /\ imageRow = "pending"
    /\ blob = "gone"
    /\ imageRow' = "deleted"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, blob, cut, stranger, closed>>

Revert ==
    /\ breakRevert
    /\ closed
    /\ job = "terminal"
    /\ imageRow = "pending"
    /\ job' = "running"
    /\ imageRow' = "ready"
    /\ UNCHANGED <<picture, ocr, usage, stored, blob, cut, stranger, closed>>

ReadStranger ==
    /\ breakLeak
    /\ job = "running"
    /\ imageRow = "ready"
    /\ blob = "held"
    /\ stranger = "hidden"
    /\ stranger' = "seen"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, blob, imageRow, cut, closed>>

Done ==
    /\ closed
    /\ UNCHANGED vars

ImageStaysOnChatModel == ocr = FALSE

MissingUsageStaysNull ==
    /\ stored /= "zero"
    /\ (usage = "missing" => stored = "null")
    /\ (usage = "failed" => stored = "none")
    /\ (usage = "counted" => stored = "numbers")

PendingDeleteBeforeBlob == cut /= "early"

TerminalStays == ~(closed /\ job = "running")

StrangerHidden == stranger = "hidden"

Next ==
    \/ Accept
    \/ Reject
    \/ Send
    \/ NoteMissing
    \/ NoteNumbers
    \/ NoteFailed
    \/ Finish
    \/ FinishEarly
    \/ Purge
    \/ CrashAfterBlob
    \/ AlreadyGone
    \/ Revert
    \/ ReadStranger
    \/ Done

Spec == Init /\ [][Next]_vars
====
