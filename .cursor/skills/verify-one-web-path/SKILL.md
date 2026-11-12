---
name: verify-one-web-path
description: "Check that one chat turn offers DeepSeek web_search on the Responses request by default, omits it when the allow list drops web, never also calls Zhipu web search, blocks a later send after stop, and does not write the calendar or invent a citation. Use when editing stream_chat, build_responses_body, execute_web, or _decision_to_plan."
---

# Verify one web path

The user-facing answer uses `POST /responses`. When the turn allow list contains `web`, or the caller does not pass a list, the body adds `{"type": "web_search"}`, selects `deepseek-v4-pro`, and leaves `tool_choice` unset. A list that omits `web` stays on the chat model and has no search tool. `execute_web` does not call `search_planned`. The router does not queue `"web"`. Stop blocks the next model send. A calendar card does not write the event. A citation requires a URL that the payload actually carried.

The old hold treated a missing networking field as a pass. That hold is not a pass for this model. `FieldWhenAllowed` and `AbsentSkipsZhipu` are gone. The token 「上网」 is still not an input.

## When this applies

Run it when a change touches any of these:

- `backend/app/core/llm.py` (`stream_chat`, `_create_responses`)
- `backend/app/core/responses_api.py` (`build_responses_body`)
- `backend/app/agents/tools/web.py` (`execute_web`)
- `backend/app/agents/router.py` (`_decision_to_plan`)
- `backend/app/agents/harness/verify.py`

## Run

From the repo root, with Java on PATH:

```sh
python verification/one-web-path/check.py
python verification/one-web-path/check.py --break-both
python verification/one-web-path/check.py --break-omit
python verification/one-web-path/check.py --break-zhipu
python verification/one-web-path/check.py --break-stop
python verification/one-web-path/check.py --break-calendar
python verification/one-web-path/check.py --break-cite
```

The plain command exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

Read a failing TLC trace with `/statechart`. Pass `TLA2TOOLS` as the absolute path of the pinned jar from `verification/tlc_jar.py` and do not let the script install a different TLC.

## What a failure means

`NoZhipu` says `zhipu` stays false. The harm is a client search on the same turn. In the trace, `ClientSearch` sets it when `breakZhipu` is true. In the code, `execute_web` returns without `search_planned`.

`NoBoth` says `netField` and `zhipu` are never both true. The harm is one turn taking the server tool and Zhipu `/web_search`. In the trace, `Build` sets the field and `ClientSearch` sets the search when `breakBoth` is true.

`DefaultOffers` says a turn whose allow list contains `web` has the server tool once the body is built. The harm is a default turn with no search capability. In the trace, `Build` leaves the field false when `breakOmit` is true. In the code, `build_responses_body` appends `{"type": "web_search"}` when `offer_web` is true.

`ExplicitOmits` says a turn whose allow list omits `web` has neither the server tool nor the client search. The harm is a search the allow list did not grant.

`StopBlocksLaterSend` says `sendsAfterStop` stays false. The harm is another model send after stop. In the trace, `LaterSend` sets it when `breakStop` is true. In the code, `_create_responses` calls `is_stopped()` before `responses.create`, and `stream_chat` returns when stop is set.

`CalendarStaysUnwritten` says `calendarWritten` stays false. The harm is the web turn writing the calendar. In the trace, `ShowCard` writes when `breakCalendar` is true. In the code, `workflow.py` does not call `create_event`.

`CitationNeedsSource` says a citation implies `hasUrl`. The harm is a link the payload did not carry. In the trace, `NoteResult` sets `citation` without `hasUrl` when `breakCite` is true. In the code, citations are taken from URL fields on a completed search item.

If the trace cannot be replayed as these functions, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one turn. It does not model a second user, an in-flight request that stop does not cancel, the click that writes the calendar, or a live DeepSeek call. `stream_chat` can replay a function tool when the caller passes one. The chat route does not pass the calendar tool into that loop. Live lanes are not part of this check.

## Keep the check with the code

The anchors read `build_responses_body`, `_create_responses`, `execute_web`, `_decision_to_plan`, and `verify.py`. If the responses body drops `{"type": "web_search"}`, sets `tool_choice`, or `execute_web` calls `search_planned`, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
