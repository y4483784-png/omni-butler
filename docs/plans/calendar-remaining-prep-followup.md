# Finish meeting prep and follow-up

The person who prepares a meeting in chat gets related Drive docs on the same brief. The person who follows up gets minutes and action items only after a confirm click. Omni does not join the call to record.

Four pull requests land in order. `cal-invitee-roundtrip`, then `cal-prep-drive`, then `cal-follow-paste`, then `cal-follow-official`. The operator lands the stack. This document does not implement the knives.

Shipped calendar confirm, RSVP, Meet links, free-busy, and Gmail prep stay shipped. Chat still writes nothing before a click.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator merges. Owners stop at stack-ready. All four pull requests are the operator's items. Nobody in this program merges.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-full.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:cursor-team-kit/skills/control-ui/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/shipping.md`
  - [ ] `git show origin/main:pstack/skills/how/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/interrogate/SKILL.md`
  - [ ] `git show origin/release:docs/plans/calendar-remaining-prep-followup.md`
- [ ] If a `git show origin/main:pstack/` command fails, read that file from the pstack plugin cache. This product repo does not contain those skills. Record the path you actually read.
- [ ] On the operator's go, arm the audit tick as `/loop 1h` with the tick prompt below. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported, such as a PR opened, a code-ready head, a round launched or closed, a verdict, a merge, a stuck agent and the action taken, a blocker added or cleared, or a decision only the operator can make. Name every such change and nothing else. Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text. Either way, log this tick's row in your decision trail. The row names the items reported, or none."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per pull request with the full lifecycle `playbooks/autopilot-stack.md` names. Owners report stack-ready. They do not merge, retarget, or rebase onto trunk.
- [ ] Follow this dependency graph. Base each child on its parent branch. Start the child only after the parent tip it names exists.
  - [ ] `cal-invitee-roundtrip` branches from `release`.
  - [ ] `cal-prep-drive` after `cal-invitee-roundtrip`.
  - [ ] `cal-follow-paste` after `cal-prep-drive`.
  - [ ] `cal-follow-official` after `cal-follow-paste`.
- [ ] Hold the file boundaries. `cal-invitee-roundtrip` does not add a scope and does not edit `meeting_prep.py`. `cal-prep-drive` does not add `CalendarAction.FOLLOWUP` and does not edit `FollowupCard`. `cal-follow-paste` does not call Drive and does not add `gmail.send`. `cal-follow-official` does not change the paste confirm click and does not add a send route.
- [ ] Hold the review gate. All four pull requests change a chat card. They wait for the operator's review in chat with screenshots and a video before the operator lands them.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`. If `command -v origin` succeeds and Origin can resolve the repository, use `origin pr` for every pull request operation. Record any fallback to `gh`. Never require `gt`.
- [ ] Open the pull request ready, never draft, per Opening a PR. Use the run's built-in pull request tool when it has one. Set draft to false. A stack child targets its parent branch. The product base for the root is `release`.
- [ ] Run the repo's lint and typecheck once before the pull-request-facing push. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review. If the deslop skill is not installed, say so in the pull request and still remove the slop patterns by hand.
- [ ] Triage every Bugbot and security-reviewer comment per `pstack/skills/poteto-mode/references/bugbot-triage.md`.
- [ ] Rebase onto the parent tip before the code-ready report. Keep that merge base in fix rounds. Rebase again only at landing prep, on a `git merge-tree` conflict with the parent, or on a CI failure that comes from a change on the parent.

### Verdict and merge, for every PR

- [ ] A full swarm runs once, at the code-ready head. Another full swarm runs only for new behavior, a redesign, or a large new diff. A rebase, a merge-conflict resolution, or a minor fix gets a single verifier at the new head. Run each full swarm per `pstack/skills/swarm/SKILL.md`. One gates lane. The ten live lanes from the pull request's Verify, live block. The perf lane from its Verify, perf block. Two or more audit lanes, each with its own focus, that read the diff and the receipts and distrust the pull request body. The root audits the receipts in the stack-ready report before the verdict.
- [ ] Use `grok-4.7-high` for every swarm role. The repo rule in `.cursor/rules/pstack-models.mdc` locks every role to that model. Do not use Claude, a fast slug, or an xhigh slug for the knife program. The Claude medium seats used to write this plan do not carry into execution.
- [ ] Clean only when every lane is `PASS`. Findings go back to the owner, including a defect that a lane filed as a note. A new head gets a fresh verdict, except for results that stay valid under the patch-id rule in `playbooks/shipping.md`.
- [ ] The owner reports stack-ready and does not merge. The root appends the pull request to the base-branch stack. The operator lands the stack bottom-up, using the patch-id rule in `playbooks/shipping.md`. A patch-id change after the verdict invalidates the lanes that the shipping playbook names.

### Boot recipe, for every live lane

Each live lane runs on its own cloud VM at the pull request head. The product control is the browser chat. `control-ui` from `cursor-team-kit` is the control skill. It was not installed when this plan was written. Appendix C records that. If the skill is present, drive only through its commands. If it is absent, drive the same clicks in the browser and write the missing skill into the lane report. Do not treat a missing skill as a pass.

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Start the stack the way `README.md` starts it. `docker compose build` the images one at a time, migrate, then `docker compose up -d --no-build`. Wait until `curl -s http://127.0.0.1/health` returns success and `curl -s http://127.0.0.1/health/ready` returns 200.
- [ ] Open `http://127.0.0.1/` in the browser. Log in. Drive the chat and the schedule card by clicks and typing. Read-only diagnostics are the browser network log, `docker compose logs api`, and `curl` against `/health`. Do not call Google from the lane except through the product. Point the lane at a fake Google client when the test names one, and say so in the lane report.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Round-trip a guest name with the email, cal-invitee-roundtrip

**Depends on.** None. Branch from `release`.

**Files.**

- [ ] Edit `Invitee`, `parse_invitee`, `attendee_body`, `dump_invitees`, and `invitee_label` in `backend/app/services/calendar_provider.py`.
- [ ] Replace the label-only read in `_participant_names` and keep `displayName` inside `merged_attendees` in `backend/app/services/calendar_google.py`.
- [ ] Edit `_base_card` and `_item_payload` in `backend/app/agents/tools/calendar_tool.py` so `participants` is a list of `{email, name}` and `guest_emails` plus `guest_names` are deleted.
- [ ] Edit `CalendarWrite` and `GoogleTitleConfirm` in `backend/app/api/calendar.py`, and `serialize_event_card` in `backend/app/services/calendar.py`, so the HTTP boundary parses a string or an object into `Invitee`.
- [ ] Edit `guestToWire`, `guestsFromWire`, and `participantsForSubmit` in `frontend/src/components/ScheduleCard.tsx`, plus the participant types in `frontend/src/api/client.ts`.
- [ ] Update the invitee tests in `backend/tests/test_calendar_google.py` and `backend/tests/test_calendar_provider.py`, and the card test in `frontend/tests/scheduleCard.test.mjs`.

**Build.**

- [ ] Make `Invitee` reject a value whose email and name are both empty after stripping. Reject an email that contains no `@`. Trust the type after `parse_invitee`.
- [ ] Teach `parse_invitee` to split `Name <email>` before the bare `@` branch. A string that contains `@` and does not match that shape stays email-only, which is today's behavior for a bare address.
- [ ] Emit `{"email", "displayName"}` from `attendee_body` when the name is non-empty. Omit `displayName` when the name is empty. Still omit a name-only person from the Google body. Still store that name on the local row.
- [ ] Return `list[Invitee]` from the Google read that replaces `_participant_names`. Skip `self` and `responseStatus == "declined"` as today. When Google sends both `email` and `displayName`, keep both. Do not prefer the email and drop the name.
- [ ] On `merged_attendees`, keep `responseStatus`. A non-empty local name wins `displayName`. Otherwise keep the `displayName` Google already stored.
- [ ] Change the card wire so every participant is `{"email": "...", "name": "..."}` with both keys present. `guestsFromWire` still accepts a legacy string from a stored message. `guestToWire` emits only the object, so a name typed beside an email survives 确认创建.
- [ ] Delete `guest_emails` and `guest_names` from `_base_card` in the same change. The client already ignored them and rebuilt guests from `participants`.
- [ ] Leave `invitee_label` as caption text. It is not the stored person and not the Gmail search input after this pull request. Search atoms that need both fields wait for `cal-prep-drive`.
- [ ] Do not add an OAuth scope. Do not change `GOOGLE_REQUESTED_SCOPES`. Do not change `MeetingBriefCard`.

**You see.**

- [ ] The add-guest dialog accepts 张三 and `zhangsan@example.com` together. After 确认创建, the list row shows 张三, and a later edit still shows that name beside the email.
- [ ] A name with no email still reads 不邀请 and is absent from the Google attendees array.
- [ ] An email with no name still invites that address and does not invent a display name.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add `test_display_name_round_trips_through_create_and_list` in `backend/tests/test_calendar_google.py`. It expects `displayName` on insert, both fields on the next list, no new scope string, and an unchanged `CalendarEvent` count before the confirm route. Run `cd backend && python -m pytest tests/test_calendar_google.py tests/test_calendar_provider.py -q -k "invite or participant or display or attendee"`.
- [ ] Extend `frontend/tests/scheduleCard.test.mjs` so `guestToWire` of a named invite returns both keys and `guestsFromWire` of a legacy string still builds a guest. Run `cd frontend && npm test`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Create a meeting with 张三 and `zhangsan@example.com`, at trunk and at head. Trunk drops the name on submit. Record that. Gate the head card showing both before the click, no write before the click, and both fields after 确认创建. Save `name-regression.png`. Pass when trunk writes nothing before its own click and head shows 张三 beside the email after confirm.
- [ ] Lane 2. Open the add-guest dialog and submit a name plus an email. Save `name-dialog.png`. Pass when the draft row says 将邀请 and the name is still visible.
- [ ] Lane 3. Confirm that draft and read the request body in the network log. Save `name-google-body.png`. Pass when the attendees entry has `displayName` 张三 and the log has one create.
- [ ] Lane 4. List the same day after confirm. Save `name-readback.png`. Pass when the row shows 张三 and not only the email.
- [ ] Lane 5. Add a guest with a name and a blank email. Save `name-only.png`. Pass when the row says 不邀请 and confirm does not put that person in `attendees`.
- [ ] Lane 6. Add a guest with only an email. Save `email-only.png`. Pass when confirm invites that email and the card does not invent a name.
- [ ] Lane 7. Create a block, not a meeting. Save `name-block.png`. Pass when the guest control is hidden and `participants` is empty.
- [ ] Lane 8. Open a stored message whose `participants` are still strings. Save `name-legacy.png`. Pass when the card renders and does not throw.
- [ ] Lane 9. RSVP 接受 on an invite that has a Google id. Save `name-rsvp.png`. Pass when the click still hits `/api/calendar/google/rsvp` and chat text 接受 does not patch.
- [ ] Lane 10. Run 会前准备 on a meeting whose attendee has both fields. Save `name-prep.png`. Pass when the brief still has no confirm button and the attendee line does not drop the meeting.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of `displayName` fields on one create of 20 named guests, the time to parse those 20 values in `parse_invitee`, and the user-visible name on the card. Trunk omits `displayName`. Also record the trunk write count before confirm, which is zero.
- [ ] Probe. Interleave 5 trunk creates and 5 head creates of the same 20-guest draft on the fake Google client. Record `displayName` count, parse time, and pre-confirm write count on both sides.
- [ ] Baseline. Record the trunk `displayName` count first. It is zero.
- [ ] Rule. Fail if head omits `displayName` for a guest that has a name. Fail if parse of 20 guests takes over 50 milliseconds. Fail if either side writes before the click. Do not ratio a new name label against trunk, because trunk has no name to show.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 4 screenshots into `docs/plans/media/cal-invitee-roundtrip-review-name-regression.png` and `docs/plans/media/cal-invitee-roundtrip-review-name-readback.png`.
- [ ] Record a 30 to 60 second video of the dialog, the confirm, and the list on a lane VM. Save it as `docs/plans/media/cal-invitee-roundtrip-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current `release` after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request first.

## Add Drive docs to the meeting brief, cal-prep-drive

**Depends on.** `cal-invitee-roundtrip`.

**Files.**

- [ ] Create `backend/app/services/meeting_signals.py` and move `_topic_terms` plus `_GENERIC_TERMS` out of `backend/app/services/gmail.py`.
- [ ] Change `search_meeting_mail` and `_queries` in `backend/app/services/gmail.py` to take `MeetingSignals`.
- [ ] Create `backend/app/services/drive.py` with `search_meeting_docs` only. Leave official-minutes reads for `cal-follow-official`.
- [ ] Edit `prepare_meeting`, the `Prep` union, `to_wire`, and `caption` in `backend/app/services/meeting_prep.py`.
- [ ] Add `GOOGLE_DRIVE_SCOPES` in `backend/app/services/calendar_secrets.py` and a `drive_access` flag beside `_mail_access`.
- [ ] Edit `MeetingBriefCard` in `frontend/src/components/MeetingBriefCard.tsx`, `MeetingBrief` in `frontend/src/api/client.ts`, and `frontend/tests/briefingCard.test.mjs`.
- [ ] Update `test_connect_redirects_with_offline_consent_and_exact_scopes` in `backend/tests/test_calendar_google.py`, and add `backend/tests/test_drive_meeting_query.py`.
- [ ] Update the stale scope comments in `README.md` and `.env.example` so they match `GOOGLE_REQUESTED_SCOPES`.

**Build.**

- [ ] Build one `MeetingSignals` from the picked `MeetingFace`. Fields are title, `tuple[Invitee, ...]`, attachment ids already on the event, and the start day. Both `search_meeting_mail` and `search_meeting_docs` take that value. Do not let Drive compile its own generic-title list.
- [ ] Keep the Gmail query as one OR of email, quoted name, and title, with the same suffix and the same 120 day then 365 day widen. A person who now has both fields contributes email atoms and name atoms. Cap stays `_ATTENDEE_CAP` of 10.
- [ ] Add `https://www.googleapis.com/auth/drive.readonly` to `GOOGLE_REQUESTED_SCOPES` and not to `GOOGLE_CALENDAR_SCOPES`. `_require_scopes` still saves a grant that lacks Drive. `include_granted_scopes` stays false. `prompt` stays `consent`.
- [ ] Gate every Drive call with `scoped_access` on that scope. No HTTP when the grant lacks it. Map a missing scope to the docs section `needs_consent`. Map HTTP 503 and other non-auth failures to `unavailable`. Do not use the reconnect sentence for `unavailable`.
- [ ] Read attachments with `files.get` by id, at most 5. Then one `files.list` only when a private query value exists. The query requires a non-generic title atom. Prefer title atoms AND owner emails when both exist. If that returns nothing, one title-only list. Never an attendee-only list. Never a match-all list. Never a second page. `pageSize` is 5. `trashed=false`. `modifiedTime` is after the start day minus 120 days.
- [ ] Reject an empty query in the type that `files.list` accepts, so a caller cannot pass `""`. When the compiler returns nothing, return the empty docs section and make zero `files.list` calls.
- [ ] For at most 3 Google Docs, `files.export` as `text/plain` and copy at most 2 sentences with the same substring rule `_sentences` uses for mail. Other mime types contribute title and link only. Do not call a model to summarize. Do not put doc text into the mail focus list.
- [ ] Replace `Ready | EmptyMail | NeedsConsent | MailUnavailable` with one picked brief that holds a mail section and a docs section. Each section is found, empty, needs consent, or unavailable. `choose` and `not_found` stay, and they do not search. A picked brief uses `status` `ready` even when a section is empty, so `empty` cannot mean "no mail" while docs exist.
- [ ] Parse stored briefs in `briefFromWire`. Old cards have top-level `emails` and a mail `status` of `empty`, `needs_consent`, or `unavailable`, plus string attendees. The renderer sees one shape.
- [ ] Show 相关邮件 as today and 相关文档 under it. Badge the match reason as 日程附件, 标题和参会人, or 标题. One `connectGoogle` button when either section needs consent. The label names Gmail, Drive, or both. The card still has no confirm control and no `/api/calendar` call.
- [ ] Captions. Both empty says 找到了「{title}」，没有相关邮件，也没有相关云端文档。 Docs empty only appends 没有找到相关文档。 Drive consent while the calendar is connected says 要查相关文档，需要授权读取云端硬盘。日历连接会保留。 Drive down says 暂时读不到云端硬盘。这次没有文档。
- [ ] Do not add a Drive tool, a router flag, or an `allowed_tools` entry. `execute_calendar` still returns on `prep` before any calendar write.

**You see.**

- [ ] 会前准备 on a meeting with a related Doc shows the mail block and the doc block on one card. The doc title is a link. A Google Doc also shows up to two copied sentences.
- [ ] A meeting with no matching file still names the meeting and says there is no related doc. It does not invent a file.
- [ ] A connected account without Drive shows 授权读取 Drive and does not list files. After reconnect, the same prep can show docs. The calendar grant remains if the new consent still includes `calendar.events`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add query tests in `backend/tests/test_drive_meeting_query.py` for attachment ids, title-and-owner, title-only, no attendee-only query, and no HTTP when terms are empty. Add `test_prep_drive_empty_invents_nothing` and `test_prep_drive_consent_skips_http` beside the Gmail prep tests in `backend/tests/test_calendar_google.py`. Run `cd backend && python -m pytest tests/test_drive_meeting_query.py tests/test_gmail_meeting_query.py tests/test_calendar_google.py -q -k "prep or drive or mail"`.
- [ ] Extend `frontend/tests/briefingCard.test.mjs` so the brief source still has no 确认 button, no `fetch(`, and no `/api/calendar`, and so an old stored brief still renders. Run `cd frontend && npm test`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run 会前准备 on one meeting that has mail, at trunk and at head. Trunk has no docs section. Record that. Gate the head mail block, the docs block or the honest empty line, and zero calendar writes. Save `drive-regression.png`. Pass when trunk still shows the mail brief and head does not hide that mail.
- [ ] Lane 2. Prep a meeting whose event has a Doc attachment. Save `drive-attachment.png`. Pass when the doc row is badged 日程附件 and the link opens that file id.
- [ ] Lane 3. Prep a meeting whose title matches a Doc the attendee owns, with no attachment. Save `drive-title.png`. Pass when the row is badged 标题和参会人 or 标题 and the sentence text is copied from the export.
- [ ] Lane 4. Prep a meeting with a unique title and no files. Save `drive-empty.png`. Pass when the card names the meeting and says 没有找到相关文档, and the fake client log has no match-all query.
- [ ] Lane 5. Prep while the grant lacks `drive.readonly` and still has Gmail. Save `drive-consent.png`. Pass when the button reads 授权读取 Drive, mail still renders, and the log has no `drive.googleapis.com` call.
- [ ] Lane 6. Force Drive HTTP 503. Save `drive-down.png`. Pass when the copy is 暂时读不到云端硬盘 and the card does not say 重新连接.
- [ ] Lane 7. Return no mail and one doc. Save `drive-mail-empty.png`. Pass when both sections are visible and the card does not use a single empty status for the whole brief.
- [ ] Lane 8. Prep a meeting titled 周会 with no usable attendee and no attachment. Save `drive-generic.png`. Pass when the fake client log has zero `files.list` calls.
- [ ] Lane 9. Use a title that matches two meetings. Save `drive-choose.png`. Pass when the chooser appears and neither meeting was searched.
- [ ] Lane 10. Read the brief card for a confirm control and watch the network log. Save `drive-no-write.png`. Pass when there is no confirm button and no `POST /api/calendar`.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of `files.list` and `files.get` inside one prep, the wall time of `search_meeting_docs` on the fake client, and the time until the brief card is on screen. Trunk makes zero Drive calls. The end state the user waits for is the brief card with a mail section and a docs section.
- [ ] Probe. Interleave 5 trunk preps and 5 head preps of the same meeting on the fake client. Record Drive call counts on both sides, and the head wall time.
- [ ] Baseline. Record the trunk Drive call count first. It is zero.
- [ ] Rule. Fail if head issues a third `files.list` or a `files.list` with an empty query. Fail if `files.get` exceeds 5. Fail if fake-client `search_meeting_docs` takes over 1 second. Fail if the brief card is absent. Do not ratio Drive time against trunk, because trunk does not call Drive.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 4 screenshots into `docs/plans/media/cal-prep-drive-review-drive-regression.png` and `docs/plans/media/cal-prep-drive-review-drive-empty.png`.
- [ ] Record a 30 to 60 second video of a brief with mail and a doc, then an empty doc section, on a lane VM. Save it as `docs/plans/media/cal-prep-drive-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the `cal-invitee-roundtrip` tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request only after `cal-invitee-roundtrip`.

## Confirm pasted minutes before any write, cal-follow-paste

**Depends on.** `cal-prep-drive`.

**Files.**

- [ ] Add `CalendarAction.FOLLOWUP` and its `_GUIDES` entry in `backend/app/services/calendar.py`. The enum walk already feeds `parse_action`.
- [ ] Add two `needs_calendar` few-shots to `ROUTER_SYSTEM` in `backend/app/core/prompts.py`, and leave the template utterances as chat.
- [ ] Create `backend/app/services/meeting_followup.py` with `follow_up`, the minutes types, and `to_wire`.
- [ ] Add the `FOLLOWUP` branch in `execute_calendar` in `backend/app/agents/tools/calendar_tool.py`.
- [ ] Create `frontend/src/components/FollowupCard.tsx` and branch on `mode === "followup"` in `frontend/src/components/ChatWindow.tsx`.
- [ ] Add the pointed prefix in `frontend/src/api/client.ts` and a 会后跟进 button beside 会前准备 in `frontend/src/components/ScheduleCard.tsx`.
- [ ] Add rows to `backend/data/eval/seeds/routing_utterances.jsonl` and a `followup` card type in `backend/app/eval/calendar_behavior.py`.
- [ ] Add `backend/tests/test_meeting_followup.py`.

**Build.**

- [ ] Route 帮我整理一下这段会议纪要, 会后跟进一下昨天的客户会, and 把这段转写整理成待办 to `needs_calendar` and then `followup`. Keep 生成会议纪要结构 and 帮我列个待办模板 on the chat gold with no tools.
- [ ] The list button sends `帮我整理这场会的纪要：{title}，{day} {time}` through the same pointed parser shape as `_POINTED`. A test locks the Python prefix and the TypeScript prefix to the same string.
- [ ] Treat the current message as a paste only when the body, after removing the pointed sentence, is at least 80 characters. A shorter note is not minutes. Do not read an earlier turn. Do not call Drive in this pull request.
- [ ] No paste returns an info card whose status is `needs_paste`, phase `info`, with the caption 把纪要或转写贴在这条消息里。这次没有整理待办，也没有编造纪要。 No `follow` id. No button that writes.
- [ ] A paste becomes `Minutes`. `ActionItem.owner`, `due_said`, and `evidence` must be substrings of that paste. Drop a model field that fails. `Minutes` refuses a leftover. The owner must also appear inside the action text. If it does not, store no owner and show 未指定. Do not copy an attendee onto an item just because they were invited.
- [ ] Build the email with a fixed template over those spans. Recipients are emails that appear in the paste, or an attendee whose name or email matched an owner exactly. The card may drop a recipient. It may not add a person who was not on the card.
- [ ] The draft card is `mode` `followup` and `phase` `draft` only when at least one action has a date the user can tick. Otherwise phase stays `info` and the minutes are still shown. There is no `event_id`, `external_id`, `action`, or `rsvp`.
- [ ] 确认加到日历 posts the existing `POST /api/calendar` once per ticked row, with `participants` empty and `meet` false, so `sendUpdates` stays off. Title is `跟进：` plus the action text. Description contains `follow_action:` plus the row id and the evidence span. Disable the button while the posts run. Retry only rows that failed. Unticked rows are not posted.
- [ ] 复制邮件 and 用邮件应用打开 do not call the network. `mailto` is built in the browser. Omni does not gain `gmail.send` or `gmail.compose`. `GoogleAccess.get` stays the only Google mail method.
- [ ] Do not insert a dispatch row during chat. Do not add `POST /api/calendar/follow/send`. The HTTP create route still does not read `phase` or `expires_at`. Say that in the module comment only if a test cannot lock it. Lock it with a test that a direct `POST /api/calendar` still creates, and that `follow_up` itself never calls `create_event`.
- [ ] The next user turn still voids `phase` `draft` through `void_draft_schedule_cards`. An expired card shows 已过期 and the button does not post.

**You see.**

- [ ] Pasting a note that says 张三周五前发报价 shows that sentence, the owner 张三, and a tick. Before the click, the calendar is unchanged.
- [ ] After 确认加到日历, one event exists on the user's calendar, with no guests. The email text is still on the card and was not sent.
- [ ] A paste that names no owner shows 未指定. The card does not fill in the organizer.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add `test_follow_paste_writes_nothing_before_confirm` and `test_follow_drops_an_owner_absent_from_the_text` in `backend/tests/test_meeting_followup.py`. Run `cd backend && python -m pytest tests/test_meeting_followup.py tests/test_calendar_google.py -q -k "follow or prep_reads_one or never_writes"`.
- [ ] Add a routing row and run `cd backend && python -m pytest tests/test_router.py -q`. The template utterance stays off the calendar tool.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Send a 200 character paste under 会后跟进, at trunk and at head. Trunk has no follow-up card. Record that. Gate the head draft, zero `POST /api/calendar` before the click, and one event after the click. Save `paste-regression.png`. Pass when trunk writes nothing and head waits for the click.
- [ ] Lane 2. Paste 张三周五前发报价，截止周五。 Save `paste-owner.png`. Pass when the row shows 张三 and a checkbox.
- [ ] Lane 3. Paste a sentence that names a task and does not name a person, while the meeting has attendees. Save `paste-no-owner.png`. Pass when the owner reads 未指定 and the email recipients do not gain those attendees.
- [ ] Lane 4. Tick the dated row and click 确认加到日历. Save `paste-confirm.png`. Pass when one event exists, `participants` is empty, and the network log has no Gmail post.
- [ ] Lane 5. Leave the draft on screen. Save `paste-no-write.png`. Pass when the network log has no `POST /api/calendar` and no `gmail.googleapis.com` post.
- [ ] Lane 6. Click 复制邮件 and 用邮件应用打开. Save `paste-mailto.png`. Pass when the clipboard or the `mailto` link holds the template and the network log stays empty.
- [ ] Lane 7. Send 生成会议纪要结构. Save `paste-template.png`. Pass when the reply is chat and no follow-up card appears.
- [ ] Lane 8. Send 会后跟进 without a paste. Save `paste-ask.png`. Pass when the card asks for a paste and contains no invented minutes.
- [ ] Lane 9. Let the draft expire, then click the button. Save `paste-expired.png`. Pass when the card reads 已过期 and the event count is unchanged.
- [ ] Lane 10. Double-click 确认加到日历 while the first post is in flight. Save `paste-double.png`. Pass when the button is disabled and the log shows one post for that row.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Calendar row count and Gmail POST count before confirm, the time from paste to the follow-up card, and the time from the click to the new event. Trunk has no card. The end state the user waits for is the event after the click, or the ask-paste card when there is no paste.
- [ ] Probe. Interleave 5 trunk pastes and 5 head pastes of a fixed 4 KB note on the fake model parser. Record pre-confirm write counts on both sides, and the head card time.
- [ ] Baseline. Record the trunk pre-confirm calendar delta first. It is zero.
- [ ] Rule. Fail if head changes the calendar or posts to Gmail before the click. Fail if the card is missing. Fail if the median paste-to-card time on the fake parser is over 3 seconds. Fail if confirm posts more than one create per ticked row. Do not ratio card time against trunk, because trunk has no follow-up card.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 4 screenshots into `docs/plans/media/cal-follow-paste-review-paste-regression.png` and `docs/plans/media/cal-follow-paste-review-paste-confirm.png`.
- [ ] Record a 30 to 60 second video of the paste, the untouched draft, and the confirm on a lane VM. Save it as `docs/plans/media/cal-follow-paste-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the `cal-prep-drive` tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request only after `cal-prep-drive`.

## Read official minutes or ask for a paste, cal-follow-official

**Depends on.** `cal-follow-paste`.

**Files.**

- [ ] Extend `backend/app/services/drive.py` with `read_official_minutes`. Do not widen `search_meeting_docs`.
- [ ] Add an optional `GOOGLE_MEET_SCOPES` entry in `backend/app/services/calendar_secrets.py` and a `meet_access` flag. Confirm the scope string against the current Meet REST `conferenceRecords` page before locking the test.
- [ ] Extend `follow_up` in `backend/app/services/meeting_followup.py` so a missing paste calls the reader and a miss becomes the existing `needs_paste` card.
- [ ] Extend `FollowupCard.tsx` for the source line and the consent state that means "a file is there and Drive is not granted".
- [ ] Extend `backend/tests/test_meeting_followup.py` and the Drive query tests. Update the exact-scope connect test.

**Build.**

- [ ] A paste in the current message still wins. Do not call Drive or Meet when the paste threshold is met.
- [ ] No paste picks one event with `pick_event`. A tie returns the existing chooser and reads nothing. A miss returns `not_found` and reads nothing.
- [ ] Probe in this order. First, Meet smart notes, only when `meet_access` is true and the event has a meeting code. A missing Meet scope skips this probe and does not show a consent button. Second, an event attachment that is a Google Doc and whose title contains a notes marker. An agenda or a pre-read is not minutes. Third, one `files.list` for a Doc whose name contains a notes marker and a non-generic title term, modified after the meeting start. A generic title such as 周会 skips this list.
- [ ] Start `_NOTES_MARKERS` from a title copied off one real Gemini notes Doc in the operator's Workspace. Until that sample exists, ship the attachment probe and the Meet probe only, and leave the Drive title list uncalled. Do not guess a marker and then treat a hit as official.
- [ ] An official artifact carries copied plain text from `files.export`, a title, and a url. It has no constructor from a prep `RelatedDoc`. A search hit that fails the marker test is not minutes.
- [ ] Empty text, a 404, or no hit becomes `needs_paste`. The card lists the probes that ran and says 这场会还没有官方纪要。把会议记录或转写贴过来。不会编造不存在的纪要。
- [ ] A notes attachment that cannot be read because Drive was not granted becomes `needs_consent`, not `needs_paste`. The caption says 日历上有纪要附件。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。
- [ ] Drive HTTP 503 becomes `unavailable` with 暂时读不到这场会的官方纪要。可以把记录贴过来。不会编造官方纪要。 Do not say 重新连接.
- [ ] A hit uses the same `Minutes` checks as a paste. Source on the card is `meet_smart_notes`, `attachment_notes`, or `drive_notes`, with the title linked. The confirm click is unchanged. Still no `gmail.send`. Still no attendees on the reminder.
- [ ] Do not add a bot, a recording upload, or a Meet media scope.

**You see.**

- [ ] 会后跟进 on a meeting that has a Gemini notes Doc shows that Doc's title, a link, and action items copied from the export. The card says the text came from that Doc.
- [ ] The same request on a meeting with no notes asks for a paste and does not show a fake summary.
- [ ] An agenda Doc attached to the event does not become the minutes.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Add `test_official_miss_asks_for_paste_and_invents_nothing`, `test_agenda_attachment_is_not_minutes`, and `test_notes_attachment_without_drive_asks_consent` in `backend/tests/test_meeting_followup.py`. Run `cd backend && python -m pytest tests/test_meeting_followup.py tests/test_drive_meeting_query.py -q`.
- [ ] Assert the connect URL contains the Drive scope and, only after the Meet page check, the Meet readonly scope. Assert it still rejects `gmail.send`, `gmail.compose`, and `gmail.modify`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run 会后跟进 on a meeting with no notes file, at trunk and at head. Trunk has no official reader. Record that. Gate the head ask-paste card and the absence of any sentence that is not in the fixture. Save `official-regression.png`. Pass when the card does not claim official minutes.
- [ ] Lane 2. Return a Meet smart-notes Doc from the fake client. Save `official-smart.png`. Pass when the source line links that Doc and the badge says the notes came from Meet.
- [ ] Lane 3. Attach a Doc titled 议程. Save `official-agenda.png`. Pass when the card asks for a paste and does not quote the agenda.
- [ ] Lane 4. Attach a Doc whose title is the notes marker, and omit `drive.readonly`. Save `official-consent.png`. Pass when the copy says a notes attachment exists and Drive is required, and it does not say the meeting has no minutes.
- [ ] Lane 5. Omit the Meet scope and return an attachment notes Doc. Save `official-no-meet-scope.png`. Pass when the Meet endpoint is not called and the attachment Doc is still read.
- [ ] Lane 6. Send a long paste and a meeting that also has notes. Save `official-paste-wins.png`. Pass when the source says the text was pasted and Drive was not called.
- [ ] Lane 7. Use the title 周会 and no attachment. Save `official-generic.png`. Pass when `files.list` is not called.
- [ ] Lane 8. Show the official draft and do not click. Save `official-no-write.png`. Pass when the calendar row count is unchanged.
- [ ] Lane 9. Force Drive HTTP 503 on the export. Save `official-down.png`. Pass when the copy says the notes could not be read and does not say 重新连接.
- [ ] Lane 10. From the ask-paste card, send a paste. Save `official-fallback.png`. Pass when the next card is the same confirm shape as `cal-follow-paste` and still writes nothing until the click.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of `files.list` and Meet list calls on a miss, the time until the ask-paste card or the minutes card, and the count of invented sentences. Trunk has no reader. The end state the user waits for is either the sourced minutes card or the ask-paste card.
- [ ] Probe. Interleave 5 trunk follow-ups and 5 head follow-ups of a meeting with no artifact, on the fake client. Record list counts on both sides, and the head card time.
- [ ] Baseline. Record the trunk Meet and Drive list counts first. Both are zero.
- [ ] Rule. Fail if a miss issues more than one `files.list` or more than one Meet list. Fail if the card contains a sentence that is not in the fixture. Fail if the median fake-client time to the card is over 2 seconds. Do not ratio that time against trunk, because trunk has no official reader.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 2 screenshots into `docs/plans/media/cal-follow-official-review-official-regression.png` and `docs/plans/media/cal-follow-official-review-official-smart.png`.
- [ ] Record a 30 to 60 second video of a sourced notes card and of the ask-paste fallback on a lane VM. Save it as `docs/plans/media/cal-follow-official-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready. Wait for the operator.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto the `cal-follow-paste` tip after the verdict, patch-id unchanged.
- [ ] The owner reports stack-ready and does not merge. The operator lands this pull request last, bottom-up.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with the stack root, the stack tip, a one-line verdict per pull request, and anything parked, per `playbooks/autopilot-stack.md`.

## Appendix A. Prototype evidence

No prototype branch was cut. The operator forbade feature code in the plan pull request. The open product choices were already written in the brief, so a throwaway UI would not have changed them.

Settled from the `release` checkout at `4a26d5f`, not from a sketch that runs the product.

- Gmail prep is `prepare_meeting` and `search_meeting_mail`. It is read-only. Drive is absent.
- Confirm is the click in `ScheduleCard.confirm`. Chat does not insert.
- There is no minutes tool, no mail draft tool, and no display-name round trip when an email is present.

Unproven until the knife pull requests run. Whether `classify_calendar_action` labels 帮我准备一下 as `prep` without a new few-shot. Whether a real Workspace notes Doc uses the marker the operator samples. Whether `refresh_access_token` drops an optional scope when Google returns a narrower `scope` string. Whether a double click that slips past the disabled button can create two reminders, because the create route still ignores `phase`.

## Appendix B. Alternatives rejected

Two designs were written from the same grounding. The cross-judge scores were 17 for the no-send shape and 14 for the send-token shape. The no-send shape is the base. Agreement was on `Invitee` as the wire person, one brief with independent mail and docs sections, and one follow-up verb. They diverged on how mail leaves the product.

Rejected a `gmail.send` scope, a `FollowDispatch` table, and `POST /api/calendar/follow/send`. That path can send exactly once, and it is a second confirm pattern beside calendar routes that do not check `phase`. The brief asked for an email draft, not for Omni to send. Copy and `mailto` stay. A later program can add a send if the operator asks for it in plain words.

Rejected a separate Drive tool and a separate minutes tool. `allowed_tools` would grow, and one reply still has one `schedule_card`.

Rejected keeping `participants` as strings and adding `guest_names` beside them. That pair is already ignored by `guestsFromWire`.

Rejected treating the only Google Doc on an event as official minutes. An agenda would become the notes.

Rejected a model-written doc summary. Prep already copies sentences. Drive exports do the same, capped at two sentences, and only for Google Docs.

Rejected a recording bot that joins Meet. It is a different product from reading a Doc the organizer already created. It needs media consent, storage, and a bot identity this repo does not have.

## Appendix C. Risks

`control-ui` was not in the plan environment. Live lanes still drive the browser chat. A lane that only curls the API is not a pass.

`drive.readonly` is a restricted scope. The consent screen can block until Google verifies the app. `cal-prep-drive` still ships the code. Docs stay on `needs_consent` until the user completes the screen. That is the honest state, not a bug.

`include_granted_scopes` is false. Existing users do not gain Drive until they click connect again. The sidebar must show the button when `drive_access` is false. A missing free-busy scope already fails this way, with text and no button. Do not copy that failure.

The create route does not check `phase` or `expires_at`. A crafted `POST /api/calendar` still inserts. The follow-up card disables the button and refuses when expired. A double submit that both pass the button can create two events. The lane watches that. A server idempotency key is out of this stack.

`refresh_access_token` may store a narrower `scope` without `_require_scopes`. After Drive ships, that hole can clear `drive_access` without a click. Watch it. Do not redesign refresh in this stack.

Meet REST may be unavailable on the operator's Workspace edition. The probe is optional. A missing scope skips it. The attachment path still works.

`card_type` in `backend/app/eval/calendar_behavior.py` scores a brief as `none` today. `cal-prep-drive` must not expect the old 150-case scorer to see docs. Add an explicit assertion in the prep tests instead of overloading `card_type` until `cal-follow-paste` adds `followup` on purpose.

README scope comments are behind `GOOGLE_REQUESTED_SCOPES`. `cal-prep-drive` updates them. A reader who trusts README over `calendar_secrets.py` will request the wrong screen.

## Appendix D. Links and reading list

Read before editing.

- `backend/app/services/meeting_prep.py`, `backend/app/services/gmail.py`, `backend/app/services/calendar_secrets.py`.
- `backend/app/agents/tools/calendar_tool.py` at `execute_calendar`, `_base_card`, and the `prep` branch.
- `backend/app/services/calendar_provider.py` at `Invitee` and `attendee_body`.
- `frontend/src/components/MeetingBriefCard.tsx`, `ScheduleCard.tsx`, and `ChatWindow.tsx`.
- `docs/plans/calendar-hard-gates.md` for confirm-before-write.
- `docs/eval.md` for `python scripts/eval.py`.

`pstack/skills/how/SKILL.md` applies to every knife. Each one places a new read or a new card on an existing flow. `pstack/skills/interrogate/SKILL.md` applies to `cal-follow-paste` and `cal-follow-official`. Those two can create calendar events after the click.

The decision trail for this plan is `docs/plans/calendar-remaining-decisions.tsv`.

## Appendix E. Scope lock and shipped surface

Done, and not a primary task in this program.

- Calendar create, update, and cancel go through a draft card. The click calls `POST /api/calendar`, `POST /api/calendar/google/confirm`, `PATCH /api/calendar/{id}`, or `DELETE /api/calendar/{id}`.
- RSVP is the 接受, 拒绝, or 暂定 click. Chat does not call `answer_invitation`.
- Meet links are `conferenceData` with `hangoutsMeet` on confirm. `conference_view` does not invent a URL.
- Free-busy with emails is one `freeBusy` query. A missing free-busy scope says 重新连接 Google 日历 and does not call the API.
- 怎么安排 and 有空吗 are list-first. Advice is `_planning_advice`. `docs/plans/calendar-cards-v2.md` and `docs/plans/calendar-time-planning.md` describe that shipped behavior.
- Gmail prep shipped in `1e3d0f8` (#60) and `4a26d5f` (#61). Trigger path is `CalendarAction.PREP`. The query builder is `_queries` in `backend/app/services/gmail.py`. The card is `mode` `brief` and `phase` `info`.

This program covers four holes. Drive docs on that brief. A confirm card for pasted minutes, action items, and an email draft. A read of official notes with a fallback to the paste. A display name that survives beside an email.

This program does not cover a bot in the call, Gmail send, a todo product, a second calendar vendor, or a rewrite of the shipped confirm routes.

Confirm before write stays the rule. `follow_up` and `prepare_meeting` do not receive a calendar provider. The click is the first insert. The email buttons do not send.

## Appendix F. Code anchors

Checked on `release` at `4a26d5f`.

Routing. `route` in `backend/app/agents/router.py` sets `needs_calendar`. `retrieve_node` calls `execute_calendar` with `{}`. `classify_calendar_action` in `backend/app/services/calendar.py` returns `create`, `update`, `cancel`, `list`, `suggest`, or `prep`. `ensure_builtin_tools` registers `kb`, `web`, `calendar`, and `sandbox` only.

Prep. `prepare_meeting` and `to_wire` in `backend/app/services/meeting_prep.py`. `search_meeting_mail` and `_queries` in `backend/app/services/gmail.py`. Scope `https://www.googleapis.com/auth/gmail.readonly` is `GOOGLE_MAIL_SCOPES` in `backend/app/services/calendar_secrets.py`. `MeetingBriefCard` renders the card. `formatPointedUtterance` sends `帮我准备这场会：...`.

OAuth. `build_authorize_url` sends `GOOGLE_REQUESTED_SCOPES` with `access_type=offline`, `prompt=consent`, and `include_granted_scopes=false`. Required to save a row are `calendar.events`, `calendar.settings.readonly`, and `calendar.events.freebusy`. Gmail is optional. Connect is `connectGoogle` to `GET /api/calendar/google/connect`. There is no desktop loopback client.

Writes. `ScheduleCard.confirm` is the click. `sendUpdates=all` is set only inside `_insert_google_event` and `_patch_google_event` when the body has attendees. Gmail has no send method.

People. `Invitee` stores email and name. `attendee_body` sends `{"email"}` only. `guestToWire` returns the email for a named invite. `_participant_names` prefers email over `displayName`. `dump_invitees` can store both, and the string path does not fill `name` when `email` is set.

Absent. No `drive.googleapis.com`, no `meet.googleapis.com`, no `docs.googleapis.com` in product code. No calendar `attachments` field mask. No follow-up action. No paste ingest.

## Appendix G. Competitor gap

Only the holes still open on `release`. Shipped items are in Appendix E.

Claude and Copilot class prep pulls related mail and related files into one brief. Omni pulls mail. It does not pull Drive docs. `cal-prep-drive` is that hole.

Gemini notes and Granola class follow-up start from a transcript the meeting already produced. Omni cannot read a Meet notes Doc, a calendar attachment, or smart notes. `cal-follow-official` is that hole. When the organizer never enabled notes, the honest result is a paste, which is `cal-follow-paste`. Pretending a summary exists is the failure these products also have to avoid, and this plan forbids it.

Lindy and calprep class follow-up turns notes into tasks and an email the user still sends. Omni has no action card and no email draft. `cal-follow-paste` is that hole. It writes a reminder onto the user's own calendar only after the click, and it does not send mail.

A name beside an email is the small hole under the prep search. Gmail's query can OR a name and an email, but the list path never passes both, because `_participant_names` drops `displayName`. `cal-invitee-roundtrip` is that hole. It is not a new product category.

Not a gap this plan takes. Joining the call to record, the way Granola does, stays out. The organizer's Doc is the source Omni can read without a bot.

## Appendix H. Card and query reference

Person wire after `cal-invitee-roundtrip`.

```json
{"email": "zhangsan@example.com", "name": "张三"}
```

Google attendee when the name is non-empty.

```json
{"email": "zhangsan@example.com", "displayName": "张三"}
```

Picked brief after `cal-prep-drive`. `status` is `ready` whenever a meeting was picked. Section state carries empty, consent, and down.

```json
{
  "mode": "brief",
  "phase": "info",
  "status": "ready",
  "meeting": {"title": "客户会", "attendees": [{"email": "a@b.com", "name": "张三"}]},
  "mail": {"state": "found", "emails": [], "focus": []},
  "docs": {
    "state": "found",
    "docs": [{
      "file_id": "file-1",
      "title": "客户会提纲",
      "url": "https://docs.google.com/document/d/file-1/view",
      "via": "title_and_people",
      "sentences": ["原文句子。"]
    }]
  }
}
```

Follow-up draft after `cal-follow-paste`. `phase` is `draft` only when a dated item can be ticked.

```json
{
  "mode": "followup",
  "phase": "draft",
  "source": {"kind": "pasted"},
  "points": ["张三周五前发报价。"],
  "actions": [{
    "id": "a1",
    "text": "张三周五前发报价。",
    "owner": "张三",
    "evidence": "张三周五前发报价。"
  }],
  "mail": {"to": [{"email": "a@b.com", "name": "张三"}], "subject": "跟进：客户会", "body": "张三周五前发报价。"}
}
```

Ask-paste after `cal-follow-official` when nothing official was found.

```json
{
  "mode": "followup",
  "phase": "info",
  "status": "needs_paste",
  "checked": ["attachment_notes"],
  "drive": "ok"
}
```

Drive list query, when it exists, is title atoms plus optional owner emails, `trashed=false`, a modified-time bound, and the Google Doc mime type. The value is never an empty string. Attachment reads do not use this query.

Generic title terms stay the set already in `_GENERIC_TERMS`. That includes 会议, 例会, 周会, 月会, 沟通, 讨论, 同步, 电话, 视频会, meeting, sync, call, and weekly.

## Appendix I. Eval gates and what the operator prepares

Gates the knife tests lock.

- Prep and follow-up copy only sentences that appear in the fetched mail, the exported Doc, or the paste. An invented summary fails the test.
- `follow_up` and `prepare_meeting` perform zero calendar inserts and zero Gmail posts. The confirm click is the first `POST /api/calendar`.
- Empty mail, empty docs, and a missing official artifact each name that fact. They do not share one vague failure.
- `needs_consent` is a missing scope or a rejected token. `unavailable` is a 503 or another outage, and the copy does not say 重新连接.
- A notes attachment with no Drive grant is consent, not "there are no minutes".
- An owner who is not in the action text is stored as absent.
- `gmail.send`, `gmail.compose`, and `gmail.modify` stay absent from `GOOGLE_REQUESTED_SCOPES`.

Commands.

```bash
cd backend && python -m pytest tests/test_gmail_meeting_query.py tests/test_drive_meeting_query.py tests/test_meeting_followup.py tests/test_calendar_google.py -q
cd frontend && npm test
cd backend && python scripts/eval.py run calendar --profile smoke
```

Smoke replays the cache. Do not refresh the cache in the same pull request that changes a card shape unless the new rows are intentional.

What the operator prepares, outside the repo.

- Add `https://www.googleapis.com/auth/drive.readonly` on the Google auth consent screen. It is restricted. Verification can lag the code.
- Add the Meet readonly scope only if `cal-follow-official` should call smart notes. Copy the string from the current `conferenceRecords` document. Leave it off and the attachment path still works.
- Do not add `gmail.send` or `gmail.compose` for this stack.
- Enable the Drive API on the same project. Enable the Meet API only for the optional probe.
- In Workspace, the organizer has to produce Gemini notes or the product will ask for a paste. That is the designed result.
- Tell existing users to click 连接 Google 日历 again after the scope ships. The app cannot add a scope onto the old token.
- There is no desktop reconnect build. The button is the web redirect already in `connectGoogle`.
- Send one sample notes-Doc title from the real Workspace before the Drive title probe is turned on.

## Appendix J. Synthesis

The base is the design that never sends mail. Public entry points are `prepare_meeting`, `search_meeting_docs`, `follow_up`, and `read_official_minutes`. Drive query text and Meet URLs stay inside `drive.py`.

Grafted from the other design. `Invitee` rejects an empty person and a bad email at construction. The Drive list accepts only a query value that cannot be empty. The plan states in `cal-follow-paste` that the old create route still ignores `phase` and `expires_at`.

Not grafted. The send-token table, the extra public helpers around paste and bind, and the rule that a single Doc on the event is official.

Scores from the cross-judge, in the order locks, extends, implementer, illegal states, depth, confirm. The send-token design was 2, 2, 3, 3, 2, 2. The no-send design was 3, 3, 3, 3, 3, 2. The confirm score stays 2 because both designs still live with calendar routes that trust the caller. This plan keeps that fact visible instead of adding a second route that checks `phase` alone.
