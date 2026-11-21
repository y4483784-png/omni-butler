# Task sweep

The held chart is one job. `createdOld = TRUE` and `createdOld = FALSE` draw the same picture, because a held step does not read that constant. TLC still checks each constant on its own. Each held run reached 126 states. `TypeOK`, `CreationAgeDoesNotStop`, `NullSampleStaysQuiet`, `OneNoticePerProgressStamp`, `WaitingLeftAlone`, and `NoDeadlock` hold in both.

The visible held path is a running job with no sample, then a saved sample, then one notice. A positive stamp and a past sweep are also reached when the sweep runs while the stamp is still 0 and the sample is saved after that. The view hides that sweep because the label stays "Running, no sample". Waiting on the person and waiting on a resource are the side exits of `notice_running`. The chart does not draw `reconcile_parked_writes` or `dispatch_task`. The view hides `Renew` and every step that stays in one label. A sweep that leaves a null sample running is hidden. The first sweep that only records a sample is hidden. The sweep that sends the notice stays visible.

## CreationAgeDoesNotStop

A sweep does not stop a job because of creation age.

When `breakAge` and `createdOld` are true, the shortest path has 1 step.

0. Running, no sample
1. the sweep runs. Stopped.

`breakStop` has that same path for an old job and for a young job. `breakAge` on a young job does not stop it. That run holds, at 126 states, and uses the held chart.

## OneNoticePerProgressStamp

One progress stamp is notified once.

When `breakDouble` is true, the shortest path has 2 steps.

0. Running, no sample
1. a progress sample is saved. Running, sample saved
2. the sweep runs. Running, notice sent

`Running, notice sent` and both waiting labels are marked because only some model states behind them break the rule.

## WaitingLeftAlone

`notice_running` leaves a waiting job's status, stamp, and notice count unchanged. The chart does not include the later dispatch.

When `breakWait` is true, the shortest path has 2 steps.

0. Running, no sample
1. the job waits on the person. Waiting on the person
2. the sweep runs. Waiting on the person, row changed

## Again

Run `/statechart` on `verification/watch/Watch.tla`. Pass `--view verification/watch/statechart/view.mjs`, set `TLA2TOOLS` to `verification/tlc/tla2tools.jar`, and pass `--out` as a new empty folder. Use the config that `verification/watch/check.py` writes for that age or break.
