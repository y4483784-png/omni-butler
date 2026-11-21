---- MODULE Picture ----
EXTENDS Integers

CONSTANT breakOcr, breakZero, breakEarlyBlob, breakRevert, breakLeak, breakFence

VARIABLES picture, ocr, usage, stored, job, blob, imageRow, cut, stranger, closed, swept
vars == <<picture, ocr, usage, stored, job, blob, imageRow, cut, stranger, closed, swept>>

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
    /\ swept = FALSE

TypeOK ==
    /\ picture \in {"none", "accepted", "rejected", "sent"}
    /\ ocr \in BOOLEAN
    /\ usage \in {"pending", "missing", "counted", "failed"}
    /\ stored \in {"none", "null", "numbers", "zero"}
    /\ job \in {"none", "running", "terminal"}
    /\ blob \in {"out", "held", "gone"}
    /\ imageRow \in {"none", "staged", "ready", "pending", "tombstone", "deleted"}
    /\ cut \in {"none", "afterPending", "early"}
    /\ stranger \in {"hidden", "seen"}
    /\ closed \in BOOLEAN
    /\ swept \in BOOLEAN

Accept ==
    /\ picture = "none"
    /\ ~closed
    /\ picture' = "accepted"
    /\ job' = "running"
    /\ blob' = "out"
    /\ imageRow' = "staged"
    /\ UNCHANGED <<ocr, usage, stored, cut, stranger, closed, swept>>

Reject ==
    /\ picture = "none"
    /\ ~closed
    /\ picture' = "rejected"
    /\ closed' = TRUE
    /\ UNCHANGED <<ocr, usage, stored, job, blob, imageRow, cut, stranger, swept>>

PutBlob ==
    /\ imageRow = "staged"
    /\ blob = "out"
    /\ job = "running"
    /\ ~closed
    /\ blob' = "held"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, imageRow, cut, stranger, closed, swept>>

Adopt ==
    /\ imageRow = "staged"
    /\ blob = "held"
    /\ job = "running"
    /\ ~closed
    /\ imageRow' = "ready"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, blob, cut, stranger, closed, swept>>

FenceMissing ==
    /\ imageRow = "staged"
    /\ blob = "out"
    /\ job = "running"
    /\ ~closed
    /\ ~swept
    /\ swept' = TRUE
    /\ imageRow' = IF breakFence THEN "tombstone" ELSE "staged"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, blob, cut, stranger, closed>>

TombstoneStaged ==
    /\ imageRow = "staged"
    /\ job = "running"
    /\ ~closed
    /\ job' = "terminal"
    /\ imageRow' = "tombstone"
    /\ closed' = TRUE
    /\ UNCHANGED <<picture, ocr, usage, stored, blob, cut, stranger, swept>>

DropTombstoneBlob ==
    /\ imageRow = "tombstone"
    /\ blob = "held"
    /\ blob' = "gone"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, imageRow, cut, stranger, closed, swept>>

DeleteLatePut ==
    /\ imageRow = "tombstone"
    /\ blob = "out"
    /\ blob' = "gone"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, imageRow, cut, stranger, closed, swept>>

Send ==
    /\ picture = "accepted"
    /\ imageRow = "ready"
    /\ ~closed
    /\ picture' = "sent"
    /\ ocr' = IF breakOcr THEN TRUE ELSE FALSE
    /\ UNCHANGED <<usage, stored, job, blob, imageRow, cut, stranger, closed, swept>>

NoteMissing ==
    /\ picture = "sent"
    /\ usage = "pending"
    /\ ~closed
    /\ usage' = "missing"
    /\ stored' = IF breakZero THEN "zero" ELSE "null"
    /\ UNCHANGED <<picture, ocr, job, blob, imageRow, cut, stranger, closed, swept>>

NoteNumbers ==
    /\ picture = "sent"
    /\ usage = "pending"
    /\ ~closed
    /\ usage' = "counted"
    /\ stored' = "numbers"
    /\ UNCHANGED <<picture, ocr, job, blob, imageRow, cut, stranger, closed, swept>>

NoteFailed ==
    /\ picture = "sent"
    /\ usage = "pending"
    /\ ~closed
    /\ usage' = "failed"
    /\ stored' = "none"
    /\ UNCHANGED <<picture, ocr, job, blob, imageRow, cut, stranger, closed, swept>>

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
    /\ UNCHANGED <<picture, ocr, usage, stored, blob, cut, stranger, swept>>

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
    /\ UNCHANGED <<picture, ocr, usage, stored, imageRow, stranger, swept>>

Purge ==
    /\ ~breakRevert
    /\ closed
    /\ imageRow = "pending"
    /\ blob = "held"
    /\ blob' = "gone"
    /\ imageRow' = "deleted"
    /\ cut' = "afterPending"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, stranger, closed, swept>>

CrashAfterBlob ==
    /\ ~breakRevert
    /\ ~breakEarlyBlob
    /\ closed
    /\ imageRow = "pending"
    /\ blob = "held"
    /\ blob' = "gone"
    /\ cut' = "afterPending"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, imageRow, stranger, closed, swept>>

AlreadyGone ==
    /\ closed
    /\ imageRow = "pending"
    /\ blob = "gone"
    /\ imageRow' = "deleted"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, blob, cut, stranger, closed, swept>>

Revert ==
    /\ breakRevert
    /\ closed
    /\ job = "terminal"
    /\ imageRow = "pending"
    /\ job' = "running"
    /\ imageRow' = "ready"
    /\ UNCHANGED <<picture, ocr, usage, stored, blob, cut, stranger, closed, swept>>

ReadStranger ==
    /\ breakLeak
    /\ job = "running"
    /\ imageRow = "ready"
    /\ blob = "held"
    /\ stranger = "hidden"
    /\ stranger' = "seen"
    /\ UNCHANGED <<picture, ocr, usage, stored, job, blob, imageRow, cut, closed, swept>>

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

StagedFence ==
    (swept /\ job = "running" /\ blob = "out") => imageRow = "staged"

Next ==
    \/ Accept
    \/ Reject
    \/ PutBlob
    \/ Adopt
    \/ FenceMissing
    \/ TombstoneStaged
    \/ DropTombstoneBlob
    \/ DeleteLatePut
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
