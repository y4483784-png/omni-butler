# ScheduleConfirm

TLC reached 452 states and 1396 steps. The chart draws 7 states and 10 arrows.
The chart does not draw 692 steps that stay in one chart state.
The view hides 452 steps, so the chart does not draw them.

## Rules that hold
TypeOK, WritesFollowConfirm, NoWriteBeforeConfirm, NoReentryWhileBusy, SecondWriteImpliesRestore, AtMostOneSuccessfulWrite, NoWriteAfterRetire, NoDeadlock
