---- MODULE Search ----
EXTENDS Integers

CONSTANT breakProvider, breakModel

VARIABLES constraint, choice, provider, posts, model, outcome, done

vars == <<constraint, choice, provider, posts, model, outcome, done>>

Init ==
    /\ constraint \in {"allow", "forbid"}
    /\ choice = "none"
    /\ provider = "none"
    /\ posts = 0
    /\ model = "chat"
    /\ outcome = "pending"
    /\ done = FALSE

Search ==
    /\ done = FALSE
    /\ choice' = "search"
    /\ IF constraint = "forbid"
       THEN /\ provider' = "none"
            /\ posts' = 0
            /\ model' = "chat"
            /\ outcome' = "denied"
       ELSE /\ provider' = IF breakProvider THEN "second" ELSE "deepseek"
            /\ posts' = 1
            /\ model' = IF breakModel THEN "other" ELSE "chat"
            /\ outcome' \in {"success", "empty", "error"}
    /\ done' = TRUE
    /\ UNCHANGED constraint

Fetch ==
    /\ done = FALSE
    /\ choice' = "fetch"
    /\ IF constraint = "forbid"
       THEN /\ provider' = "none"
            /\ posts' = 0
            /\ model' = "chat"
            /\ outcome' = "denied"
       ELSE /\ provider' = IF breakProvider THEN "second" ELSE "deepseek"
            /\ posts' = 1
            /\ model' = "chat"
            /\ outcome' = "fetched"
    /\ done' = TRUE
    /\ UNCHANGED constraint

Answer ==
    /\ done = FALSE
    /\ choice' = "answer"
    /\ provider' = "none"
    /\ posts' = 0
    /\ model' = "chat"
    /\ outcome' = "nosearch"
    /\ done' = TRUE
    /\ UNCHANGED constraint

Done ==
    /\ done = TRUE
    /\ UNCHANGED vars

OneSearchProvider ==
    /\ provider \in {"none", "deepseek"}
    /\ (done /\ constraint = "allow" /\ choice \in {"search", "fetch"}
        => provider = "deepseek" /\ posts = 1)
    /\ (done /\ constraint = "forbid" => provider = "none" /\ posts = 0)

ChatModelUnchanged ==
    /\ model = "chat"
    /\ (outcome \in {"success", "empty", "error", "fetched", "nosearch"} => model = "chat")

Next ==
    \/ Search
    \/ Fetch
    \/ Answer
    \/ Done

Spec == Init /\ [][Next]_vars
====
