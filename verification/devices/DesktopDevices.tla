---- MODULE DesktopDevices ----
EXTENDS Integers

CONSTANT breakHash, breakRevoke, breakSpoof, breakConfirm

VARIABLES stored, revoked, listed, spoofed, spoofProof, opened, claim

vars == <<stored, revoked, listed, spoofed, spoofProof, opened, claim>>

Devices == {"d1", "d2"}
Stores == {"absent", "hash", "token"}

Init ==
  /\ stored = [d \in Devices |-> "absent"]
  /\ revoked = [d \in Devices |-> FALSE]
  /\ listed = {}
  /\ spoofed = FALSE
  /\ spoofProof = FALSE
  /\ opened = FALSE
  /\ claim = "none"

Enroll(d) ==
  /\ stored[d] = "absent"
  /\ stored' = [stored EXCEPT ![d] = IF breakHash THEN "token" ELSE "hash"]
  /\ listed' = IF breakHash THEN listed \union {"hash"} ELSE listed \union {"id", "label", "created"}
  /\ UNCHANGED <<revoked, spoofed, spoofProof, opened, claim>>

Revoke(d) ==
  /\ stored[d] # "absent"
  /\ ~revoked[d]
  /\ ~\E x \in Devices: revoked[x]
  /\ revoked' = IF breakRevoke THEN [x \in Devices |-> TRUE] ELSE [revoked EXCEPT ![d] = TRUE]
  /\ UNCHANGED <<stored, listed, spoofed, spoofProof, opened, claim>>

ProofOk(d) == stored[d] = "hash" /\ ~revoked[d]

Spoof ==
  /\ ~spoofed
  /\ ~opened
  /\ spoofed' = TRUE
  /\ spoofProof' = breakSpoof
  /\ opened' = IF breakSpoof THEN TRUE ELSE opened
  /\ UNCHANGED <<stored, revoked, listed, claim>>

Arm ==
  /\ claim = "none"
  /\ claim' = "present"
  /\ UNCHANGED <<stored, revoked, listed, spoofed, spoofProof, opened>>

Done ==
  UNCHANGED vars

Commit(d) ==
  /\ ProofOk(d)
  /\ ~spoofed
  /\ opened' = IF claim = "present" \/ breakConfirm THEN TRUE ELSE opened
  /\ UNCHANGED <<stored, revoked, listed, spoofed, spoofProof, claim>>

Next ==
  \/ \E d \in Devices: Enroll(d) \/ Revoke(d) \/ Commit(d)
  \/ Spoof
  \/ Arm
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ \A d \in Devices: stored[d] \in Stores /\ revoked[d] \in BOOLEAN
  /\ listed \subseteq {"id", "label", "created", "hash", "token"}
  /\ spoofed \in BOOLEAN /\ spoofProof \in BOOLEAN /\ opened \in BOOLEAN
  /\ claim \in {"none", "present"}

HashOnly ==
  /\ \A d \in Devices: stored[d] # "token"
  /\ "hash" \notin listed
  /\ "token" \notin listed

RevokeOne ==
  (revoked["d1"] /\ stored["d2"] = "hash") => ~revoked["d2"]

SpoofDenied == spoofed => ~opened /\ ~spoofProof

TokenIsNotAConfirm ==
  (claim = "none" /\ \E d \in Devices: ProofOk(d)) => ~opened

====
