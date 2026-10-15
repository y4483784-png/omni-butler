# 聊天日程四件事的修复计划

本文只定计划，不改代码。对应 PR 只含本文。依据是 `develop` @ `87cf941` 的实机：Google 日历已连接，账户时区 `Asia/Singapore`，当天 `2026-10-15`。

已经接上的日历不用再做一遍：不另设产品时区；「现在」只来自该账户的 IANA 时区；写入仍只走确认卡片；不做双向同步；列表、侧栏、冲突继续当场 `events.list`。下面四件事是连接之后仍然会错的。

不改这些：

- 卡片上的 `start_at` / `end_at` 继续是无偏移墙钟。确认时由 `GoogleCalendarProvider._insert_google_event`（`calendar_google.py` 771–776）另附账户 `timeZone`。给字符串加上 `+08:00` 会让 `ScheduleCard.toLocalInput`（`ScheduleCard.tsx` 17–21）按浏览器时区换算，墙钟数字会变。
- 列表、侧栏、冲突仍当场 `events.list`，读完不落库。不把 Google 上后来改过的标题或时间写回本地行。
- 冲突空隙仍是每个空隙只给一段所请求的时长（`free_gaps`，`calendar_local.py` 24–53）。第 4 节只改「有空吗」的回答，不改 409 的建议。
- 不缓存时区。用户在 Google 里改了时区，下一次仍读设置。
- 不给抽槽加规则兜底：不用正则覆盖模型给出的相对日期、远处日期、标题或参与人。模型没返回 JSON 时，现有的空草稿补全保持不动。

## 1. 创建抽槽时模型不知道「今天」

句子：「明天下午三点开个测试会议 Grok测试」。卡片开始时间 `2023-04-05T15:00:00`，标题「测试会议」，参与人 `["Grok测试"]`，起止都没有偏移。

### 原因

`execute_calendar` 已经算出账户墙钟（`calendar_tool.py` 353–354 → `calendar_now`，`calendar.py` 564–573），并传给 `extract_event`。提示里没有这一天，也没有 IANA 时区。

`extract_event`（`calendar.py` 122–171）把句子交给 `complete_json`。系统提示（132–138）只要求无偏移的 ISO。用户消息（142–145）只有最近对话和当前句子。模型自己编了 `2023-04-05`。`_supplement_draft_from_message`（255–256）只在 `start_at` 为空时才用正则，所以这份日期会留在卡片上。

标题「测试会议」和参与人「Grok测试」也是模型返回的。正则不会改掉非空字段（251–254、260–261）。结束时间仍由 `_finalize_prefill`（176–180）在模型留空时补成开始后 1 小时。`_parse_dt`（191–196）去掉时区，`_iso`（`calendar_tool.py` 57–60）按 naive 输出。无偏移是墙钟约定，不是这节要改的缺陷。

### 改法

把「今天」写进抽槽提示，让模型自己解释「明天 / 后天」。相信模型。确认卡片是用户把关的地方，用户可以在确认前改卡片。

- 已连接：提示写上 `calendar_now` 的公历日期，以及 `timezone_name()` 的 IANA id（实机是 `Asia/Singapore`）。说明「明天」「后天」相对这一天，`start_at` / `end_at` 仍输出无偏移 ISO。
- 未连接：只写 `calendar_now` 的日期，不另造一个产品时区。
- 模型返回的日期、标题、参与人原样进草稿。不因为日期太远、标题不像、或参与人没有「参与人 / 和」而改写。
- 不新增正则去覆盖模型。`后天` 与「下午」的前缀问题不在这刀修。
- 模型没给结束时间时，结束仍是开始后 1 小时。卡片起止仍然没有偏移。

### 测试

放在 `backend/tests/test_calendar_card.py`。`now` 固定为 `2026-10-15`，已连接时区为 `Asia/Singapore`：

- 断言送进 `complete_json` 的提示含 `2026-10-15` 和 `Asia/Singapore`。未连接的提示有日期，没有编造的 IANA id。
- 模型返回 `2026-10-16T15:00:00`、标题「测试会议」、参与人 `["Grok测试"]` 时，卡片就是这些值。结束为 `2026-10-16T16:00:00`。字符串不含 `+` 或 `Z`。
- 模型若仍返回 `2023-04-05T15:00:00`，草稿保持该值，交给用户在卡片上改。测试不要求代码把它改成明天。
- 不新增「丢掉远处日期」「标题必须取开个后面一段」「没有标记就清空参与人」这类断言。

## 2. 时区请求的网络错误被说成授权失效

### 原因

`CalendarTokenError` 的默认文案是「Google 日历授权已失效，请重新连接。」（`calendar_secrets.py` 65–66）。

时区读取在 `_fetch_timezone_value`（`calendar_google.py` 905–934）：

- 902 行是 `_access_token` 里没有连接行，这是真的未连接，保持 `CalendarTokenError`。
- 913 行先 `refresh_access_token`。刷新时的网络错误和任何非 200（`calendar_secrets.py` 226–233）也抛 `CalendarTokenError`。访问令牌到期时，代理错误到不了 923 行，用户看到的仍是重新连接。
- 921–923 行：时区 GET 的 `httpx.HTTPError` 抛 `CalendarTokenError`。
- 926–930 行：除 200 和 404 以外的状态码同样抛，包括 500/502。`test_missing_timezone_is_etc_gmt_not_process_zone`（`test_calendar_google.py` 1165–1168）把 500 锁成了 `CalendarTokenError`。
- 404、空值、非法 IANA 仍用 `Etc/GMT`（924–925、932–933、151–156）。这不变。

聊天里这个异常没有被日历工具接住。`calendar_now` 在 list、suggest、创建之前就调用（`calendar_tool.py` 208、223、353）。`gateway.invoke`（`gateway.py` 231–234）把它收成「工具治理拒绝「calendar」：执行异常：CalendarTokenError: …授权已失效…」。确认接口则把 `CalendarTokenError` 映射成 401（`api/calendar.py` 208–209、262–263、284–285）。

列表自己的 `try`（`calendar_google.py` 181–184）包住了 `timezone_name()`，所以列表失败会退回本地行。聊天在进列表之前就因为 `calendar_now` 失败了，走不到这层。

### 改法

网络失败和授权失败分开。

- 新增 `CalendarTransientError`，不要做成 `CalendarTokenError` 的子类。文案：「暂时连不上 Google 日历，请稍后再试。」
- 时区 GET：传输错误以及 502/503/504 先重试一次。仍失败则抛瞬时错误。401/403 仍是 `CalendarTokenError`。404 仍是 `Etc/GMT`。500 改为瞬时错误，并改掉上面那个断言。
- 同一次读取里的令牌刷新：传输错误和 5xx 同样是瞬时错误。400/401/403 仍是授权失效。授权码交换那条路径不动，它已经是 502。
- 聊天：`calendar_now` 的瞬时错误由工具返回上面那句，不出卡片，也不要变成「工具治理拒绝」。不要改用进程时区或浏览器时区来凑一个「现在」。
- 列表：把瞬时错误并进现有的读取失败分支，仍返回本地行和「暂时读不到 Google 日历…」。
- suggest：仍用「暂时读不到 Google 日历，不能建议空闲时段。」不出建议卡片。
- 确认：502，不写 Google，不插本地行。

### 测试

放在 `backend/tests/test_calendar_google.py`：

- 时区 GET 第一次 `httpx.HTTPError`、第二次 200：得到原时区，不抛 `CalendarTokenError`。
- 两次都失败，或状态 500：抛 `CalendarTransientError`，文案不含「重新连接」。状态接口仍是 `connected: true`，`timezone` 为 `""`。
- 状态 401：仍是 `CalendarTokenError`。404 仍是 `Etc/GMT`。
- 刷新接口网络失败：同样是瞬时错误，不改已存令牌。刷新 400：仍是 `CalendarTokenError`，令牌不改。
- `execute_calendar` 在时区 GET 失败时，`direct_answer` 是瞬时文案，`schedule_card` 为 `None`。
- 已连接的确认 POST 在时区 GET 失败时为 502，本地无新行。

## 3. 改期和撤销不要再查本地行

### 原因

实机回答是短句「没有找到对应日程。」（`_NOT_FOUND`，`calendar_tool.py` 29、386）。聊天在抽槽之后调用 `find_event_candidates`（375–379，`calendar.py` 624–651）。已连接时它只查本地表（`calendar_google.py` 443–464），不读用户在侧栏看到的那场 `events.list`。窗口和标题对不上就没有卡片。

这不是要再改进查找。本地行不是改期和撤销的入口。

### 改法

已连接用户的改期、撤销：用第 1 节的抽槽结果预填一张确认卡片，确认之后才写 Google。

- 聊天不再调用 `find_event_candidates`，不再回复「没有找到对应日程」或「请在 Google 日历里改」。也不再用 `apply_update_times` 覆盖模型抽出的标题和时间。
- 卡片字段来自句子的抽槽：标题、时间，改期则是新的时间。用户可以改卡片。确认前不写 Google，不改本地行。
- 确认时才写主日历。改期 `patch`，撤销 `delete`。目标事件的 id 取确认当时的 `events.list`，用卡片标题对应那一条。对不上则这次确认失败，不写。不另做标题打分、放宽子串，也不把 Google 摘要写回本地。
- 这次写成功后，若本地已有同一个 `external_id`，仍按现有顺序先 Google、后本地。没有本地行就不为了这次修改去插入一行。
- 改期新时间的冲突检查仍用当场 `events.list`。那是冲突，不是在聊天里找本地候选。
- 未连接用户没有 Google 可写。他们的改期和撤销仍走现有本地确认，本刀不动。

### 测试

放在 `backend/tests/test_calendar_google.py`，模型返回值由测试给定：

- 「取消测试会议」「把测试会议改到后天下午四点」：不查本地候选。返回确认卡片，标题和时间等于抽槽结果。确认前无 `events.patch`、`events.delete`，本地行不变。
- 用户确认之后才出现对应的 patch 或 delete。假 `events.list` 里有同标题事件。没有本地行时不新插一行。已有同一 `external_id` 时，Google 成功后再改该行。
- 列表对不上卡片标题：确认失败，Google 与本地都不变。
- 现有把「没有找到对应日程」或「请在 Google 日历里改」当作改期/撤销结果的断言，改成上面的卡片行为。列表里的只读展示不变。

## 4. 「明天下午四点有空吗」只建议 12:00–13:00

### 原因

「有空」走 suggest（`calendar_tool.py` 36、46–47），不解析「四点」。

`_suggest_window`（166–174）：有「明天」且有「下午」时，窗口是明天 `12:00` 到后天 `00:00`。`find_free_slots` 要 1 小时，最多 3 段（226–228）。

`free_gaps`（`calendar_local.py` 40–53）在每一段空隙的开头只放一段所请求的时长。明天全空时，12:00 到 24:00 是一段空隙，于是只返回 `12:00–13:00`。`limit=3` 填不满。

这不是工作时间裁出来的。设置里没有工作时间时，`find_free_slots` 使用整个窗口（`calendar_google.py` 420–439）。实机 API 不返回每周工作时间。同一行为被 `test_find_free_slots_unions_google_busy_without_working_hours` 附近的空窗断言锁住：空窗的第一段就是窗口起点起的一个 `duration`。

「明天下午四点」里的 16:00，suggest 没有读。第 1 节只把今天写进抽槽提示，不替这句计算钟点。

### 改法

- 句子里有可解析的钟点：用账户墙钟算出那一小时（默认 1 小时）。空闲则直接说这个时间可以，建议卡片只放这一段。被占则在同一天的这段窗口里再给别的空隙。仍不写库。
- 没有钟点、而且整个窗口是一段空隙：回答这段时间都有空，不要只列开头的一小时。
- `free_gaps` 和创建冲突用的 `free_slots_for` 保持「每个空隙一段」。suggest 不再拿这个结果当作「四点有没有空」的答案。

### 测试

`now` 固定 `2026-10-15`，当天和明天都没有日程：

- 「明天下午四点有空吗」的 `direct_answer` 含 `2026-10-16 16:00`，不把 `12:00–13:00` 当成唯一建议。卡片 `mode=suggest`，`phase=info`，不写库。
- 明天 `16:00–17:00` 已有日程：回答里不把这一小时当作空闲，可以给出下午的另一段。
- 「明天下午有空吗」且全空：不把唯一建议写成 `12:00–13:00`。
- `test_find_free_slots_uses_gaps_without_working_hours` 的两段空隙断言不变。已有「不要出现工作时间四字」的 suggest 断言保留；若空窗文案不再是「可以考虑这些空闲时段」，只改那一条文案断言。

## 5. PR 切片

都从 `develop` 拉出，合回 `develop`。本文所在 PR 不在其内。

| 刀 | 内容 | 依赖 |
| --- | --- | --- |
| 1 | 第 1 节。抽槽提示写入 `calendar_now` 的日期和账户 IANA 时区。不覆盖模型的日期、标题、参与人 | 无 |
| 2 | 第 2 节。瞬时错误、重试一次、聊天与确认的文案和状态码 | 无。可与第 1 刀并行 |
| 3 | 第 3 节。改期和撤销不查本地行；用抽槽预填确认卡片；确认后才写 Google | 第 1 刀。卡片上的「明天」靠提示里的今天 |
| 4 | 第 4 节。有钟点就回答该小时；整段都空就不要只给第一小时 | 无。suggest 自己解析钟点，不靠第 1 刀的模型 |

每一刀单独可回滚。创建仍是确认后才写入，起止仍是无偏移墙钟。改期和撤销改为确认后写 Google，不在聊天里查本地行。列表合并规则不变，不加产品时区。
