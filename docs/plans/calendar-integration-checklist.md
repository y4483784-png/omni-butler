# 外部日历接入检查清单

这份清单只说明以后接 Google / Outlook 时要守住的边界。本阶段不写 OAuth，也不写外部客户端。本地确认流程仍然只读写 `calendar_events`。

当前门面是 `CalendarProvider`（`backend/app/services/calendar_provider.py`）。本地实现是 `LocalCalendarProvider`。工具和 HTTP 层继续只调用服务，不直接查表。

## 1. 七个方法都要实现

新的 provider 要补齐这七个方法，签名与协议一致。缺一个，工具或确认 API 就不能换实现。

| 方法 | 本地现在做什么 | 外部要做什么 |
| --- | --- | --- |
| `get_timezone()` | 进程本地时区，不是产品默认时区 | 读该账户在外部日历里的时区 |
| `list_events(user_id, start, end)` | 该用户、`active`、与窗口重叠的事件 | 同一窗口的外部事件，映射成 `CalendarEventDTO` |
| `get_event(user_id, event_id)` | 不是本人的返回 `None` | 先对上本地行，再按归属读取；别人的 id 仍返回 `None` |
| `create_event(...)` | 插入本地行，`provider='local'`，`external_id=''` | 在外部创建成功后，再写本地行并填上外部 id |
| `update_event(...)` | 只改本人的行 | 先改外部，成功后再改本地行 |
| `cancel_event(...)` | 软取消，`status='cancelled'`，不删行 | 在外部取消或删除，本地仍软取消 |
| `find_free_slots(...)` | 只按本地忙闲找空隙，不过滤夜间 | 先拿到外部工作时间，再和忙闲求交 |

`list_events` 的本地实现额外接受 `status`，默认 `active`。协议本身不要求这个参数。外部实现至少要能列出未取消的事件。

## 2. OAuth 和令牌不要进业务列

`calendar_events` 只有业务字段：`timezone`、`location`、`description`、`external_id`、`provider`，以及原有的标题和时间。

访问令牌、刷新令牌、客户端密钥不要写进这些列，也不要写进 `description`。令牌放在单独的密钥存储里，按用户和 provider 取用。日志和评测样本里也不要打印令牌。

本阶段没有多账户选择界面。接入第一家外部日历时，一个用户先只绑定一个外部账户。

## 3. `external_id` 和 `provider` 怎么对上本地行

- 本地事件：`provider='local'`，`external_id=''`。不要把空字符串当成外部 id。
- 外部事件：`provider` 用稳定短名，例如 `google` 或 `outlook`；`external_id` 用对方的事件 id。
- 同一个人、同一个 `provider`、同一个非空 `external_id` 只对应一行。查重放在写入外部 id 的时候做。现在没有部分唯一索引，因为本地行的 `external_id` 都是空的。
- 工具和确认 API 仍用本地整数 `id`。外部 id 只在 provider 内部用来调用对方接口。
- `get_event` 收到的是本地 id。别人的行继续返回 `None`，HTTP 层仍用 `require_owned_event` 回 404。

## 4. 时区改到账户上，事件上只留快照

时间真相是外部账户的时区，不是 Omni-Butler 里另设的一项偏好。

- `get_timezone()` 改为返回该账户的 IANA 时区。相对时间（「明天三点」）用这个时区的「现在」。
- 写入和修改时，把当时的时区抄进该行的 `timezone`。旧行可以继续是空字符串。
- 继续用无时区的墙钟存在 `start_at` / `end_at`。不要在第一次接入时把已有时间换算成 UTC，否则已经确认的日程会平移。
- 卡片不提供时区编辑。用户改时区，改的是外部日历账户。

## 5. 空闲要交上外部的工作时间

本地 `find_free_slots` 保持现状：不过滤夜间，相邻事件首尾相接算空闲，每个空隙只给出一段所请求的时长。

外部实现改为：先用对方日历的工作时间裁窗口，再在剩下的区间里找空隙。工作时间来自该日历，不在代码里写死 9:00–18:00。对方没有工作时间时，退回「窗口内全部算可用」，并在接入说明里写清楚。

## 6. 取消怎么映射

本地 `DELETE /api/calendar/{id}` 和 `cancel_event` 都是软取消：`status` 从 `active` 变成 `cancelled`，行还在。

外部侧把这次取消映射成对方的取消或删除。映射失败时不要先把本地标成已取消。成功之后本地再软取消。不要物理删除本地行，否则确认卡片和隔离测试对不齐。

对外状态以后可以再映射：`active` 对应对方的 confirmed，`cancelled` 对应对方的 cancelled。本阶段不要把本地 `status` 改成 `confirmed`。

## 7. 还没有双向同步

在做出站同步之前：

- 聊天里的确认仍只通过现有 `POST` / `PATCH` / `DELETE` 写本地行。外部 provider 可以在这些调用里顺带写对方，但失败要让这次确认失败，不能只写一半还回 200。
- 不做 webhook、同步令牌、也不把对方的改动拉回本地。
- 对方日历里后来被别人改掉的时间，本地不会自动跟上。这一点要写进接入说明，避免当成已经双向同步。
- 列表和空闲建议读的是 provider 返回的结果。本地实现读的是本地表。外部实现在没有同步之前，应读它刚刚写过的那些本地行，而不是临时扫全量外部日历当唯一数据源，除非那次读取也把结果落成本地行。

接入完成的最低标准：七个方法有实现；令牌不在业务列；非空 `external_id` 能对上唯一本地行；时区来自账户；空闲尊重对方工作时间；取消是软取消；没有宣称双向同步。
