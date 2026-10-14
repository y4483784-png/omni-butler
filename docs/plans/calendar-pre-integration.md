# 接入外部日历前的本地改造计划

本文只规划接入 Google/Outlook 之前的本地日历改造。产品依据是已对齐的 brief：时间以日历为准，不另设一套产品时区；Agent 只走统一门面；本阶段先把查 / 建 / 改 / 删 / 冲突与空闲做成可替换的本地能力。

本文件是计划，不是实现。对应 PR 只应包含本文。

## 1. 现状与目标

### 1.1 调用链

现状：

```text
对话
  → router.plan_tools（needs_calendar）
  → 若 sessions.pending_calendar 非空，workflow.plan_node 跳过路由，next_tool=calendar
  → calendar_tool.execute_calendar（忽略 tool args，只读用户原话）
  → app.services.calendar 里的函数（这些函数直接查 CalendarEvent）
  → SQLite / Postgres 表 calendar_events
```

目标：

```text
对话 / 路由
  → calendar 工具（action = create | update | cancel | list | suggest）
  → CalendarService
  → CalendarProvider（本阶段只有 Local）
  → calendar_events，以及以后的外部 API
```

工具和 HTTP API 都不直接 `query` / `db.get(CalendarEvent)`。允许继续碰表的只有 `LocalCalendarProvider`，以及现有的 `require_owned_event`（HTTP 归属校验，见第 3 节）。

### 1.2 对照

| 层 | 现状（`release` 已核对） | 本阶段目标 |
| --- | --- | --- |
| 工具 | `backend/app/agents/tools/calendar_tool.py` 的 `execute_calendar`。有草稿时，`is_calendar_cancel` 只放弃草稿，不删库。否则 `extract_event` / `fill_pending_calendar`。缺字段就追问；`check_conflict` 命中则用 `suggest_next_slot` 顺延第一条冲突；否则 `create_event`。成功与追问都走 `direct_answer`，成功时带 `schedule_card`。`_args` 未使用。 | 同一个工具名 `calendar`。按 action 分发。写操作成功仍是 `direct_answer` + `schedule_card`，避免回答模型再写一遍时间。 |
| 服务 | `backend/app/services/calendar.py` 同时做 LLM 抽槽、正则、冲突、CRUD、卡片序列化。`check_conflict` / `create_event` / `update_event` / `cancel_event` 直接使用 ORM。 | 抽槽留在服务；ORM 只留在 Local provider。模块级函数在第 1 步继续作为兼容入口。 |
| Provider | 无。 | `get_timezone`、`list_events`、`get_event`、`create_event`、`update_event`、`cancel_event`、`find_free_slots`。 |
| 路由 | `RouterDecision.needs_calendar`。`workflow.plan_node` 在 `pending_calendar` 为非空 dict 时跳过 `plan_tools`。`app/eval/tool_routing.py` 有同样的短路。`prompts.py` 只有一条建日程 few-shot。`calendar_hint` 存在但路由不用；`test_no_regex_fallback_branch_in_plan_tools` 禁止 `plan_tools` 引用它。 | 短路条件改成「有未完成的 `pending_calendar_op`」。路由仍只输出布尔 `needs_calendar`，不抽槽。查/改/删/空闲靠补充 few-shot，不把正则搬回路由。 |
| 会话字段 | `sessions.pending_calendar` 是 JSON 文本。`EventDraft.to_dict()` 只有 `title`、`start_at`、`end_at`、`participants`、`missing_fields`、`original_request`。工作状态文案读顶层 `title` 与 `missing_fields`。 | 列名和 Python 字段名都不改。JSON 扩成第 4 节的 op。旧草稿没有 `action` 时视为 `create`。 |
| API | `backend/app/api/calendar.py`：`PATCH /api/calendar/{id}`、`DELETE /api/calendar/{id}`。删除是软取消（`status=cancelled`）。没有列表。 | 增加 `GET /api/calendar`。PATCH/DELETE 保留。响应字段与卡片对齐。 |
| 表 | `CalendarEvent`：`id`、`user_id`、`title`、`start_at`、`end_at`、`participants`（JSON 文本）、`status`（`active` \| `cancelled`）、`created_at`。时间列是无时区 `DateTime`。 | 预留 `timezone`、`location`、`description`、`external_id`、`provider`。不把时间列改成 `timestamptz`。 |
| 前端 | `ScheduleCard.tsx` 用三次 `window.prompt` 改标题和 ISO 起止时间，再 PATCH。撤销走 DELETE。`ScheduleCard` 类型没有地点、说明、时区、provider。 | 去掉 `window.prompt`，改成表单。可选近日列表走 GET。 |
| 冲突建议 | `suggest_next_slot` 把开始时间挪到第一条冲突的结束，并保持时长。不检查顺延后是否再次冲突，也不在窗口里找多个空隙。 | 重叠判定保持不变。面向用户的建议改成窗口内最多 N 个空隙。 |

现状里工具已经不 import `CalendarEvent`，但服务在替它写表。第 1 步要补上的是这层隔离，而不是再拆一次工具注册。

时间的现状，第 2 步会改，第 1 步不能改：

- 相对时间用 `datetime.now()`，即进程本地时区，没有日历时区。
- `_parse_dt` 去掉 `tzinfo`，不做时区换算。`2026-08-06T10:00:00+08:00` 存成 `2026-08-06 10:00:00`。这是墙钟数字，不是 UTC。
- 抽槽提示词要求：用户没说结束时间时，`missing_fields` 至少包含 `end_at`。
- `_normalize_missing_fields` 在参与人为空时加入 `participants`。参与人目前是必填。
- `fill_pending_calendar` 的提示词写了「不要重写已确认字段」，但代码在模型给出 `start_at` / `end_at` / `participants` 时会覆盖已有值。`title` 只有在它仍缺失，或新标题与旧标题不同时才覆盖，因此一条不同的标题也会改掉已确认标题。

## 2. 文件放哪里，以及第 1 步如何保持行为不变

第 1 步不要把 `app.services.calendar` 从模块改成包。下面这些绑定要继续可用，否则现有测试的 monkeypatch 会指到空处：

- `app.agents.tools.calendar_tool.extract_event`
- `app.agents.tools.calendar_tool.fill_pending_calendar`
- `app.agents.tools.calendar_tool.check_conflict`
- `app.agents.tools.calendar_tool.create_event`
- `app.services.calendar.complete_json`（`extract_event` 必须仍从 `calendar.py` 的全局名读它）
- `from app.services.calendar import EventDraft`
- `from app.services import calendar as calendar_svc`

建议新增：

| 路径 | 职责 |
| --- | --- |
| `backend/app/services/calendar_provider.py` | `CalendarProvider` 协议、写入/读出用的 dataclass。不 import ORM。 |
| `backend/app/services/calendar_local.py` | `LocalCalendarProvider`。唯一的新 ORM 查询点。 |
| `backend/app/services/calendar.py` | 保持为服务门面。现有函数改成转调 provider。抽槽、正则、`EventDraft`、`suggest_next_slot` 仍留在这个文件。 |
| `backend/app/models/models.py` | `CalendarEvent` 新列；新表 `calendar_sources`。 |
| `backend/alembic/versions/0007_calendar_provider_fields.py` | 迁移，`down_revision = 0006_session_context`。 |
| `backend/app/core/db.py` | 与现有做法一样，给 SQLite / Postgres 补列，防止 `create_all` 已经跑过、Alembic 还没跟上。 |
| `backend/app/core/config.py` | 只加 `calendar_local_timezone: str = ""`。这是本地日历源的种子，不是聊天设置里的产品时区。 |

第 1 步明确不动：`calendar_tool.py` 的分支、`api/calendar.py` 的路由、`ScheduleCard.tsx`、路由 few-shot、`_parse_time_from_message` 里的 `datetime.now()`。

`CalendarService` 可以是 `calendar.py` 里的薄类，模块级函数转给一个默认实例。工具继续调用这些函数，这样 `test_workflow.py` 打在工具模块上的补丁仍然包住数据库调用。

第 1 步的等价标准：

1. 工具的可观察结果不变：缺字段追问、冲突文案、创建成功的 `direct_answer` 和卡片、放弃草稿、`pending_calendar` 的键。
2. 冲突 SQL 的语义不变：同一 `user_id`、`status == "active"`、`start_at < end_at` 且 `end_at > start_at`。首尾相接不算冲突。
3. 取消仍是 `status = cancelled`，不物理删除。
4. 新列只写默认值：`provider='local'`，其余为空字符串。第 1 步不读它们做逻辑。
5. `get_timezone()` 和 `find_free_slots()` 可以存在并有单测，但工具的冲突回复仍调用 `suggest_next_slot`。
6. 工具源码仍不出现 `CalendarEvent` 或 `db.query`。用与 `test_router.py` 相同的 `inspect.getsource` 断言锁住。

若实现时不得不移动上述函数，必须在同一提交里改测试的 patch 路径，并跑第 6 节的回归命令。默认做法是不移动。

归属校验：`require_owned_event` 继续在 PATCH/DELETE 之前做 404。Provider 的每个方法再按 `user_id` 过滤。两处都留着。所有权测试不用改语义。

## 3. 表结构与迁移

Alembic 修订号 `0007_calendar_provider_fields`，上一修订为 `0006_session_context`。加列前先看 inspector，写成与 `0006` 一样的幂等迁移，因为 `init_db()` 的 `create_all` 可能已经把模型上的新列建出来了。

### 3.1 `calendar_events` 预留列

| 列 | 类型 | 默认 | 含义 |
| --- | --- | --- | --- |
| `timezone` | String，可空，默认 `''` | 已有行回填 `''` | 写入当时的 IANA 时区，例如 `Asia/Shanghai`。空表示旧数据，没有日历时区快照。 |
| `location` | String，可空，默认 `''` | `''` | 地点。本阶段工具可以不填。 |
| `description` | Text，可空，默认 `''` | `''` | 说明。 |
| `external_id` | String，可空，默认 `''` | `''` | 外部事件 id。本地事件保持空。 |
| `provider` | String，可空，默认 `'local'` | 已有行回填 `'local'` | `local`，以后是 `google` / `outlook`。 |

不要改 `start_at` / `end_at` 的类型，也不要加时区换算。索引保持 `user_id`、`start_at`、`end_at`。本阶段不要做 `(user_id, provider, external_id)` 的部分唯一索引：SQLite 与 Postgres 的部分索引写法不同，而唯一性要等外部 id 真的写入时才有意义。可以加普通索引，不是必须。

Postgres 上 `0004_rls` 已经按 `user_id` 保护 `calendar_events`。新列不需要改这条策略。

### 3.2 `calendar_sources`

每个用户一条本地日历配置，用来放时区。不要做用户设置页。

| 列 | 说明 |
| --- | --- |
| `id` | 主键 |
| `user_id` | 外键，索引 |
| `provider` | 默认 `local` |
| `timezone` | IANA 字符串，默认 `''` |
| `display_name` | 默认 `本地日历` |
| `external_account_id` | 默认 `''`。本阶段不用于登录。 |
| `created_at` | 与其他表一样 |

不要加 `(user_id, provider)` 唯一约束。多账户 UI 不在本阶段，但两个外部账户会需要两行相同 provider。Local 实现自己「按 `user_id` + `provider='local'` 取 id 最小的一行，没有就插入」。

Postgres 上给这张表加上与 `calendar_events` 相同的 RLS（`user_id = current_setting('app.current_user_id')`）。SQLite 上这段迁移是空操作，与 `0004` 一致。

种子：新建行时，若 `CALENDAR_LOCAL_TIMEZONE` 是合法 IANA 名，写入该值。空字符串或非法值都写成 `''`，并打日志，不要让聊天请求失败。`get_timezone()` 在列为 `''` 时使用进程本地时区，这只是今天 `datetime.now()` 的等价行为，不是新产品默认时区。第 2 步的单测要注入固定时区，不能依赖宿主机。

事件上的 `timezone` 从第 2 步起，在创建和修改时抄写本次使用的 IANA 名。第 1 步保持空字符串。

### 3.3 卡片字段

`serialize_event_card` 在现有键之外增加：

```json
{
  "timezone": "",
  "location": "",
  "description": "",
  "provider": "local",
  "external_id": ""
}
```

旧前端会忽略多余的键。`id`、`title`、`start_at`、`end_at`、`participants`、`status` 保持不变。

本地 `status` 继续用 `active` / `cancelled`。不要在本阶段改成 Google 的 `confirmed`。以后的映射是 `active` ↔ `confirmed`，`cancelled` ↔ `cancelled`。

## 4. 动作与 `pending_calendar_op`

会话列、`ToolContext.pending_calendar`、SSE 和工作流状态的字段名都仍然叫 `pending_calendar`。变的是 JSON。

### 4.1 工具入参

路由现在对 calendar 传的是空 `tool_args`（只有 sandbox 会填参数）。本阶段仍以用户原话为准。`execute(ctx, args)` 同时接受下面的结构化参数，供单测和以后使用；两者冲突时，本阶段以原话解析结果为准。

```json
{
  "action": "create | update | cancel | list | suggest",
  "title": "",
  "start_at": "",
  "end_at": "",
  "participants": [],
  "location": "",
  "description": "",
  "event_id": null,
  "range_start": "",
  "range_end": "",
  "duration_minutes": null,
  "limit": null
}
```

`query` 不单列。查找用 `title` 加时间范围。模型若输出 `query`，服务里当成 `list` 的别名，对外只保留 `list`。

### 4.2 五个动作

共同规则：写操作在槽齐且没有未解决的冲突、多候选之前，一次完成。成功回复使用库里的起止时间字符串，不把时间交给回答模型重写。`reflect_node` 已经会在有 `direct_answer` 时停止后续工具；`answer_node` 在有 `direct_answer` 时不会再调 `complete_text`。改和取消要走同一条路。

**create**

- 槽：`title`、`start_at`。`end_at` 缺省为开始后 1 小时。`participants`、`location`、`description` 可空。
- 冲突：沿用第 1.2 节的重叠条件。冲突时不写入，保留草稿，`awaiting = "slots"`，并给出 `suggested_slots`。
- 成功：清空 pending，返回卡片。`direct_answer` 保持现在的「已为你安排「标题」，时间 …」句式。

**update**

- 先定位，见 4.4。定位到一条后，只改用户提到的字段。
- 改了时间就要做冲突检测，并排除自己的 `id`。
- 成功：`direct_answer` 写明新的起止时间，并返回更新后的卡片。

**cancel**

- 这里指取消已存在的事件，不是放弃草稿。
- 定位规则与 update 相同。命中后软取消。
- 成功：卡片 `status=cancelled`，`direct_answer` 说明已撤销及原时间。
- 没有 pending 时，「取消明天的面试」走这个动作。有 pending 时的「算了 / 取消」仍是放弃草稿，见 4.3。

**list**

- 默认范围：日历时区的今天 00:00 起 7 天，只含 `active`，按 `start_at` 升序，最多 20 条。
- 用户指定了日期或范围时用该范围。
- 只回 `direct_answer` 文本，不附 `schedule_card`（一张卡片会被理解成可编辑的那一条）。
- 结束时 `update_pending_calendar=true` 且 pending 为 `null`，避免下一句被短路进日历。

**suggest**

- 调用 `find_free_slots`。默认时长 1 小时，默认窗口是日历时区的当前时刻起 7 天，默认 3 个空隙。
- 用户指定了日期，窗口就是该日本地日的 00:00–24:00。用户指定了时长，用该时长。
- 不按工作时间过滤。空隙算法与重叠判定一致：按开始时间排序，游标从窗口起点走，两事件之间以及最后一条到窗口结束之间，长度不小于时长的区间可输出；终点与下一条起点相同视为空闲。
- 只读。不写 pending，除非用户接着说「就定第一个」，那一轮再进入 create。

`find_free_slots(user_id, start, end, duration, limit=3, exclude_event_id=None)`。创建冲突时，从用户要的开始时间（若它落在忙碌段内，则从该忙碌段的结束）搜到窗口末尾。零个空隙：说明这段已满，保留 create/update 草稿。

### 4.3 `pending_calendar_op`

顶层保留 `title` 和 `missing_fields`，这样 `format_working_block` / `format_context_line` 不用改语义。评测里的 context 样本还在用 `missing_fields: ["location"]` 这类提示词，它们不是工具槽位；不要为了迁就样本去改必填项，也不要改那些样本。

```json
{
  "action": "create",
  "title": "周会",
  "start_at": "2026-08-06T15:00:00",
  "end_at": "",
  "participants": [],
  "location": "",
  "description": "",
  "timezone": "Asia/Shanghai",
  "event_id": null,
  "missing_fields": ["title"],
  "candidates": [],
  "suggested_slots": [],
  "awaiting": null,
  "original_request": "明天下午三点开周会"
}
```

| 字段 | 规则 |
| --- | --- |
| `action` | 缺省为 `create`。`EventDraft.from_dict` 必须读它，`to_dict` 必须写出它。今天 `to_dict()` 会丢掉键，而 `test_agent_e2e.py` 只检查工作流入参原样传递，并不经过 `EventDraft`。 |
| `awaiting` | `null`、`disambiguate`、`slots`。没有单独的「确认后再写入」状态。 |
| `candidates` | `{id, title, start_at, end_at}`，最多 5 条。 |
| `suggested_slots` | `{start_at, end_at}`，最多 3 条。 |
| `event_id` | update/cancel 已定位时填写。 |

读入旧 JSON：没有 `action` 则是 `create`；未知键忽略。第 2 步起，若旧草稿的 `missing_fields` 里还有 `participants`，读入时去掉；若已有 `start_at` 且没有 `end_at`，补 1 小时并去掉 `end_at`。因此用户的下一句可能会直接创建，即使上一句助手还在问参与人。放弃用语仍然只清草稿、不写库。

处理顺序：

1. 有 pending，且句子只是放弃（`取消|算了|放弃|不安排了|先不安排`），并且没有另外点名一条已存在的事件：清空 pending，回复「已取消本次日程安排。」不写库。这是今天的行为。
2. `awaiting=disambiguate`：用序号或标题在 `candidates` 里选。选不中就再问，不写库。
3. `awaiting=slots`：把选中的空隙写入草稿的起止时间，再走 create 或 update 的冲突检查。
4. `action=create` 且仍在补槽：只调用 `fill_pending_calendar`，不要把这句重新分类成新动作。
5. 否则分类。工具内部可以先看明显字样（查日程、有空、改到、取消+对象、安排/开会）。看不准再让现有的 `complete_json` 在五个动作里选一个，缺省 `create`。这套判断不得进入 `router.plan_tools`。

`workflow.plan_node` 和 `eval/tool_routing.py` 继续以「pending dict 非空」短路，不解析 `action`。list/suggest 完成后必须把 pending 写成空，否则下一句闲聊也会进日历。这是现有短路的既有限制，本阶段不增加「用户突然改问知识库就打断草稿」的例外。

### 4.4 定位

1. 有 `event_id`（来自卡片或 pending）：`get_event`，并且 `user_id` 必须匹配。
2. 否则用时间窗加标题子串。用户说了日期，窗是该日本地日；只说了时刻，窗是该时刻所在的本地日。标题用子串，双向包含；ASCII 用 casefold，中文按原文。
3. 0 条：说明没找到，不创建替代事件。
4. 1 条：继续。
5. 多条：`awaiting=disambiguate`，列出标题和时间。

不要把所有事件都放进候选。没有标题也没有时间时，追问要改或取消的是哪一条，不要猜最近的一条。

## 5. 补槽与时区

改这些规则的位置是 `extract_event`、`fill_pending_calendar` 和它们调用的 `_normalize_missing_fields`，不要在 `calendar_tool.py` 里抽槽之后再补一次默认值。`test_workflow.py` 会把 `extract_event` / `fill_pending_calendar` 整个替换掉；默认值若放在工具里，那些测试会在第 2 步无故失败。工具继续信任草稿上的 `missing_fields`。

| 规则 | 现状 | 改后 |
| --- | --- | --- |
| 参与人 | 空则列入缺失，并追问 | 可选。不列入 `missing_fields`，提示语也不再把参与人当必填例子。用户说了仍解析。 |
| 结束时间 | 没说就追问 | 有开始、没有明确结束时，结束 = 开始 + 1 小时，且不追问。用户说了「到 5 点」或时长，用用户的值，不再加 1 小时。 |
| 「明天三点」 | `datetime.now()` | `datetime.now(provider.get_timezone())`。存去掉 tz 的墙钟，与现在的 `_parse_dt` 一致。 |
| 已确认字段 | 提示词禁止重写，代码仍覆盖 | 本轮开始时已有值、且不在 `missing_fields` 里的 `title` / `start_at` / `end_at` / `participants` / `location` / `description`，忽略模型返回的新值。只填充仍缺失的字段。 |
| 抽取提示词 | 强制把 `end_at` 放进缺失 | 删掉这句。提示词写上日历 IANA 时区和该时区的当前时间，要求起止时间用该时区的无偏移 ISO。没说结束时间就让 `end_at` 为空，由程序加 1 小时。参与人省略时不要写入 `missing_fields`。 |

相对时间解析增加可选参数 `now` 和 `zone`，供测试注入。第 1 步不要改默认的 `datetime.now()`。

必填只剩标题和开始时间。标题在模型失败时仍可用现有的 `_infer_title`（周会、会议等）；推断不到才追问。

时区不进 ScheduleCard 的可编辑项，也不进聊天设置。卡片上若 `timezone` 非空，只读展示。用户要改时区，改的是日历源；本阶段没有这个 UI。外部账户接入后，`get_timezone()` 改为读账户时区，事件仍保存写入时的快照。

## 6. 测试计划

### 6.1 第 1 步必须仍然全绿的测试

在改补槽规则之前先跑：

```bash
cd backend && python -m pytest \
  tests/test_calendar.py \
  tests/test_workflow.py \
  tests/test_isolation.py \
  tests/test_router.py \
  tests/test_context_manager.py \
  tests/test_agent_e2e.py \
  -q --tb=short
```

这些断言在第 1 步不能改预期：

| 测试 | 锁住的行为 |
| --- | --- |
| `test_calendar.py` | 卡片能读出参与人 JSON；`suggest_next_slot` 保持 90 分钟时长。 |
| `test_workflow.py` 四个日历用例 | 缺字段则 `direct_answer` 且 `llm_messages == []`；创建成功带卡片；有 pending 时不调用 `plan_tools`；部分补槽后 pending 还在。它们 patch 的是工具上的函数，测的是控制流，不是「参与人永远必填」这条产品规则。 |
| `test_isolation.py::test_cross_user_calendar_patch_404` | 他人 PATCH 返回 404。 |
| `test_router.py::test_no_regex_fallback_branch_in_plan_tools` | `plan_tools` 源码不含 `calendar_hint`。 |
| `test_context_manager.py::test_extract_event_history_clipped` | patch 的是 `app.services.calendar.complete_json`。 |
| `test_context_manager.py::test_build_context_views` | pending 的顶层 `title` / `missing_fields` 仍能进上下文。 |
| `test_agent_e2e.py::test_workflow_kwargs_carry_pending_calendar_and_doc_scope` | 工作流入参原样带上 pending dict。 |

第 1 步新增、且不改变上述预期的测试：

- Local provider：创建、按窗列出、更新、软取消、跨用户 `get_event` 为空。重叠与今天的 `check_conflict` 一致，包括首尾相接不冲突。
- 工具源码不出现 `CalendarEvent`。
- 旧 `EventDraft` dict 没有 `action` 时读成 `create`。
- 新列默认值不改变 `serialize_event_card` 的原有键。

### 6.2 第 2 步单测（真实抽槽函数，mock `complete_json`）

注入固定 `now` 和时区，不要用宿主机时钟。

- 「明天下午三点周会」：开始是次日 15:00，结束是 16:00，`missing_fields` 不含 `end_at` 和 `participants`。
- 「明天三点到五点，参与人张三」：结束是 17:00，参与人得到保留。
- 跨日：时区固定为 `Asia/Shanghai`，`now` 取该区 23:30，「明天三点」落在下一日，而不是进程 UTC 的下一日。
- 已确认标题「周会」、开始时间已有；补充句只说「到 5 点」。标题和开始时间不变。
- 模型把已确认的开始时间改成另一个 ISO：代码丢掉该值。
- 旧 pending 含 `missing_fields: ["end_at", "participants"]` 且已有开始时间：读入后结束时间被补上，缺失列表不再包含这两项。

第 2 步不改 `test_workflow.py` 里被 patch 的那四个用例的断言。另加上述单测覆盖新产品规则。若有人把默认 1 小时写进工具函数本体，这四个用例会失败，应把默认值移回 `extract_event` / `fill_pending_calendar`，而不是放宽工作流测试。

### 6.3 对话用例

放在 `backend/tests/test_calendar_dialogue.py`，多轮调用 `run_agent_workflow`，mock 抽槽 JSON，使用内存 SQLite。第 3 步可先覆盖单动作，第 5 步把下面整串跑通。

1. 建：「明天下午三点开周会」→ 有卡片，时长 1 小时，不追问参与人，pending 为空。
2. 查：「这周有哪些会」→ 文本里有该标题和时间，没有 `schedule_card`。
3. 改：「把周会改到四点」→ 同一 `id`，开始变 16:00，结束变 17:00，回复中的时间与库一致。
4. 冲突：再建一个重叠时段 → 不写第二条，回复给出最多 3 个不重叠空隙，pending 的 `awaiting` 为 `slots`。
5. 撤销：「取消刚才的周会」→ `status=cancelled`，卡片仍返回。
6. 放弃草稿：创建追问过程中说「算了」→ 库中无新行，pending 为空。
7. 多条同名：两个「周会」都在同一天，说「取消周会」→ 不写库，追问候选。
8. 已确认字段：草稿标题已是「周会」，用户只补结束时间 → 标题仍是「周会」。

### 6.4 第 4 步 API 与前端

- `GET /api/calendar?start=&end=` 只返回当前用户的事件。在 `test_isolation.py` 旁加跨用户用例：他人的事件不出现。
- PATCH 只改标题时，参与人保持不变（今天靠「空列表当成未传」碰巧成立）。
- 前端没有测试运行器。改完 `ScheduleCard` 后在浏览器里做：打开带卡片的会话，改标题和时间并保存，确认 PATCH 后的文案变了；点撤销，确认变成已撤销且按钮禁用；近日列表来自 GET，空窗口不显示一块错误。不要只截一张静态图。

## 7. 本阶段不做

- OAuth，以及 Google / Outlook 的 HTTP 客户端、webhook、同步令牌、双向同步。
- 外发邀请邮件、会议室或资源、多账户选择 UI。
- 在 Omni-Butler 里做时区设置页，或把时区写进用户偏好 / 记忆。
- 把 `start_at` / `end_at` 改成 `timestamptz`，或在读出时把墙钟换算成 UTC。
- 重命名 `sessions.pending_calendar` 或 `ToolContext.pending_calendar`。
- 在 `plan_tools` 里恢复 `calendar_hint` 或任何日历正则。
- 重复日程、全天事件、提醒、忙闲以外的出席状态。
- 物理删除事件。DELETE 继续软取消。
- 接入检查清单里的外部供应商实现。第 5 步只写清单，不写客户端。

`provider` 与 `external_id` 只预留。Local 写入的 `external_id` 始终是空字符串。

## 8. 建议拆分的 PR（按 brief 的 1→5）

每一刀都从当时的 `release` 集成线拉出，并单独可回滚。不要把 2–5 混进第 1 个实现 PR。本文所在 PR 不在这五刀之内。

### 第 1 刀：Provider + Local，行为等价

- 新增 `calendar_provider.py`、`calendar_local.py`。
- `calendar.py` 的 CRUD 和 `check_conflict` 改为转调 Local。函数签名不变。
- 模型、Alembic `0007`、`init_db` 补列、`calendar_sources`、RLS。
- 创建路径把 `provider` 写成 `local`，其他新列用默认空字符串。不改时钟。
- 工具、API、前端、补槽提示词不动。
- 验收：第 6.1 节命令全绿，再加上 provider 单测。

### 第 2 刀：补槽与时区占位

- 改 `extract_event` / `fill_pending_calendar` / `_normalize_missing_fields` 和抽取提示词。
- 相对时间改为注入 `now` + `get_timezone()`。
- 写入事件时抄写 `timezone`。
- 读入旧 pending 时丢掉已不再必填的槽，并补默认结束时间。
- 验收：第 6.2 节；第 6.1 节里除新增单测外的旧断言仍然成立。

### 第 3 刀：list / update / cancel / suggest

- `execute_calendar` 按第 4 节分发。`EventDraft` 往返保留 `action` 及新的 op 字段。
- 冲突回复改走 `find_free_slots`。保留 `suggest_next_slot`，以免无故改掉 `test_suggest_next_slot_keeps_duration`；工具不再用它生成用户可见建议。
- `prompts.py` 增加 few-shot：查本周日程、把明天的周会改到四点、取消明天的面试、明天下午有空吗，均为 `needs_calendar=true`。反例「什么是日历」不进日历。不改路由代码结构。
- 定位、多候选、更新时的自排除冲突。
- 验收：第 6.3 节的单轮和多轮；`test_no_regex_fallback_branch_in_plan_tools` 仍绿。

### 第 4 刀：列表 API 与日程卡片表单

- `GET /api/calendar`，查询参数 `start`、`end`、可选 `status`，默认与 list 动作相同。返回 JSON 数组，与 `GET /api/sessions` 一样，元素是卡片。
- `CalendarUpdate.participants` 改为 `list[str] | None = None`。`None` 表示不修改，`[]` 表示清空。今天的 `default_factory=list` 加上「空则忽略」，使清空无法表达。
- 卡片增加只读的 `timezone`、`provider`、`external_id`，以及可选的 `location`、`description`。
- `ScheduleCard.tsx` 改为内联表单：标题、`datetime-local` 的开始和结束、参与人文本。提交无 `Z` 的 `YYYY-MM-DDTHH:mm:00`。地点和说明若出现在类型里，做可选输入，不作为这刀的验收条件。
- 新组件 `frontend/src/components/UpcomingEvents.tsx`，由 `ChatWindow` 挂载，不塞进每张历史卡片。GET 今后 7 天；空则不渲染；失败不挡住已有卡片。
- 样式只扩展 `frontend/src/styles.css` 里已有的 `.schedule-card`。
- 验收：第 6.4 节，含浏览器里走通修改、撤销和近日列表。

### 第 5 刀：回归与接入清单（清单仅为文档）

- 补齐第 6.3 节若第 3 刀未收进同一文件的对话用例。
- 新增 `docs/plans/calendar-integration-checklist.md`，不写外部客户端。清单至少包括：实现 `CalendarProvider` 的七个方法；OAuth 与令牌存放不在本地表的业务列里；`external_id` + `provider` 如何对应本地行；账户时区替换 `calendar_sources.timezone`；取消与外部取消的映射；不在本阶段做双向同步时，本地行仍是唯一读模型。
- 再跑一遍第 6.1 节的命令。

## 9. 风险与需要人决定的问题

1. **时区种子。** 未设置 `CALENDAR_LOCAL_TIMEZONE` 时，本计划用进程本地时区顶替今天的 `datetime.now()`，避免新造一个「产品默认上海」之类的时区。若部署机器是 UTC，而用户说的「明天三点」应按中国墙钟理解，相对时间会偏。需要确认：上线时是否强制配置该环境变量，以及空值时应该失败提示，还是继续沿用进程时区。
2. **进行中的草稿。** 第 2 步上线后，上一版还在追问「结束时间和参与人」的会话，用户下一句可能会直接落库。需要确认这是否可接受，或者只对新草稿启用默认 1 小时。
3. **「取消」有两种意思。** 有草稿时，单独的「取消 / 算了」放弃草稿；「取消明天的面试」取消事件。一句里两者都像时，本计划优先放弃草稿。若希望只要出现具体标题就改为取消事件，需要明确说一声。
4. **空闲窗口。** 默认 7 天、3 个空隙、不排除夜间。brief 没有工作时间。若只要 9:00–18:00，应在第 3 刀之前定下来。
5. **墙钟存储。** `_parse_dt` 丢弃偏移、不换算。第 1 步若有人「顺便」改成 UTC，已经写入的时间和卡片展示都会平移。外部接入之前都应保持墙钟，并在抽取提示词里禁止带 `Z` 的时间。
6. **浏览器时区与日历时区不同。** `datetime-local` 没有时区。表单按墙钟数字提交，与 API 现有行为一致。若两者不一致，页面上应标出日历时区；本阶段不做自动换算。
7. **短路困住下一句。** 只要 pending 非空，知识库问题也会进日历。这是今天的行为。本阶段不增加打断规则；放弃用语是唯一出口。
8. **PATCH 清空参与人。** 要等第 4 刀把 `participants` 改成可空三态，否则表单无法把参与人清掉。第 1–3 刀不要改这个请求模型。
9. **`suggest_next_slot` 留下。** 用户可见建议改走 `find_free_slots` 后，旧函数只为现有单测保留。可以在第 5 刀之后再删，不要在第 3 刀顺手删掉导致测试红。
10. **评测样本里的假槽位。** `backend/scripts/gen_context_eval.py` 的 pending 使用 `location`、`attendees`、`start_time`。那是上下文记忆题，不是工具协议。不要把这些字符串加进必填项，也不要在本阶段重写评测集。
11. **RLS 与新表。** 忘记给 `calendar_sources` 加 Postgres RLS 时，`omni_app` 角色会读到其他用户的时区配置。迁移应复制 `0004` 的策略，并在 Postgres 上人工看一眼 `\d+ calendar_sources`。
12. **模块改成包。** 若后续清理把 `calendar.py` 收成包，必须同时改 `complete_json` 的 patch 路径和工具模块上的四个 patch。不要放进第 1 刀。
