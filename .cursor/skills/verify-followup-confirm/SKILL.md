---
name: verify-followup-confirm
description: >
  Check that a meeting follow-up writes a calendar row only after confirm,
  and only once, when confirm is submitted twice and POST /api/calendar
  ignores phase. Use when editing follow_up, to_wire, FollowupCard, confirm,
  postFollowAction, claimFollowRows, isExpired, voidOpenDrafts,
  void_draft_schedule_cards, CalendarWrite, or create_calendar_event.
---

# Verify follow-up confirm

A dated follow-up row is written only after the user confirms that ticked row on an unexpired draft. A second confirm, while that post is still in flight, does not write the row again. `POST /api/calendar` does not read `phase` or `expires_at`. The once-only gate is `claimFollowRows`.

If the gate is skipped, the user gets a reminder they did not confirm, or two reminders for one confirmed todo.

## Run the check

From the repo root, with Java 21 or newer on `PATH`:

```
python3 verification/followup-confirm/check.py
```

The script downloads TLC 1.8.0 from the pinned `tla2tools.jar` on first use, checks sha256 `5bd7b64db99ae4c6c35f290de5e809c0ee41af43f8699cf696099ffb6452b1e2`, checks the source anchors, and runs TLC with one worker. A pass prints `pass: follow-up confirm holds`.

CI runs that command, then `python3 verification/followup-confirm/check.py --break-claim`, in `.github/workflows/followup-confirm-tla.yml`.

To show the property failing without editing product code:

```
python3 verification/followup-confirm/check.py --break-claim
```

That command exits 0 only when TLC violates `AtMostOneEvent`. The trace has two `Confirm` steps, two `ConflictCheck` steps, and two `InsertEvent` steps, and `events` becomes 2.

You can also run TLC on the committed config, which keeps `ATOMIC_CLAIM = TRUE`:

```
java -XX:+UseParallelGC -cp verification/followup-confirm/.tools/tla2tools.jar tlc2.TLC -workers 1 -nowarning -config FollowupConfirm.cfg FollowupConfirm.tla
```

Run that from `verification/followup-confirm` after the script has downloaded the jar.

## Coverage

Sync commit `c48eff962e665c38898fdf268775e8324f6acc8c`.

Move that commit forward only after the diff of the covered files has been read, the spec still describes those files, and both commands have been run on that commit.

| Code | Model step |
| --- | --- |
| `follow_up` and `to_wire` in `backend/app/services/meeting_followup.py` | `FollowUpDraft`, `FollowUpNeedsPaste` |
| `Tick` path and the draft button in `frontend/src/components/FollowupCard.tsx` | `Tick`, `Untick`, `Confirm` |
| `claimFollowRows` in `frontend/src/utils/followMail.ts` | the `ATOMIC_CLAIM` guard inside `Confirm` |
| `postFollowAction` in `FollowupCard.tsx` | `PostFollowAction` |
| `create_calendar_event` and `CalendarWrite` in `backend/app/api/calendar.py` | `ConflictCheck`, `RejectOverlap`, `InsertEvent` |
| `done.current.add` after `await postFollowAction` | `ObserveSuccess` |
| the `catch` in `confirm` | `ObserveLostResponse` |
| `isExpired` in `frontend/src/utils/scheduleExpiry.ts` | `ExpireDraft` |
| `voidOpenDrafts` in `frontend/src/services/chatStream.ts` and `void_draft_schedule_cards` in `backend/app/services/calendar.py` | `VoidDraft` |

`follow_up` does not call `create_event`. `copyFollowMail` and the mailto link do not post. `CalendarWrite` has no `phase` field. `InsertEvent` does not read `phase`.

## Bounds

One `FollowupCard` instance. One dated row. At most two confirms. At most two in-flight creates. The overlap check sees committed rows only, then the insert commits. A lost response does not add the row to `done`. A later confirm of that same interval hits `RejectOverlap` and does not insert again. On the sync commit, TLC 1.8.0 generated 149 states, 58 of them distinct.

## Out of scope

A second mounted card, or a second tab, has its own `inflight` ref and its own `done` ref. Both can post. Two posts that both pass the overlap check both insert. The create route does not treat `follow_action:` as an idempotency key.

Drive consent, the Meet probe, and sending mail are out of scope. Follow-up mail stays copy and mailto. There is no `gmail.send` on this path.

`ScheduleCard` create, reschedule, and cancel are a different card. Overlap with some other event, and a Google list that omits a busy event, are out of scope. The model stops at two confirms.

## Tell a bug from a modeling error

Exit 1 is a property failure. Read the trace as steps of `confirm` and `create_calendar_event`. If every step can happen in the current code, it is a bug in the product. Leave `AtMostOneEvent`, `NoWriteBeforeConfirm`, and `NeedsPasteWritesNothing` as they are. Do not change product code unless the operator asked for a fix.

Exit 2 is a modeling error. The anchors no longer match, TLC violated `TypeOK` or `OneRequestPerLoop`, the jar or Java did not run, or the inflight guard is gone and TLC still passed. Update the spec from the code, or fix the tool, and say which one you did. Do not report exit 2 as a product bug.

A pass only covers this model and these bounds. If a covered file changed after the sync commit, read that diff before you trust the pass. The old pass can describe code that is no longer there.

## Update the check after an intentional change

Read the diff from the sync commit for every covered file. Also look for a renamed or moved function that still writes the same row. For each change, decide which it is.

- No effect. Formatting, copy, or a path the model does not include. Leave the spec.
- The spec no longer describes the code. A step split, a guard moved, or a new path reaches the calendar write. Change the spec from the code before you rerun. Do not shape the spec so the check passes.
- The promise changed on purpose. A test, a comment, or the commit message says the new behavior is intended. Change the property, and cite that evidence.
- The promise may be broken. The code changed and nothing says the promise changed. A later exit 1 is a bug.

Rerun both commands. On a pass, break the new guard the way the property forbids, confirm the check catches it, and restore the break. Then set the sync commit to the commit you checked.

## Prove the check can fail

Delete this line from `claimFollowRows` in `frontend/src/utils/followMail.ts`:

```
if (inflight.current) return [];
```

Run `python3 verification/followup-confirm/check.py`. The exit code is 1. The trace shows two `Confirm` steps before either insert, then two `InsertEvent` steps, and `events = 2`. Restore the line. Run the command again. The exit code is 0.
