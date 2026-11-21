# Watch

TLC reached 147 states and 882 steps. The chart draws 5 states and 8 arrows.
The view hides 791 steps, so the chart does not draw them.

## OneNoticePerProgressStamp breaks
The shortest path has 2 steps:
0. Running, no sample
1. a progress sample is saved: Running, sample saved
2. the sweep runs: Running, notice sent

## Rules that hold
TypeOK, CreationAgeDoesNotStop, NullSampleStaysQuiet, WaitingLeftAlone, NoDeadlock
