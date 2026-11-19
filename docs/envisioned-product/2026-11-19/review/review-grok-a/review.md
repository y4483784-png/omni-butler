grok-4.7-xhigh-fast

# Same-model review of the two 2026-11-19 plans

Baseline opened: `/agent/repos/omni-butler` at `d7710d687d147aeeda502d412454ab8a3829af66`. The working tree is that commit plus an unrelated rules edit and an untracked `docs/envisioned-product/2026-11-19/` directory. Those were not used as the design. No product file was modified.

## Scores

| Criterion | candidate-grok (grok-4.7-high, Chinese) | candidate-grok-xhigh (grok-4.7-xhigh-fast, English) |
| --- | --- | --- |
| 1. Settled decisions stay closed | 4 | 5 |
| 2. Current-code claims match files opened on this release | 4 | 4 |
| 3. Every unit can be built from the text plus this tree | 3 | 4 |
| 4. Dependencies are real and the 14 merged knives are not redone | 3 | 5 |
| 5. Tests, formal scope, and live predicates are concrete, and gaps are labeled | 4 | 4 |
| 6. Open questions are real operator choices, and the browser stays a gate | 4 | 5 |

A 5 means an implementer who has only that text and this release tree can land the unit without inventing a wire contract or fighting a merged state machine.

## Base

Use `/tmp/arena-plan/candidate-grok-xhigh/` as the base.

It keeps the 2026-11-19 decisions closed, treats the fourteen merged knives as the code you start from, and splits work on real file dependencies. `picture`, `mail`, `calendar`, `errand`, and `devices` can branch from `release`. `talk` adds the one new task edge, `continue_job`, because the current move table rejects every edge into `running`. `loop` then saves the assistant tool call before `gateway.invoke` and continues the same job after one confirm. `search` posts to DeepSeek Search and Fetch with the parent field list and does not add `date`. Mail checks the transport before `consume_schedule_claim`. Errand keeps the existing compose channel and only tightens the empty-echo branch. The browser dependency stays in `open.md` and does not block `errand`.

The Chinese plan is the stronger write-up of a few local facts, and those are the grafts below. It is not the base. An implementer who followed it would seal calendar and errand receipts with the wrong id, could cancel open claims by calling `submit_event`, would turn compose off, and would have no legal move from `waiting_person` back to `running` except on the desktop card.

The English plan is not already the contract's Chinese deliverable. Extend the English text in place. Do not translate it by merging the Chinese units on top.

## Grafts

1. From Chinese U8, file the calendar receipt with a source the inbox already allows, and set `claim_token` to the confirm token before `note_external_success`. Keep the English rule that the match key is the armed private `requestId`, and put the Google event id in `EventReceipt.body`. Do not take Chinese U8's `remote_request_id=google_id`, and do not take `submit_event` as the insert.

2. From Chinese U2, give `talk` a body that can land before `loop`: insert the job as `running`, store the goal, call no model tool and no search, and accept the unit when the previous generation is still running. In the same edit, add the parent fact the English search unit never states because it did not open the Responses page: a server-executed Responses tool of type `web_search` cannot be refused by the executor before the request leaves, so the job must not send that tool. Do not copy the names `deepseek_web_search` or `deepseek_read_url`.

## Rejected

From the Chinese plan:

- Passing the Google event id, or the merchant order id, as `note_external_success`'s `remote_request_id`. The claim's stored marker is the armed id, and the helper requires equality.
- Using `submit_event` to "just record" a calendar receipt. The default act path can reissue a card and cancel open claims.
- Stopping compose `place` when no connector exists. That removes the channel ev-errands already merged. The browser gate does not require that cut.
- Restoring `waiting_person` to `running` by assignment. The merged validator rejects any edge whose target is `running`, and `finish` on `waiting_person` keeps the status. The English `continue_job` edge is the extension that admits this.
- Search response field `date`, a request field list that drops `ori_question` and `localization`, and the tool names `deepseek_web_search` and `deepseek_read_url`.
- Treating "thinking-mode `tool_choice` of `required` or a named tool returns 400" as a verified wire rule for this review. Both plans already omit `tool_choice`. The parent fact list does not include that 400, and `ev-net.md` only lists the field.
- A default-empty `TASK_STALL_REPORT_AFTER` that sends no silence notice. The settled behavior is to check and report, and to leave the interval pending without blocking the knife. The English "one notice per unchanged stamp, no new duration, no stop" is the decision to keep. The contradictory SQL in the English watch unit is not part of that decision and is rejected below.
- Making mail, calendar, errand, and device enrollment wait on the watchdog. They do not depend on it.
- Arming a mail claim before the transport is known to exist. A crash then leaves `unknown` keyed by a local id.

From the English plan, while extending it:

- Receipt source `calendar`. It is not an allowed inbox source.
- The watch sentence that updates `notice_progress_at` only where it is null or different from `last_progress_at`. That is the opposite of "notice once when the stamp is unchanged," which is what the unit test says.
- Copying `created_at` into `last_progress_at`. Null means no progress sample yet. Do not seed the new clock with the creation time the watchdog uses today.
- "Add `web_fetch`, or keep only the names already registered." Register `web_fetch` as a second read tool, and make `web_constraint=forbid` omit and deny both `web` and `web_fetch`.
- "On an unknown local license, `perform` is not called again." The current confirm path still calls `perform`. The no-op is inside `perform` unless `kind` is `armed`.

## Findings

### 1. Chinese calendar success uses the Google event id as the inbox match key

Chinese U8 says to call `note_external_success(..., remote_request_id=google_id)` after the local `external_id` is stored, and its risk note says the 409 happens when `finish_confirm` has not run yet.

The insert arms a different id. `calendar_google.py` builds `request_id = uuid.uuid4().hex`, puts it in `extendedProperties.private.requestId`, and arms that value before POST:

```1345:1352:backend/app/services/calendar_google.py
        request_id = uuid.uuid4().hex
        private = {"requestId": request_id}
        body["extendedProperties"] = {"private": private}
        if meet:
            body["conferenceData"] = _meet_request(request_id)
            params["conferenceDataVersion"] = "1"
        self._access_token()
        self._arm_user_write(request_id)
```

`arm_remote_claim` stores that marker and does not replace it later. The only product assignment found is:

```433:434:backend/app/services/schedule_confirm.py
    claim.status = "unknown"
    claim.remote_request_id = marker
```

`note_external_success` then requires a written claim whose `remote_request_id` equals the marker it was given:

```562:580:backend/app/services/event_inbox.py
def _written_claim(
    db: Session,
    user_id: int,
    token: str,
    remote_request_id: str,
) -> ScheduleConfirmClaim | None:
    ...
    if claim is None or claim.user_id != user_id or claim.status != "written":
        return None
    if (claim.remote_request_id or "").strip() != remote_request_id:
        return None
```

The only product caller today is mail, and it passes the same id it armed:

```451:459:backend/app/services/mail_entry.py
def _seal(db: Session, *, user_id: int, delivery_id: str, remote_request_id: str) -> ReceiptView:
    try:
        return note_external_success(
            db,
            user_id=user_id,
            source="mail",
            delivery_id=delivery_id,
            remote_request_id=remote_request_id,
        )
```

The English calendar unit is the one that matches this helper: seal with the armed `requestId`, and store the provider event id in the receipt body (`models.py` line 282, `body = Column(Text, ...)`). That part of the English unit should stay.

### 2. Chinese errand success has the same match-key bug, and it turns compose off

Chinese U9 seals with `note_external_success` using `merchant_order_id`. The armed value is a uuid, and the merchant id is a different column:

```160:161:backend/app/services/errands.py
    armed = str(uuid4())
    arm_remote_claim(db, key, armed)
```

```310:312:backend/app/models/models.py
    idempotency_key = Column(String, nullable=False)
    merchant_key = Column(String, nullable=False, default="")
    order_id = Column(String, nullable=False, default="")
```

`_written_claim` would reject `merchant_order_id` unless the claim's `remote_request_id` was overwritten, which would destroy the lookup key `_lookup` already sends as `idempotency_key` (`errands.py` line 296). The English errand unit correctly adds no inbox seal. The contract asks for the correlation id and the merchant order id on the result, which `order_id` already is, not for a second inbox marker.

The same Chinese unit also says that with no connector and no passed browser gate, `commit_errand` must not call compose. The current place path does call compose when the connector is missing:

```269:283:backend/app/services/errands.py
    if connector is not None:
        body = post_json(
            connector.place_url,
            ...
        )
        return interpret(body, armed)
    body = post_json(
        _tasks_url(),
        headers=_headers(armed, compose=True),
        payload=_compose_payload(order, armed, pay, login, "place"),
        ...
    )
```

`_channel_ready` treats a configured compose URL as a channel (`errands.py` lines 380–383). Ev-errands is already on this release. The browser gate can stay closed without deleting that channel. The English unit keeps compose and only changes `interpret`.

The empty-echo bug both plans name is real:

```367:368:backend/app/services/errands.py
    if outcome == "placed" and not (echoed_key and echoed_key != armed):
        return ShopReply("placed", order_id=order_id, told=told, clicks_after_wall=clicks, price=price, named=True)
```

`_public` still drops the key by returning only four fields:

```15:27:services/errand-compose/server.py
def _public(result: dict) -> dict:
    ...
    return {
        "outcome": str(safe.get("outcome") or "unknown"),
        "order_id": str(safe.get("order_id") or ""),
        "told": str(safe.get("told") or ""),
        "clicks_after_wall": clicks,
    }
```

### 3. Chinese U8's `submit_event` is not a side-effect-free insert

`submit_event` defaults `action` to `calendar.create` (`event_inbox.py` line 98) and, unless the decision is noise or ignore, calls `_route_act` (lines 136–148). While the target is `waiting_person`, a missing fingerprint match and a card call `_reissue`:

```327:341:backend/app/services/event_inbox.py
    if already is not None or card is None:
        return _insert(
            ...
            route="queued",
            ...
        )
    token = _reissue(db, user_id, session_id, locked, action, card)
```

`_reissue` cancels every open claim in the session:

```375:377:backend/app/services/event_inbox.py
    for claim in _open_claims(db, user_id, session_id):
        claim.status = "cancelled"
        _stamp_claim_phase(db, session_id, claim.token, "cancelled")
```

Chinese U8 is right about the source name and wrong about the function. The allow-list is only:

```35:35:backend/app/services/event_inbox.py
_SOURCES = frozenset({"internal", "mail"})
```

```610:613:backend/app/services/event_inbox.py
def _source(value: str) -> str:
    name = str(value or "").strip().casefold()
    if name not in _SOURCES:
        raise EventRejected(422, "这个来源不接入")
```

### 4. English calendar source `calendar` is rejected, and the seal omits `claim_token`

The English calendar unit says to file source `calendar` and delivery id `cal:{claim token}`, then call `note_external_success` with the armed request id. `_source` rejects `calendar` with 422, as quoted above. Even after the source is fixed, `note_external_success` loads the claim from the receipt, not from the caller's token argument:

```187:189:backend/app/services/event_inbox.py
        claim = _written_claim(db, user_id, receipt.claim_token, marker)
        if claim is None:
            raise EventRejected(409, "对方还没有接受这次写入")
```

The unit never says to set `claim_token`. Mail does that in `_bind_mail_claim` (`event_inbox.py` lines 707–737). A calendar receipt inserted without that binding seals nothing. This is why graft 1 is required before the English calendar unit can be handed to an implementer. The touch list includes `event_inbox.py`, but the text does not say to extend `_SOURCES` or to bind the token.

### 5. There is no current edge back to `running`

```22:27:backend/app/services/task_run.py
    "waiting_person": {
        "finish": ("keep", None),
        "wait_person": ("keep", None),
        "wait_resource": ("reject", None),
        "stop": ("write", "stopped"),
    },
```

```55:57:backend/app/services/task_run.py
        for effect, target in edges.values():
            if target == "running":
                raise RuntimeError("task run edge targets running")
```

`_validate_moves()` runs at import (line 73). Chinese U12 says `finish_local` sets the task from `waiting_person` back to `running` and enqueues the old executor. `finish` on that status keeps it. U7, U8, and U9 never add an edge, so a confirmed calendar, mail, or errand write would leave the job in `waiting_person`. The English `talk` unit is the one that adds `continue_job` from `waiting_person` or `waiting_resource` only, and keeps `completed` and `stopped` unable to take it. That is an extension of the merged machine, not a rewrite of the five statuses.

`finish_local` today does not enqueue anything:

```236:237:backend/app/services/local_action.py
    finish_confirm(db, key)
    return {"kind": "finished", "request_id": marker}
```

### 6. Chinese search fields do not match the parent list, and one of them matches the old Zhipu hit

The parent list for `POST /v1/tools/search` is `query`, `count`, `ori_question`, `localization`, `need_content`, and `results[]` of `url`, `title`, `snippet`, `summary`, `content`. Chinese U3 and its citations name request fields `query`, `count`, `need_content` and add response field `date`. The current Zhipu hit is where a date-like field actually exists:

```201:207:backend/app/services/web_search.py
            SearchHit(
                title=title or link or "未命名",
                url=link,
                snippet=content,
                media=str(item.get("media") or "").strip(),
                publish_date=str(item.get("publish_date") or "").strip(),
                refer=str(item.get("refer") or "").strip(),
            )
```

The English search unit names `query`, `count`, `ori_question`, and `need_content`, says `localization` is not sent, and reads `title`, `url`, `snippet`, `summary`, and `content`. That matches the parent list. Its URL construction also matches this tree: `llm_base_url` defaults to `https://api.deepseek.com` (`config.py` line 22 and `.env.example` line 3), so `{llm_base_url}/v1/tools/search` is the parent URL, not a doubled `/v1`.

Chinese U3 also tells the implementer not to declare server tools `deepseek_web_search` and `deepseek_read_url`. Those names are not in the parent list and are not in the release review of the completions page. That review names a different server tool and keeps Search as its own POST:

```15:15:docs/envisioned-product/review/ev-net.md
`CreateChatCompletionRequest` lists `messages`, `model`, `thinking`, `reasoning_effort`, `max_tokens`, `response_format`, `stop`, `stream`, `stream_options`, `temperature`, `top_p`, `tools`, `tool_choice`, `logprobs`, `top_logprobs`, `user_id`, and the deprecated `frequency_penalty` and `presence_penalty`. `tools` is function type only. The page has no networking property.
```

```17:17:docs/envisioned-product/review/ev-net.md
Web search on that same site is a different API. The Messages API has a server tool `web_search_20250305`. The Responses API has its own web-search tool. `POST /v1/tools/search` is a standalone search call. None of those is a field of `POST /chat/completions`.
```

The conclusion to keep search as a client tool, so a forbid can run before the HTTP call, is the right one. The names are not. The parent name for the server-executed Responses tool is `web_search`, and that call cannot be refused before the request leaves. The English plan rejects the Pro Responses path because of the pricing page and because it is PR 93. Graft 2 adds the refusal reason the English text skipped.

`tool_choice` is a listed completions field in `ev-net.md` line 15. Neither that note nor the parent fact list says `required` or a named tool returns 400. Current `_build_request` does not send `tool_choice` (`llm.py` lines 282–295). Both plans omit it. Do not promote the 400 sentence into a new constraint.

### 7. English watch contradicts itself, and Chinese watch does not report

Current silence is creation time minus in-process credit, and a `running` row at 20 minutes is stopped:

```16:19:backend/app/tasks/watchdog.py
CHECK_INTERVAL = timedelta(seconds=60)
SUSPENDED_SWEEPS = 3
STALL_FLOOR = timedelta(seconds=60)
ORDINARY_STALL = timedelta(minutes=20)
```

```99:116:backend/app/tasks/watchdog.py
def _silence(created_at: datetime, now: datetime, credit: timedelta) -> timedelta:
    return max(timedelta(0), _as_utc(now) - _as_utc(created_at) - credit)
...
        if _silence(run.created_at, now, credit[run.key]) >= ORDINARY_STALL:
            stops.add(run.key)
```

The ledger that credit lives in is process-local (`watchdog.py` lines 158–164). Both plans are right to stop using it as recovery.

The English watch unit says a notice is due when `last_progress_at` is unchanged since `notice_progress_at`, and the unit test says the second sweep with the same stamp inserts one notice and the third inserts none. The concurrency paragraph then says the update sets `notice_progress_at` only where it is null or different from `last_progress_at`, and the message is inserted in that same update. Those two predicates select opposite rows. An implementer cannot satisfy the test and that SQL from the text alone.

The same unit copies `created_at` into `last_progress_at` on backfill. That puts the old clock into the new column. Leave the column null until a real progress write.

The Chinese U6 text says the user sees one check notice after a quiet stretch, and also says an empty `TASK_STALL_REPORT_AFTER` does not stop and does not report. The empty default is the product default, so the landed knife does not report. That blocks the settled "check and report" behavior on a number the contract said not to invent. The English decision, one notice per unchanged stamp and no new duration, is the one to keep after the SQL sentence is deleted.

The beat of 60 seconds is the existing schedule (`celery_app.py` lines 50–52). Neither plan should replace it with a silence threshold.

### 8. English `web_fetch` is an unresolved fork against a four-name allow-list

```13:17:backend/app/agents/harness/constitution.py
_DEFAULT_OMNI: dict[str, Any] = {
    "mode": "standard",
    "allowed_tools": ["kb", "web", "calendar", "sandbox"],
    "tool_risk": {"kb": "low", "web": "medium", "calendar": "medium", "sandbox": "high"},
    "tool_effect": {"kb": "read", "web": "read", "calendar": "read", "sandbox": "read"},
```

`constitution.yaml` lines 84–88 are the same four names. The English search unit says the model sees `web` and `web_fetch`, or only the names already registered if `web_fetch` would be a second constitution entry. Those are two different tools. The forbid rule is clear (forbidding `web` forbids both) only after the second name exists. Register `web_fetch` as `effect: read`.

Calendar staying `effect: read` while it only prepares a card is current code, not a bug to reopen:

```965:972:backend/app/agents/tools/calendar_tool.py
register(
    ToolSpec(
        name="calendar",
        risk="medium",
        effect="read",
        execute=execute_calendar,
        description="Prepare a local calendar change for the user to confirm",
    )
)
```

### 9. Unknown local commit still calls `perform`; the armed check is what blocks the open

English desktop says a crash after arm returns `kind=unknown` from `local_action.py` lines 178–185 and `perform` is not called again. The server branch does return `unknown` without arming again:

```178:185:backend/app/services/local_action.py
    if claim is not None and claim.user_id == user_id and claim.status in {"written", "unknown"}:
        if claim.action != action.action or claim.fingerprint != fingerprint or claim.scope != scope:
            db.rollback()
            raise LocalClosed(409, CONFIRM_DETAIL["mismatch"])
        kind = "finished" if claim.status == "written" else "unknown"
        request_id = (claim.remote_request_id or "").strip()
        db.rollback()
        return _license(kind, request_id, action)
```

The package still calls `perform` on that license:

```17:28:desktop/confirm_local.py
def confirm_local(transport, offer, *, open_file, run_program) -> bool:
    committed = transport.post(
        "/api/desktop/commit",
        ...
    )
    if not perform(committed, open_file=open_file, run_program=run_program):
        return False
```

`perform` returns false before `open_file` unless `kind` is `armed`:

```6:8:desktop/executor.py
def perform(license: object, *, open_file, run_program) -> bool:
    if not isinstance(license, dict) or license.get("kind") != "armed":
        return False
```

The safety property is "open is not called." A test that asserts `perform` was not entered fails against the current caller. The English acceptance text already says a second confirm does not call open again. Keep that predicate, and change the earlier sentence so it does not say `perform` itself is skipped.

The probe labels the English unit cites are on this tree: `desktop/preload.js` lines 3–6 (`打开测试文件`, `运行测试程序`) and `desktop/confirm_local.py` lines 12–13 (`/tmp/ev-desktop-probe.txt`, `/usr/bin/true`). Chinese U12 correctly marks `preload.js` unread. Do not import the English probe paths into a unit that still says those files were not opened; the English desktop unit may keep them.

### 10. Checked claims that should stay, because they match this tree

These were opened and are not findings against either plan. They are listed so a later edit does not "fix" them.

- `begin_turn` stops other open rows in the session except a card-action marker (`task_run.py` lines 201–205 and 262–268). `open_alongside` passes `stop_others=False` (lines 253–259).
- Disconnect and cancel stop the bound generation (`chat.py` lines 526–528, 645–647, and 699–700). After `stream_chat` returns on `is_stopped()` (`llm.py` lines 489–494), `leave_stream` can still be false, so the buffers flush (`chat.py` lines 670–679). Artifact inference still runs (lines 691–696). The generator yields `done` at line 830.
- `ChatRequest` is text, session, knowledge-base flags, and an optional regenerate id (`chat.py` lines 54–59). The client body is the same fields (`frontend/src/api/client.ts` lines 309–317).
- The composer returns while `busy` and shows `停止生成` (`ChatWindow.tsx` lines 47–49 and 140–142). `startChatStream` returns when the tab is busy (`chatStream.ts` line 293). `busy` is tab state (`useStore.ts` lines 17–25).
- Follow-up shows `确认加到日历` when the card root `phase` is `draft` (`FollowupCard.tsx` lines 192–197). The plug note says the same thing and that a second insert is refused (`docs/envisioned-product/review/ev-plug-evidence/README.md` lines 28–30). Treating that button as the architecture target would reopen a historical display note. Folding it into `resume`, as the English plan does, does not make it the target.
- `route_intent` is defined at `workflow.py` line 877 and has no call site in this tree.
- `list_memories` uses `int(user_id or 1)` (`memory.py` lines 586–587). `remember_from_turn` returns 0 when a generation is set and the status is not `completed` (lines 657–658). `settle_pending` with `discarded` updates that row (lines 881–886). English R3 matches this and correctly does not schedule a memory knife. Chinese left R3 unread and did not schedule one either.
- A completed non-mail event calls `open_alongside` (`event_inbox.py` lines 239–248 and 430–443). Mail stores `received` / `filed` and does not open a turn (lines 430–441). Both plans leave that as a product choice. Do not reopen it.
- `commit_send` consumes, then `remote = str(uuid4())`, then arms, accepts, finishes, and seals (`mail_entry.py` lines 265–273). `GOOGLE_MAIL_SCOPES` is only `gmail.readonly` (`calendar_secrets.py` line 41). `gmail.py` lines 1–4 describe read-only meeting search.
- The unknown sentence is the calendar sentence for every claim (`schedule_confirm.py` line 46). English mail explicitly does not rewrite it. That is a labeled wording gap, not a reason to block the mail seam.
- `execution_denial` returns no denial when `task_id is None` (`execution.py` lines 25–26). A local tool is denied in the gateway before execute (`gateway.py` lines 161–178) because `local_tool_denial` refuses whenever `local` is true (`surface.py` lines 20–23) and `ServerSurface` has only `WEB` (lines 6–7).
- `recognize_package` compares one shared token and rejects the words `desktop` and `web` (`desktop_channel.py` lines 30–41). `PackageProof("desktop")` raises `TypeError` (lines 13–15). `desktop.py` lines 34–38 return 403 when the header is not a proof. `desktop/main.js` line 39 passes `DESKTOP_PACKAGE_TOKEN`.
- Desktop fingerprint parts are `local_target` and `local_args` with no device id (`schedule_confirm.py` lines 97–101). Scope is `desktop:user:{id}` (lines 163–165). The web bubble skips `mode === "desktop"` (`ChatWindow.tsx` line 238).
- `assign_turn` plans tools and does not call the gateway (`dispatch.py` lines 87–109). `answer_node` calls `complete_text` with `Role.CHAT` (`workflow.py` lines 582 and 755). `_refuse_outbound` rejects tools, `reasoning_content`, `role == "tool"`, and `tool_calls` (`llm.py` lines 244–256). `_wire_messages` forces `content` to a string (lines 258–268).
- Usage helper returns `None` when the number is missing (`llm.py` lines 356–368). The process counter then adds `prompt_tokens or 0` (line 374). Stream usage reads `completion_tokens` only when present (lines 465–474). English `LlmCall` nulls are the right durable shape. The in-memory counter can keep counting calls.
- Embedding and vision code defaults are `embedding-3` and `glm-4.6v` (`config.py` lines 36 and 39). `.env.example` lines 25, 31, and 109 show `embedding-3-pro` and `glm-4v-flash`. Ten turns and 32000 are `config.py` lines 87 and 90 and `.env.example` lines 120 and 123. `tabular_max_rows` is declared twice (`config.py` lines 130–133 and 164–167). None of that is a product defect to schedule.
- `web_search_count` default is 5 (`config.py` line 138), inside the 1–50 band both plans use. `max_upload_mb` default is 20 (line 128). `llm_timeout` default is 120 (line 23). Web search does not block startup (lines 198–199).
- Celery has `task_default_queue="default"` (line 14), `task_acks_late=True` (line 22), `task_reject_on_worker_lost=True` (line 23), and `visibility_timeout` of `max(3600, settings.celery_ingest_time_limit + 300)` (line 28). Worker init checks Zhipu endpoints and the app role (lines 66–81) and does not recover tasks. The latest revision file present is `backend/alembic/versions/0017_errand.py`.
- `TaskRun` has the session-status index Chinese U6 names (`models.py` line 104). Chat message `content` is `Text` (line 52). Document uploads are the MinIO path (`models.py` line 122 and `config.py` lines 59–62). Ingest OCR posts `image_url` to the vision client (`vision_ocr.py` lines 17–46). `Secret.__str__` is empty (`errand_order.py` lines 20–21), and `_reject_secrets` rejects `password`, `pan`, `cvv`, `cookie`, and `secret` (lines 5 and 100–104). `claim_parts` has no correlation id (lines 56–70).
- `decide_replay` for `non_replayable_write` returns `reconcile` (`effects.py` lines 34–35). `unknown_write` is read in `workflow.py` lines 318–319, declared on `types.py` line 73, and constructed in `backend/tests/test_work_loop.py`. No production tool assignment was found. Both plans say that. Do not pretend the production tools already fill it.
- `generation_stop.bind` stores one user and one generation (`generation_stop.py` lines 22–23). `is_stopped` reads that generation (lines 30–35).

PR 93 is not used as source or target by either plan. R1, R4's full-table scan, R5, R6, and R7 are left as history or as a labeled unread, except where the findings above re-opened the current function. That is the right treatment.

## What an implementer still has to resolve inside the English base

Delete the watch update predicate that fires when the stamp changed. Notice once when `last_progress_at` is non-null and equal to the stamp already noticed, and do not backfill from `created_at`. File calendar receipts with source `internal`, set `claim_token`, and match the armed `requestId`. State the `talk` worker's pre-loop body. Register `web_fetch`. Say that `perform` may be entered and `open_file` may not. Add the parent sentence that server-side Responses `web_search` cannot be refused before the request leaves.

Mail transport and the browser dependency stay operator gates. The silence interval does not. An empty transport does not block the mail seam. A failed browser check does not block `errand` and does not add a library.

## Dropped

14 findings were dropped because the file that would support them was not opened:

1. The live create-chat-completion page's actual `tool_choice` 400 text.
2. Vision-guide per-image size, URL length, and `detail`.
3. The 48 MiB request-body sentence as a false number.
4. Search-guide empty-result billing and the exact retry paragraph.
5. Hermes `conversation_loop.py` / `session_stall.py` bodies.
6. OpenCode `queue.ts` default steer.
7. DS-Bot send and confirm-skip bodies.
8. OpenMausBot `devices.ts`.
9. OpenClaw, FullDots, and Rowboat approval bodies, counted as one dropped upstream-approval finding.
10. The RLS text inside `0014_event_receipt.py` and `0015_memory_ledger.py`.
11. The body of `void_draft_schedule_cards`.
12. Local `create_event` and whether a non-Google create has an event id.
13. `_write_result_unknown` and the empty-403 branch.
14. `services/errand-compose/driver.py`, `RedactBuffer`, the PR 93 diff, and the compose file that would show who consumes the default queue.
