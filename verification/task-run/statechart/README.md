# Task turn

TLC reached 25 states. The chart draws 16 states and 24 arrows. Every rule in `report.md` holds. A finished turn stays completed. A stopped turn stays stopped. A confirm card does not become completed. Nothing returns to running.

`view.mjs` hides `Done`. The chart also leaves out steps that stay inside one label.

The spec and the config sit beside this directory. To draw the chart again, run the statechart skill on `TaskRun.tla` with this view. Set `TLA2TOOLS` to the pinned jar from `python3 verification/task-run/check.py`.
