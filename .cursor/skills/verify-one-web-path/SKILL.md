---
name: verify-one-web-path
description: "Check that one chat turn does not put a networking field on the DeepSeek request and also call Zhipu web search, that stop blocks the next model send, and that a calendar card does not write the event. Use when editing _build_request, execute_web, search_planned, or _decision_to_plan."
---

# Verify one web path

The chat body has no networking field. The router can still queue `web`, and that call is the existing Zhipu search. A turn does not do both. Stop blocks the next model send. A calendar card does not write the event.

The 2026-11-10 CreateChatCompletionRequest names no networking property. Two further rules fail on the current code, and that failure is the finding. When 「上网」 is on the turn, the body still has no field. When it is absent, a router that needs web still calls search.

## When this applies

Run it when a change touches any of these:

- `backend/app/core/llm.py` (`_build_request`, `_create_sync`)
- `backend/app/agents/tools/web.py` (`execute_web`)
- `backend/app/services/web_search.py` (`_web_search_url`, `search_planned`)
- `backend/app/agents/router.py` (`_decision_to_plan`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/one-web-path/check.py
python verification/one-web-path/check.py --break-both
python verification/one-web-path/check.py --break-field
python verification/one-web-path/check.py --break-stop
python verification/one-web-path/check.py --break-calendar
python verification/one-web-path/check.py --cutover
```

The held command exits 0. Each break command exits 1 and names its invariant. `--cutover` exits 1 and names `FieldWhenAllowed` or `AbsentSkipsZhipu`. Exit 2 means the checker no longer matches the code, or TLC could not run. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`NoBoth` says `netField` and `zhipu` are never both true. The harm is one turn taking DeepSeek networking and Zhipu `/web_search`. In the trace, `Build` sets the field and `Search` sets the search when `breakBoth` is true. In the code, `_build_request` writes only the thinking switch, and `execute_web` calls `search_planned` on a separate HTTP client.

`NoGuessedField` says `netField` stays false. The harm is a key the chat-completions document does not name. In the trace, `Build` sets the field when `breakField` is true. In the code, the body keys are `model`, `messages`, `stream`, `extra_body`, and `reasoning_effort`, plus the JSON keys that function already adds.

`StopBlocksLaterSend` says `sendsAfterStop` stays false. The harm is another model send after stop. In the trace, `LaterSend` sets it when `breakStop` is true. In the code, `_create_sync` calls `is_stopped()` before `chat.completions.create`.

`CalendarStaysUnwritten` says `calendarWritten` stays false. The harm is the web turn writing the calendar. In the trace, `ShowCard` writes when `breakCalendar` is true. In the code, `workflow.py` does not call `create_event`, and `execute_web` does not either.

`FieldWhenAllowed` says a turn whose token is present has a networking field once the body is built. The harm is a web-enabled turn with no documented switch. The current code violates it. `Build` leaves `netField` false. `_build_request` does the same, because the document names no field.

`AbsentSkipsZhipu` says a turn whose token is absent does not call search. The harm is a search the allow list did not grant. The current code violates it. `Search` runs when `needsWeb` is true and does not read the token. `_decision_to_plan` appends `"web"` from `needs_web`, and the token 「上网」 is not in the router.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one turn. It does not model a second user, `verify_state` queueing web again, the click that writes the calendar, or an in-flight request that stop does not cancel. Live lanes are not part of this check. The cutover invariants are findings, not a license to invent a request field or to delete the only search.

## Keep the check with the code

The anchors were confirmed at the commit that adds this check. Move the confirmation forward only after reading `_build_request`, `_create_sync`, `execute_web`, `_web_search_url`, and `_decision_to_plan`, and seeing that the model still describes them. If `_build_request` contains `web_search`, or `_create_sync` calls `create` before `is_stopped()`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
