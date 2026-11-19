---- MODULE MailSend ----
EXTENDS Integers

CONSTANT breakNoProvider, breakTwoSends, breakResend

VARIABLES claim, remote, sends, accepted, success

vars == <<claim, remote, sends, accepted, success>>

Claims == {"open", "unknown", "written"}
Remotes == {"none", "attempt", "provider"}

Init ==
  /\ claim = "open"
  /\ remote = "none"
  /\ sends = 0
  /\ accepted = FALSE
  /\ success = FALSE

Send ==
  /\ claim = "open"
  /\ remote = "none"
  /\ sends' = IF breakTwoSends THEN 2 ELSE 1
  /\ claim' = "unknown"
  /\ remote' = "attempt"
  /\ accepted' = FALSE
  /\ success' = FALSE

Accept ==
  /\ claim = "unknown"
  /\ remote = "attempt"
  /\ sends = 1
  /\ ~accepted
  /\ claim' = "written"
  /\ remote' = IF breakNoProvider THEN "attempt" ELSE "provider"
  /\ accepted' = TRUE
  /\ success' = ~breakNoProvider
  /\ UNCHANGED sends

Reject ==
  /\ claim = "unknown"
  /\ remote = "attempt"
  /\ ~accepted
  /\ claim' = "open"
  /\ remote' = "none"
  /\ sends' = 0
  /\ accepted' = FALSE
  /\ success' = FALSE

Resend ==
  /\ breakResend
  /\ claim = "unknown"
  /\ remote = "attempt"
  /\ sends = 1
  /\ ~accepted
  /\ sends' = 2
  /\ UNCHANGED <<claim, remote, accepted, success>>

Done ==
  /\ claim = "written"
  /\ UNCHANGED vars

Next ==
  \/ Send
  \/ Accept
  \/ Reject
  \/ Resend
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ claim \in Claims
  /\ remote \in Remotes
  /\ sends \in 0..2
  /\ accepted \in BOOLEAN
  /\ success \in BOOLEAN

NoAcceptWithoutProvider == accepted => (remote = "provider")

OneSendPerClaim == sends <= 1

UnknownDoesNotResend ==
  claim = "unknown" => (sends = 1 /\ remote = "attempt" /\ ~accepted /\ ~success)

====
