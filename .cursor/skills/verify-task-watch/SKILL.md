---
name: verify-task-watch
description: "Check that a sweep does not stop a job because of creation age, that a null progress sample is not a notice, and that one progress stamp is notified once. Use when editing the task watchdog, its beat entry, last_progress_at, or verification/watch."
---

# Verify the task sweep

A sweep does not stop a job. Creation age does not matter. `created_at` is not a sample. A null `last_progress_at` stays null, produces no notice, and leaves the job `running`. One non-null stamp is notified once. `waiting_person` and `waiting_resource` stay as they are, with no notice. A hold renewal does not move `last_progress_at`.

The question is an order of steps, so the check is TLC on `verification/watch/Watch.tla`. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/tasks/watchdog.py` (`_EFFECT`, `notice_running`, `run_sweep`, `sweep_task`)
- `backend/app/tasks/celery_app.py` (the `watch-task-runs` beat entry)
- `backend/app/services/task_run.py` (`renew_hold`, `claim_web_execution`, `touch_progress`)
- `verification/watch/Watch.tla` or `verification/watch/check.py`

## Run

From the repo root, with Java on PATH:

```sh
python3 verification/watch/check.py --age old
python3 verification/watch/check.py --age young
python3 verification/watch/check.py --age old --break-stop
python3 verification/watch/check.py --age young --break-stop
python3 verification/watch/check.py --age old --break-double
python3 verification/watch/check.py --age young --break-double
python3 verification/watch/check.py --age old --break-wait
python3 verification/watch/check.py --age young --break-wait
python3 verification/watch/check.py --age old --break-age
python3 verification/watch/check.py --age young --break-age
python3 verification/watch/check.py --witness old-null
python3 verification/watch/check.py --witness young-null
python3 verification/watch/check.py --witness old-sample
python3 verification/watch/check.py --witness young-sample
python3 verification/watch/test_age.py
```

`--age old` is the default. A held command exits 0. `--age young --break-age` is also a held command. It exits 0 because the age break stops only an old job.

Each other break, and each witness, exits 1. The checker prints `TLC exit 12 violates <name>.` The name is the invariant TLC reported. Exit 2 means the source anchors no longer match, TLC exited with some other code, or the named invariant did not fail.

`test_age.py` is the offline probe file. It does not start the product. A product probe maps to `watchdog.py`. A coverage probe is a witness that a state was reached. A probe whose invariant is `TRUE` is refused.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`CreationAgeDoesNotStop` says the status stays `running`, `waiting_person`, or `waiting_resource`. The harm is a sweep that stops a job, including a stop that happens only when the job was created earlier. In the trace, `Sweep` sets `status` to `stopped` when `breakStop` is true, or when `breakAge` and `createdOld` are both true. In the code, `_EFFECT` maps every status, including `running`, to `leave`. `notice_running` does not set the status. `watchdog.py` does not read `created_at`.

`NullSampleStaysQuiet` says a stamp of 0 has no notice. The harm is a notice for a null `last_progress_at`. Stamp 0 stands for that null. In the code, `notice_running` reads `last_progress_at` and continues only when `stamp is not None`. It does not assign `last_progress_at`.

`OneNoticePerProgressStamp` says a notice for stamp 1 or stamp 2 happens only after that stamp has already been stable, and at most once. The harm is a notice on the sweep that first sees the stamp, or a second notice for the same stamp. In the shortest trace, `Sweep` sets `hits1` to 1 while `stamp` is 1 and `stable` is 1 when `breakDouble` is true. The invariant requires `stable >= 2` for that notice. In the code, the notice is inserted only when the stamp equals `observed_progress_at` and differs from `notice_progress_at`, and the same transaction then stores that stamp.

`WaitingLeftAlone` says a job that left `running` keeps the stamp, the notice count, and the waiting status. The harm is a sweep that rewrites a waiting row. In the trace, `Sweep` changes `observed` while `left` is true when `breakWait` is true. In the code, `notice_running` returns without a notice when the status is not `running`.

`TypeOK` says the variables stay in the sets in the spec, and `createdOld` is a boolean. Both true and false are legal. TLC also checks deadlock. The stuttering branches of `Next` stay enabled, so a finished sweep is not a deadlock.

The witness names are coverage, not safety. `WitnessOldNull` and `WitnessYoungNull` fail when a sweep has already run on a running job whose stamp is still 0 and whose notice count is still 0. `WitnessOldSample` and `WitnessYoungSample` fail when a sweep has run on a running job whose stamp is already greater than 0. A witness that does not fail means that state was not reached. That is exit 2, not a passing run.

If the trace cannot be replayed as `notice_running` and `run_sweep`, the model is wrong. Say so. Do not weaken the invariant to make the run pass. Do not treat a green log line as a pass unless TLC exited 0 and printed `No error has been found.`

## What this does not cover

The model is one job and at most three sweeps. Stamp is 0, 1, or 2. `createdOld` is a constant for the whole run. It stands for `created_at` being old or young in `backend/tests/test_watchdog.py`. The model does not compute that boolean from a clock, and one run does not change it. A null sample is stamp 0. The model does not store a separate `created_at` value and does not fill stamp 0 from it.

The check does not cover Celery delivery, a second worker, a second user, a provider timeout, image reconciliation, or dispatch from `run_sweep`. It does not execute `watchdog.py`. The anchors compare source text. They are not a run of the sweep. `python3 verification/watch/test_age.py` skipping for a missing Java is not a pass.

`verification/task-watch/` is an older model of a silence stop. This skill does not run it. The current sweep does not stop for silence.

## Keep the check with the code

The watchdog file blob is `892afc786f6352e929320e2c4b3ee3002714c7d4`. The model was confirmed in `b33a6481d51c14281852cc6b061ae57871847065`. Move either value forward only after reading `_EFFECT`, `notice_running`, `run_sweep`, `renew_hold`, and the `watch-task-runs` beat entry, and seeing that the model still describes them. If `running` maps to `stop`, if `watchdog.py` mentions `created_at`, or if the sweep assigns `last_progress_at`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
