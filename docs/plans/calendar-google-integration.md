# 接入 Google 日历

本文只规划怎么把已经落地的本地日历接到 Google Calendar。评审通过之前不写 provider、OAuth 或界面。对应 PR 只应包含本文，并且合进 `develop`，不合进 `release`。

已合并的本地切片（`develop` 尖端 `1dec34f`，PR #10–#11、#13–#15）不要重做。`develop` → `release` 的 PR #12 继续暂停，等本文实现也进 `develop` 之后再看；本文不改 #12。

边界以 `docs/plans/calendar-integration-checklist.md` 为准。下面每条都对照当前代码，不另起一套交互。

## 1. 现状与目标

### 1.1 现在的调用链

预接入已经做完。对话不再因 `sessions.pending_calendar` 短路。写操作只出确认卡片，点确认才落库。

```text
对话
  → plan_tools（每句都走路由）
  → calendar_tool.execute_calendar
       create / update / cancel：抽一次槽，返回卡片，不写库
       list / suggest：只读
  → 用户在卡片上点确认
  → POST / PATCH / DELETE /api/calendar
  → require_owned_event（改、删）
  → app.services.calendar 里的函数
  → LocalCalendarProvider
  → calendar_events
```

卡片规则保持不变，不要在本阶段改回去：

- 草稿 30 分钟到期（`draft_expires_at`）。
- 下一句用户消息作废未确认的写卡片（`chat.py` 调 `void_draft_schedule_cards`）。`suggest` 的 `phase=info` 不清。
- 确认接口不校验草稿票据。知道接口的客户端事后 POST，就是一次正常创建。
- 工具路径不调用 `create_event` / `update_event` / `cancel_event`。

现在所有读写都写死了 `LocalCalendarProvider`，没有「按用户选实现」的入口：

| 调用点 | 现在做什么 |
| --- | --- |
| `calendar_tool._list_text` / `_suggest_result` | 直接 `LocalCalendarProvider` |
| `calendar.py` 的 `check_conflict`、`create_event`、`update_event`、`cancel_event`、`find_event_candidates`、`free_slots_for`、`calendar_local_now` | 直接 `LocalCalendarProvider` |
| `api/calendar.py` 的 `GET /api/calendar` | 直接 `LocalCalendarProvider.list_events` |
| `POST` / `PATCH` / `DELETE` | 经上面的服务函数，仍是 Local |

`CalendarProvider` 的七个方法在 `backend/app/services/calendar_provider.py`。Local 在 `calendar_local.py`。协议上的 `get_timezone()` 没有 `user_id`；Local 返回进程本地时区，注释写明这不是产品默认时区。`find_free_slots` 只按本地忙闲找空隙，不过滤夜间。

时间现状不能在第一次接入时改掉：

- `api/calendar.py` 的 `_parse_dt` 去掉 `tzinfo`，不换算。`2026-08-06T15:00:00+08:00` 存成墙钟 `2026-08-06 15:00:00`。
- `start_at` / `end_at` 仍是无时区的 `DateTime`。Alembic `0007` 只加了预留列，没有把时间列改成 `timestamptz`。
- 相对时间（「明天三点」）用 `calendar_local_now(db)` 得到的「现在」。这个函数不接收 `user_id`，连上 Google 之前就是进程本地时钟。
- 侧栏 `UpcomingEvents.tsx` 用浏览器的 `new Date()` 算未来 7 天，再交给 `GET /api/calendar`。这是浏览器时区，不是账户时区。

### 1.2 目标调用链

交互不变。变的是确认接口背后的实现，以及「现在」从哪来。

```text
对话（卡片、30 分钟、下一句作废，与现在相同）
  → calendar_provider_for(db, user_id)
       未连接 Google → LocalCalendarProvider
       已连接 → GoogleCalendarProvider
  → 写操作仍只返回卡片
  → 用户点确认
  → 现有 POST / PATCH / DELETE
       已连接：先只读查询主日历忙闲（FreeBusy），再和本地行合并
       冲突仍是 409，不写入
       忙闲查询失败：这次确认失败，不写入，不回 200
       没有冲突：先写 Google，成功后再写本地行
       Google 写入失败：这次确认失败，本地行保持原样，不回 200
  → list / get：仍只读本地行（见第 7 节）
  → suggest / find_free_slots：已连接时，忙闲是本地行并上 FreeBusy；窗口再交 Google 工作时间
  → 「现在」：已连接则用该 Google 账户的时区
```

工具和 HTTP 层仍然不直接 `query` / `db.get(CalendarEvent)`。允许碰 `calendar_events` 的仍是 provider，以及现有的 `require_owned_event`。令牌表另说，见第 3 节。

### 1.3 对照

| 层 | 现在（`develop` @ `1dec34f`） | 接入之后 |
| --- | --- | --- |
| 选实现 | 各调用点写死 Local | `calendar_provider_for(db, user_id)`。一个用户最多一条 Google 连接 |
| 写操作 | 卡片确认后只插/改本地行，`provider='local'`，`external_id=''` | 已连接时先写 Google，再写本地行：`provider='google'`，`external_id` 为 Google 事件 id，`timezone` 为当时的账户时区 |
| 读操作 | Local 读本地表 | 列表和单条读取仍只读本地表，不把 Google 事件拉进来。已连接时，冲突和空闲额外做一次只读 FreeBusy |
| 「现在」 | 进程时区；侧栏用浏览器时区 | 已连接：Google 账户时区。未连接：保持今天的进程/浏览器时钟 |
| 空闲 | 不过滤夜间 | 已连接：忙闲并上主日历的 FreeBusy，再裁工作时间。Google 不提供工作时间时，整段窗口都算可用 |
| 令牌 | 无 | 单独表，按用户 + provider。不进 `calendar_events` |
| 取消 | 本地软取消 | 先让 Google 取消或删除成功，再把本地 `status` 改成 `cancelled` |

未连接的用户走 Local，行为与今天相同。不要为了接入给所有人加一个产品时区。

## 2. 文件放哪里

| 路径 | 职责 |
| --- | --- |
| `backend/app/services/calendar_factory.py` | 唯一选择 Local 或 Google 的地方。工具、服务、HTTP 改成问它，不再自己 `LocalCalendarProvider(...)` |
| `backend/app/services/calendar_google.py` | `GoogleCalendarProvider`。用已有的 `httpx` 调 Calendar REST，不引入 Google 官方客户端。HTTP 客户端可注入，单测不打外网 |
| `backend/app/services/calendar_secrets.py` | 令牌的加密、读写、刷新。日志里不出现令牌 |
| `backend/app/models/models.py` | 新表 `calendar_connections`。不给 `calendar_events` 加令牌列 |
| Alembic `0008_calendar_google_connection` | 新表、RLS、`calendar_events` 上非空 `external_id` 的唯一索引。`down_revision = 0007_calendar_provider_fields` |
| `backend/app/api/calendar.py` | 确认接口改为走工厂。另加连接、回调、断开。现有 POST/PATCH/DELETE/GET 的路径和冲突 JSON 不变 |
| `backend/app/core/config.py`、`.env.example`、`secrets_check.py` | 第 9 节的环境变量 |
| `frontend/src/components/SessionSidebar.tsx` | 侧栏底部一个连接入口。不新做账户选择页 |
| `frontend/src/components/UpcomingEvents.tsx` | 已连接时，7 天窗口按账户时区算，不再用浏览器「今天」 |

`app.services.calendar` 继续是模块，不要收成包。这些绑定继续可用：`extract_event`、`complete_json`、`EventDraft`、`from app.services import calendar as calendar_svc`。

协议补两处，使它和 Local 已经在用的参数一致。Local 的 `create_event` / `update_event` 已经接收 `location` 和 `description`，协议原文没有这两项；Google 实现按 Local 的参数来，并在同一刀把协议补上。`get_timezone()` 仍不接收 `user_id`：Google 实现在构造时绑住用户，方法签名不动。`list_events` 协议仍没有 `status`；Google 的列表至少返回未取消事件。Local 可以继续多一个 `status` 参数。

## 3. OAuth 与令牌

### 3.1 Google Cloud 项目

做一个 Web 应用类型的 OAuth 客户端，不要用 Android 客户端，也不要用依赖 Google Play 服务的登录 SDK。

同意屏幕先放在测试模式，把主人的 Google 账户加为测试用户。`calendar.events` 属于敏感范围；测试模式不用走 Google 的应用审核，也就不会为了审核再去碰手机。不要为了「看起来正式」提前发布同意屏幕。

回调地址只有后端这一条，例如 `https://<主机>/api/calendar/google/callback`。开发机可以用 `http://localhost` 上的同一路径。不要做手机深链。

范围只申请这三条：

| 范围 | 用来做什么 |
| --- | --- |
| `https://www.googleapis.com/auth/calendar.events` | 在主日历上创建、修改、删除事件 |
| `https://www.googleapis.com/auth/calendar.events.freebusy` | 只读主日历忙闲。`freeBusy.query` 接受的范围是 `calendar`、`calendar.readonly`、`calendar.freebusy`、`calendar.events.freebusy`，其中没有 `calendar.events`。只拿事件范围去查忙闲会 403 |
| `https://www.googleapis.com/auth/calendar.settings.readonly` | 读账户时区。事件范围读不到设置 |

`calendar.events.freebusy` 只返回忙闲区间，不返回标题和正文。不要为了忙闲改去申请 `calendar` 或 `calendar.readonly`。不要申请云端硬盘或其他产品。主日历固定为 `primary`。v1 没有日历选择器。

### 3.2 第一次连接怎么发生

产品里只有一个入口：侧栏底部，和「改密 / 记忆」放在一起。未连接显示「连接 Google 日历」。已连接显示对方账户邮箱和「断开」。没有多账户列表，没有时区设置页。

主人的手机是华为 LMR-AL00，没有 Google 移动服务，不能在这台手机上完成 Google 登录或验证提示。所以连接链接必须能在电脑浏览器里打开，并且电脑浏览器不必先登录 Omni-Butler。

流程：

1. 已登录的客户端请求 `POST /api/calendar/google/connect-link`。
2. 服务端生成一次性码，只存哈希，绑定当前 `user_id`，10 分钟过期，用一次作废。
3. 响应里给出绝对地址：`https://<主机>/api/calendar/google/connect?code=<一次性码>`。界面写出「在电脑浏览器打开，10 分钟内有效，不要发给别人」，并提供复制。
4. 电脑浏览器打开该地址。服务端核销一次性码，签发 `state`（含 `user_id`、过期时间，用现有 `SECRET_KEY` 签名），再 302 到 Google 同意页。
5. 同意页参数：`access_type=offline`，`prompt=consent`，`include_granted_scopes=false`。第一次必须拿到刷新令牌。
6. Google 回到 `GOOGLE_OAUTH_REDIRECT_URI`。回调核验 `state`，用授权码换令牌，加密后写入 `calendar_connections`，再跳回前端。回调不依赖手机上的会话饼干。
7. `state` 对不上、一次性码过期或已用过：拒绝，不写令牌。

一个用户一行。表上 `user_id` 唯一。再次连接时，Google 的 `sub` 必须和已存的一致，只更新令牌；`sub` 不同则 409，提示先断开。断开只删这一行令牌，不删本地日程，也不批量删除 Google 上的事件。

### 3.3 令牌存在哪里

新表 `calendar_connections`，不要建清单里禁止的 `calendar_sources`，也不要把令牌塞进 `calendar_events` 的 `description` 或其他业务列。

| 列 | 内容 |
| --- | --- |
| `user_id` | 唯一。一个用户一个外部账户 |
| `provider` | 固定 `google` |
| `google_sub` | Google 账户稳定 id，用来拒绝换绑 |
| `account_email` | 只用于界面显示，不是密钥 |
| `access_token` / `refresh_token` | 密文 |
| `token_expiry` | 访问令牌过期时间 |
| `scopes` | 当时同意的范围 |
| `created_at` / `updated_at` | 审计用 |

加密用 `cryptography` 的 Fernet，密钥来自 `CALENDAR_TOKEN_KEY`（urlsafe base64 的 32 字节）。不要自写加密。不要从 `SECRET_KEY` 推导后假装这就是独立密钥；`SECRET_KEY` 只签 `state`。

刷新：调用 Google 前若访问令牌快过期，用刷新令牌换新的，写回同一行。刷新失败则这次操作失败，不改本地日程，界面提示重新连接。日志、工具审计的 `args`、评测样本里只记 HTTP 状态和 Google 事件 id，不记授权码、访问令牌、刷新令牌、客户端密钥。

`bootstrap.py` 的 `_reassign_user_rows` 今天会把 `sessions`、`documents`、`calendar_events`、`memories` 从重复管理员挪到 `users.id=1`。新表加入这个列表，避免令牌留在被退役的那一行上。

## 4. `GoogleCalendarProvider` 与七个方法

构造时传入 `db` 和 `user_id`。读本地行时内部复用 `LocalCalendarProvider` 的查询，避免两套重叠条件。写 Google 用主日历 `primary`。

Google 事件与本地 DTO：

| 本地 | Google |
| --- | --- |
| `title` | `summary` |
| `location` | `location` |
| `description` | `description` |
| `start_at` / `end_at` | `start` / `end` 的 `dateTime` 使用墙钟字符串，不带 `Z`、不带偏移；同时带 `timeZone` 为账户 IANA 时区 |
| `participants` | 只留在本地列。不写 Google `attendees`，也不要 `sendUpdates`，避免发出邀请 |
| `status='active'` | 创建出来是 Google 的 `confirmed`。不要把本地 `status` 改名为 `confirmed` |
| `status='cancelled'` | 见第 6 节，对应 Google 删除 |
| `external_id` | Google 返回的事件 `id` |
| `provider` | `google` |
| `timezone` | 写入当时的账户时区快照 |
| `id` | 仍是本地整数。工具和确认接口只用它。Google id 不出卡片的可编辑字段 |

时间例子：本地 `2026-08-06 15:00:00`、账户时区 `Asia/Shanghai`，请求体是 `dateTime: "2026-08-06T15:00:00"`、`timeZone: "Asia/Shanghai"`。不要先换成 UTC 再提交。

七个方法：

| 方法 | Google 实现 |
| --- | --- |
| `get_timezone()` | `GET /calendar/v3/users/me/settings/timezone` 的 `value`。这是 IANA id。文档写明设置为默认值时 list 可能不返回该项，默认是 `Etc/GMT`。单条 get 若 404 或 value 为空，用 `Etc/GMT`。不要改用进程时区 |
| `list_events` | 读该用户本地 `active` 且与窗口重叠的行，含接入前的 `provider='local'` 行。不调用 `events.list` |
| `get_event` | 按本地 id 读，`user_id` 不符返回 `None`。不向 Google 取这一条 |
| `create_event` | 见第 6 节。成功后的行带 `provider='google'`、非空 `external_id`、时区快照 |
| `update_event` | 见第 6 节 |
| `cancel_event` | 见第 6 节。本地仍是软取消，不物理删行 |
| `find_free_slots` | 已连接：本地忙闲并上第 6.1 节的 FreeBusy，规则仍与 Local 相同（首尾相接算空闲，每个空隙只给一段所请求的时长）。窗口再按第 8 节裁工作时间。未连接：只看本地行 |

同一用户、`provider='google'`、非空 `external_id` 只对应一行。Alembic `0008` 加部分唯一索引：`(user_id, provider, external_id)`，条件是 `external_id <> ''`。本地行的空字符串不受这条约束。SQLite 测试库的表已经存在，`create_all` 不会给旧表补索引，所以 `init_db()` 里再幂等创建一次，和 `0007` 补列的做法一样。插入前先查；若撞上，删掉刚创建的 Google 事件，然后让这次确认失败。

## 5. 时区

时间真相是已连接 Google 账户的时区。Omni-Butler 不另存一项可编辑的时区偏好，不加 `CALENDAR_LOCAL_TIMEZONE`。

- `calendar_local_now(db)` 改为能接收 `user_id` 的 `calendar_now(db, user_id)`。已连接时用 `GoogleCalendarProvider.get_timezone()`，再 `datetime.now(zone).replace(tzinfo=None)`，得到该时区的墙钟「现在」。抽槽、list 的默认窗口、按标题找「还没结束的日程」都走它。
- 「明天三点」仍由现有的 `extract_event` / `_parse_time_from_message` 相对这个「现在」计算。不要另写一套时间解析。
- 写入和修改时，把当时的 IANA id 抄进该行 `timezone`。旧行继续可以是空字符串。用户以后在 Google 里改了时区，旧行的快照不回写，新的写入用新时区。已存的墙钟数字不要按新区重算。
- 卡片上仍然没有时区编辑框。`ScheduleCard.tsx` 的表单字段保持标题、起止、参与人、地点、说明。
- 未连接时，`get_timezone()` 仍是进程本地时区。这是接入前的占位，不是新产品默认。部署在 UTC 上时，未连接用户的「明天三点」仍按 UTC 理解；连上 Google 之后改为账户时区。
- 侧栏 `UpcomingEvents` 今天用浏览器时钟。已连接后先向 `GET /api/calendar/google/status` 要 `{connected, account_email, timezone}`，用返回的 IANA 时区计算「今天 00:00」起的 7 天。未连接则保持浏览器时钟。`GET /api/calendar` 在没传 `start`/`end` 时的默认窗口也改用 `calendar_now`，与侧栏一致。

第一次接入不迁移旧的 `start_at` / `end_at`，不把它们换成 UTC。

## 6. 创建、修改、取消的顺序

冲突条件不变：区间重叠才算冲突，`start_at < 对方 end_at` 且 `end_at > 对方 start_at`。首尾相接不算冲突。更新时排除自己这条本地行。冲突响应仍是 409，body 为 `{conflicts, suggested_slots}`，不调用 `events.insert` / `events.patch`，不插入、不修改。

已连接用户在查冲突和找空隙时，还要做第 6.1 节的只读忙闲查询。未连接用户仍只看本地行。

已连接用户的创建：

1. 按第 6.1 节查主日历忙闲。查询失败则确认失败（建议 502，正文「暂时读不到 Google 日历的忙闲，未写入。」），不写 Google，不插本地行。不是 409，也不是 200。
2. 把忙闲和本地 `active` 行合并。有重叠则 409，不调用 `events.insert`。
3. `events.insert` 到 `primary`。失败则不插本地行，确认接口返回错误（建议 502，正文是给用户看的短句）。不是 200。
4. 插入本地行，填上 `external_id`、`provider='google'`、`timezone`。墙钟与请求体一致。
5. 若第 4 步失败：调用 `events.delete` 删掉刚创建的 Google 事件，再返回错误。补偿删除也失败时仍然返回错误，日志只记 Google 事件 id，供人工清理。不能出现「Google 有了、本地没有、接口却 200」。

### 6.1 已连接时的忙闲查询（不是同步）

主人在 Google 日历里直接建的安排，本地表里没有。只看本地行会把新日程叠上去。已连接用户的这些路径要补一次只读查询：

- 预览卡片上的冲突和空隙（`calendar_tool` 里今天的 `check_conflict` / `free_slots_for`）。
- 确认 `POST` / `PATCH` 再查一次。
- `find_free_slots` 和聊天里的 suggest。

用 `POST https://www.googleapis.com/calendar/v3/freeBusy`，`items` 只有 `{ "id": "primary" }`。这是检查当下窗口的忙闲，不是同步：不调用 `events.list`，不把区间写成 `calendar_events`，不用 webhook，不用同步令牌。查完即弃。

请求和时区：

- 窗口用账户时区的墙钟。`timeMin` / `timeMax` 写成带该时区偏移的 RFC3339，`timeZone` 设为同一个 IANA id。这是在表达「这段墙钟在账户时区里是什么时刻」，不是把库里的 `start_at` / `end_at` 改成 UTC。
- 响应里的 `busy[].start` / `end` 按这个时区理解，去掉偏移后得到墙钟，再和本地行比较。不要把忙闲存进表。
- 冲突检测覆盖拟写入的 `[start, end)`。若还要附建议空隙，同一次查询覆盖 `free_slots_for` 的窗口（从拟开始时间起 7 天），不要为同一张卡片打两次。
- HTTP 不是 200，或 200 但 `calendars.primary.errors` 非空：算查询失败。`busy` 为空数组才是「这段没有 Google 上的安排」。

和本地行合并：

- 本地侧仍是该用户的 `active` 行。更新时用现有的 `exclude_event_id` 排除自己。
- FreeBusy 的每段只有起止，没有事件 id，也没有标题。
- 修改一条已经在 Google 上的行（`provider='google'` 且 `external_id` 非空）时，先用这条本地行当前的 `[start_at, end_at)` 去减忙闲：整段被盖住就丢掉；只重叠一部分就切掉重叠，剩下的留下。这样改到原时间不会和自己冲突。创建不减。还没写到 Google 的本地行也不减，否则会把同一时间上别人的安排减掉。
- 减完后再和本地行合并。两段重叠就并成一段。首尾相接仍然不算冲突。然后沿用 Local 的空隙算法，再按第 8 节裁工作时间。

卡片上怎么显示没有标题的忙闲：

- 本地行冲突保持今天的标题和时间。`ScheduleCard.tsx` 只把 `conflicts` 渲染成「标题 + 时间」。
- 减完后仍剩下的 FreeBusy 区间，若没有被一条已列出的本地冲突完全盖住，就多一条冲突：标题固定为「Google 日历上已有安排」，带起止，没有本地 id。同一段不要又显示本地标题又显示这句。
- 这条不放进 `candidates`。点选候选仍然只针对本地行。`ScheduleCandidate.id` 今天是 `number`；实现时让冲突项的 id 可以空着，避免前端把这段当成可修改的日程。
- 409 的 `conflicts` 可以混用本地卡片和这种无 id 的项。`suggested_slots` 的形状不变。

失败时怎么处理：

- 确认 `POST` / `PATCH`：忙闲查询失败就失败，不写 Google，不改本地。不要当成「没有冲突」继续创建。正文用上面的短句，不用 409 的冲突 JSON。
- 预览卡片：查询失败仍返回可编辑草稿，不阻断填表。`direct_answer` 写明「没能读到 Google 上的忙闲，确认时会再查；查不到就不会写入。」本地冲突可以照常列出。此时不要附 `suggested_slots`，避免推荐一段还没核对过 Google 的空隙。
- suggest：查询失败就不返回「可以考虑这些空闲时段」，也不附 `mode=suggest` 的卡片。`direct_answer` 写「暂时读不到 Google 日历的忙闲，不能建议空闲时段。」不写库。

未连接用户不调用 FreeBusy。断开之后的新创建走 Local，冲突也只看本地行。

已连接用户的修改：

- 改时间之前先做第 6.1 节的忙闲查询（含减去自身区间）。查询失败则不 `patch`，本地行不动。有重叠则 409，不 `patch`。
- 该行已经是 `provider='google'` 且 `external_id` 非空：先 `events.patch` 同一条，成功后再改本地行，并刷新 `timezone` 快照。Google 失败则本地行不动。
- 该行仍是接入前的本地行（`provider='local'` 或 `external_id` 为空）：确认修改时先 `events.insert`，成功后再把本地行改成 `provider='google'` 并写上 `external_id`。失败则本地行保持原样。这样旧日程会在第一次确认修改时出现在 Google，而不是悄悄只改本地。
- 只改标题、地点、说明时也走同一顺序。参与人只改本地列。

已连接用户的取消：

- Google 行：先 `events.delete`。成功，或 Google 返回 404/410（对方已经没有这条），再把本地 `status` 改成 `cancelled`。行还在。其他错误（403、5xx、网络）不改本地 `status`。
- 纯本地行：Google 上没有对应事件，只做今天的软取消。
- 不要先软取消再调 Google。

断开之后：

- 新的创建走 Local，`provider='local'`。
- 已经带 `external_id` 的行，PATCH 和 DELETE 返回 409 式的明确失败（正文说明需要重新连接），本地行不改。避免断开后只改本地、Google 上还是旧时间。
- 重新连上同一个 `sub` 后，这些行恢复「先 Google、后本地」。

本地 `status` 继续只有 `active` / `cancelled`。

## 7. 列表、建议、读取

清单第 7 节禁止的是把外部事件扫进本地行、当成已经同步。主人在本计划里收窄了冲突和空闲这一支：已连接用户额外做一次只读 FreeBusy，结果不落库。列表和单条读取仍遵守「不把 Google 全量当数据源」。

因此 v1：

- `list_events`、`get_event`、找候选，都读 `calendar_events`。不调用 `events.list`，也不为列表调用 FreeBusy。
- `GET /api/calendar` 和聊天里的 list 仍最多 20 条、默认 7 天、只含 `active`。侧栏近日日程也是这条路径，所以 Google 上新建、改名、改时间的条目不会出现在列表里。
- 冲突检测和 `find_free_slots` / suggest 按第 6.1 节把主日历忙闲并进来。suggest 卡片仍是 `mode=suggest`、`phase=info`。「用这个时段」仍只在前端换成一张 create 草稿，不写库。
- 不用 webhook，不用同步令牌，不把别人在 Google 里新建的事件插进本地表。

列表上的短注仍然要写：这里列出的是 Omni-Butler 保存过的日程，不是 Google 日历的完整副本。冲突和空闲会看到主日历上的忙闲，但看不到那些安排的标题。这不是双向同步。

## 8. 工作时间

Calendar API v3 的设置列表里有 `timezone`、`weekStart`、`hideWeekends` 等，没有「工作时间」。公开问题跟踪里也有「请暴露工作时间」的请求（Issue Tracker `231620767`）。`workingLocation` 事件表示人在哪办公，不是每周几点到几点，不能拿来当工作时间。

实现：

- `settings.list` 若将来带了工作时间字段，解析成「每个星期几的起止」。单测用注入的结构把求交测满，不假装今天的真实 API 已经返回它。
- 今天的真实响应没有该字段：`find_free_slots` 退回「请求窗口内全部算可用」，空隙算法与 Local 相同。建议文案不要写「已按工作时间过滤」。
- 不在代码里写死 9:00–18:00，也不做 Omni-Butler 自己的工作时间设置。

有工作时间时的求交：按账户时区把窗口切成一天一天，每天只保留工作时段和窗口的交集，再在这些片段里找空隙。忙闲已经是第 6.1 节合并后的结果。被裁掉的夜间不能当作空隙的起点。相邻事件首尾相接仍然算空闲。

## 9. 配置、密钥、隔离

环境变量，加进 `Settings` 和 `.env.example`。Compose 已经用 `env_file: ./backend/.env`，不必为这四项新开服务。

| 变量 | 作用 |
| --- | --- |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth 客户端 id |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 只在后端。不进前端包 |
| `GOOGLE_OAUTH_REDIRECT_URI` | 第 3.1 节的回调 |
| `CALENDAR_TOKEN_KEY` | Fernet 密钥 |

未配置客户端 id 时，连接入口返回明确错误，日历确认仍走 Local。配了客户端 id 却没有 `CALENDAR_TOKEN_KEY` 时，`secrets_check.py` 记一笔；`ENFORCE_SECURE_SECRETS=true` 则拒绝启动。

RLS：Postgres 上 `0004_rls` 用 `app.current_user_id` 保护 `calendar_events`。`0008` 给 `calendar_connections` 同样 `ENABLE` + `FORCE ROW LEVEL SECURITY`，策略是 `user_id = current_setting(...)`。SQLite 测试继续跳过，和 `0004` 一样。运行时仍用 `get_db_scoped` 里的 `set_rls_context`。回调在核销一次性码之后、写令牌之前，对那个 `user_id` 设置 RLS 上下文；不要用未设置租户的连接去写令牌。

`require_owned_event` 继续挡在 PATCH/DELETE 前面。POST 只用登录用户的 id，不接受 body 里的 `user_id`。别人的本地 id，`get_event` 返回 `None`，HTTP 仍是 404。

## 10. 测试与验收

单测放在 `backend/tests/test_calendar_google.py`，内存 SQLite，Google HTTP 用注入的假客户端。不在 CI 里放真实客户端密钥，也不访问 `googleapis.com`。

必须自动化的：

1. 未连接用户：七个方法的可观察结果与 Local 相同，`provider='local'`，`external_id=''`。
2. 一次性连接码：过期、重用、`state` 签名错误都不写令牌。令牌落在 `calendar_connections` 的密文列。`calendar_events` 的列集合不变，行里没有令牌字样。
3. 一个用户第二行连接被拒绝。`sub` 不同的重连被拒绝。断开后状态接口 `connected=false`。
4. `get_timezone`：假设置返回 `Asia/Shanghai` 时，「明天三点」相对该时区的现在，而不是进程 UTC。设置缺失时用 `Etc/GMT`，不用进程时区。
5. 创建：假 Google 写入返回 500 时本地无新行，接口不是 200。成功时 `provider='google'`、`external_id` 非空、`timezone` 为快照，墙钟数字与提交一致。本地冲突或 FreeBusy 重叠时 409，且不调用 `events.insert`。FreeBusy 失败时确认是 502，不插入。
6. 本地插入失败时，假客户端收到对刚创建 id 的 delete，接口不是 200。
7. 修改：Google 失败时标题和时间不变。成功时先看到 patch，再看到本地更新。旧的 `provider='local'` 行在已连接时第一次修改会先 insert。
8. 取消：Google 失败时 `status` 仍是 `active`。成功或 404 之后才是 `cancelled`，行还在。断开后对 Google 行的 PATCH/DELETE 不改行。
9. 非空 `external_id` 重复时插入失败。跨用户 `get_event` 为 `None`。
10. `list_events` / `get_event` 不调用 `events.list`，也不调用 FreeBusy。已连接的 `find_free_slots` 会调用 FreeBusy。没有工作时间、且 FreeBusy 为空时，空隙与 Local 相同；注入工作时间后，夜间不被当成空隙。注入一段没有本地行的忙闲后，该段不成为空隙，冲突标题为「Google 日历上已有安排」，且 `calendar_events` 不新增行。
11. 修改一条 `provider='google'` 的行时，减去它当前的本地区间后，原时间不再和自己冲突；相邻的另一段 FreeBusy 仍然冲突。`provider='local'` 的行不减 FreeBusy。响应里的偏移按账户时区折成同一墙钟数字。`calendars.primary.errors` 非空时，确认失败且不写。suggest 在 FreeBusy 失败时没有建议卡片。
12. 日志和审计参数的测试夹具里不出现访问令牌、刷新令牌。
13. 现有 `tests/test_calendar_card.py`、`test_calendar_provider.py`、`test_isolation.py` 仍绿。工具源码仍不出现 `db.query`。未连接路径不调用 Google。

不能自动化、要在验收说明里手测的：

- 用电脑浏览器打开一次性链接并完成 Google 同意。华为 LMR-AL00 上不要求出现 Google 登录页。
- 账户的两步验证若只绑在这台没有 Google 服务的手机上，同意会失败。手测前改用备用码、安全密钥，或一台能打开 accounts.google.com 的电脑上已经登好的会话。
- 真实账户的设置响应里今天没有工作时间，空闲应与「整段窗口可用」一致。
- 在 Google 网页上改一条已同步事件的时间，再在 Omni-Butler 里 list：仍显示本地旧时间。同一窗口上另建一条只存在于 Google 的安排，再在这里确认创建：卡片出现「Google 日历上已有安排」，本地不新增行。用来确认列表仍是本地的，而冲突已经能看见忙闲。
- 卡片路径：齐信息也先出表单；确认后 Google 和本地都有；冲突空隙只改表单；下一句作废草稿。实现者按预接入计划第 7.2 节的浏览器步骤再走一遍，并加上「连接入口只有一个」。

建议命令：

```bash
cd backend && python -m pytest \
  tests/test_calendar_google.py \
  tests/test_calendar_provider.py \
  tests/test_calendar_card.py \
  tests/test_calendar.py \
  tests/test_isolation.py \
  -q --tb=short
```

## 11. 本阶段不做

- Outlook，以及其他供应商。
- Webhook、同步令牌、把 Google 上的改动拉回本地、双向同步。
- 邀请邮件、参与人写成 Google attendees、会议室、资源日历。
- 重复日程、全天事件、提醒、专注时段、外出、办公地点事件。
- 多账户界面、日历选择器、产品内时区设置。
- 把已有墙钟换算成 UTC，或把 `start_at` / `end_at` 改成带时区类型。
- 多轮补槽，或让 `pending_calendar` 重新短路路由。
- 确认接口绑定草稿票据。
- 物理删除本地行。
- 在对话工具里直接创建事件。未确认仍然不落库。
- 为了工作时间去读 `workingLocation`，或写死 9:00–18:00。
- 为了给冲突补标题去调用 `events.list`。没有标题的忙闲就用「Google 日历上已有安排」。

## 12. PR 切片

每一刀单独可回滚，都从 `develop` 拉出、合回 `develop`。本文所在 PR 不在这六刀之内。不要为这些刀开向 `release` 的 PR，也不要合并 #12。

### 第 1 刀：连接与令牌，不改日程读写

- `calendar_connections`、`0008` 的表和 RLS、Fernet、四个环境变量。
- 一次性链接、回调、断开、状态接口。`sub` 不一致则拒绝。
- 侧栏可以先只显示复制链接；此时确认接口仍全部走 Local。
- 验收：第 10 节的 2、3、12。`calendar_events` 读写测试不需要改预期。同意范围里要包含 `calendar.events.freebusy`，这一刀可以先只把范围写进授权链接，还不调用 FreeBusy。

### 第 2 刀：工厂和「现在」

- `calendar_provider_for`。已连接用户的 `calendar_now` 使用账户时区。
- 抽槽和 list 默认窗口改走它。未连接行为不变。
- 侧栏在已连接时用状态接口里的时区计算 7 天。
- 验收：第 10 节的 1、4。现有卡片测试仍是「不写库」。不调用 `events.insert`。

### 第 3 刀：创建，并带上忙闲查询

- 已连接的预览和 `POST`：先 FreeBusy，再和本地行合并。重叠则 409，且不 `insert`。查询失败则确认失败，不写入。预览失败仍出卡片，但不给建议空隙。
- 没有本地行的忙闲，卡片标题用「Google 日历上已有安排」，不落库。
- 没有冲突时：Google 成功后再插本地。失败不插，本地失败则补偿删除。
- 部分唯一索引。时区快照。墙钟不换算。请求 FreeBusy 时用账户时区格式化窗口，不改表里的时间列。
- 协议补上 `location` / `description`。
- 验收：第 10 节的 5、6、9，以及第 10 节里「无本地行的忙闲不落库」那一句。不把这一刀推迟到第 5 刀，避免创建先上线、却看不见 Google 上已有的安排。

### 第 4 刀：修改和取消

- 修改沿用第 3 刀的 FreeBusy。`provider='google'` 的行先减去自己当前的本地区间，再判断冲突。纯本地行不减。
- 查询失败或仍有重叠：不 `patch`，本地不动。
- 通过之后，Google 行先 patch，成功后再改本地。
- 旧的本地行在已连接时，第一次确认修改会先在 Google 创建。
- 断开后，带 `external_id` 的修改和取消拒绝写入。取消本身不查 FreeBusy。
- 验收：第 10 节的 7、8、11。取消失败时本地仍是 `active`。

### 第 5 刀：空闲与只读说明

- suggest / `find_free_slots` 复用第 3 刀的 FreeBusy，再交工作时间。没有工作时间则整段窗口都可用来找空隙，文案不声称已按工作时间过滤。FreeBusy 失败时不给建议时段。
- list / get 仍不调用 `events.list`，也不调用 FreeBusy。
- 在连接状态或列表旁用一句话说明：列表不是 Google 日历的完整副本；冲突和空闲能看到忙闲，但看不到那些安排的标题。
- 验收：第 10 节的 10、13。

### 第 6 刀：手测记录

- 按第 10 节不能自动化的五项走一遍，把结果写进该 PR 的说明。
- 没有真实客户端密钥时，这一刀只保留手测步骤，不把密钥提交进仓库。
- 六刀都进 `develop` 之后，才重新看 PR #12 要不要继续暂停。那是另一次决定，不属于这六刀。

## 13. 风险

1. **华为 LMR-AL00 没有 Google 服务。** 不能在这台手机上弹出 Google 登录，也不能依赖 Google 应用的「是我」提示。连接必须用第 3.2 节的一次性链接，在电脑浏览器里完成。若该 Google 账户的两步验证只绑在这台手机上，同意页仍然过不去；要事先准备备用码、安全密钥，或在电脑上使用已经通过验证的登录会话。这是上线前的账户准备，不是再做一个手机 SDK。
2. **一次性链接等于暂时的授权委托。** 10 分钟内任何人打开都会把当时的 Google 账户绑到这个 Omni-Butler 用户。界面要写明不要转发。码只存哈希，用一次即废。
3. **测试模式的同意屏幕。** 没把主人加为测试用户时，Google 会拒绝同意。不要用发布应用来绕过，发布会触发敏感范围审核。
4. **列表仍会和 Google 不一致。** v1 不把对方的事件拉回本地。在 Google 里新建、改名或改时间，列表和侧栏仍显示本地旧内容，也没有那些条目的标题。冲突和空闲会通过 FreeBusy 看到主日历的忙闲，因此不会在已有安排上再订一条；查不到忙闲时确认会失败，而不是当成有空。剩下的偏差是：减自身区间用的是本地起止，若这条在 Google 上已被挪走，旧区间里另一条安排可能被减掉而看不见；确认修改仍可能用本地时间覆盖 Google 上的新时间。不要把这写成已经双向同步。
5. **半写。** Google 成功而本地提交失败时，必须补偿删除并返回错误。补偿也失败则日志里只留事件 id。单测要锁住「不是 200」。
6. **时区默认值。** 账户没把时区从 Google 默认改掉时，API 可能不返回该项，文档默认是 `Etc/GMT`。已连接用户这时按 `Etc/GMT` 理解「明天」，而不是悄悄改用服务器时区。未连接用户仍是进程时区。两套时钟只按「有没有连接」区分，不增加第三个产品设置。
7. **工作时间今天读不到。** 空闲在真实账户上会包含夜间，直到 Google 真的在设置接口里返回工作时间。注入测试保证求交代码在，不把 9 点到 18 点写死冒充对方的设置。
8. **旧日程。** 接入不搬迁已有墙钟。它们留在本地，直到下一次确认修改才出现在 Google。只取消、不修改的旧日程不会在 Google 上留下痕迹。
9. **刷新令牌丢失或被撤销。** 之后的确认失败，本地不改。用户用同一 `sub` 重新连接后恢复。换一个 Google 账户必须先断开；断开不删除已经写到 Google 的事件。
