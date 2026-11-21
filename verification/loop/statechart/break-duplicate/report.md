# Loop

TLC reached 17 states and 189 steps. The chart draws 12 states and 11 arrows.
The view hides 174 steps, so the chart does not draw them.

## OneProviderInvoke breaks
The shortest path has 4 steps:
0. No calls yet
1. the turn is saved: Calls saved
2. the process dies before the read: Process died before the read
3. the same pending call resumes: Same pending read resumed
4. the saved read runs again: Read ran twice

## Rules that hold
TypeOK, SaveBeforeExecute, ResultBeforeNextModel, WriteWaitsForClaim, ForbidWebBlocksSearch, StoppedJobDoesNotExecute, ContinuationDueSurvivesCrash, SavedResultBeforeModel, NoDeadlock
