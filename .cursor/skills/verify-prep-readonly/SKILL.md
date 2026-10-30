---
name: verify-prep-readonly
description: >
  Check that 会前准备 stays read-only. The prep path must not create, patch,
  or delete a calendar event, and it must not send mail. Use when editing
  MeetingBriefCard, prepare_meeting, the prep branch of execute_calendar,
  search_meeting_mail, or search_meeting_docs.
---

# Verify prep is read-only

会前准备 reads Gmail, Drive, and calendar events. It does not write the calendar and it does not send mail. The check walks that closure. The script does not download TLC.

## Run the check

From the repo root:

```
python3 verification/prep-readonly/check.py
```

Exit 0. The line is `pass: prep closure is read-only`.

```
python3 verification/prep-readonly/check.py --inject-write
```

Exit 1. The line names `prepare_meeting` and `create_event`. The splice stays in memory.

```
python3 verification/prep-readonly/check.py --inject-send
```

Exit 1. The line names `send_mail`.

```
python3 verification/prep-readonly/check.py --strip-lock
```

Exit 2. The line is `blind: no roots`.

CI runs these four commands in `.github/workflows/prep-readonly.yml` and checks that the injects are 1 and the strip is 2.

## Coverage

The roots are the 会前准备 button in `ScheduleCard.tsx`, `formatPointedUtterance`, every function in `MeetingBriefCard.tsx` and `briefFromWire.ts`, three cuts of `execute_calendar`, and `prepare_meeting`.

The prep cut is the body of `if action == "prep"`. The clock cut is the `clock_error` arm without the list and suggest branches. The load cut is the statement that calls `_load_upcoming`. Callees come from the walk.

## Out of scope

Schedule confirm, follow-up confirm, the RSVP buttons, mailto, suggest freeBusy, and the draft-card tail of `execute_calendar` are out of scope.

## Tell a bug from a blind lock

Exit 1 means a reached writer or a mail send. Replay the call. If that call can run on 会前准备, it is a product bug. Leave the forbidden name in place.

Exit 2 means a missing anchor or an empty root table. The lock cannot see the prep path, so the run is not evidence that the path is read-only.

## Prove the check can fail

Add `create_event()` as the first statement of `prepare_meeting` in `backend/app/services/meeting_prep.py`. Run the default command. The exit code is 1. Restore the line. Run the default command again. The exit code is 0.

On a clean tree, run `--inject-write`, `--inject-send`, and `--strip-lock`. The exit codes are 1, 1, and 2.
