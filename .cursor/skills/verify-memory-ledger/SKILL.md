---
name: verify-memory-ledger
description: "Check that a generation which is not completed writes no memory ledger row, that a low-confidence extract opens no confirm card, that a second pulse of the same fingerprint adds no claim, and that a turn does not replace a value a person already applied. Use when editing memory.py, the memory routes, chat.py's remember call, or the memory pulse."
---

# Verify the memory ledger

A generation that is not `completed` does not call the model and does not append a ledger row. A low-confidence extract appends a pending row and does not open a confirm card. A second pulse of the same fingerprint does not add a claim. A turn does not replace a value a person already applied.

The question is an order of steps, so the check is TLC on a small model of those steps. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/memory.py` (`decide_apply`, `remember_from_turn`, `_commit_change`)
- `backend/app/api/chat.py` (the stop gate and the completed check before `_remember_turn_isolated`)
- `backend/app/tasks/pulse.py` (`push_pulse`)
- `backend/app/models/models.py` (`MemoryLedger`, `SOURCE_ACTOR`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/memory-ledger/check.py
python verification/memory-ledger/check.py --break-stop
python verification/memory-ledger/check.py --break-quiet
python verification/memory-ledger/check.py --break-claim
python verification/memory-ledger/check.py --break-erase
```

On this machine `python` may be the backend virtualenv interpreter. The held command exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`NotCompletedWritesNothing` says a `stopped` or `waiting` status has `called` false and `turnRows` at 0. The harm is a stopped or waiting generation calling the model and appending a ledger row. In the trace, `LowRemember` or `HighRemember` runs from that status when `breakStop` is true. In the code, `remember_from_turn` returns 0 before `extract_for_ledger` when `generation` is set and `status` is not `completed`, and `chat.py` calls it only inside `if finished.status == "completed":`, which sits under `if persisted and not is_stopped():`.

`LowConfidenceNoCard` says `memoryClaims` stays 0. The harm is a low-confidence extract opening a confirm card. In the trace, `LowRemember` sets `memoryClaims` to 1 when `breakQuiet` is true. In the code, `decide_apply` returns false when `low_confidence` is true, `_commit_change` leaves `MemoryItem` unchanged, and `memory.py` does not call `publish_schedule_claim`.

`OneClaim` says `pulseClaims` stays at most 1. The harm is a second confirm card for the same fingerprint. In the trace, the second `Pulse` increments `pulseClaims` when `breakClaim` is true. In the code, `push_pulse` returns `Suppressed` when the open claim's token is already on the message, and it does not call `publish_schedule_claim` on that path.

`PersonNotUndone` says a person row plus a turn row does not leave `live` as `turn` with `pending` false. The harm is a turn silently replacing a value the person applied. In the trace, `HighRemember` sets `live` to `turn` when `breakErase` is true. In the code, `decide_apply` returns false when the incoming actor is `turn` and the latest applied actor is `person`, and a later person write does not clear the pending sibling.

`TypeOK` says the model variables stay inside the sets above. TLC also checks deadlock. A deadlock is a path that cannot settle.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one generation and one schedule fingerprint. It does not model a second user, a crash between the claim commit and the token stamp, an unknown claim, a missing session, the panel settle buttons, or the calendar confirm HTTP path. `note_external_success` is not called. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at `71b5206d99cbfbf330aacb63c507cca47a029b8c`. Move the confirmation forward only after reading `decide_apply`, `remember_from_turn`, the completed check in `chat.py`, and `push_pulse`, and seeing that the model still describes them. If the stop line moves after `_remember_turn_isolated`, if `decide_apply` applies a low-confidence write, or if `push_pulse` publishes again when the token is already on the message, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
