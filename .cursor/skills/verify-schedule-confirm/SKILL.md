---
name: verify-schedule-confirm
description: >
  Check that a ScheduleCard create, reschedule, or cancel writes only after
  confirm is entered, and that the check still finds the two breaks this card
  allows. Use when editing ScheduleCard, confirm, chooseCandidate, applySlot,
  useSlot, isExpired, voidOpenDrafts, void_draft_schedule_cards, runStreamLoop,
  updateLastAssistant, createCalendarEvent, updateCalendarEvent,
  cancelCalendarEvent, confirmGoogleCalendarChange, CalendarWrite,
  create_calendar_event, patch_event, delete_event, confirm_google_title_change,
  execute_calendar, or _base_card.
---

# Verify schedule confirm

A create, patch, or delete for this card runs only after `confirm` is entered. The card keeps that promise. A second successful patch, local delete, Google update, or create on a newly free window can still happen after the stream puts the draft back. An in-flight handler can still commit after void or expiry. Exit 0 means the first promise holds and the check still finds the other two breaks.

## Run the check

Run these commands from the repo root. Java 21 must be on `PATH`.

```
python3 verification/schedule-confirm/check.py
```

Exit 0. The run still prints two counterexamples, one for `AtMostOneSuccessfulWrite` and one for `NoWriteAfterRetire`.

```
python3 verification/schedule-confirm/check.py --break-submit-gate
```

Exit 1 when a second entry happens while `busy` is true. The violated invariant is `NoReentryWhileBusy`.

```
python3 verification/schedule-confirm/check.py --break-confirmed-lock
```

Exit 1 when `writes` becomes 2 without a stream restore. The violated invariant is `SecondWriteImpliesRestore`.

The script downloads TLC 1.8.0, checks the pinned jar sha256, checks the source anchors, and runs TLC with one worker. A deleted submit `disabled` expression makes the default command exit 1. A missing file, a TLC timeout, a deadlock, or a gap property that starts holding exits 2.

CI runs the same three commands in `.github/workflows/schedule-confirm-tla.yml` and requires the two flag statuses to be 1.

Read a TLC result with `/statechart` by name.

## Coverage

Sync commit d5472ff2c985cc40428ff81e50c0c9254b5ef66c.

`Idle` stays enabled so a finished card is not a TLC deadlock. The spec stutter does not count as an enabled action, and a deadlock exits 2.

| Code | Model step |
| --- | --- |
| `chooseCandidate` in `frontend/src/components/ScheduleCard.tsx` | `ChooseCandidate` |
| `applySlot` in `ScheduleCard.tsx` | `applySlot` |
| `confirm` through `setBusy(true)` | `Confirm` |
| the `还没有选中日程` return in `confirm` | `MissingEvent` |
| the overlap read in `create_calendar_event`, or the list inside the connected create | `CheckConflict` |
| the insert in `create_calendar_event` | `CreateCalendarEvent` |
| the 409 from that create, the conflict `catch`, and `finally` | `RejectCreateConflict` |
| `patch_event` in `backend/app/api/calendar.py` | `PatchEvent` |
| `delete_event` in `backend/app/api/calendar.py` | `DeleteEvent` |
| `confirm_google_title_change` in `backend/app/api/calendar.py` | `ConfirmGoogleTitleChange` |
| the 409 once the id is gone from the primary list, plus `finally` | `CalendarTitleUnmatched` |
| the success `onChange` and `finally` | `ApplySuccess` |
| the delta loop in `runStreamLoop` calling `updateLastAssistant` | `UpdateLastAssistant` |
| `runStreamLoop` ending the stream | `FinishRunStreamLoop` |
| `voidOpenDrafts` in `frontend/src/services/chatStream.ts` and `void_draft_schedule_cards` in `backend/app/services/calendar.py` | `VoidOpenDrafts` |
| the 先不删 button in `ScheduleCard.tsx` | `DeclineCancel` |
| `isExpired` in `frontend/src/utils/scheduleExpiry.ts` | `ExpireDraft` |

`useSlot`, `reply`, and `execute_calendar` are anchors that must not call a calendar writer. The model has no step for them.

## Bounds

One card. At most two entries. At most two successful writes. The overlap read and the insert are separate steps.

## Out of scope

A second mounted card, RSVP, list, suggest, readonly, mailto, prep, OAuth, free-busy UI, guest editor, Meet, server idempotency, `X-Request-ID`, and the follow-up inflight ref.

## Tell a bug from a modeling error

Exit 1 on the default command is a held property TLC violated. Replay the trace as clicks and handler calls. If every step can happen, it is a product bug. Leave the formula.

Exit 2 is a modeling or tool failure. A renamed call, a missing anchor, TLC violating `TypeOK` or `WritesFollowConfirm`, a jar or Java failure, a timeout, a deadlock, or a gap property that holds all exit 2. A gap property that starts holding means a reachable write was dropped, or the code changed. Do not delete the action to make the run green.

Do not change product code. Do not weaken `NoWriteBeforeConfirm`, `WritesFollowConfirm`, `NoReentryWhileBusy`, `SecondWriteImpliesRestore`, `AtMostOneSuccessfulWrite`, or `NoWriteAfterRetire`.

## Prove the check can fail

Delete the submit `disabled={locked || busy || mode === "choose"}` expression from `ScheduleCard` in `frontend/src/components/ScheduleCard.tsx`. Run the default command. The exit code is 1. Restore the line. Run the default command again. The exit code is 0.

Then run `--break-submit-gate` without editing the product. The exit code is 1. Then run `--break-confirmed-lock`. The exit code is 1.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
