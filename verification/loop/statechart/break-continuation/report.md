# Loop

TLC reached 38 states and 420 steps. The chart draws 12 states and 12 arrows.
The view hides 384 steps, so the chart does not draw them.

## ContinuationDueSurvivesCrash breaks
The shortest path has 8 steps:
0. No calls yet
1. the turn is saved: Calls saved
2. the saved read runs: Read in flight
3. the read result is stored: Read result stored
4. the confirm card opens: Confirm card open
5. the write is confirmed: Write confirmed
6. the write is confirmed again: Second confirm stored
7. the model is requested: Model requested
8. the process dies after the model: Crashed after the model

## Rules that hold
TypeOK, SaveBeforeExecute, ResultBeforeNextModel, WriteWaitsForClaim, ForbidWebBlocksSearch, StoppedJobDoesNotExecute, OneProviderInvoke, SavedResultBeforeModel, NoDeadlock
