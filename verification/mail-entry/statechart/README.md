# Mail send

TLC reached 20 states and 28 steps. The chart draws 10 states and 12 arrows. The rules in `report.md` hold. An attempt id is not repaired. A stored provider id can be repaired without calling send. A lookup that is not accepted leaves the claim unknown. An acceptance that is already stored seals its receipt. A sealed receipt stays sealed.

`send-view.mjs` hides `Done`. The report counts 6 hidden steps. Those are the end stutter. The chart also joins two repairs that land on the same sealed receipt. The arrow says `lookup accepts or repair [no transport]`.

The spec and the config sit beside this directory. To draw the chart again, run the statechart skill on `MailSend.tla` with `send-view.mjs`. Set `TLA2TOOLS` to the pinned jar from `python3 verification/mail-entry/check.py`. The output folder has to be empty.
