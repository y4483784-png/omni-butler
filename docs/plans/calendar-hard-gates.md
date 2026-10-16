# 日历硬门禁

本文只定计划，不改产品代码，也不写页面脚本。对应 PR 只含本文。

读者是改 Omni-Butler 日历代码的工程师。这是合并前必须通过的 pytest / CI 门禁，不是给人逐句点的页面脚本册。约 100 条页面脚本是另一份交付（下面称 Plan 1），不在本文，也不在本文对应的后续测试 PR 里。

守住已经落地的行为。门禁打在现有边界上：`execute_calendar`（`backend/app/agents/tools/calendar_tool.py`）、确认接口（`POST /api/calendar`、`POST /api/calendar/google/confirm`、`PATCH` / `DELETE /api/calendar/{id}`）、卡片字段、空档重叠过滤（`_without_busy_overlap`）、空档说明替换（`_replace_unreliable_free_note`）、`CalendarTransientError` 与 `CalendarTokenError`。模型返回值由测试给定。不打真 Google，不打真模型。

`now` 沿用现有日历测试：`2026-10-15`，已连接时区 `Asia/Singapore`，除非某条旧测试已经固定成别的日子。

## 1. 必须通过的清单

五条都是合并门槛。已有测试锁住的，保持绿色，不要为了“再写一遍”去改断言。缺口只在第 3 节点名的扩展和新测试。

| ID | 必须成立 |
| --- | --- |
| G1 | 聊天不写 Google。`execute_calendar` 对创建、改期、撤销、列表、空档只出卡片或文字。`events.insert` / `events.patch` / `events.delete` 只出现在用户确认之后的接口：创建是 `POST /api/calendar`，已连接的改期和撤销是 `POST /api/calendar/google/confirm`，未连接的改期和撤销是 `PATCH` / `DELETE /api/calendar/{id}`。确认前本地行的标题、起止、`status` 不变。 |
| G2 | 已连接、且预填对上唯一一条现存日程时，改期卡和撤销卡带上该条的 Google `external_id`。确认请求里有这个 id 时，patch 或 delete 的 URL 就是这个 id，即使用户改了标题。两条同标题、卡片又没有 id 时，确认返回 409，两条都不删、都不改。没有 id、且主日历上恰好一条同标题时，仍允许按标题精确匹配（`docs/plans/calendar-three-fixes.md` 第 2 节）。对不上的 id 或对不上的标题都是 409，不写。 |
| G3 | 会议卡和时间块卡字段不同。`kind=block` 的创建卡和改期卡没有参与人、没有地点，只有标题、开始、由起止推出的时长、备注。`kind` 为空或其他值按 meeting。meeting 保留参与人。撤销卡不按 `kind` 换成时间块表单，确认不改 description。备注是可空的 `description`：用户没说就不进 `missing_fields`；确认时写入 Google 事件的 `description` 和本地行的 `description`；空字符串也可以确认。Google 请求体没有 `attendees`，参与人只留在本地行。 |
| G4 | 空档不把已占用的时间说成有空。先列出当天日程，再采用模型给的时段。和已列出事件重叠的时段丢掉；只是钟点（含模型给的 `00:00`）不是丢掉的理由。丢掉之后，说明若仍把那段说成有空，改成「请以上面的列表为准，已有安排的时段不是空闲。」说明自己已经承认重叠的，保留原句。读失败不出时段，也不把没读到的时间说成空闲。 |
| G5 | 连不上和授权失效不是同一句话，在已经分得开的路径上保持分开。时区 GET 或令牌刷新的传输错误、以及 500/502/503/504，是 `CalendarTransientError`：「暂时连不上 Google 日历，请稍后再试。」聊天里这句不是「工具治理拒绝」，也不含「重新连接」。确认接口这时是 502，不写 Google，不插本地行。401/403 以及刷新 400 仍是 `CalendarTokenError`：「Google 日历授权已失效，请重新连接。」确认接口是 401。两种失败都不写。`events.insert` 返回 500 仍是现有的写入失败（502，详情含「未保存」），不要改口成上面两句。 |

G4 里「先列出当天」能自动锁住的部分：`list_events` 的窗口是点名的那一天（00:00 到次日 00:00），卡片 `items` 含列出的标题和墙钟，`suggested_slots` 来自模型并经过重叠过滤，`direct_answer` 是说明句或上面那句安全说明，不调用 `find_free_slots`，不写库。气泡里列表卡排在文字上面，由前端源码测试锁顺序。人眼是否先看到日程、以及模型换一种说法绕过「有空 / 空闲 / 方便 / 可以」，留在 Plan 1。

## 2. 哪些进 pytest，哪些留在页面

| 门禁 | pytest（现有文件） | 只留页面（Plan 1） |
| --- | --- | --- |
| G1 确认前不写 | `backend/tests/test_calendar_card.py`、`backend/tests/test_calendar_google.py`、`backend/tests/test_calendar_dialogue.py` | 桌面里点确认之前，Google 网页上没有新日程。侧栏和聊天同时看一眼。 |
| G2 带 Google id，按 id 删 | `backend/tests/test_calendar_google.py`。前端提交体：`frontend/tests/scheduleCard.test.mjs` | 人在卡片上改了标题再点确认，Google 上删的仍是原来那一条。 |
| G3 会议 / 时间块 / 备注 | `backend/tests/test_calendar_card.py`、`backend/tests/test_calendar_google.py`。表单有没有参与人栏：`frontend/tests/scheduleCard.test.mjs` | 时间块卡上看不到参与人栏；两张创建卡都能手填备注；确认后在 Google 事件说明里看得到。 |
| G4 忙时不当有空；先列表 | `backend/tests/test_calendar_card.py`。列表卡在文字上面、没有确认按钮：`frontend/tests/scheduleCard.test.mjs` | 真气泡里先看到当天那几行，再看到建议。模型用词避开第 1 节那些字时，人还是能看出它没有把忙时说成有空。点「用这个时段」只换成创建草稿。 |
| G5 网络和授权 | `backend/tests/test_calendar_google.py` | 真断网、真过期同意屏幕上的文案。假客户端分得开的路径不必再手测一遍。 |

不进门禁的现有行为，测试仍要绿，但不是这五条的新增范围：OAuth 同意跳转、令牌密文、断开连接、列表合并、只读 Google 项不出改期卡、工作时间解析、`Etc/GMT` 兜底。它们已经在 `test_calendar_google.py` 里，日历 PR 跑整文件时会一起过。

`.github/workflows/eval.yml` 的 offline job 已经 `python -m pytest tests -q`，所以上面的 Python 文件在每次 push / PR 上会跑。同一 workflow 的 frontend job 目前只跑 `frontend/tests/scheduleExpiry.test.mjs`，不跑 `scheduleCard.test.mjs`。第 3 节的后续 PR 要把这一个 node 测试加进该 job。本文不改 workflow。

## 3. 测试名和断言

断言看行为：卡片字段、接口状态码、用户看得到的句子、假 Google 的 insert/patch/delete 有没有发生、本地行有没有变。不断言私有函数被调用了几次，除了 G4 已经在用的「空档路径不得调用 `find_free_slots`」。

### 已经是门禁、不要改写

这些测试就是 G1–G5 的现锁。后续 PR 只在第 3 节后半追加断言；失败时先看是不是产品退步，不要放宽下面这些。

G1

- `test_create_phrase_returns_draft_and_writes_nothing`：创建是 `phase=draft`，库行数不变。
- `test_cancel_one_match_is_a_card_not_a_delete`：一条匹配是撤销卡，不是删除。
- `test_unconfirmed_create_then_reschedule_does_not_move_the_event`：未确认的下一句不改已有行。
- `test_conflict_preview_offers_slots_without_insert`、`test_personal_block_conflict_stays_on_the_card`：冲突留在卡上，不插入。
- `test_connected_reschedule_and_cancel_write_google_only_after_confirm`：确认前无 patch、无 delete；确认之后才有。
- `test_suggest_returns_slot_card_without_writing`、`test_named_day_list_appends_advice_and_writes_nothing`：列表和空档不写库。
- `test_dialogue_create_list_move_conflict_and_cancel`：对话路径同一条边界。

G2

- `test_connected_prefill_soft_aligns_one_live_title`：唯一软匹配时卡上 `external_id` 是那条 Google id，确认前无 delete。
- `test_confirm_prefers_prefilled_external_id_over_exact_title`：带 id 时 patch/delete 打到该 id；标题对不上且没有 id 是 409；未知 id 是 409，且没有 delete。
- `frontend/tests/scheduleCard.test.mjs` 的 `google confirm submits the card external id`：改期和撤销的确认体带上卡片的 `external_id`。

G3

- `test_create_kind_splits_meeting_and_block`：block 卡 `participants == []`，即使模型给了参与人；空 `kind` 按 meeting；不写库。
- `test_update_kind_follows_the_matched_event_and_cancel_does_not`：改期表单跟已匹配事件走；撤销卡 `kind == ""`，确认前 `status` 仍是 `active`。
- `test_notes_prefill_description_and_stay_optional`：两张卡的 `description` 是备注；缺备注不进 `missing_fields`。
- `test_create_notes_are_written_to_google_description`：确认后 Google `description` 和本地行一致；空备注仍可确认，Google `description` 为 `""`。
- `test_cancel_confirm_does_not_change_description`：撤销确认不提交对 description 的修改。
- `test_update_keeps_matched_duration_and_notes_unless_asked`、`test_reschedule_ignores_filled_end_and_notes_without_change_flags`、`test_connected_reschedule_keeps_duration_and_notes_unless_flags`：没说改结束或改备注时，时长和备注留在对上的那条上。时间块改期卡 `participants == []` 且 `location == ""`。
- `test_connected_create_writes_google_then_local_wall_clock`：Google 体没有 `attendees`，参与人留在本地行。
- `scheduleCard.test.mjs` 的 `block form shows duration and hides participants`、`cancel card title is editable`。

G4

- `test_free_slot_lists_the_day_then_uses_model_slots`：先列出「周会」，`suggested_slots` 是模型的 `15:00–16:00`，提示含「不能说成有空」，不调用 `find_free_slots`，不写库。
- `test_free_slot_drops_overlapping_slots_but_not_by_hour`：重叠段丢掉；终点相接的下一段留下；午夜段留下。
- `test_free_slot_note_does_not_call_a_busy_hour_free`：丢掉 `10:00–11:00` 后，说明不再是「10点有空」，而是「请以上面的列表为准，已有安排的时段不是空闲。」
- `test_free_slot_empty_when_every_slot_overlaps`：说明已经承认重叠时保留原句，`suggested_slots` 为空，列表还在。
- `test_free_slot_read_failure_skips_the_slot_model`：读失败的文字是「暂时读不到 Google 日历，不能建议空闲时段。」，`schedule_card` 为 `None`。
- `test_free_slot_keeps_a_model_midnight_slot`：模型给的 `00:00–01:00` 照样留下。
- `scheduleCard.test.mjs` 的 `list card is time and title without a confirm button`、`list card renders above the advice text`。

G5

- `test_transient_error_is_not_a_token_error`：瞬时错误不是授权错误的子类，文案不含「重新连接」。
- `test_timezone_get_retries_once_then_returns_the_zone`：传输错误重试一次后仍用原来的时区。
- `test_timezone_outage_is_transient_and_status_stays_connected`：500 是瞬时错误；状态接口仍是已连接。
- `test_refresh_network_is_transient_and_keeps_tokens`、`test_refresh_400_stays_token_error_and_keeps_tokens`：刷新的网络失败是瞬时错误；刷新 400 是授权失效；两种都不改已存令牌。
- `test_chat_timezone_outage_is_transient_not_a_governance_denial`：聊天 `direct_answer` 是瞬时文案，`denied` 为假，没有「工具治理拒绝」，没有卡片。列表退回本地行并说明读不到。空档是「暂时读不到 Google 日历，不能建议空闲时段。」
- `test_confirm_post_timezone_outage_is_502_and_writes_nothing`：确认 502，详情是瞬时文案，不含「重新连接」，无 insert，无新本地行。
- `test_google_insert_failure_writes_no_local_row`：insert 500 仍是 502 且详情含「未保存」，无本地行。不要把它改成 G5 的两句。

### 要扩展的

都在原测试上加断言，不新开文件。

`test_connected_prefill_leaves_an_exact_title`（`test_calendar_google.py`）

- 唯一精确标题时，`schedule_card["external_id"] == "evt-real"`。
- `event_id` 仍是 `None`。确认前 delete 仍为空。

`test_connected_prefill_leaves_zero_or_many_soft_hits`（同文件）

- 零条和多条软匹配时，`external_id` 都是 `""`。
- 仍不发 patch、不发 delete。

`test_create_kind_splits_meeting_and_block`（`test_calendar_card.py`）

- 模型给 block 时即使带了地点，卡片 `location == ""`，`participants == []`。
- 模型给 meeting 时地点和参与人留在卡上。

`test_create_notes_are_written_to_google_description`（`test_calendar_google.py`）

- `execute_calendar` 返回之后、`POST /api/calendar` 之前：insert、patch、delete 都是空列表，本地行数不变。卡片 `phase=draft`。
- block 那次 insert 的 JSON 没有 `attendees`。本地行参与人是空的。meeting 的参与人在本地行里，不在 Google 体里。

### 要新增的

`test_connected_chat_never_writes_google_before_confirm`（`test_calendar_google.py`）

一条已连接用户，假 `events.list` 里有一条「测试会议」。依次用给定抽槽跑创建、改期、撤销、列表、空档。

- 五次之后 insert、patch、delete 仍为空，本地行数不变。
- 创建卡 `mode=create`、`phase=draft`。改期卡 `mode=update` 且 `external_id` 是列表里那条的 id。撤销卡同样带这个 id。
- 列表卡 `mode=list`、`phase=info`，没有确认语义（无 `phase=draft`）。
- 空档不写库。读得到事件时可以有列表卡；本测试不要求推荐哪一段。

`test_confirm_duplicate_title_deletes_only_the_given_id`（`test_calendar_google.py`）

假列表里两条摘要都是「测试会议」，id 分别是 `evt-a`、`evt-b`。

- `POST /api/calendar/google/confirm`，`action=cancel`，只有标题、没有 `external_id`：409，delete 为空，两条都还在。
- 同一次确认带 `external_id=evt-a`：200，唯一一次 delete 的 URL 以 `/evt-a` 结尾，没有打到 `evt-b`。
- `external_id=evt-missing`：409，delete 仍为空。

`test_free_slot_all_day_row_is_not_called_free`（`test_calendar_card.py`）

列出的行是全天：`2026-10-16T00:00:00` 到 `2026-10-17T00:00:00`。模型返回落在这一天里的 `15:00–16:00`，说明是「下午有空」。

- 该时段不在 `suggested_slots` 里。
- `direct_answer` 是安全说明，不是「下午有空」。
- 卡片仍列出这一天，不写库。

`test_confirm_post_auth_failure_is_401_and_writes_nothing`（`test_calendar_google.py`）

已连接。时区 GET 为 401（或刷新为 401，二选一，不要两条都测同一映射）。`POST /api/calendar` 创建。

- 状态码 401。详情含「重新连接」，不含「暂时连不上」。
- 无 insert，本地无新行。

`test_chat_auth_failure_is_not_called_a_network_outage`（`test_calendar_google.py`）

同一类 401，走 `gateway.invoke` 的 calendar，句子是创建。

- 用户看得到的文字不是「暂时连不上 Google 日历，请稍后再试。」
- 文字里有「授权已失效」或「重新连接」。现在这条路径会变成「工具治理拒绝」，测试接受这个包装，不要为了门禁改聊天文案。
- `schedule_card` 为 `None`。无 insert，无新本地行。

前端不新写测试。`scheduleCard.test.mjs` 里已有的四条（列表无确认按钮、时间块不渲染参与人、确认体带 `external_id`、列表卡在文字前）算 G2–G4 的门禁。后续 PR 只把这个文件加进 `eval.yml` 里现有的 node 步骤，和 `scheduleExpiry.test.mjs` 一起跑。

## 4. 日历相关 PR 怎样算做完

改了 `backend/app/agents/tools/calendar_tool.py`、`backend/app/services/calendar.py`、`backend/app/services/calendar_google.py`、`backend/app/services/calendar_provider.py`、`backend/app/services/calendar_local.py`、`backend/app/api/calendar.py`、`frontend/src/components/ScheduleCard.tsx`、`frontend/src/components/ChatWindow.tsx` 里日程卡渲染，或上述测试文件的 PR，合并前跑：

```bash
cd backend && python -m pytest tests/test_calendar.py tests/test_calendar_card.py tests/test_calendar_dialogue.py tests/test_calendar_provider.py tests/test_calendar_google.py -q
cd frontend && node --experimental-strip-types --test tests/scheduleCard.test.mjs tests/scheduleExpiry.test.mjs
```

五条门禁对应的测试都要绿。CI 的 offline job 会跑整个 `tests/`，不能只靠本地这五行。frontend 在 `scheduleCard.test.mjs` 进 workflow 之前，PR 说明里写明本地 node 命令已过。

新行为落在某一条门禁上时，扩展第 3 节点名的测试，不要另起一套只在描述里出现的脚本。Plan 1 的约 100 条页面脚本是单独的发布套件，不是日历代码 PR 的完成条件，也不是本文的交付。

落地第 3 节缺口的那个 PR 只加测试和 workflow 里的一行 node 命令。先对着当前代码写测试。精确标题预填、零条或多条不带 id、同标题无 id 返回 409、确认 401，按现在的 `confirm_titled_change` 和 `api/calendar.py` 应当已经成立。测试失败才改产品，而且只改让该断言成立的那一处。

## 5. 不在本文范围

- 撰写约 100 条页面说法，以及任何按这些句子做的手工或自动走查（Plan 1）。
- LLM 产品打分、路由评测、`routing_utterances.jsonl`、`gen_office_eval.py`、`contracts.py`。
- 在本文件的 PR 里实现上面的测试或改日历行为。
- 双向同步、工作时间、用正则覆盖模型给出的日期或标题。
- 真实 OAuth 同意屏幕。那份手测步骤在 `docs/plans/calendar-google-manual-acceptance.md`。
