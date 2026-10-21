# Close the calendar competitor gaps

The person who books in chat can create, change, and cancel the signed-in Google calendar only after confirming. Five gaps remain against Claude, Cursor, and Gemini Workspace.

Phase 0 is pull request 55. Do not rewrite it. cal-bind-local then offers a confirm card for a local row that connected chat currently hides. Phase 1 is cal-invite-create and cal-invite-update. Phase 2 is cal-rsvp. Phase 3 is cal-meet. Phase 4 is cal-freebusy.

The operator lands every pull request. This run does not merge and does not deploy.

The first implement pull request after approval is cal-bind-local.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator merges. Owners stop at stack-ready. Pull request 55 is the operator's item. The six new pull requests stop at stack-ready too. Nobody in this program merges.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-full.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/shipping.md`
  - [ ] `git show origin/main:pstack/skills/how/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/interrogate/SKILL.md`
  - [ ] `git show origin/release:docs/plans/calendar-competitor-gaps.md`
- [ ] If a `git show origin/main:pstack/` command fails, read that file from the pstack plugin cache. This product repo does not contain those skills. Record the path you actually read.
- [ ] On the operator's go, arm the audit tick as `/loop 1h` with the tick prompt below. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported, such as a PR opened, a code-ready head, a round launched or closed, a verdict, a merge, a stuck agent and the action taken, a blocker added or cleared, or a decision only the operator can make. Name every such change and nothing else. Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text. Either way, log this tick's row in your decision trail. The row names the items reported, or none."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per pull request with the full lifecycle `playbooks/autopilot-stack.md` names. Owners report stack-ready. They do not merge, retarget, or rebase onto trunk.
- [ ] Follow this dependency graph. Base each child on its parent branch. Start the child only after the parent tip it names exists.
  - [ ] Pull request 55 is already open. Its head branch is `cursor/calendar-unbound-mutate-a393`. Do not open a second pull request for that work.
  - [ ] cal-bind-local branches from that head until pull request 55 is on `release`. After it merges, the parent is `release`.
  - [ ] cal-invite-create after cal-bind-local.
  - [ ] cal-invite-update after cal-invite-create.
  - [ ] cal-rsvp after cal-invite-update.
  - [ ] cal-meet after cal-rsvp.
  - [ ] cal-freebusy after cal-meet.
- [ ] Hold the file boundaries. cal-bind-local touches only `backend/app/services/calendar.py`, `backend/app/agents/tools/calendar_tool.py`, and `backend/tests/test_calendar_google.py`. cal-invite-create does not edit `_patch_body`. cal-invite-update does not edit `_insert_google_event`. cal-rsvp does not edit insert. cal-meet does not change who is invited. cal-freebusy does not edit the insert body.
- [ ] Hold the review gate. cal-bind-local, cal-invite-create, cal-invite-update, cal-rsvp, cal-meet, and cal-freebusy change the chat card. They wait for the operator's review in chat with screenshots and a video before the operator lands them.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`. If `command -v origin` succeeds and Origin can resolve the repository, use `origin pr` for every pull request operation. Record any fallback to `gh`. Never require `gt`.
- [ ] Open the pull request ready, never draft, per Opening a PR. Use the run's built-in pull request tool when it has one. Set draft to false. A stack child targets its parent branch.
- [ ] Run the repo's lint and typecheck once before the pull-request-facing push. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review. If the deslop skill is not installed, say so in the pull request and still remove the slop patterns by hand.
- [ ] Triage every Bugbot and security-reviewer comment per `pstack/skills/poteto-mode/references/bugbot-triage.md`.
- [ ] Rebase onto the parent tip before the code-ready report. Keep that merge base in fix rounds. Rebase again only at landing prep, on a `git merge-tree` conflict with the parent, or on a CI failure that comes from a change on the parent.

### Verdict and merge, for every PR

- [ ] A full swarm runs once, at the code-ready head. Another full swarm runs only for new behavior, a redesign, or a large new diff. A rebase, a merge-conflict resolution, or a minor fix gets a single verifier at the new head. Run each full swarm per `pstack/skills/swarm/SKILL.md`. One gates lane. The ten live lanes from the pull request's Verify, live block. The perf lane from its Verify, perf block. Two or more audit lanes, each with its own focus, that read the diff and the receipts and distrust the pull request body. The root audits the receipts in the stack-ready report before the verdict.
- [ ] Use `grok-4.7-high` for every swarm role. The operator locked every role to that model. Do not use Claude, a fast slug, or an xhigh slug.
- [ ] Clean only when every lane is `PASS`. Findings go back to the owner, including a defect that a lane filed as a note. A new head gets a fresh verdict, except for results that stay valid under the patch-id rule in `playbooks/shipping.md`.
- [ ] The owner reports stack-ready and does not merge. The root appends the pull request to the base-branch stack. The operator lands the stack bottom-up, using the patch-id rule in `playbooks/shipping.md`. A patch-id change after the verdict invalidates the lanes that the shipping playbook names.

### Boot recipe, for every live lane

Each live lane runs on its own cloud VM at the pull request head. The product control is the browser chat. `control-ui` from `cursor-team-kit` is the control skill. It was not installed in the plan run. Appendix C records that. If the skill is present, drive only through its commands. If it is absent, drive the same clicks in the browser and write the missing skill into the lane report. Do not treat a missing skill as a pass.

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Start the stack the way `README.md` starts it. `docker compose build` the images one at a time, migrate, then `docker compose up -d --no-build`. Wait until `curl -s http://127.0.0.1/health` returns success and `curl -s http://127.0.0.1/health/ready` returns 200.
- [ ] Open `http://127.0.0.1/` in the browser. Log in. Drive the chat and the schedule card by clicks and typing. Read-only diagnostics are the browser network log, `docker compose logs api`, and `curl` against `/health`. Do not call the Google Calendar API from the lane except through the product.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Bind a local row to a Google event, cal-bind-local

**Depends on.** Pull request 55, branch `cursor/calendar-unbound-mutate-a393`.

**Files.**

- [ ] Edit `upcoming_events` and `_row_has_identity` in `backend/app/services/calendar.py`.
- [ ] Edit `_mutate_from_selection` in `backend/app/agents/tools/calendar_tool.py`.
- [ ] Edit `backend/tests/test_calendar_google.py`.

**Build.**

- [ ] When Google writes are on and the read succeeded, keep a row that has a local id and an empty `external_id`. Today `_row_has_identity` drops that row, so chat cannot match it.
- [ ] In `_mutate_from_selection`, return the draft card for that row. Today that branch returns no card.
- [ ] Leave `external_id` empty on that card. `ScheduleCard` then calls `updateCalendarEvent`. `GoogleCalendarProvider.update_event` already inserts that row on the first confirmed edit.
- [ ] Say in the direct answer that confirm creates the Google event. Unconfirmed chat still writes nothing.
- [ ] Do not change the zero-match or many-match branch. Pull request 55 owns that branch. Do not send attendees. Do not call Google inside `execute_calendar`.

**You see.**

- [ ] A connected chat update of one local row with no Google id shows a draft card, and the network log has no calendar write before the click.
- [ ] After 确认修改, the card reads 已安排 and the saved event has a non-empty `external_id`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add `test_connected_local_row_offers_bind_card` in `backend/tests/test_calendar_google.py`. It expects a draft card, an empty `external_id`, no insert before confirm, and one insert after the confirm route. Run `cd backend && python -m pytest tests/test_calendar_google.py::test_connected_local_row_offers_bind_card tests/test_calendar_google.py::test_connected_chat_never_writes_google_before_confirm -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run a connected update of one local row that has an empty Google id, at trunk and at head. Trunk has no card. Record that. Gate the draft card, the absence of a write before the click, and 已安排 with a Google id after the click. Save `bind-regression.png`. Pass when trunk writes nothing and head shows the draft before the click and 已安排 after it.
- [ ] Lane 2. Open the draft and read the answer text. Save `bind-copy.png`. Pass when the answer says confirm creates the Google event and the button is 确认修改.
- [ ] Lane 3. Stay on the draft and wait. Save `bind-no-write.png`. Pass when the network log shows no `POST /api/calendar` and no `POST /api/calendar/google/confirm` before the click.
- [ ] Lane 4. Click 确认修改. Save `bind-confirm.png`. Pass when the card reads 已安排 and a following list shows the same title.
- [ ] Lane 5. Cancel that same local row before it has a Google id. Save `bind-cancel.png`. Pass when the card is a delete draft, the click does not call `/api/calendar/google/confirm`, and the row becomes cancelled.
- [ ] Lane 6. Ask to change a title that matches nothing. Save `bind-miss.png`. Pass when the behavior matches pull request 55. A non-writing draft if that pull request has landed, and no Google write either way.
- [ ] Lane 7. Ask to change a title that matches two upcoming events. Save `bind-many.png`. Pass when no single event is written and the card does not confirm a guessed id.
- [ ] Lane 8. Repeat the unique local-row update while Google is disconnected. Save `bind-offline.png`. Pass when confirm stays on the local row and the answer does not say a Google event was created.
- [ ] Lane 9. Put a conflicting event in the slot, then confirm the bind. Save `bind-overlap.png`. Pass when the card shows the conflict and the local row still has an empty `external_id`.
- [ ] Lane 10. Let the draft expire, then click confirm. Save `bind-expired.png`. Pass when the card reads 已过期 and the network log has no write.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Time from sending the update utterance to the draft card on screen, the count of Google writes before the click, and the time from the click to 已安排. Trunk has no card, so also record that trunk writes nothing. The end state the user waits for is 已安排 with a Google id.
- [ ] Probe. Interleave 5 trunk tries and 5 head tries of the same utterance. Record the card time, the write count, and the click-to-已安排 time on head. Both sides must record the write count. Trunk records no card.
- [ ] Baseline. Record the trunk write count first. It is zero, and there is no card.
- [ ] Rule. Fail if head writes before the click. Fail if the draft card is missing. Fail if the median click-to-已安排 time is over 5 seconds. Fail if that confirm issues more than one `events.insert`. Do not ratio the new card time against trunk, because trunk has no card.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 4 screenshots into `docs/plans/media/cal-bind-local-review-bind-regression.png` and `docs/plans/media/cal-bind-local-review-bind-confirm.png`.
- [ ] Record a 30 to 60 second video of the draft and the confirm on a lane VM. Save it as `docs/plans/media/cal-bind-local-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the parent tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request only after pull request 55, bottom-up.

## Invite guests when creating, cal-invite-create

**Depends on.** cal-bind-local.

**Files.**

- [ ] Edit `backend/app/services/calendar_google.py` at `_insert_google_event` and `create_event`.
- [ ] Edit `backend/app/services/calendar_provider.py` so the create contract allows invitees.
- [ ] Edit `backend/app/agents/tools/calendar_tool.py` and `frontend/src/components/ScheduleCard.tsx` so the card lists who will be invited.
- [ ] Edit `backend/app/eval/calendar_behavior.py` and add email rows in `backend/data/eval/calendar_behavior.jsonl`.
- [ ] Edit `backend/tests/test_calendar_google.py`.

**Build.**

- [ ] Parse participant input at the chat and HTTP boundary into invitees. Each invitee has `email` and `name`. A legacy stored string that contains `@` is an email. Any other legacy string is a name. Writers store JSON objects in the existing `participants` column. Do not add a column. Do not keep a second write format.
- [ ] On insert, send `attendees` only for invitees with a non-empty email. Omit `responseStatus` so Google treats them as `needsAction`. Set query `sendUpdates` to `all` only when that list is non-empty. A meeting with no email omits both, which is today's body.
- [ ] On the draft card, list emails under 将邀请 and bare names under 不邀请. A time block clears both and sends no attendees.
- [ ] Do not add a people directory. Do not edit `_patch_body` in this pull request. Do not call `freebusy.query`.

**You see.**

- [ ] The create card shows each email under 将邀请 and each bare name under 不邀请 before 确认创建.
- [ ] After confirm, the Google insert body contains those emails, and a bare name is only on the local row.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Extend `backend/tests/test_calendar_google.py` so an email is an attendee, a bare name is absent from the Google body, `sendUpdates` is `all` only for the email case, and a time block sends neither. Keep the existing name-only rows expecting no attendees. Run `cd backend && python -m pytest tests/test_calendar_google.py tests/test_calendar_provider.py tests/test_calendar_behavior.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Create a meeting with one email and one bare name, at trunk and at head. Trunk keeps both as local text and sends no attendees. Record that. Gate the 将邀请 line, the 不邀请 line, and a confirm body that includes the email. Save `invite-create-regression.png`. Pass when trunk shows no 将邀请 line and head shows both lines before the click.
- [ ] Lane 2. Type two emails and stop before the click. Save `invite-create-two.png`. Pass when both emails are under 将邀请 and the network log has no `POST /api/calendar`.
- [ ] Lane 3. Click 确认创建 for those two emails. Save `invite-create-confirm.png`. Pass when the card reads 已安排 and the confirm request carries both emails.
- [ ] Lane 4. Create a meeting with only the name 张三. Save `invite-create-name.png`. Pass when 张三 is under 不邀请 and the confirm request does not pretend that name is an email.
- [ ] Lane 5. Create a time block and type an email into chat. Save `invite-create-block.png`. Pass when the block form hides participants and the confirm body has an empty guest list.
- [ ] Lane 6. Create a meeting with no participants. Save `invite-create-empty.png`. Pass when the card has no 将邀请 line and the create still succeeds.
- [ ] Lane 7. Type `not an email` with spaces and no `@`. Save `invite-create-bad.png`. Pass when that text is under 不邀请 and is not sent as an attendee email.
- [ ] Lane 8. Disconnect Google and confirm a meeting that has an email. Save `invite-create-offline.png`. Pass when the row stays local and the answer does not say an invitation was sent.
- [ ] Lane 9. Choose a time that overlaps an existing event. Save `invite-create-overlap.png`. Pass when the conflict stays on the card and no insert is sent.
- [ ] Lane 10. Let the draft expire, then click 确认创建. Save `invite-create-expired.png`. Pass when the card reads 已过期 and the network log has no create.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Median time from the confirm click to 已安排, and the count of `events.insert` calls for one create. The attendee fields ride on that same insert. The end state the user waits for is 已安排 with the invited emails still listed.
- [ ] Probe. Interleave 5 trunk creates and 5 head creates of a meeting with one email. Record the click-to-已安排 time and the insert count on both sides. Trunk has no attendee fields. Both sides still create one event.
- [ ] Baseline. Record the trunk median and the trunk insert count first.
- [ ] Rule. Fail if the head median is more than 300 ms above the trunk median. Fail if head issues more than one `events.insert` for that create. Fail if the invited email is missing on the confirmed card.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 3 screenshots into `docs/plans/media/cal-invite-create-review-invite-create-regression.png` and `docs/plans/media/cal-invite-create-review-invite-create-confirm.png`.
- [ ] Record a 30 to 60 second video of the two guest lines and the confirm on a lane VM. Save it as `docs/plans/media/cal-invite-create-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the cal-bind-local tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request after cal-bind-local.

## Update guests on the Google event, cal-invite-update

**Depends on.** cal-invite-create.

**Files.**

- [ ] Edit `backend/app/services/calendar_google.py` at `update_event`, `_patch_body`, `_patch_google_event`, and `_participant_names`.
- [ ] Add `events.get` in that same file.
- [ ] Edit `backend/tests/test_calendar_google.py` at the participants-only patch test.

**Build.**

- [ ] When the user confirms a guest-list change on a row that already has `external_id`, patch that Google event. Today a participants-only change calls `LocalCalendarProvider.update_event` and skips Google.
- [ ] `GET` the event first. Patch `attendees` as the full confirmed email list. Google replaces the array when the field is present. Copy each remaining guest's `responseStatus` from the GET.
- [ ] Omit `attendees` on a title, time, location, or notes patch that does not change guests. An omitted array leaves existing guests in place.
- [ ] Set `sendUpdates` to `all` only on the patch that includes `attendees`. Keep the email on the local invitee when Google returns a display name, so the next edit does not drop that guest.
- [ ] If the row has no `external_id`, do not invent one here. cal-bind-local owns that confirm.

**You see.**

- [ ] Changing the emails on a linked event and clicking 确认修改 sends a patch whose `attendees` are exactly the emails on the card.
- [ ] A title-only confirm does not include `attendees` in the patch.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Replace the expectation that a participants-only patch makes no Google call. Expect one `events.get` and one `events.patch` whose attendees match the card. A title-only patch must still omit `attendees`. Run `cd backend && python -m pytest tests/test_calendar_google.py -q -k "update or prefill or participant"`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Change only the guests on a linked event, at trunk and at head. Trunk skips Google. Record that. Gate a patch that carries the card emails and no write before the click. Save `invite-update-regression.png`. Pass when trunk makes no guest patch and head shows the new email under 将邀请 before the click.
- [ ] Lane 2. Remove one of two emails and confirm. Save `invite-update-remove.png`. Pass when the confirm request lists the remaining email and the card no longer shows the removed one.
- [ ] Lane 3. Add an email to a linked event and confirm. Save `invite-update-add.png`. Pass when 已安排 still shows that email.
- [ ] Lane 4. Change only the title. Save `invite-update-title.png`. Pass when the confirm request omits a guest-list change and the previous emails remain on the card.
- [ ] Lane 5. Change guests, then cancel the draft without clicking confirm. Save `invite-update-draft.png`. Pass when the network log has no patch.
- [ ] Lane 6. Confirm a guest change while the Google read fails. Save `invite-update-read-fail.png`. Pass when the card shows an error and the local guest list is unchanged.
- [ ] Lane 7. Confirm a guest change on a local row that cal-bind-local has not bound. Save `invite-update-unbound.png`. Pass when the flow creates the Google event first and the emails are on that insert, with no patch to an empty id.
- [ ] Lane 8. Edit a time block that has no guests. Save `invite-update-block.png`. Pass when the form still hides participants and the patch has no attendees.
- [ ] Lane 9. Re-open the same linked event after a guest confirm. Save `invite-update-reopen.png`. Pass when the email is still on the card and was not replaced by a display name alone.
- [ ] Lane 10. Let the update draft expire, then click 确认修改. Save `invite-update-expired.png`. Pass when the card reads 已过期 and the network log has no patch.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Median time from the guest-change confirm click to 已安排, and the count of `events.get` plus `events.patch`. Trunk skips Google for this edit, so do not ratio the two confirms. The end state the user waits for is 已安排 with the confirmed emails.
- [ ] Probe. Interleave 5 trunk guest-only edits and 5 head guest-only edits. Record the click time and the Google call count on both sides.
- [ ] Baseline. Record the trunk call count first. It is zero Google calls for a participants-only edit.
- [ ] Rule. Fail if head issues more than one `events.get` and one `events.patch`. Fail if the median click-to-已安排 time is over 5 seconds. Fail if a title-only edit includes `attendees`.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 4 screenshots into `docs/plans/media/cal-invite-update-review-invite-update-regression.png` and `docs/plans/media/cal-invite-update-review-invite-update-title.png`.
- [ ] Record a 30 to 60 second video of a guest change and a title-only change on a lane VM. Save it as `docs/plans/media/cal-invite-update-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the cal-invite-create tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request after cal-invite-create.

## Answer an invitation, cal-rsvp

**Depends on.** cal-invite-update.

**Files.**

- [ ] Edit `backend/app/services/calendar_google.py` at the list mapping and the patch helper from cal-invite-update.
- [ ] Edit `backend/app/agents/tools/calendar_tool.py` so a list item carries the signed-in user's `responseStatus`.
- [ ] Edit `frontend/src/components/ScheduleCard.tsx` and `frontend/src/api/client.ts` to add 接受, 拒绝, and 暂定.
- [ ] Edit `backend/app/api/calendar.py` so the click patches that `external_id`.
- [ ] Edit `backend/tests/test_calendar_google.py`.

**Build.**

- [ ] Show 接受, 拒绝, and 暂定 only when the listed event has an `external_id` and the signed-in user is an attendee other than the organizer. The click is the write. `execute_calendar` must not patch.
- [ ] `GET` the event, change only the self attendee's `responseStatus` to `accepted`, `declined`, or `tentative`, and patch the full attendees array. Set `sendUpdates` to `all`.
- [ ] If `events.get` fails, show the error and do not patch. After a decline, the next list may hide the event through the existing `_self_declined` filter. The click's own card still shows the new status.
- [ ] Do not add a mail reader. This repo has no inbox. Do not set `responseStatus` on an outbound insert.

**You see.**

- [ ] An invitation on the calendar list has 接受, 拒绝, and 暂定.
- [ ] Clicking one of them updates that Google event and the card shows the new status, with no second confirm dialog.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add a test that a self attendee patch keeps every other attendee and changes only self `responseStatus`. A chat turn that says 接受 must not patch before the click. Run `cd backend && python -m pytest tests/test_calendar_google.py -q -k "rsvp or declined or attendee"`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Open a list that includes an event where the signed-in user is an attendee, at trunk and at head. Trunk has no RSVP buttons. Record that. Gate the three buttons and a patch after the click only. Save `rsvp-regression.png`. Pass when trunk shows no 接受 button and head shows all three before any write.
- [ ] Lane 2. Click 接受. Save `rsvp-accept.png`. Pass when the card shows the accepted status and the request carries that event's `external_id`.
- [ ] Lane 3. Click 暂定 on a second invitation. Save `rsvp-tentative.png`. Pass when the card shows tentative and the other attendees remain in the patch body the backend records.
- [ ] Lane 4. Click 拒绝. Save `rsvp-decline.png`. Pass when the click's card shows declined. Refresh the list. Pass when that event is hidden, matching `_self_declined`.
- [ ] Lane 5. Open an event the user organizes. Save `rsvp-organizer.png`. Pass when 接受, 拒绝, and 暂定 are absent.
- [ ] Lane 6. Open a local row with an empty `external_id`. Save `rsvp-local.png`. Pass when the three buttons are absent.
- [ ] Lane 7. Click 接受 while the Google read fails. Save `rsvp-read-fail.png`. Pass when the card shows an error and no patch is sent.
- [ ] Lane 8. Say 接受 in chat and do not click. Save `rsvp-chat.png`. Pass when the network log has no RSVP patch.
- [ ] Lane 9. Click 接受 twice. Save `rsvp-twice.png`. Pass when the second click stays accepted and does not duplicate the event.
- [ ] Lane 10. Disconnect Google and open the list. Save `rsvp-offline.png`. Pass when the three buttons are absent on local rows and the answer does not claim an RSVP was sent.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Median time from the RSVP click to the new status on the card, and the count of `events.get` plus `events.patch`. Trunk has no RSVP click, so do not ratio it. The end state the user waits for is the new status on that same event id.
- [ ] Probe. Interleave 5 trunk list opens and 5 head accepts. Record the trunk absence of a button, the head click time, and the Google call count.
- [ ] Baseline. Record the trunk result first. The list has no RSVP control and issues no RSVP patch.
- [ ] Rule. Fail if one click issues more than one `events.get` and one `events.patch`. Fail if the median click-to-status time is over 5 seconds. Fail if any other attendee's email is missing from the patch.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2 and lane 4 screenshots into `docs/plans/media/cal-rsvp-review-rsvp-accept.png` and `docs/plans/media/cal-rsvp-review-rsvp-decline.png`.
- [ ] Record a 30 to 60 second video of accept and decline on a lane VM. Save it as `docs/plans/media/cal-rsvp-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the cal-invite-update tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request after cal-invite-update.

## Add a Meet link, cal-meet

**Depends on.** cal-rsvp.

**Files.**

- [ ] Edit `_insert_google_event` and `_patch_google_event` in `backend/app/services/calendar_google.py` to send `conferenceData` and `conferenceDataVersion` `1`.
- [ ] Edit the list and insert readers in that file to copy `hangoutLink` or the video entry point onto the card.
- [ ] Edit `frontend/src/components/ScheduleCard.tsx` to add the control 添加 Google Meet.
- [ ] Edit `backend/tests/test_calendar_google.py`.

**Build.**

- [ ] Show 添加 Google Meet on a meeting create card, and on an update card whose event has no link yet. Leave it off until the user turns it on, or until the utterance asks for a meeting link. Hide it on a time block.
- [ ] When the control is on, send `conferenceData.createRequest` with a new `requestId` and solution type `hangoutsMeet`. Set query `conferenceDataVersion` to `1`. Retry the same `requestId` if that HTTP call is retried.
- [ ] If the response conference status is `pending`, the card says the link is not ready. Do not invent a URL. If a link already exists, do not send another `createRequest`.
- [ ] Read the link from `hangoutLink`, or from the video entry point when `hangoutLink` is empty. Return it on the card. Do not add a database column. Do not copy one event's conference onto another event. Do not change Meet settings. Do not add Zoom.

**You see.**

- [ ] Turning on 添加 Google Meet and confirming shows a Meet link on the card, or the pending text when Google has not finished.
- [ ] A meeting created with the control off has no conference field in the insert.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add tests that the control-on insert sends `conferenceDataVersion` `1` and `hangoutsMeet`, the control-off insert omits `conferenceData`, a pending response does not fabricate a URL, and a second confirm does not send a new `createRequest` once a link exists. Run `cd backend && python -m pytest tests/test_calendar_google.py -q -k "conference or meet or insert"`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Create a meeting without asking for a link, at trunk and at head. Both must omit conference data. Record trunk. Then turn the control on at head only. Gate a link or the pending text after confirm. Save `meet-regression.png`. Pass when the off state matches trunk and the on state shows the control before the click.
- [ ] Lane 2. Turn the control on and do not click confirm. Save `meet-draft.png`. Pass when the network log has no insert.
- [ ] Lane 3. Click 确认创建 with the control on. Save `meet-confirm.png`. Pass when the card shows a `meet.google` link or the pending text, and the phase is 已安排.
- [ ] Lane 4. Create the same meeting with the control off. Save `meet-off.png`. Pass when the confirmed card has no Meet link.
- [ ] Lane 5. Ask in chat for a meeting link. Save `meet-utterance.png`. Pass when the draft opens with the control already on, and no insert happens before the click.
- [ ] Lane 6. Create a time block. Save `meet-block.png`. Pass when 添加 Google Meet is hidden.
- [ ] Lane 7. Add a link to an existing meeting that has none. Save `meet-update.png`. Pass when confirm patches that `external_id` and the card shows a link or the pending text.
- [ ] Lane 8. Confirm again on an event that already has a link. Save `meet-again.png`. Pass when the same link remains and the request does not contain a new `createRequest`.
- [ ] Lane 9. Force a pending conference response. Save `meet-pending.png`. Pass when the card does not show a made-up URL.
- [ ] Lane 10. Let the draft expire with the control on. Save `meet-expired.png`. Pass when the card reads 已过期 and the network log has no insert.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Median time from confirm to a link or the pending text, and the count of inserts. The conference fields ride on the same insert as the event. The end state the user waits for is the link or the pending text on 已安排.
- [ ] Probe. Interleave 5 trunk creates with no link and 5 head creates with the control on. Record click time and insert count on both sides.
- [ ] Baseline. Record the trunk median and insert count first. Trunk creates the event and returns no Meet link.
- [ ] Rule. Fail if head issues more than one `events.insert` for that create. Fail if the median click-to-result time is more than 300 ms above the trunk median. Fail if the card shows a URL that was not in the Google response.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 3 and lane 4 screenshots into `docs/plans/media/cal-meet-review-meet-confirm.png` and `docs/plans/media/cal-meet-review-meet-off.png`.
- [ ] Record a 30 to 60 second video of the control off, then on, then confirm, on a lane VM. Save it as `docs/plans/media/cal-meet-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the cal-rsvp tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request after cal-rsvp.

## Propose a mutual open time, cal-freebusy

**Depends on.** cal-meet.

**Files.**

- [ ] Edit `GOOGLE_CALENDAR_SCOPES` in `backend/app/services/calendar_secrets.py`.
- [ ] Edit `find_free_slots` in `backend/app/services/calendar_google.py` to call `freebusy.query` when the grant includes the new scope.
- [ ] Edit the connect scope test in `backend/tests/test_calendar_google.py`.
- [ ] Edit the suggest card path in `backend/app/agents/tools/calendar_tool.py` and the slot picker in `frontend/src/components/ScheduleCard.tsx`.

**Build.**

- [ ] Add scope `https://www.googleapis.com/auth/calendar.events.freebusy`. Do not add `https://www.googleapis.com/auth/calendar` or `https://www.googleapis.com/auth/calendar.readonly`.
- [ ] If the saved grant lacks the new scope, the suggest answer says 重新连接 Google 日历 and does not call `freeBusy`.
- [ ] `POST https://www.googleapis.com/calendar/v3/freeBusy` with `timeMin`, `timeMax`, `timeZone`, and `items` for `primary` plus each invitee email. Skip a name that has no email and say that name was not checked. Cap `items` at 50.
- [ ] If a calendar returns `notFound`, that person is not free. Say whose free time is hidden. Intersect the busy blocks with the existing working-hours cut. Propose at most 3 slots on the existing slot control. The user picks one, then the existing confirm creates the event. Do not auto-book.

**You see.**

- [ ] A request for a time that works for named emails shows up to 3 slots, or a reconnect message when the grant is old.
- [ ] Picking a slot fills the card. Nothing is written until 确认创建.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add tests that a grant without the new scope makes no `freeBusy` call, a `notFound` calendar is not treated as free, and the proposed slots do not overlap any returned busy block. Update the connect test that currently rejects `calendar.events.freebusy`. Run `cd backend && python -m pytest tests/test_calendar_google.py -q -k "scope or free or slot"`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Ask for a free slot with no guests, at trunk and at head. Both use the signed-in calendar only. Record trunk. Then ask head for a slot with two emails. Gate the reconnect message or the slots, and no write before a later confirm. Save `freebusy-regression.png`. Pass when the no-guest request still returns slots and the two-email request does not write an event by itself.
- [ ] Lane 2. With an old grant, ask for a mutual slot. Save `freebusy-reconnect.png`. Pass when the answer says 重新连接 Google 日历 and the network log has no `freeBusy` call from the product.
- [ ] Lane 3. After reconnect, ask again with two emails that are free. Save `freebusy-slots.png`. Pass when the card shows 1 to 3 slots and no create request.
- [ ] Lane 4. Pick the first slot. Save `freebusy-pick.png`. Pass when the draft times match that slot and the network log still has no create.
- [ ] Lane 5. Click 确认创建 on the picked slot. Save `freebusy-book.png`. Pass when the card reads 已安排 and the create body includes the two emails.
- [ ] Lane 6. Ask with one email whose calendar returns `notFound`. Save `freebusy-hidden.png`. Pass when the card says that person's free time is hidden and does not offer a slot that ignores them.
- [ ] Lane 7. Ask with a bare name and one email. Save `freebusy-name.png`. Pass when the card says the bare name was not checked and the query items omit that name.
- [ ] Lane 8. Ask for a slot when everyone is busy all day. Save `freebusy-none.png`. Pass when the card shows no slot and does not create an event.
- [ ] Lane 9. Disconnect Google and ask for a mutual slot. Save `freebusy-offline.png`. Pass when the answer does not invent a mutual slot.
- [ ] Lane 10. Pick a slot, let the draft expire, and click confirm. Save `freebusy-expired.png`. Pass when the card reads 已过期 and the network log has no create.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Median time from the mutual-slot utterance to the slots or the hidden-calendar message, and the count of `freeBusy` calls. Trunk has no `freeBusy` call, so do not ratio this against trunk's `events.list` suggest. The end state the user waits for is the slot list, with no event written.
- [ ] Probe. Interleave 5 trunk no-guest suggests and 5 head two-email suggests. Record trunk's lack of a `freeBusy` call, the head utterance-to-slots time, and the head call count.
- [ ] Baseline. Record the trunk call count first. It is zero `freeBusy` calls.
- [ ] Rule. Fail if one suggest issues more than one `freeBusy` call. Fail if the median utterance-to-slots time is over 5 seconds. Fail if any proposed slot overlaps a busy block from that response.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 3 and lane 5 screenshots into `docs/plans/media/cal-freebusy-review-freebusy-slots.png` and `docs/plans/media/cal-freebusy-review-freebusy-book.png`.
- [ ] Record a 30 to 60 second video of the slots, the pick, and the confirm on a lane VM. Save it as `docs/plans/media/cal-freebusy-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the cal-meet tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request after cal-meet.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with the stack-ready report `playbooks/autopilot-stack.md` names. Include the stack root, the stack tip, a one-line verdict per pull request, and anything parked with the reason.

## Appendix A. Prototype evidence

No prototype was built. The operator forbade product code and asked for a read-only pass. The open questions are facts in the repo and in pull request 55. The prototype playbook says no decision means no prototype.

Unproven, because this run did not call Google.

- A second Google account was not used, so guest delivery is taken from the create-events guide. That guide says the event appears on the attendee's primary calendar, and `sendUpdates` `all` or `externalOnly` also sends mail.
- A live token with only `calendar.events` was not sent to `freeBusy`. The freebusy method page does not list `calendar.events` as a scope. The plan treats that scope as insufficient.
- A live Meet create was not sent. The create-events guide says the first response may be `pending`. The plan shows pending text and does not invent a URL.
- Pull request 55 was read from the diff. Its tests were not executed in this checkout.

## Appendix B. Alternatives rejected

A rewrite of pull request 55 lost. The diff already stops an unbound confirm. The remaining hole is one branch it does not touch. cal-bind-local edits that branch.

Resolving a bare name to an email lost. The repo has no people directory. Inventing an address would invite the wrong person. The card says the name is not invited.

Putting the Meet URL in `description` lost. Notes and the link would be one field. The link stays on the card, read from the Google response, with no new column.

Auto-booking the first mutual slot lost. The product rule is confirm before write. The user picks a slot, then 确认创建 writes.

Adding the full `calendar` scope for free-busy lost. `calendar.events.freebusy` is on the freebusy method page. The connect test already rejects the full calendar scope. Widening to that scope would ask for more than this program uses.

An inbox RSVP lost. A search of `backend/app` and `frontend/src` found no mail product. RSVP stays on the calendar list.

Always attaching Meet lost. A time block and a meeting that did not ask for a link would gain a conference. The control stays off until the user turns it on or the utterance asks.

## Appendix C. Risks

`control-ui` and the deslop skill were not in the plan environment. They ship in `cursor-team-kit`, which was not installed. Live lanes still drive the browser chat. A lane that cannot drive the browser fails.

Pull request 55 can move. Owners re-read its head SHA before branching cal-bind-local. Measured on 2026-10-20, it was open and ready for review, mergeable, and clean, at `f60a9df82b63ee3e0dd1dd8ec6ee45b05e791e0f`.

cal-bind-local can confirm a local row into a new Google event. The answer text has to say that before the click. A silent insert would surprise the user. Editing only `_mutate_from_selection` does nothing, because `upcoming_events` already dropped the row.

cal-invite-create sends mail when `sendUpdates` is `all`. The 将邀请 lines are the warning. A bug that invites on a time block, or before the click, is the failure to watch.

cal-invite-update replaces the attendees array. A patch that sends a partial list deletes the guests it omits. The GET-then-full-list step is the guard. A title-only patch must omit the field.

cal-rsvp uses that same array replace. The failure to watch is a patch that drops other guests or that writes from the chat tool before the click.

cal-meet can return `pending`. Showing a guessed URL is the failure to watch. Reusing one conference across events is the other failure. Google's guide says that exposes the meeting.

cal-freebusy is wrong when a `notFound` calendar is treated as free. Old grants must reconnect. Do not call `freeBusy` with a token that lacks the scope.

The eval suite still expects an empty Google guest list for bare names. Email cases are new rows. Do not flip `CAL-137` through `CAL-139` into emails. Smoke replays a model cache. Follow `docs/eval.md` when a new row has no cache entry.

A real guest calendar is not visible from this repo's tests. The live lanes prove the card and the request the browser sends. Guest receipt stays on the unit fake plus the operator's own second account when they have one. Do not mark a lane passed because the fake returned 200.

## Appendix D. Links and reading list

Read these before editing.

- `docs/plans/calendar-google-integration.md` sections 4 and 6. They currently forbid attendees and `sendUpdates`. The invite pull requests replace that decision. Do not leave the old sentence in place.
- `docs/eval.md` for `python scripts/eval.py run calendar --profile smoke` and `--profile full`.
- `README.md` for the compose boot the live lanes use.
- Google Calendar `events.insert`, `events.patch`, the create-events guide, and `freebusy.query`. Measured pages are the Workspace Calendar v3 reference and the create-events guide. Insert and patch accept scope `calendar.events`. `conferenceDataVersion` defaults to 0 and ignores conference data until it is 1. `freebusy.query` is `POST /calendar/v3/freeBusy` and accepts `calendar.events.freebusy`.

`pstack/skills/how/SKILL.md` applies to cal-invite-create, cal-rsvp, cal-meet, and cal-freebusy. Those four choose a data shape or a write policy. `pstack/skills/interrogate/SKILL.md` applies to cal-invite-create and cal-rsvp. Those two replace an attendees array and can email people.

The decision trail for this plan run is the local `decisions.tsv` from `pstack/skills/show-me-your-work/SKILL.md`. It stays out of git. The execution program keeps its own trail when the operator says go.

## Appendix E. Current capability

This appendix is the lookup for the five gaps. It describes the release checkout plus pull request 55. It is not the how-to.

Stable id on update and cancel. A connected read keeps a row only when `external_id` is non-empty. That filter is `upcoming_events` and `_row_has_identity`. A unique hit copies that id onto the draft. Confirm posts it to `/api/calendar/google/confirm`. On release, a missing id is HTTP 400 with detail 需要日程编号. `_event_for_confirm` selects by that id and ignores the title. `_event_for_title` has no callers. A visible chat update already targets the Google event.

Pull request 55 changes the miss. When no unique event matches, release returns no card. The pull request returns a draft with an empty `external_id` and changes the missing-id confirm to HTTP 409 with detail 需要日程编号，未写入。 Eval row `CAL-057` already expects that draft, an absent Google id, and HTTP 409. Release tests still expect no card. Landing the pull request is what makes the suite and the code agree. That draft has no local id either, so the card stops at 还没有选中日程 and does not post. The 409 is the API backstop. Leave that card non-writing. The miss branch runs whether or not Google writes are on, so an unconnected miss with a title gets the same draft. The unbound update does not fill conflicts. Leave both of those in pull request 55. A card that must not write should not grow a slot that looks bookable. A blank query still returns no card. That is correct.

A local row with an empty `external_id` never reaches the match while the Google read succeeded. Direct `PATCH /api/calendar/{id}` on that row inserts a new Google event. cal-bind-local keeps the row in the connected list and returns a confirm card so the person can bind it on purpose. Cancel of that row stays local.

Real invites. `participants` is a JSON list of strings on `calendar_events`. Insert and patch send `summary`, `location`, `description`, `start`, and `end` only. The insert comment says no attendees and no `sendUpdates`. A participants-only update never calls Google. Inbound attendees are flattened to a display name, or to the email only when the display name is empty. The provider contract says an external provider must not turn participants into invitations. The `attendees` eval slice locks an empty Google guest list. Names such as 张三 stay local. There is no people directory.

RSVP. Nothing writes `accepted`, `declined`, or `tentative`. `_self_declined` hides an event the signed-in user already declined. `_participant_names` drops another person's declined row from the copied names. There is no mail product to answer an invitation from.

Meet. `conferenceData`, `hangoutLink`, and `conferenceDataVersion` are absent. Search covered Python, TypeScript, and the docs.

Mutual free-busy. `find_free_slots` unions local rows with one primary `events.list`. `freebusy.query` is absent from product code. The integration plan says not to request `calendar.events.freebusy`. Granted scopes are `calendar.events` and `calendar.settings.readonly`, plus `openid` and `email`. The connect test rejects the full calendar scope and the freebusy scope.

Confirm before write. `execute_calendar` returns a card and does not write. The card button is the write. `确认创建` posts `/api/calendar`. `确认修改` and `确认删除` post `/api/calendar/google/confirm` when `external_id` is set, and otherwise patch or delete the local id.

Google methods called today are `events.insert`, `events.patch`, `events.delete`, `events.list`, `settings.list`, timezone `settings.get`, and `calendars.get`. `events.get` and `freebusy.query` are not called.

## Appendix F. Non-goals

Outlook, Exchange, and any calendar other than Google are out. Digests and a second sync design are out. Zoom is out. Meet settings are out. A people directory is out. Gmail and any inbox are out. Recurring events, rooms, and group expansion are out. Inviting guests on a time block is out. Auto-booking a slot is out. Rewriting pull request 55 is out. Deploying is out. Merging is out.

## Appendix G. First implement pull request

After the operator approves this plan, the first code pull request is cal-bind-local. Branch it from `cursor/calendar-unbound-mutate-a393` until pull request 55 is on `release`. Visible chat updates already target a Google id. This slice is the local row connected chat hides. It keeps that row in `upcoming_events` and returns a confirm card that inserts it on Google. Pull request 55 does not do that.

If the operator wants guest invites first instead, cal-invite-create can branch from `release` after pull request 55 merges. It does not need the local-row card. Say that in plain words and the stack order changes.
