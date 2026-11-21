# Loop

TLC reached 12 states and 134 steps. The chart draws 6 states and 5 arrows.
The view hides 124 steps, so the chart does not draw them.

## SavedResultBeforeModel breaks
The shortest path has 4 steps:
0. No calls yet
1. the turn is saved: Calls saved
2. the process dies before the read: Process died before the read
3. the same pending call resumes: Same pending read resumed
4. the model is asked before the result: Model asked before the result

## Rules that hold
TypeOK, SaveBeforeExecute, ResultBeforeNextModel, WriteWaitsForClaim, ForbidWebBlocksSearch, StoppedJobDoesNotExecute, ContinuationDueSurvivesCrash, OneProviderInvoke, NoDeadlock
