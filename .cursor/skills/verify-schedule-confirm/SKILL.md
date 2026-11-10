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

Exit 0. `AtMostOneSuccessfulWrite`, `NoWriteAfterRetire`, and `NoWriteWithoutToken` hold, along with the older card invariants. The same command also checks `ClaimGate.tla` and `CardContinue.tla`.

```
python3 verification/schedule-confirm/check.py --break-submit-gate
```

Exit 1 when a second entry happens while `busy` is true. The violated invariant is `NoReentryWhileBusy`.

```
python3 verification/schedule-confirm/check.py --break-confirmed-lock
```

Exit 1 when `writes` becomes 2 without a stream restore. The violated invariant is `SecondWriteImpliesRestore`.

```
python3 verification/schedule-confirm/check.py --break-tokenless
```

Exit 1 when a create proceeds with `token = "absent"`. The violated invariant is `NoWriteWithoutToken`.

```
python3 verification/schedule-confirm/check.py --break-claim token
```

Exit 1 when `Consume` runs with no claim row. The violated invariant is `NoWriteWithoutToken` in `ClaimGate.tla`. The other `--break-claim` values are `match`, `retire`, `once`, `dedup`, `owner`, and `borrow`. Each must exit 1 on its own invariant. `borrow` is the continuation model. A click while another turn is live sets `borrowed` and violates `NoBorrow`.

The script loads `verification/tlc/tla2tools.jar`, checks sha256 `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`, checks manifest version `2.0 2026-11-06` and revision `d8fb231de4e4b5d02c1b2ed1ac759c086f694021`, checks the source anchors, and runs TLC with one worker. That file is the v1.8.0 asset GitHub published on 2026-11-06. The previous pin `e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf` was the 2026-11-03 upload, and the release URL no longer serves it. A deleted submit `disabled` expression makes the default command exit 1. A missing file, a hash or manifest mismatch, a TLC timeout, a deadlock, or an anchor that no longer matches the code exits 2.

CI runs the default command and the break commands in `.github/workflows/schedule-confirm-tla.yml`, including `--break-claim borrow`. Each break status must be 1.

Read a claim-gate TLC result with `/statechart` by name. The view is `verification/schedule-confirm/statechart/claim-view.mjs`. The review chart is `docs/envisioned-product/review/ev-gate-statechart.mmd`.

Read a TLC result with `/statechart` by name.

## Coverage

Card model sync commit d5472ff2c985cc40428ff81e50c0c9254b5ef66c.

Claim-gate sync commit `083ffdfb4ff691873004f6a0b4ce6f34293d437e`. The parent is release `5a6e0f847a238061fa719778689705bcccf0f01d`. Move this commit forward only after a run confirms that `ClaimGate.tla` and the new `NoWriteWithoutToken` conjunct still describe the code.

Card-continuation sync commit `a1ec95f1319b2d467ee19dba96511d43a77e3e90`. `CardContinue.tla` describes that commit. A click gets its own waiting generation. An unrelated live turn is not the authorization. Move this commit forward only after a run confirms `NoBorrow` and `UnrelatedStopKeepsClick` still describe the code.

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

`useSlot`, `reply`, and `execute_calendar` are anchors that must not call a calendar writer. `reply` may call `openConfirmClaim` and `rsvpCalendarEvent`. Those are not in the writer list. The card model has no step for them.

Claim row coverage:

| Code | Model step |
| --- | --- |
| `issue_schedule_claim` and the open branch of `Issue` in `ClaimGate.tla` | one open row for user, action, and fingerprint |
| `continue_from_card` and `insert_card_action` | `Continue` opens one waiting click and does not set `borrowed` |
| `consume_schedule_claim` rejecting an empty token | `Consume` disabled while `claim = "none"`, and `NoWriteWithoutToken` |
| fingerprint or scope mismatch | `Edit` sets `match = FALSE`, and `WriteMatchedFingerprint` |
| a stopped or completed linked generation | `Stop1`, `Stop2`, `Complete1`, `Complete2` |
| the last live generation ending | `OpenClaimHasLiveGeneration` by cancelling the open claim |
| a second generation on the same fingerprint | the attach branch of `Issue`, and `AtMostOneOpenRow` |
| `rsvp_google_event` | the same consume anchor as create, patch, delete, and Google confirm |

## Bounds

One card. At most two entries. At most two successful writes. The overlap read and the insert are separate steps.

`ClaimGate.tla` is one user, one action, and one fingerprint. Two generations. `writes` is at most 2 so a broken second consume is visible. Stopped and completed stay distinct. The pass run reached 350 states generated and 115 distinct.

`CardContinue.tla` is the click after a finished read. One unrelated turn, one click generation, one claim. The pass run reached 38 states generated and 15 distinct. It does not model expiry, the sha256 canonicalizer, or a second fingerprint.

## Out of scope

A second mounted card, list, suggest, readonly, mailto, prep, OAuth, free-busy UI, guest editor, Meet, `X-Request-ID`, and the follow-up inflight ref. The card model still does not include RSVP or server idempotency. `ClaimGate.tla` covers the claim row, not the button. It leaves out expiry, the sha256 canonicalizer, SQLite's no-op row lock, a second fingerprint, and a new open row after a successful write.

## Tell a bug from a modeling error

Exit 1 on the default command is a held property TLC violated. Replay the trace as clicks and handler calls. If every step can happen, it is a product bug. Leave the formula.

Exit 2 is a modeling or tool failure. A renamed call, a missing anchor, TLC violating `TypeOK` or `WritesFollowConfirm`, a jar or Java failure, a timeout, or a deadlock exits 2. Do not delete an action to make the run green.

Do not weaken `NoWriteBeforeConfirm`, `WritesFollowConfirm`, `NoReentryWhileBusy`, `SecondWriteImpliesRestore`, `AtMostOneSuccessfulWrite`, `NoWriteAfterRetire`, or `NoWriteWithoutToken`. Do not weaken `WriteMatchedFingerprint`, `WriteSawLiveGeneration`, `OpenClaimHasLiveGeneration`, `AtMostOneOpenRow`, or `OwnerWrites`. Do not weaken `NoBorrow` or `UnrelatedStopKeepsClick`. A product change that removes the claim check or lets a refresh reopen a settled card has to update the model in the same change. If TLC then reports a violation, replay the trace before editing a formula.

## Prove the check can fail

Delete the submit `disabled={locked || busy || mode === "choose"}` expression from `ScheduleCard` in `frontend/src/components/ScheduleCard.tsx`. Run the default command. The exit code is 1. Restore the line. Run the default command again. The exit code is 0.

Then run `--break-submit-gate` without editing the product. The exit code is 1. Then run `--break-confirmed-lock`. The exit code is 1.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
