# Watch

TLC reached 170 states and 1020 steps. The chart draws 7 states and 12 arrows.
The view hides 860 steps, so the chart does not draw them.

## WaitingLeftAlone breaks
The shortest path has 2 steps:
0. Running, no sample
1. the job waits on the person: Waiting on the person
2. the sweep runs: Waiting on the person, row changed

## Rules that hold
TypeOK, CreationAgeDoesNotStop, NullSampleStaysQuiet, OneNoticePerProgressStamp, NoDeadlock
