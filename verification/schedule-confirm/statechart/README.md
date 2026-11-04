# Schedule confirm

TLC reached 452 states. The chart draws 7 states and 10 arrows. Every rule in `report.md` holds, including `AtMostOneSuccessfulWrite` and `NoWriteAfterRetire`.

`view.mjs` hides `Idle`. That step changes nothing. The chart also leaves out steps that stay inside one label.

The spec and the config sit beside this directory. To make the files again, run the statechart command on `ScheduleConfirm.tla` with `--view` set to `view.mjs`, `TLA2TOOLS` set to the pinned jar under `.tools`, and `--out` set to a new empty folder.
