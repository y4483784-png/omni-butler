---
name: verify-loop-round
description: "Check that a crash after saved pending tool calls, and before the read runs, resumes that same call once and stores its result before the next model request. Use when editing the loop driver, save_turn, begin_read, or verification/loop."
---

# Verify the saved read

After `save_turn` commits pending tool calls, the process can die before `begin_read` hands the call to the provider. A later drive resumes that same pending call once, stores its result, and only then requests the model. A crash after the model has already been requested is a different path. It leaves `continuation_due` set.

The question is an order of steps, so the check is TLC on `verification/loop/Loop.tla`. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

`saved = 2` is the number of tool rows one `save_turn` commits. It is not two resumed reads and it is not the invocation count. Recovery in the model follows one eligible read, id `read` and arguments `kb`. The other row stays on the existing open, confirm, and write path.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/loop_round.py` (`save_turn`, `begin_read`, `_cas_running`)
- `backend/app/tasks/job.py` (`_drive_held`, `_one_pass`, `_invoke`)
- `verification/loop/Loop.tla` or `verification/loop/check.py`

## Run

From the repo root, with Java on PATH:

```sh
python3 verification/loop/check.py
python3 verification/loop/check.py --break-save
python3 verification/loop/check.py --break-result
python3 verification/loop/check.py --break-write
python3 verification/loop/check.py --break-forbid
python3 verification/loop/check.py --break-stop
python3 verification/loop/check.py --break-continuation
python3 verification/loop/check.py --break-duplicate
python3 verification/loop/check.py --break-early-model
python3 verification/loop/check.py --witness ordered-resume
python3 verification/loop/check.py --witness post-confirm
python3 verification/loop/test_resume.py
```

The held command exits 0 and prints `TLC exit 0, no error.` Each break and each witness exits 1 and prints `TLC exit 12 violates <name>.` Exit 2 means the source anchors no longer match, TLC exited with some other code, more than one invariant failed, the named invariant did not fail, or the output contradicted itself.

`test_resume.py` is the offline probe file. It does not start the product. A product probe maps to the loop driver. A coverage probe is a witness that an ordered trace was reached. A probe whose invariant is `TRUE` is refused. A type error, a parser error, a wrong name, a success line beside a violation, and TLC liveness exit 13 are exit 2. They are not expected negatives.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC. The hand-off for the current charts is `verification/loop/statechart/README.md`.

The behavior tests use the local fake tools. They do not call a provider.

```sh
python3 -m pytest backend/tests/test_loop_job.py::test_a_crash_after_save_leaves_the_calls_pending backend/tests/test_loop_job.py::test_a_later_drive_runs_a_pending_read_before_the_model backend/tests/test_loop_job.py::test_a_later_drive_does_not_run_a_running_read_again backend/tests/test_loop_job.py::test_a_second_run_job_recovers_a_pending_read -q
```

Run that from `backend/` with the project dependencies installed. A crash after save leaves the call pending. The later drive invokes once, before the model, and stores `saw kb`. A second `run_job` does the same and does not add a job or a second start note. The `expire=True` argument on that dispatch adopts the held job. The call is still pending. It is not evidence that a provider call ended. A call already in `running` returns busy and is not invoked again.

## What a failure means

`OneProviderInvoke` says the provider invocation counter is at most 1. The harm is a resumed pending read that runs twice. The counter's domain is 0, 1, or 2, so the value 2 is well typed. In the trace, `Invoke` sets `invoked` to 2 after `CrashBeforeInvoke` and `Resume` when `breakDuplicate` is true. On the release spec that same assignment violated `TypeOK`, because the domain stopped at 1. That record is `verification/loop/baseline-preinvoke.txt`.

`SavedResultBeforeModel` says `nextModel` stays 0 until `readResult` is 1. The harm is a model request that happens before the saved read has a result. In the trace, `RequestBeforeSavedResult` sets `nextModel` to 1 while `readResult` is 0, `callPhase` is `pending`, and `invoked` is 0, and it appends the mark `EarlyModel`. That mark is not `Model`, and the ordered resume witness does not accept it. The mutation also sets `terminal` to 2, which is the old confirm-completion counter, so `ResultBeforeNextModel` stays true and TLC names only `SavedResultBeforeModel`. Deleting that assignment and adding `readResult = 0` to the old rule would make the old rule true whenever the result is missing, including a real model request that skipped the confirm. The assignment stays.

`ContinuationDueSurvivesCrash` says a crash leaves `due` true and `enqueued` false. The harm is dropping the continuation, or enqueueing recovery, after the model was requested. `Crash` is enabled at step 6, after `RequestAfterTools`. `CrashBeforeInvoke` does not set `crashed` and does not touch `due`. The two crashes are different actions.

`SaveBeforeExecute`, `ResultBeforeNextModel`, `WriteWaitsForClaim`, `ForbidWebBlocksSearch`, and `StoppedJobDoesNotExecute` are the older rules. Their break flags are unchanged.

`WitnessOrderedResume` and `WitnessPostConfirmCrash` are coverage, not safety. Each one fails when that ordered trace is reached. A witness that does not fail means the trace was not reached. That is exit 2, not a passing run. The held run does not include the witnesses.

The ordered resume witness requires the first six marks to be Save, PreinvokeCrash, Resume, Invoke, Persist, and Model. Slot k is written only when the length was k-1, so the slot order is the event order. Save, the crash, and the resume keep id `read`, arguments `kb`, and phase `pending`. The crash mark has volatile 0. The resume mark has volatile 1. Invoke is phase `running`. Persist is phase `result`. Model comes after Persist. The post-confirm witness requires a Model mark immediately followed by PostConfirmCrash, with due true, volatile 0, and the same id and arguments. On the short path those are slots 4 and 5. On the path that also died before the read, they are slots 6 and 7.

The spec has no fairness condition. A stuttering safety model shows that a resume is reachable. It does not show that a crash is eventually resumed.

If the trace cannot be replayed as `save_turn`, then a crash, then `_drive_held` calling `begin_read` before `_invoke`, the model is wrong. Say so. Do not weaken the invariant to make the run pass. Do not treat a green log line as a pass unless TLC exited 0 and printed `No error has been found.`

## What this does not cover

The model is one job and one eligible read. `saved = 2` only counts the other row. Open, Confirm, and ConfirmAgain do not carry a second call id. The invocation counter stops at 2. A duplicate is one `Invoke` step that sets the counter to 2 and appends one `Invoke` mark. The longest trace uses seven of the eight slots. A real provider can still run twice if the process and the model disagree. This is not an exactly-once proof for an external side effect. There is no second worker, no lease expiry, and no liveness proof. `NoDeadlock` holds because every action can stutter. It does not mean a crash is resumed. `CrashBeforeInvoke` does not set `crashed`, so `ContinuationDueSurvivesCrash` does not see that death. A running call is busy in the source anchors. The model does not retry a running call because a lease expired.

`verification/loop/baseline-preinvoke.txt` records the release gap. `CrashedOnlyAfterModel` held, so every crashed state had already requested the model. The conjunction `saved = 2 /\ invoked = 0 /\ crashed` was reachable on the old forbid path after the model request. That conjunction is not the preinvoke edge.

## Keep the check with the code

`loop_round.py` blob `c70b2b87043531d8d817bdc4d4bfe9099d524bcf`. `job.py` blob `f6f3f94ab152a0d8fd9d410b0ababc8a42dd222b`. The model was confirmed in `00af165831d4789bad23f4db1a300dcfbbc223bb`, against release `3038f9c8909cdbec8674fba9d66bead7568027a8`. Those source blobs were read there and were not edited. Move that commit forward only after reading `save_turn`, `begin_read`, `_cas_running`, `_drive_held`, and `_one_pass`, and seeing that the model still describes them. `save_turn` stores `CallState.PENDING`. `begin_read` returns `"busy"` for `RUNNING` before `_cas_running`. `_cas_running` matches `PENDING`. In `_one_pass`, `begin_read` is before `_invoke`. If those anchors move, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
