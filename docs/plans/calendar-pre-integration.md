# 接入外部日历前的本地改造计划

本文只规划接入 Google/Outlook 之前的本地日历改造。评审后交互已改：写操作不再多轮补槽，而是返回一张可编辑的确认卡片；未点确认就不落库。时间仍以日历为准，不另设产品时区。Agent 只走统一门面。

本文件是计划，不是实现。对应 PR 只应包含本文。

评审已确认三件事：草稿有效期 30 分钟，并且下一句用户消息会更早作废；确认 API 不绑定草稿票据；评测种子与契约改动并进第 2 刀，与去掉 `pending_calendar` 短路同一提交，这样路由评测可以和改完后的生产路径对照。

## 1. 现状与目标

### 1.1 调用链

现状：

```text
对话
  → router.plan_tools（needs_calendar）
  → 若 sessions.pending_calendar 非空，workflow.plan_node 跳过路由，next_tool=calendar
  → calendar_tool.execute_calendar（忽略 tool args）
  → extract_event / fill_pending_calendar，缺字段就追问并写回 pending
  → 槽齐则 check_conflict，无冲突则 create_event 立刻落库
  → app.services.calendar 直接查 CalendarEvent
```

目标：

```text
对话 / 路由（每句都走路由，不再因 pending 短路）
  → calendar 工具（create | update | cancel | list | suggest）
  → CalendarService
  → CalendarProvider（本阶段只有 Local）
  → 写操作：只返回确认卡片，不写库
  → 用户在卡片上点确认
  → POST / PATCH / DELETE（归属校验）
  → Local provider → calendar_events
```

工具和 HTTP 层都不直接 `query` / `db.get(CalendarEvent)`。允许碰表的只有 `LocalCalendarProvider`，以及现有的 `require_owned_event`。

### 1.2 对照

| 层 | 现状（`release` 已核对） | 本阶段目标 |
| --- | --- | --- |
| 工具 | `execute_calendar` 只做创建，或在有草稿时用 `is_calendar_cancel` 放弃草稿。缺字段追问；冲突用 `suggest_next_slot` 顺延第一条；否则立刻 `create_event`。`_args` 未使用。 | 同一个工具名。create / update / cancel 只抽一次槽，返回预填卡片，不写库。list / suggest 只回文本。 |
| 补槽 | `fill_pending_calendar` 多轮补 `sessions.pending_calendar`。参与人和结束时间都算缺失。提示词写了不重写已确认字段，代码仍会覆盖。 | 取消多轮补槽。抽到的字段预填卡片，没抽到的留空。用户在卡片上改。下一句聊天视为放弃这张卡片，而不是继续补槽。 |
| 服务 | `backend/app/services/calendar.py` 同时做抽槽、正则、冲突、CRUD。 | 抽槽留在服务。ORM 只留在 Local provider。第 1 步模块级函数签名不变。 |
| Provider | 无。 | `get_timezone`、`list_events`、`get_event`、`create_event`、`update_event`、`cancel_event`、`find_free_slots`。 |
| 路由 | `pending_calendar` 非空时 `workflow.plan_node` 和 `app/eval/tool_routing.py` 都跳过 `plan_tools`。`prompts.py` 几乎只有建日程 few-shot。`calendar_hint` 存在但路由不用；`test_no_regex_fallback_branch_in_plan_tools` 禁止 `plan_tools` 引用它。 | 新流程不依赖这段短路。安全退役见第 5 节。查 / 改 / 取消 / 有空靠 few-shot 和评测样本，不把正则搬回路由。 |
| 草稿放哪 | `sessions.pending_calendar` JSON。`EventDraft` 只有 title、起止、参与人、missing_fields、original_request。 | 草稿在卡片上，带 `expires_at`。不写入会话 pending。列和 `ToolContext.pending_calendar` 先留着，避免旧读路径崩掉。 |
| API | `PATCH` 与 `DELETE /api/calendar/{id}`。删除是软取消。没有创建、没有列表。 | 确认时 `POST` 创建、`PATCH` 修改、`DELETE` 撤销，都走归属校验。另加 `GET` 列表，给 list 动作和以后的外部形态对齐。 |
| 表 | `id`、`user_id`、`title`、`start_at`、`end_at`、`participants`、`status`、`created_at`。时间列无时区。 | 只预留 `timezone`、`location`、`description`、`external_id`、`provider`。不建 `calendar_sources`。不改成 `timestamptz`。 |
| 前端 | `ScheduleCard.tsx` 用三次 `window.prompt` 改已落库的事件。 | 同一张卡片变成确认表单：预填、改、确认、放弃、冲突空隙、多条候选。 |
| 冲突 | 只看第一条冲突，顺延相同时长，不检查顺延后是否再冲突。 | 重叠条件不变。空隙显示在卡片上，不在对话里追问。不按工作时间过滤。 |

时间的现状，第 1 步不能改：

- 相对时间用 `datetime.now()`，即进程本地时区。
- `_parse_dt` 去掉 `tzinfo` 且不换算。`2026-08-06T10:00:00+08:00` 存成墙钟 `2026-08-06 10:00:00`。

## 2. 文件放哪里，以及第 1 步如何保持行为不变

第 1 步不要把 `app.services.calendar` 从模块改成包。下面这些绑定要继续可用：

- `app.agents.tools.calendar_tool.extract_event`
- `app.agents.tools.calendar_tool.fill_pending_calendar`
- `app.agents.tools.calendar_tool.check_conflict`
- `app.agents.tools.calendar_tool.create_event`
- `app.services.calendar.complete_json`（`extract_event` 仍从 `calendar.py` 的全局名读它）
- `from app.services.calendar import EventDraft`
- `from app.services import calendar as calendar_svc`

| 路径 | 第 1 步 | 卡片那一步才动 |
| --- | --- | --- |
| `backend/app/services/calendar_provider.py` | 协议和 dataclass。不 import ORM。 | 卡片和确认 API 只用这些类型。 |
| `backend/app/services/calendar_local.py` | `LocalCalendarProvider`，唯一的新 ORM 查询点。 | 确认写入走这里。 |
| `backend/app/services/calendar.py` | 现有函数改成转调 provider。抽槽、正则、`suggest_next_slot` 仍留在本文件。 | 工具改为只抽槽、不调用 `create_event`。`fill_pending_calendar` 不再被工具调用。 |
| `backend/app/models/models.py` 与 Alembic `0007` | 只给 `calendar_events` 加预留列。 | 不追加时区配置表。 |
| `backend/app/agents/tools/calendar_tool.py` | 不动。 | 按动作返回卡片或文本。 |
| `backend/app/agents/workflow.py`、`app/eval/tool_routing.py` | 不动短路。 | 去掉 pending 短路，并清空遗留 pending。 |
| `backend/app/api/calendar.py`、`ScheduleCard.tsx` | 不动。 | 确认 API 和表单。 |

第 1 步的等价标准：

1. 工具可观察结果不变：缺字段追问、冲突文案、创建成功的 `direct_answer` 和卡片、放弃草稿、写回的 pending 键。
2. 冲突条件不变：同一 `user_id`、`status == "active"`、`start_at < 对方 end_at` 且 `end_at > 对方 start_at`。首尾相接不算冲突。
3. 取消仍是 `status = cancelled`，不物理删除。
4. 新列只写默认值：`provider='local'`，其余空字符串。工具逻辑不读它们。
5. `get_timezone()` 可以存在，但抽槽仍用 `datetime.now()`。`find_free_slots()` 可以有单测，工具的冲突回复仍调用 `suggest_next_slot`。
6. 工具源码不出现 `CalendarEvent` 或 `db.query`。用与 `test_router.py` 相同的 `inspect.getsource` 断言。
7. `serialize_event_card` 用 `getattr` 读新字段，缺省为空。`test_workflow.py` 和 `test_calendar.py` 里的假事件对象没有这些属性，直接读会让第 1 步的旧测试失败。

若不得不移动上述函数，必须在同一提交里改 patch 路径并跑第 7.1 节的命令。默认不移动。

`require_owned_event` 继续挡在 PATCH/DELETE 前面。Provider 的每个方法再按 `user_id` 过滤。POST 只用登录用户的 id，不接受 body 里的 `user_id`。

## 3. 表结构

Alembic `0007_calendar_provider_fields`，`down_revision = 0006_session_context`。加列前看 inspector，写成与 `0006` 一样的幂等迁移。`init_db()` 用现有的 `_sqlite_add_column_if_missing` / `_postgres_add_column_if_missing` 再补一次。

| 列 | 类型 | 默认 | 含义 |
| --- | --- | --- | --- |
| `timezone` | String，默认 `''` | 旧行 `''` | 预留。本地阶段不作为时间真相，接入外部日历后再写账户时区的快照。 |
| `location` | String，默认 `''` | `''` | 地点。卡片上可编辑，可空。 |
| `description` | Text，默认 `''` | `''` | 说明。可空。 |
| `external_id` | String，默认 `''` | `''` | 外部事件 id。本地保持空。 |
| `provider` | String，默认 `'local'` | 旧行 `'local'` | 以后是 `google` / `outlook`。 |

不改 `start_at` / `end_at` 的类型，不做偏移换算，不加 `(user_id, provider, external_id)` 部分唯一索引。`0004_rls` 已按 `user_id` 保护此表，新列不用改策略。

不建 `calendar_sources`，不加 `CALENDAR_LOCAL_TIMEZONE`。本地阶段没有日历配置行可以当时间真相；接入后的外部账户时区才是真相。`get_timezone()` 在 Local 上返回进程本地时区，只为把接口留住，并且与今天的 `datetime.now()` 一致。不要把这个返回值写成产品默认时区，也不要在本地阶段用它做工作时间过滤。

`status` 继续 `active` / `cancelled`。以后再映射 `active` ↔ 外部的 `confirmed`。

卡片序列化在原有键之外带上这五个字段的默认值。没有 `phase` 的旧卡片表示「已经落库的事件」，不是待确认草稿。

## 4. 动作与确认卡片

路由对 calendar 仍传空 `tool_args`。工具以用户原话分类；单测可以传结构化 `action`。分类放在工具内部，不进 `plan_tools`。看不准时用一次 `complete_json` 在五个动作里选，缺省 `create`。

`query` 若被模型输出，视为 `list`。对外只有 `list`。

### 4.1 写操作：一律先给卡片

create、update、cancel 每次只抽当前这句话，返回一张卡片。就算「明天三点开周会」已经齐了，也只出卡片，不调用 `create_event`。缺的字段留空，不追问下一句。

参与人可空。有开始时间、没有结束时间时，卡片上的结束时间预填为开始后 1 小时，用户可以改。明确说了结束时间或时长就用用户的值。

`direct_answer` 用卡片上的字面时间写一句短话，例如「请确认日程「周会」，15:00–16:00。未确认不会写入。」这样 `answer_node` 不会再叫回答模型改写时间。有 `direct_answer` 时现有的 `reflect_node` 会停。

下一句用户消息，无论内容是什么，都表示这张未确认卡片作废：

- 前端在发送时立刻把本会话里 `phase=draft` 的卡片标成 `cancelled`。
- `chat.py` 在写入新的用户消息时，把该会话里仍为 `draft` 的 `messages.schedule_card` 改成 `phase=cancelled`，避免刷新后又变成可确认。
- 不读、不写 `sessions.pending_calendar`。

到期同样作废。卡片带 `expires_at`。到期后按钮禁用；刷新后仍禁用，因为时间写在卡片 JSON 里。建议 30 分钟，见第 10 节。

「取消明天的面试」永远是取消那条事件：返回 cancel 卡片，确认后才 DELETE。不再和「放弃草稿」抢解释。用户若想放弃当前卡片，发任意下一句即可，包括「算了」。

### 4.2 卡片 JSON

沿用 SSE 事件 `schedule_card` 和 `messages.schedule_card`。不新增一种消息类型。

```json
{
  "mode": "create",
  "phase": "draft",
  "expires_at": "2026-08-06T15:30:00",
  "event_id": null,
  "title": "周会",
  "start_at": "2026-08-06T15:00:00",
  "end_at": "2026-08-06T16:00:00",
  "participants": [],
  "location": "",
  "description": "",
  "timezone": "",
  "provider": "local",
  "external_id": "",
  "status": "",
  "conflicts": [],
  "suggested_slots": [],
  "candidates": []
}
```

| `mode` | 确认时调用 | 说明 |
| --- | --- | --- |
| `create` | `POST /api/calendar` | 新建。`event_id` 为空。 |
| `update` | `PATCH /api/calendar/{event_id}` | 已定位到一条。 |
| `cancel` | `DELETE /api/calendar/{event_id}` | 已定位到一条。确认前不改 `status`。 |
| `choose` | 先不调用 | update/cancel 命中多条。点选一条后，前端把同一张卡片改成 `update` 或 `cancel` 并填上 `event_id`。 |
| `suggest` | 不确认 | 只读空隙。点「用这个时段」在前端换成一张 `create` 草稿，标题留空，时间用该空隙。刷新后若还没确认，仍看到原来的 suggest 卡片。 |

`phase`：`draft`、`confirmed`、`cancelled`、`expired`。只有 create/update/cancel/choose 的 `draft` 会被下一句或到期取消。`suggest` 用 `phase=info`，不要被当成未完成的写操作清掉。

旧卡片没有 `mode` / `phase` 时，按已落库事件显示。修改仍走这张表单，确认时 PATCH。去掉 `window.prompt`。

### 4.3 冲突与候选都在卡片上

预览和确认各查一次。预览让用户在点确认前看到空隙；确认防止用户改完时间后又撞上。

重叠条件与今天相同。`find_free_slots(user_id, start, end, duration, limit=3, exclude_event_id=None)`：按开始时间扫，两段忙碌之间以及最后一段到窗口末尾，长度够且首尾相接算空闲。默认从用户要的开始时间搜 7 天，要了日期则用该日 00:00–24:00，默认 3 个、默认时长 1 小时。零个空隙时卡片照常可编辑，并写明这段已满。不写 9:00–18:00 之类的工作时间；那是以后外部日历自己的工作时间。

更新时的冲突排除自己的 id。

定位：

1. 卡片上已有 `event_id`：`get_event`，且 `user_id` 必须匹配。
2. 否则用本地日窗口加标题子串。ASCII casefold，中文按原文，双向包含。
3. 0 条：只有 `direct_answer`「没有找到对应日程」，不出可确认卡片，不新建一条来代替。
4. 1 条：`update` 或 `cancel` 卡片，预填该事件。
5. 多条：`mode=choose`，`candidates` 最多 5 条（id、标题、起止）。未点选不能确认。

没有标题也没有时间时，卡片上这两项留空，不猜「最近的一条」。

### 4.4 确认 API

| 方法 | 作用 | 失败 |
| --- | --- | --- |
| `POST /api/calendar` | 创建。body：title、start_at、end_at、participants、location、description。结束时间空则服务端补开始后 1 小时。 | 缺标题或开始时间：400。重叠：409，body 为 `{conflicts, suggested_slots}`，不插入。 |
| `PATCH /api/calendar/{id}` | 修改。先 `require_owned_event`。 | 同上。冲突检测排除自己。 |
| `DELETE /api/calendar/{id}` | 软取消。先归属校验。 | 404。 |
| `GET /api/calendar?start=&end=&status=` | 当前用户的卡片数组，与 `GET /api/sessions` 一样返回 JSON 数组。 | 不返回他人事件。 |

`CalendarUpdate.participants` 改为 `list[str] | None = None`。`None` 表示不改，`[]` 表示清空。今天的 `default_factory=list` 加上「空列表当成没传」，表格无法把参与人清掉。

确认请求不携带草稿令牌。到期和「下一句作废」只约束界面上的那张卡片，不把 POST 变成一次性票据。见第 10 节。

提交的时间是无 `Z` 的 `YYYY-MM-DDTHH:mm:00`。`datetime-local` 没有时区，与现在 `_parse_dt` 保留墙钟数字的行为一致。卡片上不提供时区编辑框。

### 4.5 list 与 suggest

两者不写库，也不占用 `draft`。

- list：默认从进程本地的今天 00:00 起 7 天，只含 `active`，按开始时间升序，最多 20 条。`direct_answer` 列出标题和时间。不附可确认卡片。
- suggest：调用 `find_free_slots`，`direct_answer` 写出这些时段，并附 `mode=suggest` 的卡片。每个时段一个「用这个时段」，点了以后换成 create 草稿。用户不点就什么都不写。

## 5. 退役 `pending_calendar`

新流程不再生产这段状态。退役要让遗留数据和评测短路都变成无害，而不是在第 1 步就拆掉现有行为。

今天依赖它的地方：

- `workflow.plan_node`：pending 非空则跳过 `plan_tools`，强制 `next_tool=calendar`。
- `app/eval/tool_routing.py`：同样的短路，因此 `slot_fill` 样本不经过路由器就能得 calendar。
- `calendar_tool`：有 pending 就 `fill_pending_calendar`，或用 `is_calendar_cancel` 放弃草稿。
- `chat.py`：把工作流返回的 pending 写回 `sessions.pending_calendar`。
- `format_working_block` / `format_context_line`：若调用方传入 pending，就显示「未完成日程草稿」。
- 评测：`routing_utterances.jsonl` 里 24 条 `slot_fill` 带 pending；`gen_office_eval.py` 断言 `pending == 24`；`contracts.py` 要求 routing 里 pending 占比 0.05–0.12，且 agent 的 `slot_fill` 至少 4 条、有该标签就必须有 pending。`gen_agent_eval.py` 也生成了这 6 条。

卡片那一刀同时做这些事：

1. 工具不再读 pending，不再调用 `fill_pending_calendar`，不再把 `update_pending_calendar` 写成一份新草稿。成功路径把 pending 设为空，避免再写回会话。
2. `plan_node` 删掉 pending 短路，每句都调用 `plan_tools`。`tool_routing.py` 的对应分支一起删，否则评测仍在测量一条已经不存在的生产路径。
3. `chat.py` 在准备上下文时，若 `session.pending_calendar` 非空，把它写成 `""` 再继续。不解释里面的 JSON，不据此创建事件。这样库里的旧草稿只会在下一次开口时被清掉。
4. 保留列、`ToolContext.pending_calendar` 和 `format_working_block` 的读法。生产路径不再传入。`test_build_context_views` 这类直接传入 dict 的单测可以继续通过。不要在这一刀把 `calendar.py` 收成包，也不要改 context 评测里那些「还缺地点和参会人」的记忆题；它们测的是回忆，不是工具状态机。
5. `fill_pending_calendar` 在没有调用方、并且 `test_workflow.py` 的两个 pending 用例已改写之后再删。
6. 同一刀改评测种子和契约，不要留到后面。否则短路已经去掉，金标却还在给旧路径加分，路由分数无法和这一刀之后的实现对照。范围是 `routing_utterances.jsonl` 里的 24 条 `slot_fill`、`gen_office_eval.py` 的 `pending == 24`、`contracts.py`、`gen_agent_eval.py`、`test_eval_datasets_contract.py`，以及新增的查日程 / 改期 / 取消 / 有空吗样本。细则见第 7.4 和 7.5 节。

`test_pending_calendar_skips_planner_and_fills_multiple_fields` 与 `test_pending_calendar_partial_fill_keeps_calendar_flow` 断言的是旧状态机。它们留在第 1 步全绿，在第 2 刀改成：传入遗留 pending 时仍然会调用 `plan_tools`，且工具不会 `create_event`。

## 6. 抽槽与时区（只为预填，不追问）

规则放在 `extract_event` 及其使用的归一化函数里，供卡片那一刀使用。第 1 步不改这些规则，这样被 patch 的工作流测试仍描述旧控制流。

| 项 | 卡片预填 |
| --- | --- |
| 参与人 | 说到才填，否则空数组。不算缺失，不追问。 |
| 结束时间 | 有开始、没有明确结束：预填开始后 1 小时。 |
| 「明天三点」 | 仍相对 `datetime.now()` / `get_timezone()` 的进程本地「现在」。存墙钟，不换算偏移。 |
| 已确认字段 | 不再有跨句补槽，因此没有「下一句不许改标题」这条。用户要改，就在卡片上改，或另说一句「把周会改到四点」（那一句会先作废上一张草稿，再给出一张 update 卡片）。 |
| 抽取提示词 | 删掉「没说结束时间就必须把 end_at 放进 missing_fields」。要求无偏移 ISO。参与人可省略。 |

相对时间函数加上可选的 `now`，供单测注入。不要为了时区新建表。

工作时间：`find_free_slots` 不过滤夜间。协议注释写明，以后由外部 provider 用该日历自己的工作时间再交一批空隙；本地实现不硬编码。

## 7. 测试

### 7.1 第 1 步必须仍然全绿

```bash
cd backend && python -m pytest \
  tests/test_calendar.py \
  tests/test_workflow.py \
  tests/test_isolation.py \
  tests/test_router.py \
  tests/test_context_manager.py \
  tests/test_agent_e2e.py \
  tests/test_eval_datasets_contract.py \
  -q --tb=short
```

第 1 步不改这些预期：缺字段追问、创建成功带卡片、有 pending 时不调用 `plan_tools`、`suggest_next_slot` 保持时长、跨用户 PATCH 404、`plan_tools` 源码不含 `calendar_hint`、`complete_json` 的 patch 路径、pending 原样进入工作流。

第 1 步只追加：provider 的创建、列出、更新、软取消、跨用户读为空、首尾相接不冲突；工具源码不含 `CalendarEvent`；新列默认值不改变旧卡片键。

### 7.2 卡片行为

`backend/tests/test_calendar_card.py`，mock `complete_json`，内存 SQLite。工具路径不得调用 `create_event` / `update_event` / `cancel_event`。

1. 「明天三点开周会」：`mode=create`、`phase=draft`，时长 1 小时，参与人为空，库中无新行，返回的 pending 为空。
2. 缺标题或开始时间：卡片仍返回，对应字段为空，库中无新行。
3. 预填时段与已有事件重叠：卡片带最多 3 个 `suggested_slots`，仍无新行。
4. 两个同名「周会」且说「取消周会」：`mode=choose`，两条候选，不调用取消。
5. 「取消明天的面试」且只有一条：`mode=cancel`，`event_id` 已填，`status` 仍是 `active`。
6. 传入一份遗留 pending：`plan_tools` 仍被调用，工具不把这句话当成补槽创建。
7. POST 缺标题返回 400；重叠返回 409 且无插入；PATCH/DELETE 他人 id 返回 404；POST 不采信 body 里的 `user_id`。
8. 只 PATCH 标题时参与人不变；PATCH `participants: []` 能清空。

前端没有测试运行器。浏览器里走通：齐信息的创建也要先看到表单，确认后才出现已落库状态；改时间后确认会 PATCH；撤销确认会 DELETE 并禁用按钮；点一个冲突空隙只改表单里的时间，不立刻写库；多条候选要点选后才能确认；发送下一句后草稿按钮不可用，刷新后仍不可用。不要只截一张静态图。

### 7.3 对话与只读动作

1. 查：「这周有哪些会」→ 文本含标题和时间，没有 `phase=draft`。
2. 空闲：「明天下午有空吗」→ 文本列出空隙，`mode=suggest`。
3. 建完不确认又说「把周会改到四点」→ 上一张草稿作废，新卡片是 update 或「没找到」，库仍是原时间，直到确认。
4. 确认 create 之后再 list，能看见新事件。

### 7.4 评测：把查、改、取消、有空测进工具路由

现有 `office_tool_routing.jsonl` 里的日历句大多是「安排一下今天 X 点的周会」这种创建改写，外加 24 条靠 pending 短路的 `slot_fill`。查日程、改期、取消、有空没有成组的金标，所以新能力测不到。

这些样本放进第 2 刀，与去掉 `tool_routing.py` 短路同一提交。改种子而不是手改生成结果：

- 文件：`backend/data/eval/seeds/routing_utterances.jsonl`（现为 400 条）和 `routing_multiturn_extra.jsonl`（现为 20 条）。`gen_office_eval.py` 写死了这两个长度，以及 `pending == 24`。用新句子替换掉一批重复的创建改写，使两个长度不变。
- 每类至少 4 条，标签分别是 `calendar_list`、`calendar_update`、`calendar_cancel`、`calendar_suggest`。金标都是 `intent=calendar`、`tools=["calendar"]`，`context` 里不要带 `pending_calendar`。
- 例句方向：查「我这周有哪些日程」「明天有什么会」；改「把明天的周会改到四点」「将面试推迟到下午三点」；取消「取消明天的面试」「撤销周五的周会」；有空「明天下午有空吗」「这周四上午什么时候有空」。带历史的各放 1 条进 extra 种子，并同样用替换保持 20 条。
- 反例不要改类：「什么是日历」仍是 chat。`yz_fullchain.jsonl` 里「日程冲突怎么办」仍是知识库产品说明，不要改成 calendar 工具。
- 生成器加断言：四类标签各自 `>= 4`。然后重跑脚本，提交生成的 `office_tool_routing.jsonl`。
- 同一刀处理短路样本，否则 `pending == 24` 和 `contracts.py` 的占比会和「生产已不再短路」互相矛盾。见下一节。

`agent_tasks.jsonl` 里已经有「我明天有哪些安排？」和「把它改到下午四点」。路由套件补的是 office routing 的缺口，不是把 agent 任务再抄一遍。

### 7.5 短路样本怎么改才不会把契约改坏

`tool_routing.py` 去掉短路之后，那 24 条 `slot_fill` 会第一次真正走路由器。其中不少是旧产品的补槽话术（改会议室、加提醒、改成每两周重复），本阶段工具并不做这些事。

同一提交里：

- 从 `routing_utterances.jsonl` 去掉这些行的 `pending_calendar`，并去掉 `slot_fill` 标签。
- 仍然明显是在改已有日程的（「时间改成明天下午两点」「日期换到下周一」）保留 `intent=calendar`，改标 `calendar_update`，计入 7.4 的配额。
- 本阶段不做的（重复、提醒、会议室、邮件）改为 chat，或直接换成 7.4 的查 / 取消 / 有空句子。不要为了维持 calendar 占比而把做不到的能力继续标成 calendar。
- `gen_office_eval.py` 的 `pending == 24` 改为 `pending == 0`。`contracts.py` 里 `routing.pending_calendar` 的占比改为 `(0.0, 0.02)`，或改成断言计数为 0。calendar 意图占比若出界，按替换后的真实分布改 `contracts.py` 里现有的 `(0.15, 0.20)`，并在 PR 里写上新区间。
- `gen_agent_eval.py` 的 6 条 `slot_fill` 同样去掉 pending。`contracts.py` 里「`slot_fill` 至少 4 条」和「有 `slot_fill` 就必须有 pending」一并删除，避免契约还在要求一条已退役的路径。`test_eval_datasets_contract.py` 里对 `slot_fill` 和「至少一行 pending」的断言一起改。
- context 记忆集（`gen_context_eval.py` / `context_recall.jsonl`）不动。

第 1 步不要改这些种子。契约、生成器和短路代码都在第 2 刀落地，不单列后面的评测刀。拆开的话，中间会有一轮分数既不能和 `release` 比，也不能和卡片实现比。

## 8. 本阶段不做

- OAuth、外部日历 HTTP 客户端、webhook、同步令牌、双向同步。
- 邀请邮件、会议室、多账户 UI、时区设置页。
- `calendar_sources` 或任何「本地日历配置」表。
- 把时间列改成 `timestamptz`，或在读出时把墙钟换算成 UTC。
- 重命名 `sessions.pending_calendar` 列。第 5 节只是停止使用它。
- 在 `plan_tools` 里恢复 `calendar_hint`。
- 重复日程、全天事件、提醒、按工作时间过滤空隙。
- 物理删除。DELETE 仍是软取消。
- 工具在对话过程中直接 `create_event`。未确认不落库。
- 近日列表侧栏。list 的结果放在回复文本里。
- 外部供应商客户端。最后一刀的清单只写文档。

`provider` 与 `external_id` 只预留。Local 的 `external_id` 始终是空字符串。

## 9. PR 切片

每一刀单独可回滚。本文所在 PR 不在这四刀之内。第 1 刀保持今天的对话行为；从第 2 刀起才换成确认卡片。

### 第 1 刀：Provider + Local，行为等价

- 新增 provider 协议和 Local 实现。`calendar.py` 的 CRUD 与 `check_conflict` 转调 Local，参数签名不变。
- Alembic `0007` 只加第 3 节的五列。不建 `calendar_sources`。
- `get_timezone()` 返回进程本地时区，抽槽不调用它。
- 工具、API、前端、路由短路、抽取提示词、评测种子都不动。
- 验收：第 7.1 节全绿，加上 provider 单测。

### 第 2 刀：确认卡片，并同时对齐路由评测

- 工具的 create / update / cancel 返回第 4 节的卡片，不写库。
- 预填规则用第 6 节。冲突空隙和多条候选在卡片上。
- `POST` 创建；PATCH/DELETE 仍是确认动作。409 把空隙交回卡片。草稿 30 分钟有效，下一句用户消息更早作废。确认 API 不校验草稿票据。
- `ScheduleCard.tsx` 改为表单，去掉 `window.prompt`。下一句和 `expires_at` 把 `draft` 标为作废，并写回 `messages.schedule_card`。
- 按第 5 节去掉生产短路、清空遗留 pending、改写两个 workflow pending 测试。
- 同一刀按第 7.4 和 7.5 节改种子、`gen_office_eval.py`、`contracts.py`、`gen_agent_eval.py`、`test_eval_datasets_contract.py`，并重新生成 `office_tool_routing.jsonl` 与 `agent_tasks.jsonl`。新增查日程 / 改期 / 取消 / 有空吗样本。不要把评测留到后续刀，否则这一刀的路由分数无法对照。
- 验收：第 7.2 节；浏览器里走通确认、冲突空隙、候选和「下一句作废」；`pytest tests/test_eval_datasets_contract.py` 全绿；四类新标签各自至少 4 条；生成器里的 pending 计数为 0。

### 第 3 刀：list、suggest、路由 few-shot

- 两个只读动作，suggest 的「用这个时段」在前端换成 create 卡片。
- `GET /api/calendar`。
- `prompts.py` 增加 few-shot：查本周日程、把明天的周会改到四点、取消明天的面试、明天下午有空吗，均为 `needs_calendar=true`。反例「什么是日历」不进日历。不改 `plan_tools` 的结构。
- 验收：第 7.3 节；`test_no_regex_fallback_branch_in_plan_tools` 仍绿。

### 第 4 刀：回归与接入清单（清单仅为文档）

- 把第 7.3 节没收进第 3 刀的用例补进 `backend/tests/test_calendar_card.py`。
- 新增 `docs/plans/calendar-integration-checklist.md`。清单包括：实现 provider 的七个方法；OAuth 与令牌不进业务列；`external_id` + `provider` 如何对上本地行；`get_timezone()` 改为外部账户时区，事件上的 `timezone` 列只做写入快照；`find_free_slots` 改为交叠该日历的工作时间，本地实现保持不过滤；取消与外部取消的映射；未做双向同步时，确认 API 仍只写本地行。
- 再跑第 7.1 节里仍然适用的测试。第 1 步那些「pending 跳过路由」「缺字段则追问」的断言以第 2 刀改写后的版本为准。

## 10. 风险与尚未收口的点

已决定，不再改：

1. **草稿有效期 30 分钟**，任意下一句用户消息会更早作废。
2. **确认 API 不校验卡片是否过期，也不发草稿票据。** 过期只禁用界面上的按钮。知道接口的客户端仍可以稍后 POST 同样的内容，那就是一次正常的创建。
3. **评测种子与去掉短路在第 2 刀一起改。** 旧的 24 条补槽金标里，改会议室、加提醒、改成双周重复的句子会改成 chat，或换成查 / 改 / 取消 / 有空。第 2 刀之后的路由分数对的是新金标，不要拿第 2 刀中间态和 `release` 比。

仍需实现时注意：
4. **进程本地时区只是占位。** 部署在 UTC 上时，「明天三点」会按 UTC 理解，直到外部日历提供账户时区。本阶段不再做配置表或环境变量种子来掩盖这件事。
5. **墙钟存储。** `_parse_dt` 丢弃偏移、不换算。第 1 刀不要把它改成 UTC，否则已有时间和卡片会平移。
6. **「用这个时段」换出的 create 卡片只活在前端，直到点确认。** 中途刷新会回到 suggest 卡片，而不是那张填了一半的创建表单。这是为了不把草稿放进会话状态。
7. **context 记忆题仍在说「未完成草稿还缺地点和参会人」。** 与新交互不一致，但测的是回忆而不是路由。本阶段不改 `context_recall.jsonl`。
8. **`suggest_next_slot` 先留着。** 第 1 刀的单测还锁着它。用户可见的空隙从第 2 刀起走 `find_free_slots`。不要在第 1 刀删这个函数。
9. **模块改成包会打断 `complete_json` 和工具上的四个 patch。** 不要放进第 1 刀。
