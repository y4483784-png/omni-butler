---
name: verify-event-inbox
description: "Check that an event delivery is stored once, that receiving it is not external success, that a stopped generation writes nothing, that a completed generation is not reused, that a running generation is not a new dispatch, and that a waiting generation keeps one write card. Use when editing event_inbox.py, the inbox route, or EventReceipt."
---

# Verify the event inbox

One delivery id is one receipt. Receiving the event only stores `received`. `external_success` comes after a write the other side accepted. A stopped generation does not write and does not open a card. A completed generation is not reused. A running generation stays that generation. While someone is waiting, one write card stays open, and a later different event queues.

The question is an order of steps, so the check is TLC on a small model of those steps. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/services/event_inbox.py` (`submit_event`, `note_external_success`, `_route_act`, `_while_waiting`, `_reissue`, `_open_task`)
- `backend/app/api/events.py` (`POST /api/events/inbox`)
- `backend/app/main.py` (the `events.router` registration)
- `backend/app/models/models.py` (`EventReceipt`)
- `backend/app/services/gmail.py` (`search_meeting_mail` must stay a search)

## Run

From the repo root, with Java on PATH:

```sh
python verification/event-inbox/check.py
python verification/event-inbox/check.py --break-dedupe
python verification/event-inbox/check.py --break-stopped
python verification/event-inbox/check.py --break-reuse
python verification/event-inbox/check.py --break-second
python verification/event-inbox/check.py --break-success
python verification/event-inbox/check.py --break-pass
```

The held command exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`OneReceipt` says `seen` stays at most 1. The harm is a second receipt for the same delivery. In the trace, `Repeat` increments `seen` when `breakDedupe` is true. In the code, `submit_event` returns the existing row when `(user_id, source, delivery_id)` is already stored.

`ReceiveIsNotSuccess` says `success` implies `written`. The harm is marking external success because a receipt arrived. In the trace, `Receive`, `Passthrough`, or `RefuseStopped` sets `success` when `breakSuccess` is true. In the code, `submit_event` never calls `note_external_success`, and `_insert` refuses `state="external_success"`. `note_external_success` requires a claim whose status is `written` and whose `remote_request_id` matches.

`StoppedWritesNothing` says a `stopped` route has no card, no new generation, and no success. The harm is a write or a success mark on a stopped generation. In the trace, `RefuseStopped` opens a card and a generation when `breakStopped` is true. In the code, the `stopped` branch only inserts `state="received"` and `route="stopped"`.

`CompletedNotReused` says an opened route after a completed aim uses a new generation, not `gen`. The harm is pouring the event back into the finished generation. In the trace, `OpenAfterCompleted` sets `newGen` to `gen` when `breakReuse` is true. In the code, the `completed` branch calls `_open_task`, which calls `open_alongside` and does not pass `target.generation`.

`OneCard` says `cards` stays at most 1. The harm is a second writable card. In the trace, `Later` sets `cards` to 2 when `breakSecond` is true. In the code, `_while_waiting` queues after one `reissued` row exists for that generation, and `_reissue` cancels the open claims before it binds one new claim.

`QueueStaysOne` says a queued route still has one card and no success. The harm is a queued event executing or opening another card. The held model keeps `cards` at 1. In the code, the queue insert does not call `_reissue` and does not set `external_success`.

`PassthroughStays` says a passthrough route leaves `newGen` at 0. The harm is a second dispatch while the generation is still running. In the trace, `Passthrough` sets `newGen` to 2 when `breakPass` is true. In the code, `running` and `waiting_resource` insert `route="passthrough"` on `target.generation`.

`TypeOK` says the model variables stay inside the sets above. TLC also checks deadlock. A deadlock is a path that cannot settle.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one user and one delivery, with at most one later event and one repeat. A waiting generation starts with one open card. It does not model a second user, a mail fetch, the calendar HTTP call, a crash between the claim commit and the request, or a waiting generation that has no card. `search_meeting_mail` is not an event entry. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at acf29b7ec17298604eb7f1f68970380ae2a47eb9. Move the confirmation forward only after reading `submit_event`, `note_external_success`, `_route_act`, `_while_waiting`, and `search_meeting_mail`, and seeing that the model still describes them. If `submit_event` loses `if existing is not None:`, marks success itself, or `gmail.py` imports the inbox, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
