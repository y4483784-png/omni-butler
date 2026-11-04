---
name: verify-schedule-confirm
description: >
  Check that a ScheduleCard create, reschedule, or cancel writes only after
  confirm is entered, that one success stays one write, and that a voided or
  expired card cannot still write. Use when editing ScheduleCard, confirm, chooseCandidate, applySlot,
  useSlot, isExpired, voidOpenDrafts, void_draft_schedule_cards, runStreamLoop,
  updateLastAssistant, createCalendarEvent, updateCalendarEvent,
  cancelCalendarEvent, confirmGoogleCalendarChange, CalendarWrite,
  create_calendar_event, patch_event, delete_event, confirm_google_title_change,
  execute_calendar, or _base_card.
---

# Verify schedule confirm

A create, patch, or delete for this card runs only after `confirm` is entered. The card keeps that promise. A successful write locks the card, and a stream refresh does not turn it back into a draft. A voided or expired claim does not let an in-flight handler commit. Exit 0 means every invariant holds.

## Run the check

Run these commands from the repo root. Java 21 must be on `PATH`.

```
python3 verification/schedule-confirm/check.py
```

Exit 0. `AtMostOneSuccessfulWrite` and `NoWriteAfterRetire` hold, along with the older invariants.

```
python3 verification/schedule-confirm/check.py --break-submit-gate
```

Exit 1 when a second entry happens while `busy` is true. The violated invariant is `NoReentryWhileBusy`.

```
python3 verification/schedule-confirm/check.py --break-confirmed-lock
```

Exit 1 when `writes` becomes 2 without a stream restore. The violated invariant is `SecondWriteImpliesRestore`.

The script downloads the pinned `tla2tools.jar` from the v1.8.0 release URL, checks sha256 `e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf`, checks the source anchors, and runs TLC with one worker. GitHub replaced that asset on 2026-11-03. The jar reports Implementation-Version 2.0, revision 309e489. A deleted submit `disabled` expression makes the default command exit 1. A missing file, a TLC timeout, a deadlock, or an anchor that no longer matches the code exits 2.

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
| `consume_schedule_claim` refusing a claim that is not open | `~retired` on each write step |
| `mergeStreamScheduleCard` and `overlaySettledScheduleCards` | `UpdateLastAssistant` leaving a settled card in place |

`useSlot`, `reply`, and `execute_calendar` are anchors that must not call a calendar writer. The model has no step for them.

## Bounds

One card. At most two entries. At most two successful writes. The overlap read and the insert are separate steps.

## Out of scope

A second mounted card, RSVP, list, suggest, readonly, mailto, prep, OAuth, free-busy UI, guest editor, Meet, server idempotency, `X-Request-ID`, and the follow-up inflight ref.

## Tell a bug from a modeling error

Exit 1 on the default command is a held property TLC violated. Replay the trace as clicks and handler calls. If every step can happen, it is a product bug. Leave the formula.

Exit 2 is a modeling or tool failure. A renamed call, a missing anchor, TLC violating `TypeOK` or `WritesFollowConfirm`, a jar or Java failure, a timeout, or a deadlock exits 2. Do not delete an action to make the run green.

Do not weaken `NoWriteBeforeConfirm`, `WritesFollowConfirm`, `NoReentryWhileBusy`, `SecondWriteImpliesRestore`, `AtMostOneSuccessfulWrite`, or `NoWriteAfterRetire`. A product change that removes the claim check or lets a refresh reopen a settled card has to update the model in the same change. If TLC then reports a violation, replay the trace before editing a formula.

## Prove the check can fail

Delete the submit `disabled={locked || busy || mode === "choose"}` expression from `ScheduleCard` in `frontend/src/components/ScheduleCard.tsx`. Run the default command. The exit code is 1. Restore the line. Run the default command again. The exit code is 0.

Then run `--break-submit-gate` without editing the product. The exit code is 1. Then run `--break-confirmed-lock`. The exit code is 1.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
