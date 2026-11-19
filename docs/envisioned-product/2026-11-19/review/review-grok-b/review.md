grok-4.7-xhigh-fast

## Scores

| Criterion | candidate-grok | candidate-grok-xhigh |
| --- | --- | --- |
| 1. Settled decisions stay closed | 4 | 5 |
| 2. Current-code claims match the release tree | 4 | 5 |
| 3. Each unit is executable from the text | 3 | 4 |
| 4. Dependencies are real, and the 14 knives stay done | 3 | 4 |
| 5. Tests, formal scope, and live predicates | 3 | 4 |
| 6. Open questions, and the browser gate | 3 | 4 |

## Base

`/tmp/arena-plan/candidate-grok-xhigh` is the base a later maintainer should extend.

The unit split follows the functions that change together. `talk` adds `continue_job` and says the validator must allow `waiting_person` and `waiting_resource` to return to `running`. `calendar` keeps the armed `requestId` as the `note_external_success` match key and puts the Google event id in the receipt body. `errand` keeps the compose service and does not seal the inbox. `watch` reports one notice per unchanged progress stamp and does not invent a stall duration. `open.md` leaves mail transport and the browser library as gates. PR 93 is not the target. R2 and R3 stay closed.

The contract asked for a Chinese design. This base is English. Extend the English text. Translating it is not a reason to switch to the other plan.

## Grafts

Take these two pieces from `/tmp/arena-plan/candidate-grok` and apply them onto the base.

1. **Confirm-only write tools, and no server-executed search.** Chinese U3 puts `mail.send`, `errand.place`, `desktop.open`, and `desktop.run` on the executor as tools that only propose a claim. The base loop builds the tool list from the constitution, whose names are `kb`, `web`, `calendar`, and `sandbox`. Without this graft, `desktop` describes a card the model cannot propose. Also keep Chinese U3's refusal to declare a search tool that runs on the provider before the executor can reject it. The parent page list says Responses `web_search` runs on the server for `deepseek-v4-pro` and is ignored on `deepseek-flash`.

2. **Receipt source `internal`.** Chinese U8 uses source `internal` for a calendar receipt. That name is already allowed. The base's source `calendar` is rejected by `_source`. Keep the base's match key: the armed `requestId`, with the event id in the body.

## Rejected

From the Chinese plan:

- `note_external_success(..., remote_request_id=google_id)` and the same call with `merchant_order_id`. The match key is the armed id already stored on the claim.
- Calling `note_external_success` from the errand path. The merged errand check forbids that string, and `_save` finishes the claim without an inbox seal.
- Stopping compose `place` when no connector row exists. Compose is the current channel. The browser gate blocks a new library.
- An empty `TASK_STALL_REPORT_AFTER` that neither stops nor reports. The settled behavior is to check silence and report it. The missing number does not block that.
- A watchdog effect named `report`. The import-time validator only allows `stop` and `leave`, and `running` must stay `stop` until that validator is edited. The base can leave the table in place and change `decide`.
- Restoring `waiting_person` to `running` with the existing events. `finish` on that status keeps it, and any edge whose target is `running` fails import.
- Search result field `date`. The parent's 2026-11-19 field list for `results[]` is `url`, `title`, `snippet`, `summary`, `content`.
- The U7 to U11 chain. Mail, calendar, errand, and device enrollment do not wait on each other. The Chinese plan says U8 waits on U7 only to stagger write paths.
- Clipping a search query inside the executor. The base returns a tool error and lets the model shorten it.

From the base, do not extend these as written:

- `EventReceipt` source `calendar`.
- The migration that copies `created_at` into `last_progress_at`. A new job should leave the stamp null until the worker saves progress. Old rows should not inherit the creation clock.
- A receipt that never sets `claim_token`. `note_external_success` loads the claim from that column.

## Findings

### 1. Chinese calendar seal uses the Google event id as the match key

Chinese U8 tells the implementer to call `note_external_success` with the Google id after `finish_confirm`. The inbox compares that argument to the id already armed on the claim.

```577:580:backend/app/services/event_inbox.py
    if claim is None or claim.user_id != user_id or claim.status != "written":
        return None
    if (claim.remote_request_id or "").strip() != remote_request_id:
        return None
```

```187:189:backend/app/services/event_inbox.py
        claim = _written_claim(db, user_id, receipt.claim_token, marker)
        if claim is None:
            raise EventRejected(409, "对方还没有接受这次写入")
```

The armed value is the private `requestId`, generated before insert:

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

An implementer who follows the Chinese acceptance ("`external_request_id` is `evt-1`") stores the event id where the lookup key must stay. The base avoids this: the marker stays the armed request id, and the event id goes in the receipt body.

### 2. A calendar receipt has to carry the confirm token, and `submit_event` is a router

`note_external_success` does not take the confirm token as its own argument. It reads `receipt.claim_token`. Mail binds that column through a mail-only helper:

```717:735:backend/app/services/event_inbox.py
    receipt = _existing(db, user_id, "mail", delivery)
    if receipt is None or receipt.route != "filed":
        raise EventRejected(409, "回执不存在")
    current = (receipt.claim_token or "").strip()
    if current == token:
        return _view(db, receipt, created=False)
    if receipt.state == "external_success":
        raise EventRejected(409, "回执已经绑定另一张确认")
    if current:
        bound = db.get(ScheduleConfirmClaim, current)
        if bound is not None and bound.status != "cancelled":
            raise EventRejected(409, "回执已经绑定另一张确认")
    claim = db.get(ScheduleConfirmClaim, token)
    if (
        claim is None
        or claim.user_id != user_id
        or claim.action != "mail.send"
        or claim.status not in {"open", "unknown", "written"}
    ):
```

Chinese U8 says to call `submit_event` before arm, with `delivery_id` equal to the claim token and source `internal`. `submit_event` routes. `model_decision="ignore"` inserts a row whose `claim_token` stays empty. `model_decision="act"` can open another task. The base says to file a receipt and then call `note_external_success`, and it cites the match, but it never says to set `claim_token`. Extending the base means: insert one receipt, source `internal`, `claim_token` equal to the confirm token, `external_request_id` left empty until success, then pass the armed `requestId` as the marker.

### 3. Chinese errand success calls the inbox with the merchant order id

Chinese U9 seals with `note_external_success` and `merchant_order_id`. The armed value is a uuid written before the shop call:

```160:161:backend/app/services/errands.py
    armed = str(uuid4())
    arm_remote_claim(db, key, armed)
```

The merged check rejects that call, and the review of the merged knife records the same boundary:

```81:81:verification/errand-place/check.py
    require("note_external_success" not in errands, "the errand path notes external success")
```

`docs/envisioned-product/review/ev-errands.md` line 17: `_save` inserts `ErrandRecord` and then `finish_confirm`. It does not call `note_external_success`. The base keeps that boundary. The public reply still has to echo the armed id, and `placed` still requires that echo plus a merchant order id.

### 4. Chinese U12 returns a waiting job to `running` with no legal edge

```22:26:backend/app/services/task_run.py
    "waiting_person": {
        "finish": ("keep", None),
        "wait_person": ("keep", None),
        "wait_resource": ("reject", None),
        "stop": ("write", "stopped"),
    },
```

```56:57:backend/app/services/task_run.py
            if target == "running":
                raise RuntimeError("task run edge targets running")
```

`_validate_moves()` runs at import. Chinese U12 says `finish_local` sets the same task back to `running` and does not add an event. The base adds `continue_job` from the two waiting statuses only, and it says the validator changes with that event. `completed` and `stopped` still reject it.

### 5. Chinese U6's `report` effect fails the watchdog import check

```42:46:backend/app/tasks/watchdog.py
        if effect not in {"stop", "leave"}:
            raise RuntimeError("watchdog effect is unknown")
        if status == "running":
            if effect != "stop":
                raise RuntimeError("running watchdog effect is not stop")
```

The current stop is creation age, and it is real:

```99:100:backend/app/tasks/watchdog.py
def _silence(created_at: datetime, now: datetime, credit: timedelta) -> timedelta:
    return max(timedelta(0), _as_utc(now) - _as_utc(created_at) - credit)
```

```116:117:backend/app/tasks/watchdog.py
        if _silence(run.created_at, now, credit[run.key]) >= ORDINARY_STALL:
            stops.add(run.key)
```

The base removes that stop from `decide` and writes one notice per unchanged `last_progress_at`. It can do that without renaming the effect. Chinese U6 also says an unset `TASK_STALL_REPORT_AFTER` inserts no notice. That drops the settled "check silence and report it" behavior until someone invents a duration. The base's open point is the right shape: the number is unset, one notice still happens, and `watch` is not blocked.

### 6. Chinese U9 turns the browser gate into a shutdown of compose

With no connector row, a configured compose URL is a ready channel, and `_place` posts to it:

```380:383:backend/app/services/errands.py
def _channel_ready(connector: MerchantConnector | None) -> bool:
    if connector is not None:
        return bool(str(connector.place_url or "").strip())
    return bool((settings.errand_compose_url or "").strip())
```

```277:281:backend/app/services/errands.py
    body = post_json(
        _tasks_url(),
        headers=_headers(armed, compose=True),
        payload=_compose_payload(order, armed, pay, login, "place"),
        timeout=_timeout(),
    )
```

`ev-errands.md` lines 9–11 describe the same split: a connector posts `place_url`, and no connector posts compose `/tasks`. Chinese U9 and `open.md` return "no ordering interface" in that case. The base leaves compose in place and keeps the new browser library behind the gate.

The empty-echo bug both plans fix is real:

```367:368:backend/app/services/errands.py
    if outcome == "placed" and not (echoed_key and echoed_key != armed):
        return ShopReply("placed", order_id=order_id, told=told, clicks_after_wall=clicks, price=price, named=True)
```

`_apply_reply` then saves when `order_id` is non-empty (`errands.py:220-221`). A `placed` body with an order id and no echo becomes a record today. Both plans close that. The review sentence that a bare `已下单` page never places is narrower than this branch. Both plans say so. That part stays.

### 7. Base source `calendar` is not an inbox source

```35:35:backend/app/services/event_inbox.py
_SOURCES = frozenset({"internal", "mail"})
```

```610:613:backend/app/services/event_inbox.py
def _source(value: str) -> str:
    name = str(value or "").strip().casefold()
    if name not in _SOURCES:
        raise EventRejected(422, "这个来源不接入")
```

`note_external_success` calls `_source` before it looks up the row (`event_inbox.py:177`). The base's calendar unit files source `calendar`. Graft 2 replaces that name with `internal`. The delivery id `cal:{claim token}` can stay.

Putting the call in `backend/app/api/calendar.py` also fails two merged checkers:

```108:108:verification/errand-place/check.py
    require("note_external_success" not in calendar, "calendar confirm notes external success")
```

```80:81:verification/mail-entry/check.py
    calendar = read("backend/app/api/calendar.py")
    require("note_external_success" not in calendar, "calendar confirm notes mail success")
```

`_seal_schedule_write` is only `finish_confirm` today (`backend/app/api/calendar.py:498-501`). The new seal has to update these checkers, or the call has to live outside `calendar.py`. Neither plan names those two lines.

### 8. The base loop cannot see mail, errand, or desktop tools

```15:17:backend/app/agents/harness/constitution.py
    "allowed_tools": ["kb", "web", "calendar", "sandbox"],
    "tool_risk": {"kb": "low", "web": "medium", "calendar": "medium", "sandbox": "high"},
    "tool_effect": {"kb": "read", "web": "read", "calendar": "read", "sandbox": "read"},
```

```40:43:backend/app/agents/tools/registry.py
    from app.agents.tools import calendar_tool as _cal  # noqa: F401
    from app.agents.tools import kb as _kb  # noqa: F401
    from app.agents.tools import sandbox_tool as _sb  # noqa: F401
    from app.agents.tools import web as _web  # noqa: F401
```

`desktop.open` and `desktop.run` exist as claim actions (`backend/app/services/schedule_confirm.py:38-39`), and the calendar tool is registered as effect `read` while its description says it prepares a confirmation (`backend/app/agents/tools/calendar_tool.py:965-972`). The base says the job's tool names are the existing constitution names, then later says a job proposes `desktop.open`. Graft 1 is the missing registration. Those four names stay confirm-only. `invoke` must not perform the write.

### 9. Copying `created_at` into `last_progress_at` keeps the creation clock for the first notice

The silence function subtracts `created_at` (`watchdog.py:99-100`). The base migration copies that column into `last_progress_at` for existing rows, and `watch` treats an unchanged stamp as one notice. A running row that has not saved progress yet is then "silent since creation." Leave the new column null until a worker saves a step. `note_progress` in the Chinese U1 sense, which refuses to fill from `created_at`, is the right rule. The rest of Chinese U6 is not.

### 10. A device id on `PackageProof` does not distinguish devices until equality changes

```10:21:backend/app/agents/harness/desktop_channel.py
class PackageProof:
    __slots__ = ()

    def __init__(self, source: object) -> None:
        if isinstance(source, str) or source is not _SEAL:
            raise TypeError("PackageProof is not built from a client string")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PackageProof)
```

Both plans say the proof carries a device id and still cannot be built from the string `desktop`. The current class rejects every attribute write, and any two proofs compare equal. The device unit has to replace `__slots__`, `__setattr__`, and `__eq__`. `recognize_package` today compares one shared token (`desktop_channel.py:30-41`). Both plans retire that token. That part is right. `desktop/main.js:39` does pass `DESKTOP_PACKAGE_TOKEN` through, as the base says.

### 11. Chinese chat-request summary drops `document_ids`

```54:59:backend/app/api/chat.py
class ChatRequest(BaseModel):
    session_id: int
    message: str = ""
    use_kb: bool = False
    document_ids: list[int] = Field(default_factory=list)
    regenerate_message_id: int | None = None
```

The Chinese plan describes this block as session, text, the knowledge-base switch, and the regenerate id. The base's picture unit cites `chat.py:54-59` and `frontend/src/api/client.ts:309-314`, which send `document_ids`. An image field is added beside that list, not instead of it.

### 12. Checked anchors that both plans got right

These were opened and match. They are not defects.

- Release head `d7710d687d147aeeda502d412454ab8a3829af66`. Alembic head revision string is `0017_errand` (`backend/alembic/versions/0017_errand.py:8`). There is no `ev-gate.md` or `ev-plug.md` under `docs/envisioned-product/review/`.
- Outbound chat refuses tools and reasoning (`backend/app/core/llm.py:244-256`). `_wire_messages` forces string content (`258-268`). `_record_call` does `prompt_tokens or 0` (`374`). `stream_chat` returns on `is_stopped()` inside the chunk loop (`489-494`). `chat.py:670-679` then flushes the buffers when `leave_stream` is still false, `691-696` can emit an artifact, and `830` yields `done` from `finally`.
- `begin_turn` stops other open rows (`task_run.py:201-205`, `262-268`). `open_alongside` does not (`253-259`). `route_intent` is defined at `workflow.py:877` and a repo search shows no caller.
- `assign_turn` returns a plan and does not call the gateway (`dispatch.py:87-109`). `gateway.invoke` is at `workflow.py:474`. Local tools are denied before execute (`gateway.py:163-178`). `ServerSurface` is only `WEB` (`surface.py:6-23`).
- Zhipu search posts `{base}/web_search` (`web_search.py:58-62`) with `search_query` (`255-262`). `execute_web` calls `search_planned` (`web.py:10-11`). Chat default is `deepseek-flash` (`config.py:25`). Embedding default is `embedding-3` (`36`). Vision default is `glm-4.6v` (`39`). `.env.example:25` is `embedding-3-pro`, `:31` is `glm-4v-flash`, `:109` repeats those names. `tabular_max_rows` is declared twice (`config.py:131` and `165`).
- `commit_send` uses `str(uuid4())` (`mail_entry.py:269`). Gmail scope is `gmail.readonly` (`calendar_secrets.py:41`). `gmail.py:1` and `57-64` are read-only meeting search.
- `lookup_inserted_event` only GETs (`calendar_google.py:1234-1266`). `_keep_found_create` does not insert (`calendar.py:519-558`).
- `_public` returns `outcome`, `order_id`, `told`, `clicks_after_wall` (`services/errand-compose/server.py:22-27`). `claim_parts` has no armed id (`errand_order.py:56-70`).
- Watchdog beat is schedule `60` (`celery_app.py:50-54`). `task_acks_late`, `task_reject_on_worker_lost`, and `visibility_timeout = max(3600, celery_ingest_time_limit + 300)` are `celery_app.py:22-28`. The default queue exists (`14`). `load_runs` reads every `TaskRun` (`watchdog.py:126`).
- The stop button is `ChatWindow.tsx:140-143`. Send returns when `busy` (`49`). `startChatStream` returns when the session is busy (`chatStream.ts:293`). `FollowupCard.tsx:192-197` shows `确认加到日历` when `card.phase === "draft"`. Action rows gain their own `phase` only in `_stamp_claim_phase` (`schedule_confirm.py:890-893`). `FollowAction` in `client.ts:59-68` has `confirm_token` and no `phase` field. The base's button change matches the server stamp and the note in `ev-plug-evidence/README.md:28`.
- `list_memories` uses `int(user_id or 1)` (`memory.py:587`). `remember_from_turn` returns 0 when a generation is set and the status is not `completed` (`657-658`). `settle_pending` with `discarded` updates that same row (`881-886`). A completed target calls `_open_task` (`event_inbox.py:239-248`). Mail does not open a turn (`430-441`).
- `perform` runs only `kind == "armed"` (`desktop/executor.py:7`). Probe labels are `desktop/preload.js:4-5`. Probe paths are `desktop/confirm_local.py:12-13`. `commit_local` returns `kind=unknown` without arming again (`local_action.py:178-185`).
- Ingest OCR posts `image_url` to the vision endpoint (`vision_ocr.py:17-42`). Document bytes go to MinIO (`backend/app/storage/s3.py:55`, `config.py:59-61`). `0014_event_receipt.py:15` and `:61-66` use `app.current_user_id` and enable row level security.
- `llm_base_url` is `https://api.deepseek.com` (`config.py:22`), so the base's `{llm_base_url}/v1/tools/search` is one `/v1`, not two. `web_search_count` defaults to 5 (`138`). `llm_timeout` defaults to 120 (`23`). `max_upload_mb` defaults to 20 (`128`).

Parent field list, used as the wire contract: Search body `query`, `count`, `ori_question`, `localization`, `need_content`. Search hits `url`, `title`, `snippet`, `summary`, `content`. Fetch body `url`. Fetch response `title`, `content`. A request with tools must send prior `reasoning_content` or the API returns 400. A request without tools ignores `reasoning_content`. `deepseek-flash` accepts user `image_url` content. Assistant-message images return 400. Both plans follow that thinking and vision split. The Chinese plan adds `date` on search hits. The base does not.

## Dropped

10 findings were dropped because the cited file was not opened:

1. The Alembic revision that created `schedule_confirm_claims.fingerprint`, so no claim about a varchar length.
2. `calendar_google.py` `_write_result_unknown` and the empty-403 branch.
3. `desktop/actions.js`.
4. `services/errand-compose/driver.py` and `decide.py`.
5. Upstream trees named in the pins. Both plans say those function bodies were not re-opened. No pin was checked against source.
6. The PR 93 tree.
7. `RedactBuffer`.
8. The policy body of `0015_memory_ledger.py` below the tenant string.
9. Calendar delete and RSVP function bodies.
10. `backend/app/services/calendar.py` `create_event`.

Vendor pages other than the parent's field list were not opened, so no finding is kept about 32 MiB, 48 MiB, an 8192-character URL, `tool_choice` returning 400, search retry statuses, empty-result billing, or the names `deepseek_web_search` and `deepseek_read_url`.
