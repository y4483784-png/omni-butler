# 日历发布脚本（100 条固定题）

本文只定一份固定的页面验收题，不改产品行为。落地时只加本文，以及同目录的题单 [`fixtures/calendar-release-scripts-100.csv`](fixtures/calendar-release-scripts-100.csv)。受众是人，或对着真实页面跑的 Playwright：已连接的 Google 日历，逐条看卡片、回复原文和 Google 上的结果。

指标只有一个：`passed/total`，总分母是 100。这不是仓库里的 LLM 评分评测，也不进 CI 的 agent eval。pytest 硬门禁不在本文，留给另一份计划。

对齐现有能力，不改它们：确认卡才写 Google；会议卡和时间块卡字段不同；时间块没有参与人、没有地点；备注进 Google `description`；标题对上唯一一条时，卡片预填该条的全部字段，含 Google 事件 id，删除用这个 id；空档先列出当天日程，不把已占用时段说成有空；相信模型，不加墙钟正则；账户 IANA 时区；卡片上的起止是无偏移墙钟；网络失败说暂时连不上；授权失败说请重新连接。

## 1. 出题规范

每题一个对象，字段只许下面这些。目录里的一行展开成这个对象时，缺的字段用第 1.4 节的缺省，不许另加字段，不许改枚举。本套已经写满 100 条。再加题是另一份计划，而且新题必须沿用同一形状。

### 1.1 字段

| 字段 | 必填 | 取值 |
| --- | --- | --- |
| `id` | 是 | `CRS-001` … `CRS-100`，三位数字，稳定不重排 |
| `category` | 是 | 第 2 节的 13 个枚举之一 |
| `utterances` | 是 | 1–3 句中文，按顺序原样发送。不许改写、不许同义替换 |
| `clock.zone` | 是 | 缺省 `Asia/Singapore` |
| `clock.run_window` | 是 | 缺省账户当地 `09:00–11:00`。整套在这个窗口开跑 |
| `clock.today` | 是 | 记号 `D0`，开跑时账户时区的公历日期，由执行人记下 |
| `setup.connection` | 是 | `connected` 或 `disconnected` |
| `setup.fault` | 是 | `none`、`blackhole`、`revoke`。见第 5.3 节 |
| `setup.seed_events` | 是 | 数组，可空。每条含 `key`、`title`、`start_at`、`end_at`、`description`、`location`、`all_day`、`local_participants` |
| `setup.cleanup_prefix` | 是 | 固定 `CRS` |
| `expect.card.type` | 是 | 见第 1.2 节 |
| `expect.card.phase` | 是 | `draft`、`info`、`absent` |
| `expect.card.prefill` | 否 | 确认前卡片上必须相等的字段 |
| `expect.card.absent_controls` | 否 | 页面上不得出现的控件 |
| `expect.card.must_list_titles` | 否 | 列表卡必须出现的标题 |
| `expect.card.must_not_list_titles` | 否 | 列表卡不得出现的标题 |
| `expect.card.suggested_slots` | 否 | `absent`、`present_ok`、`non_overlapping` |
| `expect.card.must_not_overlap` | 否 | 建议时段不得与这些区间相交。端点相接不算相交 |
| `expect.before_confirm_google` | 是 | 固定 `none` |
| `expect.confirm` | 是 | 见第 1.2 节 |
| `expect.google.op` | 是 | `none`、`create`、`update`、`delete` |
| `expect.google.match` | 否 | 确认之后 Google 上必须成立的字段 |
| `expect.google.unchanged` | 否 | 必须原样留下的种子 `key` |
| `expect.copy_must` | 否 | 回复或卡片错误里必须出现的子串 |
| `expect.copy_must_not` | 否 | 不得出现的子串 |
| `pass` | 是 | 一句话，把上面的断言收成执行人勾选的条件 |
| `notes` | 否 | 只给执行人看，不另加断言 |

`prefill` / `google.match` 里允许的键：`title`、`start_at`、`end_at`、`duration_minutes`、`participants`、`location`、`description`、`external_id`、`kind`、`attendees`。没有列出的键不检查。

### 1.2 枚举

`category`：`create_meeting`、`create_block`、`reschedule_keep`、`reschedule_change`、`cancel_by_id`、`list_schedule`、`free_busy`、`conflict_filter`、`notes_description`、`attendees`、`error_paths`、`wall_clock`、`confirm_gate`。

`expect.card.type`：

| type | 页面 |
| --- | --- |
| `meeting_create` | `确认创建`。控件：标题、开始、结束、参与人、地点、说明。没有「时长」 |
| `block_create` | `确认创建`。控件：标题、开始、时长、说明。没有参与人，没有地点，没有单独的结束框 |
| `meeting_update` | `确认修改`。控件同会议创建 |
| `block_update` | `确认修改`。控件同时间块创建 |
| `cancel` | `确认撤销`。标题可改。开始、结束、参与人、地点、说明都在，且不可改 |
| `list` | 只读行：时间加标题。没有确认按钮。空档题才有「用这个时段」 |
| `none` | 没有日程卡 |

`expect.confirm`：`do_not_confirm`、`confirm_create`、`confirm_update`、`confirm_cancel`、`click_slot_then_stop`、`edit_then_confirm`、`confirm_after_seen_empty`。

`external_id` 的取值：`absent`（载荷里空字符串）、`seed:<key>`（等于该种子的 Google 事件 id）、`new`（确认后新插入的 id）。页面不渲染这个 id。执行人从聊天响应的 `schedule_card.external_id` 读，浏览器开发工具或 Playwright 监听 SSE 均可。

### 1.3 时间记号

开跑时记下账户时区 `Asia/Singapore` 的公历日期，称为 `D0`。

| 说法 | 日期 |
| --- | --- |
| 今天 | `D0` |
| 今晚 | `D0` 的 20:00 |
| 明天 | `D0+1` 个公历日，记作 `D1` |
| 后天 | `D2` |
| 大后天 | `D3` |

钟点：早上九点 = 09:00，上午九点 = 09:00，上午十点 = 10:00，上午十一点 = 11:00，中午十二点 = 12:00，下午三点 = 15:00，下午四点 = 16:00，晚上七点 = 19:00，晚上八点 = 20:00。

时长：半小时 = 30，一小时 = 60，两小时 = 120，三小时 = 180。创建句没有说结束、也没有说时长时，结束 = 开始 + 60 分钟。这是现有草稿补全。题仍然期望这个结束时间。模型给了别的钟点，题记 FAIL。不许为了让题变绿，在产品里加正则去改卡片。

改期句只搬开始、没有要求改结束或备注时：时长等于种子的时长，备注等于种子的备注全文。

写成比较用的墙钟：`D1T15:00:00` 表示 `D1` 那天的 `YYYY-MM-DDTHH:MM:00`。字符串里不得有 `+`，不得有 `Z`。

本套不用「下周几」「周几」。只用今天、明天、后天、大后天、今晚，加上面的钟点。

### 1.4 缺省

目录行没有写的字段，展开时填这些值：

- `clock.zone = Asia/Singapore`，`run_window = 09:00–11:00`，`today = D0`
- `setup.connection = connected`，`setup.fault = none`，`cleanup_prefix = CRS`
- `seed_events = []`，种子的 `description = ""`，`location = ""`，`all_day = false`，`local_participants = []`
- `expect.before_confirm_google = none`
- 创建会议：`absent_controls = [时长]`，`participants = []`，`location = ""`，`description = ""`，`external_id = absent`
- 创建时间块：`absent_controls = [参与人, 地点, 结束]`，`description = ""`
- 写操作在点确认之前，Google 没有新事件，种子的 id、标题、起止、说明都不变
- 任何确认写进 Google 的事件，来宾（attendees）为空。参与人只留在卡片和本地，不作为 Google 来宾发出
- 标题都以 `CRS` 开头，方便清理

### 1.5 怎么判一条

一条 PASS，当且仅当该题 `pass` 里的每一条都成立，并且 `before_confirm_google` 成立。有一条不成立就是 FAIL。没有半分，没有「跳过算通过」。

观察三处，缺一处就不判：

1. 页面：控件、按钮文案、状态（待确认、待确认撤销、未确认、已过期）、列表行、「用这个时段」、卡片下的文字和错误。
2. 载荷：`schedule_card` 的 `kind`、`external_id`、`start_at`、`end_at`、`description`、`participants`、`timezone`。`start_at` / `end_at` 不得含 `+` 或 `Z`。
3. Google 主日历：事件在或不在、是不是同一个事件 id、墙钟、说明、来宾是否为空、事件时区。

建议模型写出来的密度、缓冲句子不判措辞。列表还在、没有写入，建议失败时出现「暂时给不出建议」，列表题仍可 PASS。空档题要判的是不得把已占用时段说成有空，不是建议好不好。

### 1.6 目录行怎么展开

第 3 节每一行已经是冻结的题，不是让执行人再编一句。展开步骤：

1. `id`、`category` 用该行。
2. `utterances` 用该行的原句，多句按 ` / ` 拆开，按顺序发送。
3. 种子列为空则 `seed_events = []`。否则每条写成 `{key, title, start_at, end_at, description, location}`。`people=` 表示 `local_participants`，这种种子要先经产品确认创建一次（那一次不计分），再开新对话发送正式原句。`ui` 表示只在 Google 网页上建，不经过 Omni-Butler。
4. 卡片列给出 `type`、预填和缺席控件。其余用第 1.4 节。
5. 确认列给出 `expect.confirm`。
6. Google 列给出 `op` 和 `match`。`unchanged` 是没有被点名修改或删除的种子。
7. `pass` 用该行最后一格，再加上第 1.5 节的三处观察。

第 4 节的 20 条是展开后的全文。其余 80 条只按第 3 节和本节展开，句式不再改。

## 2. 配额

目标正好 **100**。下表之和是 100。

| id | category | 条数 | 这条在锁什么 |
| --- | --- | ---: | --- |
| CRS-001–010 | `create_meeting` | 10 | 会议确认卡；确认前不写；确认后墙钟与卡片一致 |
| CRS-011–020 | `create_block` | 10 | 时间块卡；有时长；无参与人无地点 |
| CRS-021–030 | `reschedule_keep` | 10 | 只改开始时保留时长和备注；预填 Google id |
| CRS-031–036 | `reschedule_change` | 6 | 用户明确改了时长或备注时才改 |
| CRS-037–044 | `cancel_by_id` | 8 | 预填 id；删除用 id；对不上不删 |
| CRS-045–052 | `list_schedule` | 8 | 只读列表卡；没有确认按钮；不写 Google |
| CRS-053–062 | `free_busy` | 10 | 先列出当天；已占用不得说成有空；推荐空档可以 |
| CRS-063–070 | `conflict_filter` | 8 | 重叠出冲突；建议空隙不重叠；端点相接不算冲突 |
| CRS-071–078 | `notes_description` | 8 | 备注写入 Google 说明；空备注可确认；改期不截短 |
| CRS-079–084 | `attendees` | 6 | 会议有参与人栏；时间块没有；Google 来宾始终为空 |
| CRS-085–090 | `error_paths` | 6 | 网络说连不上；授权说重新连接；读失败不编空闲 |
| CRS-091–094 | `wall_clock` | 4 | 无偏移墙钟；账户时区 Asia/Singapore；浏览器改成 UTC 也不挪点 |
| CRS-095–100 | `confirm_gate` | 6 | 未确认不写；下一句作废；过期；断开后仍不写 Google |
| 合计 |  | **100** |  |

## 3. 目录（100 条）

原句照发。种子时间是账户墙钟。`D1T15:00:00` 这种记号见第 1.3 节。

### 3.1 创建会议 CRS-001–010

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-001 | 明天三点开个CRS周会 | 无 | `meeting_create`，标题 CRS周会，`D1T15:00:00`–`D1T16:00:00`，说明空 | `confirm_create` | create，来宾空，说明 `""` | 确认前 Google 没有；确认后这一条的墙钟与卡片一致，起止无偏移 |
| CRS-002 | 后天上午十点开CRS站会 | 无 | 标题 CRS站会，`D2T10:00:00`–`D2T11:00:00` | `confirm_create` | create | 同上，标题是 CRS站会 |
| CRS-003 | 明天下午四点到五点开CRS评审 | 无 | `D1T16:00:00`–`D1T17:00:00`，不是默认的一小时以外的别的长度 | `confirm_create` | create 16:00–17:00 | 结束是五点，不是四点加一小时以外的值 |
| CRS-004 | 明天上午九点开CRS客户会，参与人小李，地点会议室A | 无 | 参与人含小李，地点会议室A，`D1T09:00:00`–`D1T10:00:00` | `confirm_create` | create，来宾空，地点会议室A | 卡片有参与人栏；Google 来宾为空 |
| CRS-005 | 大后天晚上八点开CRS同步会 | 无 | `D3T20:00:00`–`D3T21:00:00` | `confirm_create` | create | 墙钟是大后天 20:00–21:00 |
| CRS-006 | 大后天上午十一点开CRS规划会 | 无 | `D3T11:00:00`–`D3T12:00:00` | `confirm_create` | create | 墙钟是大后天 11:00–12:00 |
| CRS-007 | 明天中午十二点开CRS午饭会 | 无 | `D1T12:00:00`–`D1T13:00:00` | `confirm_create` | create | 墙钟是明天 12:00–13:00 |
| CRS-008 | 今晚八点开CRS夜会 | 无 | `D0T20:00:00`–`D0T21:00:00` | `confirm_create` | create | 墙钟是今天 20:00–21:00 |
| CRS-009 | 明天三点开CRS周会 | 无 | 会议草稿 `D1T15:00:00`–`D1T16:00:00` | `do_not_confirm` | none | 有确认卡和「未确认不会写入」；Google 没有 CRS周会 |
| CRS-010 | 帮我定明天上午十点的CRS周会 | 无 | 标题 CRS周会，`D1T10:00:00`–`D1T11:00:00` | `confirm_create` | create | 确认后 Google 墙钟与卡片一致 |

### 3.2 创建时间块 CRS-011–020

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-011 | 明天下午三点留两小时写CRS报告 | 无 | `block_create`，CRS报告，`D1T15:00:00`–`D1T17:00:00`，时长 120 | `confirm_create` | create，来宾空 | 无参与人栏、无地点栏；确认前不写 |
| CRS-012 | 后天上午十点留一小时做CRS复盘 | 无 | CRS复盘，`D2T10:00:00`–`D2T11:00:00`，时长 60 | `confirm_create` | create | 时间块，无参与人 |
| CRS-013 | 大后天早上九点到十一点专注CRS设计 | 无 | CRS设计，`D3T09:00:00`–`D3T11:00:00`，时长 120 | `confirm_create` | create | 结束是十一点 |
| CRS-014 | 明天晚上七点留半小时CRS运动 | 无 | CRS运动，`D1T19:00:00`–`D1T19:30:00`，时长 30 | `confirm_create` | create | 时长 30 分钟 |
| CRS-015 | 今天中午十二点留一小时CRS午饭 | 无 | CRS午饭，`D0T12:00:00`–`D0T13:00:00` | `confirm_create` | create | 日期是 D0 |
| CRS-016 | 后天晚上八点留两小时看CRS材料 | 无 | CRS材料，`D2T20:00:00`–`D2T22:00:00`，时长 120 | `confirm_create` | create | 无参与人无地点 |
| CRS-017 | 明天上午九点留两小时写CRS报告 | 无 | 时间块草稿，时长 120 | `do_not_confirm` | none | 有卡；Google 没有 |
| CRS-018 | 明天下午四点留一小时整理CRS邮件 | 无 | CRS邮件，`D1T16:00:00`–`D1T17:00:00` | `confirm_create` | create | 时长 60 |
| CRS-019 | 明天下午三点留两小时写CRS报告 | 无 | 先是时长 120 | `edit_then_confirm`：把时长改成 90 再确认 | create，结束 = 开始 + 90 分钟 | Google 结束等于改后的卡片，不是模型原来的两小时 |
| CRS-020 | 后天上午十点留三小时做CRS预算 | 无 | CRS预算，`D2T10:00:00`–`D2T13:00:00`，时长 180 | `confirm_create` | create | 时长 180 |

### 3.3 改期保留时长和备注 CRS-021–030

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-021 | 把CRS周会改到后天下午四点 | A CRS周会 `D1T10:00:00`–`D1T11:30:00` desc=带上大纲 | `meeting_update`，`external_id=seed:A`，`D2T16:00:00`–`D2T17:30:00`，说明仍是带上大纲 | `confirm_update` | update A | 同一个 id；时长仍是 90；说明未被收成「大纲」 |
| CRS-022 | 把CRS写报告改到明天晚上七点 | B CRS写报告 `D1T15:00:00`–`D1T17:00:00` desc=提纲 | `block_update`，无参与人无地点，`D1T19:00:00`–`D1T21:00:00`，说明提纲，`external_id=seed:B` | `confirm_update` | update B | 时长仍是 120 |
| CRS-023 | 把CRS评审提前到明天上午九点 | C CRS评审 `D1T15:00:00`–`D1T16:00:00` | `D1T09:00:00`–`D1T10:00:00`，`external_id=seed:C` | `confirm_update` | update C | 时长仍是 60 |
| CRS-024 | 把CRS同步推迟到后天上午十点 | D CRS同步 `D1T09:00:00`–`D1T10:00:00` desc=议程 | `D2T10:00:00`–`D2T11:00:00`，说明议程，`external_id=seed:D` | `confirm_update` | update D | 说明仍是议程 |
| CRS-025 | 把CRS客户会改到后天下午三点 | E CRS客户会 `D1T14:00:00`–`D1T15:30:00` loc=会议室A people=小李 | 参与人小李，地点会议室A，`D2T15:00:00`–`D2T16:30:00`，`external_id=seed:E` | `confirm_update` | update E，来宾仍空 | 时长 90；小李还在卡片上 |
| CRS-026 | 把CRS周会改到后天三点 | F CRS周会 `D1T10:00:00`–`D1T11:00:00` | 已预填 `external_id=seed:F` | `do_not_confirm` | none，F 不变 | 确认前 id、时间、说明都不变 |
| CRS-027 | 把CRS周会改到后天三点 | G 标题为 CRS周会（新加坡） `D1T10:00:00`–`D1T11:30:00` desc=大纲 | 标题变成 CRS周会（新加坡），`external_id=seed:G`，`D2T15:00:00`–`D2T16:30:00`，说明大纲 | `confirm_update` | update G | 软匹配只此一条；用的是完整标题和原时长 |
| CRS-028 | 把CRS周会改到后天三点 | H CRS周会 `D1T10:00:00`–`D1T11:00:00`；I CRS周会加长 `D1T13:00:00`–`D1T15:00:00` | `external_id=seed:H`，`D2T15:00:00`–`D2T16:00:00` | `confirm_update` | update H，I 不变 | 精确标题只改 H |
| CRS-029 | 把CRS设计挪到明天上午十点 | J CRS设计 `D2T09:00:00`–`D2T12:00:00` desc=保持 | `D1T10:00:00`–`D1T13:00:00`，说明保持，`external_id=seed:J` | `confirm_update` | update J | 时长仍是 180 |
| CRS-030 | 把CRS夜会改到明天晚上八点 | K CRS夜会 `D0T20:00:00`–`D0T21:00:00` desc=夜 | `D1T20:00:00`–`D1T21:00:00`，说明夜，`external_id=seed:K` | `confirm_update` | update K | 同一个 id |

### 3.4 改期并明确改时长或备注 CRS-031–036

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-031 | 把CRS周会改到后天四点，改成两小时 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` desc=大纲 | `D2T16:00:00`–`D2T18:00:00`，说明仍是大纲，`external_id=seed:A` | `confirm_update` | update A | 时长改成 120，说明不改 |
| CRS-032 | 把CRS周会改到后天四点，备注改成带上数据 | A CRS周会 `D1T10:00:00`–`D1T11:30:00` desc=带上大纲 | `D2T16:00:00`–`D2T17:30:00`，说明带上数据 | `confirm_update` | update A | 时长仍是 90，说明换成带上数据 |
| CRS-033 | 把CRS周会改到明天三点，改成两小时，备注改成新备注 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` desc=旧备注 | `D1T15:00:00`–`D1T17:00:00`，说明新备注 | `confirm_update` | update A | 时长和说明都按原句改 |
| CRS-034 | 把CRS写报告改成从明天上午十点开始，延长到三小时 | B CRS写报告 `D1T15:00:00`–`D1T17:00:00` desc=提纲 | `block_update`，`D1T10:00:00`–`D1T13:00:00`，说明提纲 | `confirm_update` | update B | 时长 180，说明仍是提纲，无参与人栏 |
| CRS-035 | 把CRS周会的备注改成取消茶歇 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` desc=大纲 | 起止仍是 `D1T15:00:00`–`D1T16:00:00`，说明取消茶歇 | `confirm_update` | update A | 时间不动，说明改了 |
| CRS-036 | 把CRS复盘改到明天上午九点，改成半小时 | A CRS复盘 `D2T10:00:00`–`D2T11:00:00` | `D1T09:00:00`–`D1T09:30:00` | `confirm_update` | update A | 时长 30 |

### 3.5 按 id 撤销 CRS-037–044

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-037 | 取消CRS周会 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` desc=大纲 loc=会议室A；B CRS别的名字 `D1T18:00:00`–`D1T19:00:00` | `cancel`，`external_id=seed:A`，时间锁定为 A，说明显示大纲且不可改 | `edit_then_confirm`：把标题改成 CRS别的名字再点确认撤销 | delete A，B 仍在 | 删的是 A 的 id，不是改后的标题 |
| CRS-038 | 取消CRS双份 | A、B 标题都是 CRS双份，分别 `D1T10:00:00`–`D1T11:00:00` 与 `D1T15:00:00`–`D1T16:00:00` | `external_id=absent` | `confirm_cancel` | none，A 和 B 都在 | 文案含「没有在 Google 日历上找到同标题的日程，未写入。」 |
| CRS-039 | 撤销CRS评审 | A CRS评审 `D1T11:00:00`–`D1T12:00:00` | `cancel`，`external_id=seed:A` | `do_not_confirm` | none，A 仍在 | 确认前不删除 |
| CRS-040 | 取消CRS同步 | A CRS同步 `D1T14:00:00`–`D1T15:00:00` desc=不要改说明 | 说明可见且不可改，`external_id=seed:A` | `confirm_cancel` | delete A | 没有另建一条；说明没有被改写后留下 |
| CRS-041 | 取消CRS周会 | A CRS周会 `D1T10:00:00`–`D1T11:00:00`；B CRS别的会 `D1T13:00:00`–`D1T14:00:00` | `external_id=seed:A` | `confirm_cancel` | delete A，B 仍在 | 只删 A |
| CRS-042 | 取消CRS外来 | A CRS外来，只在 Google 网页创建，`D1T16:00:00`–`D1T17:00:00`，标记 `ui` | `external_id=seed:A` | `confirm_cancel` | delete A | 没有本地来源也能按预填 id 删除 |
| CRS-043 | 取消CRS不存在 | 无 | `cancel`，`external_id=absent` | `confirm_cancel` | none | 文案含「没有在 Google 日历上找到同标题的日程，未写入。」 |
| CRS-044 | 删掉CRS周会 | A CRS周会 `D2T10:00:00`–`D2T11:00:00` | `external_id=seed:A` | `confirm_cancel` | delete A | 「删掉」同样是撤销卡，确认后该 id 消失 |

### 3.6 日程列表 CRS-045–052

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-045 | 明天有什么安排 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | `list`，含 CRS周会和 10:00，无确认按钮，`suggested_slots=absent` | `do_not_confirm` | none | 卡片在文字上面；不写 Google |
| CRS-046 | 明天有什么安排 | A CRS周会 `D1T10:00:00`–`D1T11:00:00`；B CRS评审 `D2T11:00:00`–`D2T12:00:00` | 列出 A，不列出 B | `do_not_confirm` | none | 点名了明天就只列明天 |
| CRS-047 | 后天有什么安排 | 无（D2 当天没有 CRS 事件） | `none` | `do_not_confirm` | none | 文案含「这一天没有日程。」没有确认按钮，没有「用这个时段」 |
| CRS-048 | 我的日程 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` | `list`，含 CRS周会，无确认按钮 | `do_not_confirm` | none | 建议句子不判措辞；出现「暂时给不出建议」且列表还在，仍通过 |
| CRS-049 | 今天有什么安排 | A CRS周会 `D0T15:00:00`–`D0T16:00:00` | `list`，含 CRS周会 | `do_not_confirm` | none | 不写 Google |
| CRS-050 | 查一下日程 | A CRS早会 `D0T15:00:00`–`D0T16:00:00`；B CRS午会 `D1T12:00:00`–`D1T13:00:00`；C CRS晚会 `D2T20:00:00`–`D2T21:00:00` | `list`，三条标题都在，无确认按钮 | `do_not_confirm` | none | 没点名某一天时七天窗口里这三条都出现 |
| CRS-051 | 帮我看看明天怎么安排 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | `list`，含 CRS周会，无确认按钮 | `do_not_confirm` | none | 不是创建卡；卡片下可以有建议，不另写 Google |
| CRS-052 | 明天有什么安排 | A CRS全天，`all_day`，日期 D1，标记 `ui` | `list`，该行含日期和「全天」 | `do_not_confirm` | none | 全天不写成 00:00 的钟点会议 |

### 3.7 空档是否说真话 CRS-053–062

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-053 | 明天有空吗 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | `list` 先列出 CRS周会；`suggested_slots=non_overlapping`；不得重叠 10:00–11:00 | `do_not_confirm` | none | 回复不得把 10 点说成有空、空闲或方便。推荐一段不重叠的空档可以通过 |
| CRS-054 | 明天上午十点有空吗 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | 先列出 CRS周会 | `do_not_confirm` | none | 不得声称上午十点有空 |
| CRS-055 | 明天有空吗 | 无 | 有推荐时段则为 `list`（可以没有事件行，只有「用这个时段」）；没有推荐则为 `none`。都没有确认按钮 | `do_not_confirm` | none | 文案不得出现「已按工作时间过滤」；不写 Google |
| CRS-056 | 明天有空吗 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | 先列表；点一条不重叠的「用这个时段」 | `click_slot_then_stop` | none，A 不变 | 换成一张会议创建草稿，时间等于所点时段；确认前 Google 不变 |
| CRS-057 | 明天有空吗 | A CRS满档 `D1T09:00:00`–`D1T18:00:00` | 列出 CRS满档；建议不得落入 09:00–18:00 | `do_not_confirm` | none | 若没有可留的时段，文案含「请以上面的列表为准，已有安排的时段不是空闲。」晚上的空档可以推荐 |
| CRS-058 | 明天下午三点有空吗 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` | 列出 CRS周会 | `do_not_confirm` | none | 不得把下午三点说成有空 |
| CRS-059 | 明天有空吗 | A CRS早会 `D1T09:00:00`–`D1T10:00:00` | 列出 CRS早会；若有建议，不重叠 09:00–10:00 | `do_not_confirm` | none | 推荐上午十点以后的空档可以通过 |
| CRS-060 | 明天有空吗 | 无 | `none`，没有「用这个时段」 | `do_not_confirm` | none | `fault=blackhole`。文案含「暂时读不到 Google 日历，不能建议空闲时段。」 |
| CRS-061 | 什么时候方便 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | 先看到 CRS周会；建议不重叠该段 | `do_not_confirm` | none | 不得把这一个小时说成有空或方便 |
| CRS-062 | 明天有空吗 / 先这样 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | 点一条不重叠时段后出现创建草稿 | `click_slot_then_stop`，再发「先这样」 | none | 草稿变未确认或按钮不可用；Google 仍只有种子 A |

### 3.8 冲突过滤 CRS-063–070

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-063 | 明天三点开CRS另一场 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` | `meeting_create` 带冲突；建议空隙不重叠 15:00–16:00 | `do_not_confirm` | none，A 不变 | 确认前不插入 CRS另一场 |
| CRS-064 | 明天三点开CRS另一场 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` | 点一条不重叠的建议空隙再确认 | `edit_then_confirm` | create 在空隙上，不在 15:00；A 不变 | 新事件墙钟等于所点空隙 |
| CRS-065 | 明天三点开CRS另一场 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` | 冲突仍在 | `confirm_create` 不改时间 | none | 文案含「这个时间有冲突，可以改用卡片上的空闲时段。」A 不变，没有 CRS另一场 |
| CRS-066 | 明天下午三点留两小时写CRS另一份 | A CRS写报告 `D1T15:00:00`–`D1T17:00:00` | `block_create`，无参与人，带冲突 | `do_not_confirm` | none | 时间块卡上也标出冲突，确认前不写 |
| CRS-067 | 把CRS甲改到明天下午三点 | A CRS甲 `D1T10:00:00`–`D1T11:00:00`；B CRS乙 `D1T15:00:00`–`D1T16:00:00` | `meeting_update` 带冲突，`external_id=seed:A` | `do_not_confirm` | none，A 与 B 都不变 | 确认前不改期 |
| CRS-068 | 把CRS甲改到明天下午三点 | 同 CRS-067 | 点一条不重叠空隙再确认修改 | `edit_then_confirm` | update A 到该空隙，B 不变 | A 的 Google id 不变 |
| CRS-069 | 明天四点到五点开CRS紧挨 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` | `meeting_create`，`D1T16:00:00`–`D1T17:00:00`，无冲突 | `confirm_create` | create 16:00–17:00，A 不变 | 端点相接不算冲突 |
| CRS-070 | 明天四点到五点开CRS重叠 | A CRS周会 `D1T15:00:00`–`D1T16:30:00` | 带冲突 | `do_not_confirm` | none | 重叠半小时不写入 |

### 3.9 备注 CRS-071–078

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-071 | 明天三点开CRS周会，备注带上大纲 | 无 | `meeting_create`，说明「带上大纲」，`D1T15:00:00`–`D1T16:00:00` | `confirm_create` | create，description 为带上大纲 | Google 事件说明里是这一句 |
| CRS-072 | 明天下午三点留两小时写CRS报告，备注先列提纲 | 无 | `block_create`，说明「先列提纲」，无参与人 | `confirm_create` | create，description 为先列提纲 | 时间块的备注同样进说明 |
| CRS-073 | 明天三点开CRS周会 | 无 | 说明为空 | `confirm_create` | description `""` | 空说明也能确认 |
| CRS-074 | 明天三点留一小时CRS运动 | 无 | `block_create`，说明为空，时长 60 | `confirm_create` | description `""` | 空说明的时间块也能确认 |
| CRS-075 | 取消CRS周会 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` desc=带上大纲 | `cancel`，说明显示带上大纲且不可改 | `confirm_cancel` | delete A | 确认不会把说明改成别的句子再留下一条 |
| CRS-076 | 把CRS周会改到后天四点 | A CRS周会 `D1T10:00:00`–`D1T11:30:00` desc=带上大纲 | 说明全文仍是「带上大纲」，时长 90，`D2T16:00:00`–`D2T17:30:00` | `confirm_update` | update，description 仍是带上大纲 | 不得变成「大纲」，不得变成 60 分钟 |
| CRS-077 | 把CRS周会的备注改成带上完整大纲 | A CRS周会 `D1T15:00:00`–`D1T16:00:00` desc=旧 | 起止不变，说明为带上完整大纲 | `confirm_update` | description 为带上完整大纲 | 只有备注变了 |
| CRS-078 | 明天三点开CRS周会，备注临时 | 无 | 说明先是「临时」 | `edit_then_confirm`：清空说明再确认 | description `""` | 以确认时卡片上的说明为准 |

### 3.10 参与人 CRS-079–084

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-079 | 明天上午九点开CRS客户会，参与人小李 | 无 | `meeting_create`，参与人栏有小李 | `confirm_create` | 来宾为空 | 页面有参与人；Google 没有来宾 |
| CRS-080 | 明天三点开CRS周会，参与人小李、小王 | 无 | 参与人含小李和小王 | `confirm_create` | 来宾为空 | 两个名字都在卡片上 |
| CRS-081 | 明天下午三点留两小时写CRS报告 | 无 | `block_create`，`absent_controls` 含参与人和地点 | `confirm_create` | 来宾为空 | 时间块不渲染参与人 |
| CRS-082 | 明天下午三点到五点留给自己写CRS报告 | 无 | `block_create`，`D1T15:00:00`–`D1T17:00:00`，无参与人无地点 | `confirm_create` | 来宾为空 | 留给自己是时间块 |
| CRS-083 | 把CRS客户会改到后天三点 | E：先不计分地确认创建 CRS客户会，参与人小李，`D1T14:00:00`–`D1T15:00:00`，再开新对话 | `meeting_update`，参与人小李，时长 60，`external_id=seed:E`，`D2T15:00:00`–`D2T16:00:00` | `confirm_update` | 来宾仍空 | 本地参与人出现在改期卡上 |
| CRS-084 | 把CRS写报告改到明天晚上七点 | B CRS写报告 `D1T15:00:00`–`D1T17:00:00`，无地点、无来宾，标记 `ui` | `block_update`，无参与人无地点，时长 120，`D1T19:00:00`–`D1T21:00:00` | `confirm_update` | update B，来宾空 | 没有参与人、没有地点的改期用时间块卡 |

### 3.11 错误路径 CRS-085–090

这六条的 `fault` 不是产品功能，是执行人制造的故障。造法见第 5.3 节。恢复连接和恢复网络不计分，也不另编号。

| id | 原句 | 故障 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-085 | 明天三点开CRS周会 | `blackhole`，授权仍在 | `none` | `do_not_confirm` | none | 文案含「暂时连不上 Google 日历，请稍后再试。」不得含「重新连接」 |
| CRS-086 | 明天三点开CRS周会 | `revoke`，网络正常 | `none` | `do_not_confirm` | none | 文案含「请重新连接」。不得含「暂时连不上」 |
| CRS-087 | 明天有什么安排 | 先在网络正常时确认创建一条 CRS本地（不计分），再 `blackhole` | 若有本地行可以列出 CRS本地 | `do_not_confirm` | none | 文案含「暂时读不到 Google 日历」。不得把没读到的下午说成有空 |
| CRS-088 | 明天有空吗 | `blackhole` | `none`，没有「用这个时段」 | `do_not_confirm` | none | 文案含「暂时读不到 Google 日历，不能建议空闲时段。」 |
| CRS-089 | 明天三点开CRS周会 | 先在网络正常时看到会议草稿，再 `blackhole`，然后点确认创建 | 草稿已经在 | `confirm_create` | none | 文案是「暂时连不上 Google 日历，请稍后再试。」或「暂时读不到 Google 日历，未写入。」Google 没有 CRS周会 |
| CRS-090 | 明天三点开CRS周会 | 先看到草稿，再 `revoke`，然后点确认 | 草稿已经在 | `confirm_create` | none | 文案含「请重新连接」。Google 没有这条 |

CRS-086 之后先重新连接，侧栏重新出现账户邮箱，再做 CRS-087。CRS-089 之后恢复出网，再做 CRS-090。CRS-090 之后再次连接，再做 CRS-091。恢复失败则整次记「未跑」，不报 `passed/total`。

### 3.12 墙钟和时区 CRS-091–094

这四条把浏览器时区设为 UTC，Google 账户时区保持 `Asia/Singapore`。看 Google 事件时，日历时区用账户时区。

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-091 | 明天三点开CRS时区会 | 无 | 载荷 `start_at` 为 `D1T15:00:00`，无 `+` 无 `Z`；输入框显示 15:00 | `confirm_create` | 新加坡墙钟 15:00–16:00 | 不是 07:00，也不是 23:00 |
| CRS-092 | 明天三点开CRS时区会 | 无 | 载荷 `timezone` 为 `Asia/Singapore` | `confirm_create` | 事件时区为 Asia/Singapore，墙钟 15:00 | 时区 id 来自账户，不来自浏览器 |
| CRS-093 | 明天有什么安排 | A CRS周会，新加坡 `D1T15:00:00`–`D1T16:00:00` | 列表行显示 15:00 | `do_not_confirm` | none | 浏览器在 UTC 时，行上仍是 15:00，不是 07:00 或 23:00 |
| CRS-094 | 明天三点开CRS墙钟会 | 无 | 载荷起止无偏移，输入框 15:00–16:00 | `do_not_confirm` | none | 未确认不写；墙钟已经是新加坡的三点 |

### 3.13 确认关口 CRS-095–100

| id | 原句 | 种子 | 卡片 | 确认 | Google | 通过 |
| --- | --- | --- | --- | --- | --- | --- |
| CRS-095 | 明天三点开CRS周会 / 今天天气怎么样 | 无 | 第一句是会议草稿；第二句后状态为未确认 | `do_not_confirm` | none | 刷新后按钮仍不可用；Google 没有 CRS周会 |
| CRS-096 | 明天三点开CRS过期会 | 无 | 等到状态「已过期」 | `do_not_confirm` | none | 确认按钮不可用；这是唯一需要等待的题，草稿有效期 30 分钟 |
| CRS-097 | 取消CRS周会 / 今天天气怎么样 | A CRS周会 `D1T10:00:00`–`D1T11:00:00` | 第一句是撤销草稿且 `external_id=seed:A`；第二句后作废 | `do_not_confirm` | none，A 仍在 | 未确认不删除 |
| CRS-098 | 明天有空吗 | A CRS周会 `D1T09:00:00`–`D1T10:00:00` | 点一条不重叠的「用这个时段」 | `click_slot_then_stop` | none | 只有草稿，没有新事件 |
| CRS-099 | 明天三点开CRS门禁会 | 无 | 会议草稿 | `confirm_after_seen_empty` | 点之前 none；点 `确认创建` 之后 create | 点之前文案含「未确认不会写入」；点之后 Google 墙钟与卡片一致 |
| CRS-100 | 明天三点开CRS离线会 | 无。`connection=disconnected`（侧栏先断开） | 仍有确认卡 | `confirm_create` | none | 断开后确认也不往 Google 主日历写入 CRS离线会 |

CRS-100 做完后重新连接，以便收尾清理。

## 4. 全文样例（20 条）

下面 20 条是第 3 节对应行的完整对象。其余 80 条按第 1.6 节展开，字段集合与这 20 条相同。

### CRS-001

```yaml
id: CRS-001
category: create_meeting
utterances: ["明天三点开个CRS周会"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: meeting_create
    phase: draft
    prefill:
      title: CRS周会
      start_at: D1T15:00:00
      end_at: D1T16:00:00
      participants: []
      location: ""
      description: ""
      external_id: absent
      kind: meeting
    absent_controls: [时长]
    suggested_slots: absent
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match:
      title: CRS周会
      start_at: D1T15:00:00
      end_at: D1T16:00:00
      description: ""
      attendees: []
      event_id: new
    unchanged: []
  copy_must: ["未确认不会写入"]
  copy_must_not: ["+", "Z"]
pass: 确认前主日历没有 CRS周会。卡片是会议草稿，墙钟为明天 15:00–16:00 且字符串无偏移。点确认创建后，Google 新增这一条，来宾为空，说明为空，墙钟与卡片一致。
```

### CRS-004

```yaml
id: CRS-004
category: create_meeting
utterances: ["明天上午九点开CRS客户会，参与人小李，地点会议室A"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: meeting_create
    phase: draft
    prefill:
      title: CRS客户会
      start_at: D1T09:00:00
      end_at: D1T10:00:00
      participants: [小李]
      location: 会议室A
      description: ""
      external_id: absent
      kind: meeting
    absent_controls: [时长]
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match: {title: CRS客户会, start_at: D1T09:00:00, end_at: D1T10:00:00, location: 会议室A, attendees: [], event_id: new}
  copy_must: ["未确认不会写入"]
pass: 卡片有参与人栏且含小李，有地点会议室A。确认前不写。确认后 Google 有这条，来宾列表为空。
```

### CRS-011

```yaml
id: CRS-011
category: create_block
utterances: ["明天下午三点留两小时写CRS报告"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: block_create
    phase: draft
    prefill:
      title: CRS报告
      start_at: D1T15:00:00
      end_at: D1T17:00:00
      duration_minutes: 120
      description: ""
      kind: block
    absent_controls: [参与人, 地点, 结束]
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match: {title: CRS报告, start_at: D1T15:00:00, end_at: D1T17:00:00, description: "", attendees: [], event_id: new}
  copy_must: ["未确认不会写入"]
pass: 时间块卡有标题、开始、时长、说明。没有参与人，没有地点。确认前不写。确认后 Google 为明天 15:00–17:00，来宾为空。
```

### CRS-021

```yaml
id: CRS-021
category: reschedule_keep
utterances: ["把CRS周会改到后天下午四点"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T10:00:00, end_at: D1T11:30:00, description: 带上大纲, location: ""}
expect:
  card:
    type: meeting_update
    phase: draft
    prefill:
      title: CRS周会
      start_at: D2T16:00:00
      end_at: D2T17:30:00
      duration_minutes: 90
      description: 带上大纲
      external_id: seed:A
      kind: meeting
  before_confirm_google: none
  confirm: confirm_update
  google:
    op: update
    match: {event_id: seed:A, title: CRS周会, start_at: D2T16:00:00, end_at: D2T17:30:00, description: 带上大纲, attendees: []}
    unchanged: []
  copy_must: ["未确认不会写入"]
pass: 卡片预填 A 的 Google id、原说明全文和 90 分钟时长，新开始是后天 16:00。确认前 A 仍在 10:00。确认后还是 A 这个 id，说明仍是「带上大纲」，不是「大纲」。
```

### CRS-027

```yaml
id: CRS-027
category: reschedule_keep
utterances: ["把CRS周会改到后天三点"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: G, title: CRS周会（新加坡）, start_at: D1T10:00:00, end_at: D1T11:30:00, description: 大纲, location: ""}
expect:
  card:
    type: meeting_update
    phase: draft
    prefill:
      title: CRS周会（新加坡）
      start_at: D2T15:00:00
      end_at: D2T16:30:00
      duration_minutes: 90
      description: 大纲
      external_id: seed:G
  before_confirm_google: none
  confirm: confirm_update
  google:
    op: update
    match: {event_id: seed:G, title: CRS周会（新加坡）, start_at: D2T15:00:00, end_at: D2T16:30:00, description: 大纲}
pass: 原句里的标题是前缀。只对上这一条时，卡片改用 Google 上的完整标题和该条 id，时长仍是 90 分钟。
```

### CRS-031

```yaml
id: CRS-031
category: reschedule_change
utterances: ["把CRS周会改到后天四点，改成两小时"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T10:00:00, end_at: D1T11:00:00, description: 大纲, location: ""}
expect:
  card:
    type: meeting_update
    phase: draft
    prefill:
      start_at: D2T16:00:00
      end_at: D2T18:00:00
      duration_minutes: 120
      description: 大纲
      external_id: seed:A
  before_confirm_google: none
  confirm: confirm_update
  google:
    op: update
    match: {event_id: seed:A, start_at: D2T16:00:00, end_at: D2T18:00:00, description: 大纲}
pass: 原句要改时长，所以结束是 18:00，不再沿用原来的 60 分钟。说明仍是大纲。
```

### CRS-037

```yaml
id: CRS-037
category: cancel_by_id
utterances: ["取消CRS周会"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T10:00:00, end_at: D1T11:00:00, description: 大纲, location: 会议室A}
    - {key: B, title: CRS别的名字, start_at: D1T18:00:00, end_at: D1T19:00:00, description: "", location: ""}
expect:
  card:
    type: cancel
    phase: draft
    prefill:
      title: CRS周会
      start_at: D1T10:00:00
      end_at: D1T11:00:00
      description: 大纲
      location: 会议室A
      external_id: seed:A
    absent_controls: []
  before_confirm_google: none
  confirm: edit_then_confirm
  google:
    op: delete
    match: {event_id: seed:A}
    unchanged: [B]
  notes: 点确认前把标题改成 CRS别的名字。时间框和说明框保持不可编辑。
pass: 载荷里的 external_id 是 A。把标题改成和 B 一样再确认，删除的仍是 A，B 还在。确认前两条都在。
```

### CRS-038

```yaml
id: CRS-038
category: cancel_by_id
utterances: ["取消CRS双份"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS双份, start_at: D1T10:00:00, end_at: D1T11:00:00, description: "", location: ""}
    - {key: B, title: CRS双份, start_at: D1T15:00:00, end_at: D1T16:00:00, description: "", location: ""}
expect:
  card:
    type: cancel
    phase: draft
    prefill: {external_id: absent}
  before_confirm_google: none
  confirm: confirm_cancel
  google: {op: none, unchanged: [A, B]}
  copy_must: ["没有在 Google 日历上找到同标题的日程，未写入。"]
pass: 两条同名都对得上时，卡片不带唯一的 Google id。点确认撤销后两条都还在。
```

### CRS-045

```yaml
id: CRS-045
category: list_schedule
utterances: ["明天有什么安排"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T10:00:00, end_at: D1T11:00:00, description: "", location: ""}
expect:
  card:
    type: list
    phase: info
    must_list_titles: [CRS周会]
    suggested_slots: absent
    absent_controls: [确认创建, 确认修改, 确认撤销]
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none, unchanged: [A]}
pass: 先看到列表卡，行上有 CRS周会和 10:00。没有确认按钮。Google 没有新事件，A 的时间不变。建议句子不判措辞。
```

### CRS-047

```yaml
id: CRS-047
category: list_schedule
utterances: ["后天有什么安排"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card: {type: none, phase: absent, suggested_slots: absent}
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none}
  copy_must: ["这一天没有日程。"]
  copy_must_not: ["用这个时段"]
pass: 没有列表卡，没有确认按钮，没有空档按钮。文字里有「这一天没有日程。」Google 没有新事件。
```

### CRS-053

```yaml
id: CRS-053
category: free_busy
utterances: ["明天有空吗"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T10:00:00, end_at: D1T11:00:00, description: "", location: ""}
expect:
  card:
    type: list
    phase: info
    must_list_titles: [CRS周会]
    suggested_slots: non_overlapping
    must_not_overlap: ["D1T10:00:00/D1T11:00:00"]
    absent_controls: [确认创建, 确认修改, 确认撤销]
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none, unchanged: [A]}
  copy_must_not: ["十点有空", "10:00有空", "10点是空的", "上午十点方便"]
pass: 回复先列出 CRS周会，再可能给出空档。任何建议时段不得与 10:00–11:00 相交。正文不得把这一小时说成有空、空闲或方便。有一段不重叠的推荐可以通过。不写 Google。
notes: 重叠过滤后若不再声称有空，短句「请以上面的列表为准，已有安排的时段不是空闲。」算通过。
```

### CRS-056

```yaml
id: CRS-056
category: free_busy
utterances: ["明天有空吗"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T10:00:00, end_at: D1T11:00:00, description: "", location: ""}
expect:
  card:
    type: meeting_create
    phase: draft
    suggested_slots: absent
  before_confirm_google: none
  confirm: click_slot_then_stop
  google: {op: none, unchanged: [A]}
pass: 先有列表。点一条不与 10:00–11:00 相交的「用这个时段」后，卡片变成会议创建草稿，起止等于所点时段。不点确认。Google 仍只有 A。
```

### CRS-063

```yaml
id: CRS-063
category: conflict_filter
utterances: ["明天三点开CRS另一场"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup:
  connection: connected
  fault: none
  cleanup_prefix: CRS
  seed_events:
    - {key: A, title: CRS周会, start_at: D1T15:00:00, end_at: D1T16:00:00, description: "", location: ""}
expect:
  card:
    type: meeting_create
    phase: draft
    prefill: {title: CRS另一场, start_at: D1T15:00:00, end_at: D1T16:00:00}
    suggested_slots: non_overlapping
    must_not_overlap: ["D1T15:00:00/D1T16:00:00"]
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none, unchanged: [A]}
pass: 会议草稿标出与 CRS周会的冲突。建议空隙不与 15:00–16:00 相交。不点确认，Google 没有 CRS另一场。
```

### CRS-071

```yaml
id: CRS-071
category: notes_description
utterances: ["明天三点开CRS周会，备注带上大纲"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: meeting_create
    phase: draft
    prefill: {title: CRS周会, start_at: D1T15:00:00, end_at: D1T16:00:00, description: 带上大纲}
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match: {title: CRS周会, description: 带上大纲, start_at: D1T15:00:00, end_at: D1T16:00:00, attendees: []}
pass: 卡片说明为「带上大纲」。确认后 Google 事件说明是同一句。确认前没有这条事件。
```

### CRS-079

```yaml
id: CRS-079
category: attendees
utterances: ["明天上午九点开CRS客户会，参与人小李"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: meeting_create
    phase: draft
    prefill: {title: CRS客户会, participants: [小李], start_at: D1T09:00:00, end_at: D1T10:00:00}
    absent_controls: [时长]
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match: {title: CRS客户会, attendees: []}
pass: 页面参与人栏有小李。确认后的 Google 事件来宾为空。
```

### CRS-081

```yaml
id: CRS-081
category: attendees
utterances: ["明天下午三点留两小时写CRS报告"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: block_create
    phase: draft
    prefill: {title: CRS报告, duration_minutes: 120, start_at: D1T15:00:00, end_at: D1T17:00:00}
    absent_controls: [参与人, 地点, 结束]
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match: {attendees: [], start_at: D1T15:00:00, end_at: D1T17:00:00}
pass: 页面没有参与人二字的输入框，也没有地点。确认后 Google 来宾为空。
```

### CRS-085

```yaml
id: CRS-085
category: error_paths
utterances: ["明天三点开CRS周会"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: blackhole, seed_events: [], cleanup_prefix: CRS}
expect:
  card: {type: none, phase: absent}
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none}
  copy_must: ["暂时连不上 Google 日历，请稍后再试。"]
  copy_must_not: ["重新连接"]
pass: 出网被断开但授权还在时，没有草稿卡，不写 Google，文案是暂时连不上，不是请重新连接。
```

### CRS-086

```yaml
id: CRS-086
category: error_paths
utterances: ["明天三点开CRS周会"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: revoke, seed_events: [], cleanup_prefix: CRS}
expect:
  card: {type: none, phase: absent}
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none}
  copy_must: ["请重新连接"]
  copy_must_not: ["暂时连不上"]
pass: 在 Google 账户里撤销日历授权、网络仍然正常时，文案要求重新连接，不把这次说成暂时连不上，也不写入。
notes: 本条之后重新连接，侧栏出现邮箱，再继续 CRS-087。
```

### CRS-091

```yaml
id: CRS-091
category: wall_clock
utterances: ["明天三点开CRS时区会"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0, browser_zone: UTC}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card:
    type: meeting_create
    phase: draft
    prefill: {title: CRS时区会, start_at: D1T15:00:00, end_at: D1T16:00:00}
  before_confirm_google: none
  confirm: confirm_create
  google:
    op: create
    match: {start_at: D1T15:00:00, end_at: D1T16:00:00, attendees: []}
  copy_must_not: ["+08", "Z"]
pass: 浏览器时区是 UTC。载荷起止是无偏移的明天 15:00 和 16:00。输入框显示 15:00。Google 在 Asia/Singapore 下也是 15:00–16:00，不是 07:00，也不是 23:00。
```

### CRS-095

```yaml
id: CRS-095
category: confirm_gate
utterances: ["明天三点开CRS周会", "今天天气怎么样"]
clock: {zone: Asia/Singapore, run_window: "09:00-11:00", today: D0}
setup: {connection: connected, fault: none, seed_events: [], cleanup_prefix: CRS}
expect:
  card: {type: meeting_create, phase: draft}
  before_confirm_google: none
  confirm: do_not_confirm
  google: {op: none}
  copy_must: ["未确认不会写入"]
pass: 第一句给出会议草稿且 Google 没有事件。第二句后该卡状态为未确认，按钮不可用。刷新后仍不可用。主日历没有 CRS周会。
```

## 5. 执行办法

### 5.1 什么时候跑

在准备把 `develop` 提到 `release` 之前跑完一次。不进每次提交，不进 `.github/workflows/eval.yml`，也不进 `python scripts/eval.py`。一次跑完 100 条才有分数。中途恢复授权失败、或开跑时账户时区不是 `Asia/Singapore`，整次记「未跑」，不发表 `passed/total`。

### 5.2 环境

- 专用 Google 账户，只给这套题用。主日历里不要放真实日程。账户时区先在 Google 日历里设成 `Asia/Singapore`，跑的过程中不改。
- 桌面浏览器已登录 Omni-Butler。侧栏显示该账户邮箱，按钮是「断开」，表示已经连接。CRS-100 才断开。
- 服务端环境有 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET`、`GOOGLE_OAUTH_REDIRECT_URI`、`CALENDAR_TOKEN_KEY`。这些值留在运行环境，不写入仓库、题单或记录表。该 Google 账户已列为 OAuth 客户端的测试用户。
- 后端进程能访问 `https://www.googleapis.com` 和 `https://oauth2.googleapis.com`。机器如果走出口代理，在启动后端之前设好进程的代理。代理中断按第 5.3 节的 `blackhole` 处理。
- 开跑时钟在账户当地 09:00–11:00，以便「今天中午」和「今晚八点」仍在当下之后。记下这一天的公历日期当作 `D0`。
- CRS-091–094 把浏览器时区改为 UTC，做完改回。其余题浏览器时区可以是 `Asia/Singapore`。
- 每条用新对话，或至少没有残留的待确认卡。上一条的 Google 事件已经按第 5.4 节删掉。

### 5.3 故障怎么造

只用于 CRS-085–090。

- `blackhole`：授权保持有效。让后端访问 `www.googleapis.com` 和 `oauth2.googleapis.com` 超时或连不上（停掉出口代理，或把这两个主机指到无效地址）。期望文案走「暂时连不上」或「暂时读不到」，不走「重新连接」。
- `revoke`：网络保持正常。在 Google 账户的安全设置里撤销 Omni-Butler 的日历权限。期望文案含「请重新连接」。
- 造完按第 3.11 节恢复。恢复是夹具，不是第 101 条。

### 5.4 清理

- 种子和确认产生的事件，标题都以 `CRS` 开头。建种子时记下 Google 事件 id。
- 每条结束，按记下来的 id 删除。删除用 id。不用标题搜索去删一个不以 `CRS` 开头的事件。
- 全部跑完后再扫一遍主日历，从 `D0` 到 `D3` 之后两天，只删标题以 `CRS` 开头的事件。
- 某条删不掉：该条若因此留下脏数据，先清掉再跑下一条。清不掉就中止，整次记「未跑」。
- 不以 `CRS` 开头的事件不删。

`people=` 的种子（CRS-025、CRS-083）先用产品自己的确认卡创建，让参与人留在本地，再开新对话发送正式原句。那一次创建不计分。`ui` 的种子只在 Google 网页上建。

### 5.5 记录和分数

记录表每条一行：

```text
id,result,note
CRS-001,PASS,
CRS-002,FAIL,卡片结束时间是 15:30
```

`result` 只有 `PASS` 或 `FAIL`。没跑记 `FAIL`，并在 `note` 写「未执行」。全部 100 条都有了 PASS 或 FAIL，分数才是 `passed/100`，例如 `87/100`。分子是 PASS 的个数。

发布记录里附这张表和 `D0`、账户时区、浏览器时区。不附令牌、客户端密钥或授权码。

## 6. 不在本文

- pytest、以及任何要改产品代码才能锁上的硬门禁。那是另一份计划，本文不写。
- CI 里的 agent 评分、`docs/eval.md` 的评测套件、题集扩写、用模型给回复打分。本套的指标是固定脚本的通过条数，不是评分。
- 同一意图的措辞扫荡。每条只有目录里的那一句。不把「开个会」「约个会」「订个会」再各做一条。
- 实现 Playwright 跑器、改卡片、改提示词、改 Google 写入。本文发布的是题，不是功能。

## 7. 题单文件

[`fixtures/calendar-release-scripts-100.csv`](fixtures/calendar-release-scripts-100.csv) 与第 3 节同一套 100 条，列就是第 1.1 节的扁平字段。人和 Playwright 都以本文第 3 节为准；CSV 方便照着填记录，不另定规则。两处不一致时，改 CSV 去跟第 3 节，不改第 3 节去跟 CSV。
