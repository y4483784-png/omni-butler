---- MODULE WebBlock ----
EXTENDS Integers

CONSTANT breakSpoof, breakLocal, breakShell

VARIABLES claim, kind, surface, phase, ran
vars == <<claim, kind, surface, phase, ran>>

Claims == {"none", "desktop", "token"}
Kinds == {"local", "builtin", "unknown"}

Init ==
    /\ claim \in Claims
    /\ kind \in Kinds
    /\ surface = "unset"
    /\ phase = "start"
    /\ ran = 0

\* surface_for_login. The body is not an argument. breakSpoof copies a desktop claim.
Mint ==
    /\ phase = "start"
    /\ phase' = "minted"
    /\ surface' = IF breakSpoof /\ claim = "desktop" THEN "desktop" ELSE "web"
    /\ UNCHANGED <<claim, kind, ran>>

\* invoke peeks the spec before the whitelist. A local spec returns before execute.
Decide ==
    /\ phase = "minted"
    /\ UNCHANGED <<claim, kind, surface>>
    /\ IF kind = "local" /\ ~breakLocal THEN
         /\ phase' = "denied"
         /\ ran' = 0
       ELSE IF kind = "local" /\ breakLocal THEN
         /\ phase' = "executed"
         /\ ran' = 1
       ELSE IF kind = "unknown" /\ breakShell THEN
         /\ phase' = "denied"
         /\ ran' = 0
       ELSE IF kind = "unknown" THEN
         /\ phase' = "whitelist"
         /\ ran' = 0
       ELSE
         /\ phase' = "executed"
         /\ ran' = 1

Done ==
    /\ phase \in {"denied", "executed", "whitelist"}
    /\ UNCHANGED vars

Next ==
    \/ Mint
    \/ Decide
    \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ claim \in Claims
    /\ kind \in Kinds
    /\ surface \in {"unset", "web", "desktop"}
    /\ phase \in {"start", "minted", "denied", "executed", "whitelist"}
    /\ ran \in {0, 1}

SurfaceStaysWeb ==
    phase \in {"minted", "denied", "executed", "whitelist"} => surface = "web"

NoLocalRun ==
    kind = "local" => ran = 0

ShellStaysWhitelist ==
    kind = "unknown" /\ phase \in {"denied", "executed", "whitelist"} => phase = "whitelist"

TokenStillDenied ==
    claim = "token" /\ kind = "local" /\ phase \in {"denied", "executed"} =>
        phase = "denied" /\ ran = 0

====
