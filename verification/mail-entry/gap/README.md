# Boolean send model

`MailSendGap.tla` is the send model from release `9e336b42b26edd008eea75b1bd259a73fc59b719`. `Init` starts with `remote = "none"` and `accepted = FALSE`. `Accept` is the only step that sets `remote` to `provider`, and that same step sets `accepted` to `TRUE`. `Send`, `Reject`, `Resend`, and `Done` do not store a provider id. The next-state relation has no state where `remote` is `provider` and `accepted` is false. That structural fact is the gap.

`RepairStateReachable` is the liveness formula `<>(remote = "provider" /\ ~accepted)`. TLC checks it as a temporal property. On this copy TLC exits 13. The log says `Temporal property RepairStateReachable was violated.` The printed trace is `Init`, `Send`, `Accept` with `accepted = TRUE`, then a stutter. That trace is one liveness counterexample. One counterexample shows a behavior that never enters the repair state. It does not by itself prove that every behavior misses that state. The next-state relation above is the unreachability argument for this model.

This file is not a CI check. The held model is `../MailSend.tla`.

```sh
java -XX:+UseParallelGC -cp verification/tlc/tla2tools.jar tlc2.TLC -workers 1 -nowarning -config verification/mail-entry/gap/MailSendGap.cfg verification/mail-entry/gap/MailSendGap.tla
```
