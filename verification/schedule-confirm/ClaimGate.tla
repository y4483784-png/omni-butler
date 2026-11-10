------------------------------ MODULE ClaimGate ------------------------------

EXTENDS Integers

CONSTANT BREAK

VARIABLES claim, match, g1, g2, link1, link2, writes, rows, sawLive, foreign

vars == <<claim, match, g1, g2, link1, link2, writes, rows, sawLive, foreign>>

Gen == {"idle", "live", "stopped", "completed"}

TypeOK ==
  /\ claim \in {"none", "open", "written", "cancelled"}
  /\ match \in BOOLEAN
  /\ g1 \in Gen
  /\ g2 \in Gen
  /\ link1 \in BOOLEAN
  /\ link2 \in BOOLEAN
  /\ writes \in 0..2
  /\ rows \in 0..2
  /\ sawLive \in BOOLEAN
  /\ foreign \in BOOLEAN
  /\ BREAK \in {"none", "token", "match", "retire", "once", "dedup", "owner"}

live(link, gen) == link /\ gen = "live"

liveNow == live(link1, g1) \/ live(link2, g2)

AtMostOneSuccessfulWrite == writes <= 1
NoWriteWithoutToken == writes = 0 \/ claim = "written"
WriteMatchedFingerprint == writes = 0 \/ match
WriteSawLiveGeneration == writes = 0 \/ sawLive
OpenClaimHasLiveGeneration == claim # "open" \/ liveNow
AtMostOneOpenRow == rows <= 1
OwnerWrites == ~foreign

Init ==
  /\ claim = "none"
  /\ match = TRUE
  /\ g1 = "idle"
  /\ g2 = "idle"
  /\ link1 = FALSE
  /\ link2 = FALSE
  /\ writes = 0
  /\ rows = 0
  /\ sawLive = FALSE
  /\ foreign = FALSE

retire(next1, next2) ==
  LET still == live(link1, next1) \/ live(link2, next2)
  IN IF claim = "open" /\ ~still /\ BREAK # "retire"
     THEN "cancelled"
     ELSE claim

Start1 ==
  /\ g1 = "idle"
  /\ g1' = "live"
  /\ UNCHANGED <<claim, match, g2, link1, link2, writes, rows, sawLive, foreign>>

Start2 ==
  /\ g2 = "idle"
  /\ g2' = "live"
  /\ UNCHANGED <<claim, match, g1, link1, link2, writes, rows, sawLive, foreign>>

Stop1 ==
  /\ g1 = "live"
  /\ g1' = "stopped"
  /\ claim' = retire("stopped", g2)
  /\ rows' = IF claim' = "open" THEN rows ELSE 0
  /\ UNCHANGED <<match, g2, link1, link2, writes, sawLive, foreign>>

Stop2 ==
  /\ g2 = "live"
  /\ g2' = "stopped"
  /\ claim' = retire(g1, "stopped")
  /\ rows' = IF claim' = "open" THEN rows ELSE 0
  /\ UNCHANGED <<match, g1, link1, link2, writes, sawLive, foreign>>

Complete1 ==
  /\ g1 = "live"
  /\ g1' = "completed"
  /\ claim' = retire("completed", g2)
  /\ rows' = IF claim' = "open" THEN rows ELSE 0
  /\ UNCHANGED <<match, g2, link1, link2, writes, sawLive, foreign>>

Complete2 ==
  /\ g2 = "live"
  /\ g2' = "completed"
  /\ claim' = retire(g1, "completed")
  /\ rows' = IF claim' = "open" THEN rows ELSE 0
  /\ UNCHANGED <<match, g1, link1, link2, writes, sawLive, foreign>>

Issue ==
  /\ g1 = "live" \/ g2 = "live"
  /\ \/ /\ claim \in {"none", "cancelled"}
        /\ rows = 0
        /\ claim' = "open"
        /\ match' = TRUE
        /\ link1' = (g1 = "live")
        /\ link2' = (g2 = "live")
        /\ rows' = 1
        /\ UNCHANGED <<g1, g2, writes, sawLive, foreign>>
     \/ /\ claim = "open"
        /\ match
        /\ link1' = (link1 \/ (g1 = "live"))
        /\ link2' = (link2 \/ (g2 = "live"))
        /\ rows' = IF BREAK = "dedup" THEN 2 ELSE rows
        /\ UNCHANGED <<claim, match, g1, g2, writes, sawLive, foreign>>

Edit ==
  /\ claim = "open"
  /\ match
  /\ match' = FALSE
  /\ UNCHANGED <<claim, g1, g2, link1, link2, writes, rows, sawLive, foreign>>

Restore ==
  /\ claim = "open"
  /\ ~match
  /\ match' = TRUE
  /\ UNCHANGED <<claim, g1, g2, link1, link2, writes, rows, sawLive, foreign>>

Consume ==
  /\ writes < 2
  /\ \/ /\ claim = "open"
        /\ match
        /\ liveNow
        /\ writes = 0
     \/ /\ BREAK = "token"
        /\ claim = "none"
        /\ writes = 0
     \/ /\ BREAK = "match"
        /\ claim = "open"
        /\ ~match
        /\ liveNow
        /\ writes = 0
     \/ /\ BREAK = "once"
        /\ claim = "written"
        /\ writes = 1
     \/ /\ BREAK = "retire"
        /\ claim = "open"
        /\ ~liveNow
        /\ writes = 0
  /\ writes' = writes + 1
  /\ sawLive' = liveNow
  /\ claim' = IF claim = "none" THEN "none" ELSE "written"
  /\ rows' = IF claim = "open" THEN 0 ELSE rows
  /\ UNCHANGED <<match, g1, g2, link1, link2, foreign>>

Foreign ==
  /\ BREAK = "owner"
  /\ claim = "open"
  /\ match
  /\ liveNow
  /\ writes = 0
  /\ writes' = 1
  /\ claim' = "written"
  /\ rows' = 0
  /\ foreign' = TRUE
  /\ sawLive' = TRUE
  /\ UNCHANGED <<match, g1, g2, link1, link2>>

Idle == UNCHANGED vars

Next ==
  \/ Start1
  \/ Start2
  \/ Stop1
  \/ Stop2
  \/ Complete1
  \/ Complete2
  \/ Issue
  \/ Edit
  \/ Restore
  \/ Consume
  \/ Foreign
  \/ Idle

Spec == Init /\ [][Next]_vars

================================================================================
