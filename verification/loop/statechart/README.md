# Saved tool call

The held chart is one job. A save commits two tool rows. The model follows one of them, the read whose id is `read` and whose arguments are `kb`. The other row stays on the existing confirm and write path. TLC reached 38 distinct states. `TypeOK`, `SaveBeforeExecute`, `ResultBeforeNextModel`, `WriteWaitsForClaim`, `ForbidWebBlocksSearch`, `StoppedJobDoesNotExecute`, `ContinuationDueSurvivesCrash`, `OneProviderInvoke`, `SavedResultBeforeModel`, and `NoDeadlock` hold.

The main path saves the calls, runs the read, stores the result, opens the confirm card, confirms the write twice, requests the model, and can then die. The orange states are the other path. The process dies while the read is still pending, the same pending read resumes, and only then does the read run. The tan state is the older crash, after the model was already requested. Those are different states.

The view hides a step that stays in one label. That includes each action's stutter and the allow and forbid copies of one visible step. The forbid copy of the read shares the label "Read in flight". The arrow does not say that the provider was skipped. `ForbidWebBlocksSearch` is still checked. The chart draws 12 arrows.

`held/tlc.out` is the complete search. It reports 422 states generated, 38 distinct, and depth 11. The fingerprint line is TLC's usual probability estimate. On a break chart, `tlc.out` stops when the rule breaks, so its count is a partial search. The report's "TLC reached" line is a later count, after the chart command removes the broken rule. Those two counts are different searches.

## OneProviderInvoke

The saved read runs once.

When `breakDuplicate` is true, the shortest path has 4 steps.

0. No calls yet
1. the turn is saved. Calls saved
2. the process dies before the read. Process died before the read
3. the same pending call resumes. Same pending read resumed
4. the saved read runs again. Read ran twice

That one `Invoke` step sets the counter to 2. It does not append a second invoke mark. TLC keeps going, and every later state still has the counter at 2, so those states break the same rule. The shortest path is the four steps above.

## SavedResultBeforeModel

The model is requested only after the read result is stored.

When `breakEarlyModel` is true, the shortest path has 4 steps.

0. No calls yet
1. the turn is saved. Calls saved
2. the process dies before the read. Process died before the read
3. the same pending call resumes. Same pending read resumed
4. the model is asked before the result. Model asked before the result

The mutation sets the old confirm-completion counter to 2 and does not store the read result. `ResultBeforeNextModel` stays true. `SavedResultBeforeModel` is the rule TLC names. The phase is still pending and the result is still empty. The step appends the mark `EarlyModel`. That mark is not `Model`.

## ContinuationDueSurvivesCrash

A crash after the model leaves the continuation due, and does not enqueue recovery.

When `breakContinuation` is true, the shortest path has 8 steps.

0. No calls yet
1. the turn is saved. Calls saved
2. the saved read runs. Read in flight
3. the read result is stored. Read result stored
4. the confirm card opens. Confirm card open
5. the write is confirmed. Write confirmed
6. the write is confirmed again. Second confirm stored
7. the model is requested. Model requested
8. the process dies after the model. Crashed after the model

This path never enters the orange states. It is the old crash.

## Again

Run `/statechart` on `verification/loop/Loop.tla`. Pass `--view verification/loop/statechart/view.mjs`, set `TLA2TOOLS` to `verification/tlc/tla2tools.jar`, and pass `--out` as a new empty folder. Use the config that `verification/loop/check.py` writes for the held run or for that break.
