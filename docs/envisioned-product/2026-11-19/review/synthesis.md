# Same-model review

Both reviews used `grok-4.7-xhigh-fast`. No Gemini, Fable, Claude, or Composer call was part of this review. This is not cross-model verification.

| Role | Agent | Model slug | Result |
| --- | --- | --- | --- |
| First candidate | `bc-4dbf3bd0-8a57-5172-a1df-71fe955d3f5d` | `grok-4.7-high` | Chinese plan, 12 units U1 through U12. Kept. Not the base. |
| Second candidate | `bc-fcff8fd3-6be0-51e4-b222-39309d29db46` | `grok-4.7-xhigh-fast` | English plan, 11 units. Did not read the first plan. Base. |
| Review A | `bc-ed9a78ef-7047-580b-86a6-11d2f9bd7590` | `grok-4.7-xhigh-fast` | Base is the English plan. Dropped 14 findings it did not open. |
| Review B | `bc-2153726a-b02a-5c33-84fd-3dd7d75c21a9` | `grok-4.7-xhigh-fast` | Base is the English plan. Dropped 10 findings it did not open. |

The two reviews agreed on the base and on the corrections now written into `../plan.md`. Agreement is same-model agreement. It does not add a second vendor.

## What was rejected from the Chinese plan

- Passing the Google event id, or the merchant order id, as `note_external_success`'s match key. The claim stores the armed id, and `_written_claim` requires equality (`event_inbox.py` lines 577-580).
- Using `submit_event` to file a calendar receipt. The default act path can cancel open claims (`event_inbox.py` lines 375-377).
- Stopping compose `place` when no connector row exists. Compose is the channel already on release (`errands.py` lines 269-283).
- Restoring `waiting_person` to `running` without a new event. The import check rejects every edge whose target is `running` (`task_run.py` lines 55-57).
- A search hit field `date`, and the tool names `deepseek_web_search` and `deepseek_read_url`.
- An empty stall duration that sends no notice.
- Making mail, calendar, errand, and devices wait on each other.

## What was rejected from the English draft

- Receipt source `calendar`. `_SOURCES` is `internal` and `mail` (`event_inbox.py` line 35).
- Copying `created_at` into `last_progress_at`.
- The watch update that notifies when the stamp changed.
- Leaving `web_fetch` optional.
- Saying `perform` is not entered on an unknown local license. The package calls `perform`. The open runs only when `kind` is `armed`.

## Dissent

The reviews did not disagree on the base. Review A wanted the pre-loop `talk` body and the server-side `web_search` refusal stated in the plan. Review B wanted the four confirm-only tool names registered in `loop`. Both are now in `plan.md`. Neither review asked the operator to pick a silence duration. The plan does not ask either.

## Unresolved operator choices

1. Mail transport. `mail` lands with an empty setting that does not send and does not say sent. A live mailbox still needs Gmail `gmail.send` or SMTP, and a lookup key for the case where the transport accepts the message and the response is lost before any provider id is stored. That gap is not closed by picking a vendor.
2. Browser dependency. `errand` keeps the current compose service. A library is allowed only after a later check shows Flash tool calls, a write blocked before it runs, and structured evidence with payment and login fields removed. Poke and Vesta are examples. They are not a success rate and they are not a dependency. Playwright in the compose service is not an autonomous agent.

Device enrollment is not left open. The signed-in user enrolls, sees the token once, and revokes by id from the web list. A pairing-code paste can replace that later without changing the hash row.

R2 and R3 are not questions. They stay as the current code.

## What this run did not verify

Upstream function bodies named in the OSS report were not re-opened. The pins in `sources/oss-issue-references.md` stay the research boundary. Hermes, Harness, OpenCode, DS-Bot, OpenClaw, FullDots, Rowboat, and OpenMausBot are mechanism notes, not code copied into this repository.

The parent opened these pages on 2026-11-19:

- `https://api-docs.deepseek.com/guides/search`
- `https://api-docs.deepseek.com/guides/thinking_mode`
- `https://api-docs.deepseek.com/guides/vision`
- `https://api-docs.deepseek.com/zh-cn/guides/responses_api`

The parent did not re-open the pricing page, the chat-completion reference, a provider cancel API, calendar delete and RSVP bodies, `driver.py`, or the empty-403 branch. The plan says so inside the units that depend on those files.

The parent review of head `059b643` is a separate review. `parent-review-response.md` answers it. This file does not turn that answer into approval.

## Operator order

Run one unit at a time from the latest `release`. The order is `picture`, `talk`, `watch`, `loop`, `search`, `mail`, `calendar`, `errand`, `devices`, `resume`, `desktop`. The parent review of `059b643` is answered in `parent-review-response.md`. That answer is not approval to implement.

## First unit

The first unit in that order is `picture`. `talk` is the first unit that changes job behavior. Do not start `talk` until `picture` is on `release`.
