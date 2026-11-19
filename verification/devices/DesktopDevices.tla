---- MODULE DesktopDevices ----
EXTENDS Integers

CONSTANT breakHash, breakRevoke, breakSpoof, breakConfirm

VARIABLES stored, revoked, listed, spoofed, spoofProof, spoofOpened, opened, claim, paired

vars == <<stored, revoked, listed, spoofed, spoofProof, spoofOpened, opened, claim, paired>>

Devices == {"d1", "d2"}
Stores == {"absent", "hash", "token"}

Init ==
  /\ stored = [d \in Devices |-> "absent"]
  /\ revoked = [d \in Devices |-> FALSE]
  /\ listed = {}
  /\ spoofed = FALSE
  /\ spoofProof = FALSE
  /\ spoofOpened = FALSE
  /\ opened = FALSE
  /\ claim = "none"
  /\ paired = FALSE

Enroll(d) ==
  /\ stored[d] = "absent"
  /\ stored' = [stored EXCEPT ![d] = IF breakHash THEN "token" ELSE "hash"]
  /\ listed' = IF breakHash THEN listed \union {"hash"} ELSE listed \union {"id", "label", "created"}
  /\ UNCHANGED <<revoked, spoofed, spoofProof, spoofOpened, opened, claim, paired>>

Revoke(d) ==
  /\ stored[d] # "absent"
  /\ ~revoked[d]
  /\ revoked' = IF breakRevoke THEN [x \in Devices |-> TRUE] ELSE [revoked EXCEPT ![d] = TRUE]
  /\ paired' = IF breakRevoke THEN TRUE ELSE paired
  /\ UNCHANGED <<stored, listed, spoofed, spoofProof, spoofOpened, opened, claim>>

ProofOk(d) == stored[d] = "hash" /\ ~revoked[d]

Spoof ==
  /\ ~spoofed
  /\ spoofed' = TRUE
  /\ spoofProof' = breakSpoof
  /\ spoofOpened' = breakSpoof
  /\ UNCHANGED <<stored, revoked, listed, opened, claim, paired>>

Arm ==
  /\ claim = "none"
  /\ claim' = "present"
  /\ UNCHANGED <<stored, revoked, listed, spoofed, spoofProof, spoofOpened, opened, paired>>

Commit(d) ==
  /\ ProofOk(d)
  /\ opened' = IF claim = "present" \/ breakConfirm THEN TRUE ELSE opened
  /\ UNCHANGED <<stored, revoked, listed, spoofed, spoofProof, spoofOpened, claim, paired>>

Done ==
  UNCHANGED vars

Next ==
  \/ \E d \in Devices: Enroll(d) \/ Revoke(d) \/ Commit(d)
  \/ Spoof
  \/ Arm
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ \A d \in Devices: stored[d] \in Stores /\ revoked[d] \in BOOLEAN
  /\ listed \subseteq {"id", "label", "created", "hash", "token"}
  /\ spoofed \in BOOLEAN /\ spoofProof \in BOOLEAN /\ spoofOpened \in BOOLEAN
  /\ opened \in BOOLEAN /\ paired \in BOOLEAN
  /\ claim \in {"none", "present"}

HashOnly ==
  /\ \A d \in Devices: stored[d] # "token"
  /\ "hash" \notin listed
  /\ "token" \notin listed

RevokeOne == ~paired

SpoofDenied == spoofed => ~spoofOpened /\ ~spoofProof

TokenIsNotAConfirm ==
  (claim = "none" /\ \E d \in Devices: ProofOk(d)) => ~opened

====
