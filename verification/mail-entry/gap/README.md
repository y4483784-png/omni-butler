# Boolean send model

`MailSendGap.tla` is the send model from release `9e336b42b26edd008eea75b1bd259a73fc59b719`. `Accept` stores a provider id and an acceptance together. The property `RepairStateReachable` asks for a state where `remote` is `provider` and `accepted` is false.

TLC on this copy exits 13. The error is `Temporal property RepairStateReachable was violated.` The trace is `Init`, `Send`, `Accept` with `accepted = TRUE`, then a stutter. This file is the gap witness. It is not a CI check. The held model is `../MailSend.tla`.

```sh
java -XX:+UseParallelGC -cp verification/tlc/tla2tools.jar tlc2.TLC -workers 1 -nowarning -config verification/mail-entry/gap/MailSendGap.cfg verification/mail-entry/gap/MailSendGap.tla
```
