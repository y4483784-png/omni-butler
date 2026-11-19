# 从 release `d7710d687d147aeeda502d412454ab8a3829af66` 到 2026-11-19 架构

基线是本地 `release` 头，提交说明 `feat(desktop): open a file from the install package (#92)`。本文件只写实施设计。不改仓库，不实现，不合并，不部署。PR 93（头 `b41e74dcc0ca8c4800d4fd5861957e7d9445fcaf`，分支 `cursor/ev-net-responses-5880`）不是目标。旧计划 `docs/envisioned-product/envisioned-product-plan.md` 不改。

目标图是逻辑职责，不是新目录。主线是用户入口，持续交互派活，独立后台执行者，工具通道，结果回到聊天。确认闸留在后台执行层。

## 相对现状会改变什么

现状是一次网页聊天独占一条 `TaskRun`。`begin_turn` 把同一会话里还开着、且不是确认单标记的回合写成 `stopped`。页面在 `busy` 时拒绝再发，并有常驻「停止生成」。浏览器断开或取消 SSE 会 `task_run.stop`。工具由服务器先跑完，再请 Flash 写回答。出站请求拒绝 `tools`、`tool` 角色和 `reasoning_content`。网页搜索是智谱 `POST .../web_search`。看门狗用 `created_at` 满 20 分钟且状态为 `running` 就停止。邮件确认后在本进程生成 `uuid4`，写入 `MailAcceptance`，再记外部成功，没有交给邮箱服务。日历确认 HTTP 只 `finish_confirm`，不调用 `note_external_success`。办事公开回包不带回武装号，`outcome=placed` 且回声为空仍可记成已下单。桌面是一枚共享 `DESKTOP_PACKAGE_TOKEN`。聊天请求没有图片字段。入库识图仍走智谱。

落地后用户能看到这些差别。

- 长任务进行中可以再发文字、原图或文件。旧任务不因此停止。
- 新消息被分成补充、修改、新任务、取消。只有歧义会改变动作时才追问。明确文字取消只停被点名的任务。
- 界面没有常驻停止按钮。断网、关页、刷新不把任务标成取消。重连后看到同一次任务的进展、结果和待确认。
- 两个窗口读同一份服务端记录。过期确认被拒绝。同一次写只执行一次。
- 后台执行者用 Flash 自己决定要不要搜索。搜索走 DeepSeek 独立 Search / Fetch。回答模型仍是 Flash。明确说这一轮不要搜，这一轮不给搜索工具，执行端也拒绝搜索。下一轮恢复默认。
- 重要写操作先出确认卡。未确认不写。确认后回到原任务的下一步。拒绝自动允许和始终允许。
- 邮件只有邮箱服务接受之后才显示已发送。接受不等于对方已收到。超时标未知，先查，不重发。
- 日历在返回 event id 之后才记成功回执。拿不到 id 就保持未知，不按标题再插。
- 订单回包带回操作关联号和商家订单号。对不上只查，不重下。
- 本机动作卡绑定具体设备、文件和参数。只在安装包设备上执行。网页不能冒充。每台设备单独登记、只存哈希、可单独作废。
- 无进展先检查并报告，不按创建满 20 分钟强停。等人或等外部资源不算故障。明确失败或用户取消才结束。

## 已经在 release 上、不重做

`TaskRun` 五态、代际唯一键、迟到 `finish` 拒绝覆盖 `stopped`、确认单指纹与一次消费、`arm_remote_claim` 在出站前提交、未知写走 `decide_replay`、事件收件箱去重、`open_alongside` 不停止已在跑的任务、邮件与办事的确认卡、网页执行面拒绝本地工具、安装包证明不能用客户端字符串 `desktop` 伪造。这些机制继续用。缺的是接线和行为，不是再造一套状态机。

智谱嵌入、重排、入库识图保留。`32000`、最近十轮、普通轮不回放思考，不记成缺陷。`.env.example` 与代码默认的名字差异只在 U4 改说明，不据此改生产模型，不重建索引。

## 十二个实施单元

按依赖排序。不凑原来的 14 个刀名。能放进已有模块的不新造框架。

| 顺序 | 单元 | 从哪拉出 | 等谁 | 用户能验收的一件事 |
| --- | --- | --- | --- | --- |
| 1 | U1 任务账本 | `release` | 无 | 迟到写进不了已停止或已换代的任务 |
| 2 | U2 持续交互派活 | U1 合入后的 `release` | U1 | 忙时仍能发，旧任务不被新消息停掉 |
| 3 | U3 后台执行循环与独立搜索 | U2 之后 | U1、U2 | 寒暄仍是 Flash，新闻查询带来源且不再打智谱网页搜索 |
| 4 | U4 分任务模型、聊天原图、用量与模板说明 | U3 之后 | U3 | 聊天原图进 Flash 请求，入库识图仍走智谱 |
| 5 | U5 断线、重连、多窗口、重启恢复 | U4 之后 | U1、U2、U3 | 断开不把任务标取消，重连读到同一步 |
| 6 | U6 按进展看护 | U5 之后 | U1、U3 | 仍在产出的长任务不被 20 分钟创建时钟停掉 |
| 7 | U7 邮件接受 | U6 之后 | U3 的确认闸 | 没有提供方接受编号就不显示已发送 |
| 8 | U8 日历回执 | U7 之后 | U3 的确认闸 | 有 event id 才有一条外部成功 |
| 9 | U9 订单关联号 | U8 之后 | U3 的确认闸 | 回包没有对上的关联号就不记已下单 |
| 10 | U10 浏览器选择关口 | U3 之后即可写核对，不合入依赖 | U3 | 关口未过时依赖文件不变 |
| 11 | U11 设备登记 | U9 之后 | U1 | 作废一台不影响另一台，网页自报仍被拒 |
| 12 | U12 本机动作卡 | U11 之后 | U3、U5、U11 | 卡上的设备、文件、参数与实际执行一致，确认后原任务继续 |

U10 不改产品依赖，所以 U11 不必等 U10 合入。U10 的合入只表示核对记录可留档。浏览器依赖在关口通过并且操作者另说「合」之前不进任何单元。

```
release
  └─ U1 账本
       └─ U2 派活
            └─ U3 执行循环与搜索
                 ├─ U4 模型、原图、用量、模板
                 │    └─ U5 断线与恢复
                 │         └─ U6 看护
                 │              └─ U7 邮件
                 │                   └─ U8 日历
                 │                        └─ U9 订单
                 │                             └─ U11 设备
                 │                                  └─ U12 动作卡
                 └─ U10 浏览器关口（不改依赖，不挡住 U11）
```

共同规则。每一单元从当时的 `release` 拉分支。只改该单元「允许碰的文件」。单元测试、形式检查和本单元的运行场景都过了，操作者看过代码、形式结果和状态图并说「合」，才合并。没有明说的部署请求不部署。不发真实邮件，不写外部日历，不下真实订单。整体活体留到十二单元都落地之后，沿方案页 E2。形式模型暂不覆盖真实供应商网络、浏览器像素和多机时钟。

派活层和执行层分开。派活层接收并尽快回答归属。执行层对单个任务跑工具循环。Hermes 只借循环零件。OpenCode 只在「还没开始的一句可以撤下」这个局部可借，不把新消息默认排成一队，也不抄它的默认 steer。

## U1 任务账本

### 用户可见行为

这一单元没有新的聊天按钮。已停止或已完成的任务不会被后到的步骤、工具结果或用量改回进行中。进行中的任务多了一条可查询的步骤序列。看护和重连要的进展时间从这里开始记，但这一单元还不改变看门狗，也不改变断线。

### 代码锚点（本仓库路径:行）

- `backend/app/models/models.py:96-112` `TaskRun` 只有代际、状态、允许工具和 `created_at`。
- `backend/app/services/task_run.py:15-46` 五态迁移。`stopped` 上的 `finish` 是 `reject`。`completed` 上的 `stop` 是 `keep`。
- `backend/app/services/task_run.py:127-150` 条件更新，行数不是 1 就重读。
- `backend/app/services/task_run.py:187-219` 新回合默认停掉同会话其他开着的非确认单回合。这一刀先不改这个行为，留给 U2。
- 最新迁移是 `backend/alembic/versions/0017_errand.py`。下一号用 `0018`。

### 模块、类型、状态

不新增服务，不换数据库。在现有 SQLAlchemy 模型上加两类记录。

`TaskRun` 增加列，都有默认值，旧行可读。

- `goal`，文本，默认空。
- `search_policy`，`default` 或 `forbid`，默认 `default`。
- `last_progress_at`，可空时间。空表示还没有进展记录，不允许用 `created_at` 填。
- `executor_job_id`，可空字符串。空表示没有后台作业。

新表 `task_steps`。

- `id`，`user_id`，`task_run_id`，`generation`，`seq`
- `kind`：`model`、`assistant`、`tool`、`confirm`、`notice`
- `status`：`proposed`、`running`、`succeeded`、`failed`、`unknown`、`discarded`
- `tool_name`，`effect`，`payload` JSON，`reasoning_content` 可空，`usage_json` 可空
- `created_at`，`updated_at`
- 唯一约束 `(task_run_id, seq)`

新表 `task_messages`。

- `id`，`user_id`，`session_id`，`message_id` 可空，`task_run_id` 可空
- `relation`：`supplement`、`redirect`、`new`、`cancel`、`ask`
- `created_at`
- 这一表在 U1 只建结构和约束。U2 才写入。

`TaskRun.status` 仍只用现有五态。步骤状态不写进 `TaskRun.status`。确认单状态仍是 `open`、`unknown`、`written`、`cancelled`。

### 接口

在 `task_run.py` 增加，不改 `begin_turn` 的停旧行为。

- `note_progress(user_id, generation) -> bool`。仅当该行状态是 `running` 或 `waiting_resource` 时写入 `last_progress_at=now`。`waiting_person`、`completed`、`stopped` 返回 false，不改时间。
- `append_step(user_id, generation, kind, status, payload, ...) -> Step`。在同一事务里锁住 `TaskRun`。状态不是 `running` 且也不是 `waiting_person`（确认步骤允许挂在等人状态）时抛 `LateResult`，不插入。`seq` 取该任务当前最大 seq 加 1。
- `discard_late(user_id, generation, seq) -> None`。把已存在且 generation 不匹配的步骤标 `discarded`，不改任务状态。

`usage_json` 只存供应商返回的对象。没有用量就存 JSON `null`，不写 0。

### 持久化与迁移

`0018_task_step.py`。升级加列和两张表。降级删表、删列。PostgreSQL 新表沿用现有 `app.current_user_id` 行级策略，写法抄 `0014_event_receipt` 与 `0015_memory_ledger` 已有模式。SQLite 测试库不加策略。旧 `task_runs` 行不回填 `last_progress_at`。

### 并发、断线与重启恢复

步骤插入使用 `SELECT ... FOR UPDATE` 锁任务行，再算 `seq`。两个人同时写同一步时，唯一约束冲突的一方重读后重试一次，第二次仍冲突则失败，不覆盖已成功的 seq。

本单元不改 SSE。进程重启后新列还在，内存里没有第二份账。`executor_job_id` 重启后保持原值，U5 才解释它。

### 确认、取消、未知结果

确认步骤只是一条 `kind=confirm` 的记录，不消费 `ScheduleConfirmClaim`。取消不在本单元执行。未知结果不在本单元发明新枚举，步骤状态里的 `unknown` 只表示「这一步的结果还没核对」。

### 参考源的版本，以及可借与不可借的边界

不借外部项目的表。Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` 的会话活动时间是 U6 的参考，本单元只留列，不抄它 60 秒落盘节流，也不抄 300 秒通知。本单元没有打开 Hermes 函数体，行号以开源报告为准，不在这里当已核对。

### 明确不做

不改 `begin_turn`。不改看门狗。不改 `llm.py`。不把步骤当成新的任务状态。不回填历史进展时间。

### 验收

单元。新建运行中的任务可以追加 seq 1 和 2。对 `stopped` 调用 `append_step` 抛 `LateResult`，任务仍是 `stopped`，步骤表没有新行。`note_progress` 对 `waiting_person` 返回 false 且 `last_progress_at` 仍空。用量缺省写入 `null`，读回来不是 0。

形式。小模型只含一个任务、一个 generation、步骤 seq。要证明 `StoppedRejectsStep`、`CompletedRejectsStep`、`SeqIncreases`、`ProgressSkipsWaitingPerson`。模型不覆盖数据库锁等待和 SSE。

运行场景。pytest 用现有 SQLite 配方。通过谓词是第二次迟到写入后，`task_runs.status` 仍为停止前的终态，`task_steps` 行数不增加。

### 依赖

无。从 `release` 拉出。

### 风险

迁移加列若漏了 PostgreSQL 默认值，旧行读会失败。降级测试要在 SQLite 和迁移脚本审查里都看一遍。PostgreSQL 的 RLS 只在有策略的环境生效，SQLite 测试不能冒充 RLS 已证明。

### 本单元的待定点

无。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/models/models.py`、`backend/app/services/task_run.py`、`backend/alembic/versions/0018_task_step.py`、`backend/tests/test_task_step.py`、`verification/task-step/` 下的新检查。不改前端，不改 `chat.py`。

## U2 持续交互派活

### 用户可见行为

一个任务还在跑时，同一会话可以再发一句。原任务状态不变。新句子会得到派活层的回复，说明它被当成补充、修改、新任务、取消，或者在追问。界面不再显示「停止生成」。输入框在对方还在工作时可以发送。用户用明确文字取消时，只有被点名的那个任务变成 `stopped`。没有点名且当时有两个以上进行中的任务时，先追问，不全部停掉。普通补充不弹出确认卡。

### 代码锚点（本仓库路径:行）

- `backend/app/services/task_run.py:201-205` 同会话开着的非确认单回合会被 `stop`。
- `backend/app/services/task_run.py:253-259` `open_alongside` 已经能不停止其他回合。事件入口在用，聊天入口没有用。
- `backend/app/api/chat.py:54-59` 请求只有会话、文字、知识库开关和重生 id。
- `backend/app/api/chat.py:429` 每条聊天都 `begin_turn`。
- `frontend/src/components/ChatWindow.tsx:47-49` `busy` 时直接 return。
- `frontend/src/components/ChatWindow.tsx:140-142` 常驻停止按钮。
- `frontend/src/services/chatStream.ts:24-26` 停止只是 abort 本地 fetch。
- `frontend/src/store/useStore.ts:17-25` `busy` 在浏览器内存。

### 模块、类型、状态

派活决定放进已有 `backend/app/agents/dispatch.py`，新函数 `relate_incoming`。不另做队列服务。

决定的类型：

```
relation: supplement | redirect | new | cancel | ask
task_id: int | null
search_policy: default | forbid
question: str
```

`ask` 不新建任务，不改旧任务。`supplement` 把约束写进已有任务，不改它的 `goal` 除非正文明确在改目标。`redirect` 改 `goal` 或下一步，任务保持原状态。`new` 调用 `open_alongside`，不调用会停别人的 `begin_turn`。`cancel` 只对 `task_id` 调用现有 `task_run.stop`。

`search_policy=forbid` 只写在被关联的那个任务上，直到下一条用户消息替换它。它不是账户开关。

确认单标记回合继续免于被新消息停掉。`is_card_action` 的行为保留。

### 接口

`relate_incoming(user_id, session_id, text) -> Decision`。

判定用现有 `Role.PLANNER` 的 `complete_json`。输出必须是上面四个字段。解析失败就 `ask`，问题是「这句要作用在哪一项工作上」，不猜。模型提示只描述四类和「歧义会改变实际动作才问」。不维护关键词表当总路由。

测试注入 `Decision`，不把单元测试绑在真实模型上。

`POST /api/chat` 在 U2 仍返回 SSE。派活层先落用户消息和 `task_messages` 行，再流式给出一句归属说明。真正的工具循环还没接上，所以新任务在 U2 只创建 `running` 的 `TaskRun` 并记下 goal，不跑工具。U3 接上循环之前，这条新任务会停在「已接收」。U2 的运行场景必须断言它是 `running` 而不是 `stopped`，并在发布说明里写明回答内容要等 U3。不把 U2 假装成已经会搜。

文字取消的识别以结构化 `relation=cancel` 为准。用户打出「停止」两个字且当前只有一个进行中的任务时，提示要求模型输出 `cancel`。有多个任务时必须 `ask`。这是提示和测试夹具，不是关键词路由器。若模型把一句普通补充标成 `cancel`，执行前再看 `question` 是否空。`cancel` 且 `task_id` 空且进行中任务数不等于 1，降级为 `ask`。

### 持久化与迁移

不新迁移。写 U1 的 `task_messages`、`TaskRun.goal`、`TaskRun.search_policy`。用户原文仍进现有 `messages`。

### 并发、断线与重启恢复

两个窗口同时发，各自 `open_alongside` 或各自关联，靠 U1 的行锁。后到的消息不得调用 `_insert_running(..., stop_others=True)`。

U2 还不改断开即停止。关页仍会停掉当前这条 SSE 所绑定的代际。这一点在 U2 的界面文案里不承诺「关页也继续」。U5 才改。U2 只保证「再发一句」不杀旧任务。

重启后 `task_messages` 还在。U2 没有后台作业，`executor_job_id` 保持空。

### 确认、取消、未知结果

取消不是确认卡。明确且目标唯一就 `stop`。目标不唯一就 `ask`。修改不自动消费已有确认单。若修改改变了已发卡动作的参数，把该确认单保持原样，等 U12 和写操作单元按指纹失效处理。U2 不新增失效逻辑，避免半套。

未知归属不默认 `new`，也不默认 `cancel`。

### 参考源的版本，以及可借与不可借的边界

总体是 DeepSeek SuperAgent 的分工，方案页写的是持续交互与后台干活分开。本轮没有打开 `dsh-superagent-prototype` 的仓库函数，不把方案页句子写成已读源码。

OpenCode `79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294` 的 `packages/app/src/session/composer/queue.ts` 只说明「收下一句可以不打断正在跑的执行」。不抄默认 steer。不把每条消息放进同一队列。Hermes `AIAgent.steer` 与 `redirect` 的差别只作反例，`redirect` 会中止当前模型请求，不用于补充。DS-Bot `maybeCut` 会取消正在跑的模型调用，不抄。这些函数体本轮未重读，边界沿开源报告，不新增行号。

### 明确不做

不跑工具。不接搜索。不拿掉断线即停止。不把所有新消息排成一队。不做地球按钮。不因这句话升到 Pro。

### 验收

单元。会话里已有 `running` 任务时，再插入一条用户消息，旧任务仍是 `running`。结构化决定为 `cancel` 且只有一个进行中任务时，该任务变 `stopped`，另一个用户的任务不变。两个进行中任务且 `task_id` 为空时，结果是 `ask`，两个都仍是 `running`。`search_policy=forbid` 写在目标任务上，不写进别的任务。

形式。两个任务、一条消息。证明 `SecondMessageDoesNotStopFirst`、`CancelNamesOne`、`AmbiguousCancelAsks`、`CardActionUntouched`。不覆盖自然语言的全部句子。

运行场景。pytest 里用注入的 `Decision` 打 `POST /api/chat` 的派活函数。通过谓词是响应里能读到归属，数据库里旧 generation 的 status 与决定一致。不要求浏览器。

### 依赖

U1。

### 风险

派活模型误判会把补充当成取消。降级规则只挡住「没有唯一目标的取消」，挡不住「模型把唯一任务误标成取消」。验收里要有一条注入误判的回归，产品上靠下一句补充把新目标说清，不靠自动复活已停止任务。误停止不自动重启。

### 本单元的待定点

无。四类的产品含义已定。句子怎么判由结构化输出加降级规则执行，不再问用户。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/agents/dispatch.py`、`backend/app/services/task_run.py`、`backend/app/api/chat.py`、`backend/app/core/prompts.py`（只加派活提示）、`frontend/src/components/ChatWindow.tsx`、`frontend/src/services/chatStream.ts`、`frontend/src/store/useStore.ts`、对应测试与 `verification/turn-relation/`。

## U3 后台执行循环与独立搜索

### 用户可见行为

新任务或被修改的任务在后台继续。派活层不等它结束。执行者用 Flash 看目标和已保存的步骤，自己决定要不要搜索或读取网页。搜索结果以可点开来源回到同一次任务，随后 Flash 写成回答。同一轮不再调用智谱网页搜索。没有搜索的寒暄不计成 Pro。用户这一轮要求不搜时，请求里没有搜索工具，执行端若仍收到搜索调用就拒绝，并把拒绝写进步骤。下一轮用户没再禁止时，搜索恢复。写操作在这一单元只接到已有确认闸，未确认不执行。知识库检索和沙箱仍可被模型选，它们不是搜索。

### 代码锚点（本仓库路径:行）

- `backend/app/core/llm.py:244-256` `_refuse_outbound` 拒绝工具和思考字段。
- `backend/app/core/llm.py:272-295` 请求体只有模型、字符串消息、流式开关、`extra_body.thinking`、`reasoning_effort`。
- `backend/app/core/llm.py:411-424` `stream_chat` 先拒绝工具。
- `backend/app/core/llm.py:134-153` 角色只有 chat、planner、router、judge。聊天默认 `deepseek-flash`。
- `backend/app/agents/tools/web.py:10-36` `execute_web` 调 `search_planned`。
- `backend/app/services/web_search.py:58-62` 空基址也落到智谱 `/web_search`。
- `backend/app/services/web_search.py:255-262` 智谱字段 `search_query`、`search_engine`。
- `backend/app/agents/workflow.py:290-311` 日历名走确认，其他走读。停止和允许名单在执行前检查。
- `backend/app/agents/workflow.py:474` 服务器调用 `gateway.invoke`，不是模型选出的 `tool_calls`。
- `backend/app/agents/harness/constitution.yaml:84-88` 允许名单是 `kb`、`web`、`calendar`、`sandbox`。
- `backend/app/agents/harness/effects.py:18-36` 读可以再发，不可重放的写只核对。

### 模块、类型、状态

执行者放在已有 Celery 上，新任务模块 `backend/app/tasks/executor.py`。不新开队列产品。作业进现有 `maintenance` 以外的已有 worker 能力。若现有路由只有 `maintenance` 能跑长作业，就用 `maintenance`，不新增 broker。

一轮循环的记录用 U1 的 `task_steps`。

1. 读任务。状态不是 `running` 就退出。
2. 组装消息。普通历史不带 `reasoning_content`。从「这一个任务里、上一次带 `tools` 的请求」起，助手步骤里的 `reasoning_content` 和 `tool_calls` 必须原样带回。这是 2026-11-19 打开的思考模式文档的规则，不是 Hermes 补空格。
3. 调 Flash 聊天补全。`tools` 只在执行者这条路出现。`tool_choice` 用 `auto`。不使用 `required` 或指定工具名，因为同一天打开的创建补全文档写明，思考模式下这两种会 400。
4. 先把助手消息落成步骤。落库失败则不执行工具。
5. 没有 `tool_calls` 就写最终回答，`finish` 任务。
6. 有工具则逐个执行。搜索工具在 `search_policy=forbid` 时不发 HTTP，步骤记 `failed`，原因是本轮禁止搜索。
7. 工具结果落库后再回到第 2 步。

搜索工具名 `web_search` 与 `web_fetch`。旧名 `web` 从执行者的工具名单去掉。`execute_web` 不再被执行者调用。智谱网页搜索函数留在文件里直到本单元测试证明没有聊天路径引用，然后删除聊天路径上的调用。嵌入、重排、识图的智谱客户端不动。

写工具 `calendar`、`mail.send`、`errand.place`、`desktop.open`、`desktop.run` 在 U3 只允许「提出确认」：写一条 `confirm` 步骤，`wait_for_person`，不调用会真正写入的函数。已有确认消费仍由后面的 U7、U8、U9、U12 接。U3 不把这些写做成模型直接执行。

### 接口

DeepSeek 独立搜索，2026-11-19 打开的文档 `https://api-docs.deepseek.com/guides/search.md`。

- `POST https://api.deepseek.com/v1/tools/search`
- 请求字段用文档里的 `query`、`count`、`need_content`。`query` 1 到 128 个字符。`count` 用现有 `settings.web_search_count`，再夹到 1 与 50。不新编一个条数。
- `need_content` 默认 false。正文由模型再调用 fetch 取得。
- 响应读 `results[]` 的 `title`、`url`、`snippet`、`summary`、`content`、`date`。
- `POST https://api.deepseek.com/v1/tools/fetch`，请求字段 `url`。响应 `title`、`content`。
- 鉴权头 `Authorization: Bearer` 与 `Content-Type: application/json`。密钥用现有 `LLM_API_KEY`。不把智谱 `WEB_SEARCH_API_KEY` 拿去调这个接口。
- HTTP 408、429、5xx 按文档可以等后再试。本单元对同一步最多再试一次。其他 4xx 不重试。失败写入步骤 `failed`，不把空结果说成已搜到。

聊天补全仍是 `https://api.deepseek.com` 的现有客户端。工具定义用文档 `https://api-docs.deepseek.com/api/create-chat-completion` 里的 `tools`，类型 `function`。不声明文档里的服务端工具 `deepseek_web_search` 和 `deepseek_read_url`。那两个在供应商侧执行，执行端无法在发出前拒绝。

思考字段用 `reasoning_content`。不把 Harness 的 `thinking` 块写进这条聊天补全。

### 持久化与迁移

不新迁移。助手步骤保存 `reasoning_content` 只因这一请求带了 `tools`。最终回答那一步不要求下一步再回放。用量对象原样放进 `usage_json`，缺了就是 `null`。

`executor_job_id` 在入队前用条件更新写入。更新行数不是 1 说明已有作业，不再入队。

### 并发、断线与重启恢复

执行者在 Celery worker，不在 SSE 的 `asyncio.Task` 里。U5 之前，SSE 断开仍会把绑定的代际 `stop`。执行者每一步开始时读状态，发现 `stopped` 就不再发模型请求，也不再发搜索。已经发出的 HTTP 不假装能撤回。

重启后 U5 才负责重新入队。U3 保证作业函数本身可重入：开始时若最后一条工具步骤是 `running`，先把它标 `unknown`，不自动重放搜索。

### 确认、取消、未知结果

搜索是读。失败不改成已搜索。禁止搜索是拒绝，不是未知。写操作停在确认步骤。用户取消把任务 `stop`，执行者退出。迟到的工具结果走 U1 的 `LateResult`，不写进回答。

### 参考源的版本，以及可借与不可借的边界

DeepSeek Harness `ca33fd6843b205e279c7ccb6cb73a472c78f5760` 的独立 `POST /v1/tools/search` 是产品选定的挂法。本轮未重读 Harness 函数体。字段以当天打开的官方 Search / Fetch 文档为准。

Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` 的「先保存助手消息，保存失败不执行工具」是要借的顺序。不借它在缺少思考文本时补空格。不把 Hermes 循环换成整个产品。函数体未重读。

不借 Responses API 的 `web_search`。2026-11-19 打开的 `https://api-docs.deepseek.com/zh-cn/guides/responses_api` 写明该工具在 `deepseek-v4-pro` 下由服务端执行，`deepseek-flash` 忽略。

### 明确不做

不升 Pro。不加地球按钮。不删嵌入、重排、入库识图。不在这一单元做真实邮件、日历写入或下单。不接浏览器。不回放普通寒暄的思考。

### 验收

单元。注入「只返回一段文字」的模型客户端时，任务 `completed`，没有搜索 HTTP。注入「先调用 `web_search`」时，发出的 URL 是 `https://api.deepseek.com/v1/tools/search`，随后模型看到的工具结果里有 `url`。`search_policy=forbid` 时该 URL 出现次数为 0，步骤原因含禁止。智谱 `/web_search` 出现次数为 0。助手步骤在工具 HTTP 之前已提交。落库失败的桩不会发出搜索。

形式。证明 `NoToolBeforeSave`、`ForbidSkipsSearch`、`WriteStopsAtConfirm`、`StoppedSkipsModel`。不覆盖供应商 400 的真实报文。

运行场景。pytest 假 HTTP。通过谓词是新闻这条路径的聊天模型名仍是 `settings.chat_model`，且假智谱服务器收到 0 次请求。

### 依赖

U1、U2。

### 风险

`query` 超过 128 个字符会被 400。执行端截断前要记一条步骤说明，不静默改掉用户的问题。截断规则用文档上限，不另发明。思考回传漏了会 400。测试要有一条「带工具的第二跳缺少 `reasoning_content` 时不把错误当成已完成回答」。

### 本单元的待定点

无。搜索挂法已定。检查间隔不属于这一单元。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/core/llm.py`、`backend/app/agents/tools/web.py`、`backend/app/agents/tools/registry.py`、`backend/app/services/web_search.py`、`backend/app/agents/workflow.py`、`backend/app/agents/dispatch.py`、`backend/app/agents/harness/constitution.yaml`、`backend/app/agents/harness/gateway.py`、`backend/app/tasks/executor.py`、`backend/app/tasks/celery_app.py`、`backend/app/api/chat.py`、对应测试。不改 `rag/` 下的嵌入和识图。

## U4 分任务模型、聊天原图、用量与模板说明

### 用户可见行为

聊天里发出的图片随这轮文字一起交给 Flash，不再先交给智谱转成文字。资料入库的图片摘要仍走智谱识图。各任务所用模型名能在步骤或日志里对上实际请求。供应商没返回用量时，界面和记录都不显示 0。`.env.example` 写明不填时代码用哪一个名字。

### 代码锚点（本仓库路径:行）

- `backend/app/core/config.py:25-32` 四个角色有模型名和思考开关，没有温度、`top_p`、输出上限。
- `backend/app/core/config.py:36` 代码默认 `embedding-3`。
- `backend/app/core/config.py:39` 代码默认 `glm-4.6v`。
- `backend/app/core/config.py:87` `max_context_turns` 默认 10。
- `backend/app/core/config.py:90` `context_max_tokens` 默认 32000，注释写明不是模型物理窗口。
- `.env.example:25` `EMBEDDING_MODEL=embedding-3-pro`。
- `.env.example:31` `VISION_MODEL=glm-4v-flash`。
- `.env.example:109` 并发示例写着 `embedding-3-pro` 和 `glm-4v-flash`。
- `backend/app/core/llm.py:258-268` `_wire_messages` 把内容收成字符串。
- `backend/app/rag/vision_ocr.py:17-46` 入库识图用智谱端点和 `image_url`。
- `backend/app/api/chat.py:54-59` 没有图片字段。
- 前端聊天输入只是 textarea，`ChatWindow.tsx:122-139`。没有聊天图片选择器。

### 模块、类型、状态

不新增角色枚举。派活用已有 `PLANNER`。执行循环和聊天回应用已有 `CHAT`。路由和核验保持 `ROUTER`、`JUDGE`。

请求参数只发送代码里已经有的值。温度、`top_p`、`max_tokens` 没有已确认的数字，请求里省略这些字段，让供应商用自己的默认。不在配置里写新的数字默认。

聊天图片是用户消息的内容块，不是新的任务状态。入库 OCR 仍是文档解析，不读这套内容块。

### 接口

2026-11-19 打开的 `https://api-docs.deepseek.com/guides/vision` 与创建补全文档。

用户消息 `content` 可以是数组。文字块是 `{"type":"text","text":...}`。图片块是 `{"type":"image_url","image_url":{"url":...}}`。`url` 用 data URL 或不超过 8192 字符的 http(s)。格式只接受 JPEG、PNG、GIF、WebP。不传 `detail`，避免替用户选 `low` 或 `high`。不走 Files API，本单元没有文件上传产品。单张上限沿文档 32 MiB 的检查，超出就拒绝这一张并告诉用户，不先转文字。

`deepseek-v4-pro` 在价格页 `https://api-docs.deepseek.com/quick_start/pricing` 的 Vision 列是不支持。聊天原图只允许发到 Vision 列为支持的模型。当前聊天默认是 `deepseek-flash`，可以发。若运行中的 `CHAT_MODEL` 是 `deepseek-v4-pro`，这一轮图片被拒绝并说明模型不支持，不改落到智谱。

用量读创建补全文档中的 `usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens`。流式时只采用最后一个带非空 `usage` 的块。没有这些字段就保持 `null`。

`.env.example` 在嵌入和识图两行下面各加一句：未设置环境变量时，代码默认分别是 `embedding-3` 和 `glm-4.6v`。不改这两行现有示例值，避免把示例改成另一套未被部署核实的生产值。并发示例那一行加同样的说明，不改数字。

### 持久化与迁移

图片不进数据库原文若现有消息列只是文本。消息表增加可空 `attachments` JSON，迁移 `0019_message_attachments`。只存 mime 与对象存储键。现有上传已经走 MinIO，沿用，不把 base64 塞进 Postgres。发给模型时再读对象并组成 data URL。对象缺失则这一轮报「图片不在」，不调用识图。

### 并发、断线与重启恢复

图片对象在发模型前已落存储。请求中断后对象还在，U5 重连不重新上传。两个窗口看同一条消息时读同一附件键。

### 确认、取消、未知结果

传图不是写操作，没有确认卡。取消后不再把这张图发进新的模型请求。已经发出的请求不撤回。

### 参考源的版本，以及可借与不可借的边界

Hermes 的按任务解析模型是方向参考，函数体未重读。不借它的具体温度和预算数字。Agent Zero 的嵌入切换不在本单元使用。

### 明确不做

不编造角色参数。不把 32000 或十轮改掉。不迁移嵌入、重排、识图。不核实生产环境实际配了哪个模型。不展示费用账单，只记下返回的用量。

### 验收

单元。带一张 PNG 的聊天请求，假 Flash 客户端收到的用户 `content` 含 `image_url`，且 `vision_ocr.ocr_image_bytes` 被调用 0 次。同一张图走文档入库时仍调用智谱识图。`CHAT_MODEL=deepseek-v4-pro` 时图片请求不发出。用量缺失的响应在步骤里是 `null`。

形式。证明 `IngestVisionUntouched`、`ChatImageUsesChatModel`、`MissingUsageIsNull`。不覆盖真实像素。

运行场景。pytest 假模型。通过谓词是假请求的 `model` 等于 `settings.chat_model`，且消息里能找到该图片的 data URL 前缀。

### 依赖

U3。与 U3 共用 `llm.py`，所以排在后面。

### 风险

大图会顶到文档写的 48 MiB 请求体。超限时拒绝，不降级到智谱。对象存储未配置时，聊天传图失败，文字聊天保持原样。

### 本单元的待定点

无。具体角色数字没有出处，省略即是决定。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/core/llm.py`、`backend/app/core/config.py`、`backend/app/api/chat.py`、`backend/app/models/models.py`、`backend/alembic/versions/0019_message_attachments.py`、`.env.example`、`frontend/src/components/ChatWindow.tsx`、`frontend/src/services/chatStream.ts`、对应测试。不改 `backend/app/rag/vision_ocr.py` 的供应商。

## U5 断线、重连、多窗口、重启恢复

### 用户可见行为

关掉页面、刷新、网络断开，进行中的任务仍是 `running` 或原来的等人、等资源状态，不会变成 `stopped`。重新打开同一会话，能看到已落库的步骤、回答、待确认卡。两个窗口看到同一条任务。一边断开，另一边还在。取消之后才到达的片段不再追加到回答里。服务重启后，重启前还在跑的任务按下面的步骤恢复，而不是因为「库还在」就当作已经恢复。

### 代码锚点（本仓库路径:行）

- `backend/app/api/chat.py:526-528` 发现断开就 `task_run.stop` 并取消 `wf_task`。
- `backend/app/api/chat.py:644-647` 流里再查一次断开并 `stop`。
- `backend/app/api/chat.py:691-696` 停止后仍可能从已接收文本推断 artifact。
- `backend/app/api/chat.py:719-830` `finally` 会保存已累积文本，并总是发送 `done`。
- `frontend/src/services/chatStream.ts:24-32` abort 只作用于本页。
- `frontend/src/store/useStore.ts:17-25` 忙碌不跨窗口。
- `backend/app/tasks/celery_app.py:66` 已有 worker 启动钩子，没有任务恢复。

### 模块、类型、状态

SSE 变成订阅，不是任务的主人。订阅者离开不写 `stopped`。明确取消仍走 U2。

新增读接口 `GET /api/sessions/{id}/tasks`。返回该用户此会话的任务、步骤、确认卡阶段。不返回其他用户的行。

前端 `busy` 不再阻止发送。进行中的展示改读这个接口。本地 abort 只停止本页的字节流。

### 接口

事件带 `task_id` 与 `generation`。客户端只把 `generation` 与当前任务一致、且步骤状态不是 `discarded` 的内容画进气泡。

`done` 只在任务 `completed` 或本页订阅结束时发送。任务被取消时发送 `stopped`，不再发送一个看起来像正常结束的 `done` 附带新正文。

### 持久化与迁移

不新迁移。恢复只读 U1 的列和现有确认单。

### 并发、断线与重启恢复

断开的处理改成返回，不调用 `stop`。`CancelledError` 同样不调用 `stop`，除非这次取消来自 U2 的文字取消。

重启步骤，写在 worker 启动钩子里，顺序固定。

1. 不读进程内存里的旧账。`watchdog.py:158` 那种模块级账本重启后本来就会丢，恢复不依赖它。
2. 查询本库 `status in (running, waiting_resource)` 的 `TaskRun`。`waiting_person` 不重新执行。`completed` 与 `stopped` 不碰。
3. 对每个待恢复任务读最大 `seq` 的步骤。
4. 最后一步是 `model` 且状态 `running`：标 `failed`。不撤回已经发给供应商的请求。从最后一条已成功的步骤继续。
5. 最后一步是 `tool`、效果是读、状态 `running`：标 `unknown`。不由恢复程序再发搜索。执行者下一轮把「结果未知」交给模型，由模型决定要不要再搜。
6. 最后一步对应不可重放的写，且确认单已是 `unknown` 并有 `remote_request_id`：只调用现有核对，不插入、不发送、不下单。核对不上就保持 `unknown`。
7. 确认单仍是 `open`：不执行，任务保持等人。
8. 用 U3 的条件更新重新写入 `executor_job_id` 并入队。行数不是 1 就不再入队。
9. 执行者开工前再读状态。已是 `stopped` 就退出，不发模型请求。

旧代际的迟到结果必须带 generation。更新条件包含 `status` 仍是允许写入的状态且 `generation` 相等。否则丢弃。

### 确认、取消、未知结果

断线不取消确认单。过期确认仍由现有 `consume_schedule_claim` 拒绝。两个窗口点同一张卡，数据库行锁保证只有一个消费成功。另一个得到已有的关闭或未知，不写第二次。

取消之后，`finally` 里不得再把新的 token 追加进回答，也不得再从这段文本推断 artifact。取消前已经落库的文字保留。

### 参考源的版本，以及可借与不可借的边界

Hermes `session_authority.py` 的 attach 增加订阅、detach 只去掉这一路，是订阅形状的参考。不抄「最后一个订阅者离开后关闭」。函数体未重读。供应商取消接口未打开，不承诺能撤回已接受的补全，也不承诺能停掉已计费。

### 明确不做

不在这一单元改 20 分钟看门狗。不把断线画成用户取消。不填未知用量为 0。

### 验收

单元。调用聊天生成器的断开分支后，目标 `TaskRun.status` 仍是 `running`。取消分支之后，桩里后到的 token 不在保存的助手正文里，响应事件含 `stopped`，不含新的 artifact。两个客户端读 `GET /api/sessions/{id}/tasks` 得到同一个 `task_id`。第二次入队在 `executor_job_id` 已存在时不创建第二个作业。

形式。证明 `DisconnectKeepsStatus`、`CancelDropsLateToken`、`OneJobPerTask`、`WaitingPersonNotRerun`。不覆盖真实 TCP 半开连接。

运行场景。pytest 先创建运行中的任务和一条 `running` 的搜索步骤，再调用恢复函数。通过谓词是该步骤变为 `unknown`，搜索假服务器新增请求数为 0，任务仍是 `running`，作业入队次数为 1。

### 依赖

U1、U2、U3。排在 U4 后，避免同时改 `ChatWindow.tsx`。

### 风险

旧客户端仍把本地 abort 当成取消。U5 必须让服务端忽略这种断开。只改前端不够。

### 本单元的待定点

无。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/api/chat.py`、新的会话任务读路由（放在现有 `backend/app/api/` 会话模块，不新服务）、`backend/app/tasks/celery_app.py`、`backend/app/tasks/executor.py`、`frontend/src/components/ChatWindow.tsx`、`frontend/src/services/chatStream.ts`、`frontend/src/store/useStore.ts`、对应测试与 `verification/reconnect/`。

## U6 按进展看护

### 用户可见行为

创建已超过 20 分钟、但执行者仍在写步骤的任务不会被停掉。一段时间没有步骤和进展时，用户看到一次检查说明，而不是任务突然结束。等人确认、等外部核对期间不出现这条「无进展」说明。任务只在明确失败或用户取消时结束。同一次沉默不重复刷屏。

### 代码锚点（本仓库路径:行）

- `backend/app/tasks/watchdog.py:16` `CHECK_INTERVAL` 是 60 秒。
- `backend/app/tasks/watchdog.py:19` `ORDINARY_STALL` 是 20 分钟。
- `backend/app/tasks/watchdog.py:29-35` 只有 `running` 映射到 `stop`。
- `backend/app/tasks/watchdog.py:99-116` 沉默等于现在减去 `created_at` 再减去扫表空档，达到 20 分钟就列入停止。
- `backend/app/tasks/watchdog.py:122-126` `load_runs` 读出全部 `TaskRun`。
- `backend/app/tasks/celery_app.py:50-53` beat 名字 `watch-task-runs`，schedule 60。
- `backend/app/tasks/watchdog.py:158` 账本只在进程内存。

### 模块、类型、状态

仍用 `sweep_task`。效果表改成：沉默对四种非失败状态都是 `leave`。新增效果 `report` 只作用于 `running`，且只在配置了停滞阈值时。阈值未配置时，扫表不 `stop`，也不 `report`。

`waiting_person` 与 `waiting_resource` 不进入 `report`。明确失败是执行者写入的步骤 `failed` 且任务被执行者自己 `stop` 的路径，看门狗不推断失败。用户取消仍是 U2 的 `stop`。

报告记成一条 `kind=notice` 的步骤。同一任务在 `last_progress_at` 没有更新之前，已有一条未过期的 notice 就不再插第二条。

### 接口

配置项 `TASK_STALL_REPORT_AFTER` 默认空。空字符串表示不因沉默报告，也不因沉默停止。不给它填 300 秒或 20 分钟。现有 beat 的 60 秒只保留为扫表节拍，不解释成停滞时间。

`load_runs` 改为只查询 `status=running` 且 `last_progress_at` 非空的行，加上 `status=running` 且 `last_progress_at` 为空的行（这些行不因为创建时间被停，只在阈值已配置且从第一条步骤算起超时才报告）。不 `query.all()` 全表。

### 持久化与迁移

不新迁移。notice 用 `task_steps`。进程内 `SweepLedger` 不再决定停止。睡眠空档学分可以留下，但不得再参与「创建满 20 分钟就停」。

### 并发、断线与重启恢复

扫表与执行者同时写时，`note_progress` 的条件更新胜出。看门狗在 `stop` 之前已经不再因沉默调用 `stop`。重启丢了内存学分也不再误停。

### 确认、取消、未知结果

等人的确认单不因扫表被取消。未知写保持未知。看门狗不代用户确认，也不代用户取消。

### 参考源的版本，以及可借与不可借的边界

Hermes `gateway/session_stall.py` 用最近活动而不是创建时间，这是要借的方向。不借它的 300 秒默认，不借「只通知让人去敲 /stop」当本产品的文案。函数体未重读。OpenMausBot 的 20 分钟 `TURN_STALL_MS` 是现码注释里的来源，本单元停止使用它当杀任务的阈值。

### 明确不做

不编造秒数。不在阈值空着的时候发提醒。不扫全表。不把等确认当成故障。

### 验收

单元。`created_at` 早于 20 分钟、`last_progress_at` 是刚才、状态 `running`，`decide` 的停止列表为空。阈值空，`last_progress_at` 很旧，停止列表仍为空，notice 行数为 0。阈值注入为已有测试时钟的一个间隔时，第一次扫表插入一条 notice，第二次不插入。`waiting_person` 不在报告集合。

形式。证明 `FreshProgressStays`、`UnsetThresholdDoesNotStop`、`OneNoticePerSilence`、`WaitingUntouched`。模型里的阈值用测试常量，不写进产品默认。

运行场景。pytest 调用 `run_sweep`。通过谓词是旧的创建时间不再让 `task_run.get` 返回 `stopped`。

### 依赖

U1、U3。排在 U5 后，让 notice 有订阅者可读。阈值未定不挡住本单元合入。

### 风险

全表改成条件查询后，若漏了索引，扫表变慢。`task_runs` 已有 `(user_id, session_id, status)` 索引。查询条件要落在 `status` 上。这不是把 60 秒改掉的理由。

### 本单元的待定点

`TASK_STALL_REPORT_AFTER` 的具体时长未定。空值时不报告、不停止，因此不挡住 U6。以后要填，只填这一处，不改其他单元。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/tasks/watchdog.py`、`backend/app/core/config.py`、`backend/tests/test_watchdog.py`、`verification/task-watch/`。不改 beat 的 60 秒，除非测试断言需要改成「60 秒仍是节拍」。

## U7 邮件接受

### 用户可见行为

用户确认发送之后，界面在邮箱服务接受之前不显示已发送。接受后显示已发送，并说明这不等于对方已经收到。明确拒绝不显示已发送。超时或连接中断显示未知，不出现第二封。同一张确认再点，不产生第二次发送。没有配置真实传输时，确认后的结果是「未能发送」，不是本地编号冒充的成功。

### 代码锚点（本仓库路径:行）

- `backend/app/services/mail_entry.py:226-273` `commit_send` 在消费确认后执行 `remote = str(uuid4())`，再 `_accept` 和 `_seal`。
- `backend/app/services/mail_entry.py:397-418` `_accept` 插入 `MailAcceptance`。
- `backend/app/services/mail_entry.py:451-459` `_seal` 调 `note_external_success`。
- `backend/app/services/calendar_secrets.py:41` `GOOGLE_MAIL_SCOPES` 只有 `gmail.readonly`。
- `backend/app/services/event_inbox.py:164-194` 外部成功要求对方编号非空，且确认单已是 `written`、编号一致。

### 模块、类型、状态

传输接口放在 `mail_entry.py`，不新服务。

```
accept(draft) -> Accepted(provider_id) | Rejected(reason) | Unknown(query_key | none)
```

`provider_id` 必须来自传输返回值。本地 `uuid4` 不能传入 `note_external_success`。

`Accepted` 之后才 `_accept` 和 `_seal`。`Rejected` 走现有 `reopen_known_rejection`，不插入接受行。`Unknown` 保持确认单 `unknown`。若 `query_key` 存在，再次点击只调用 `lookup(query_key)`，不调用 `accept`。`query_key` 为空时，再次点击仍返回未知，不发送。

未配置传输时 `accept` 直接 `Rejected`，理由是未配置发送。这比现在的本地成功诚实。活体发送要等 open.md 里的传输选择。

### 接口

不在本单元选定 Gmail `users.messages.send` 或 SMTP。两者的接受证据不一样。Gmail 发送能返回 `id`。SMTP 的 `send` 返回不等于远程消息 id。在选择落地之前，生产配置不指向会真正发信的主机。

测试用假传输。假传输返回的 `provider_id` 原样进入 `MailAcceptance.remote_request_id` 和收件箱 `external_request_id`。

### 持久化与迁移

不新列。现有 `mail_acceptances.remote_request_id` 改由提供方编号填。旧的本地 uuid 行不回写，也不当成已投递。

### 并发、断线与重启恢复

`arm_remote_claim` 仍在真正的 `accept` 调用之前提交。崩溃点在 arm 之后、传输返回之前，确认单保持 `unknown`，没有 `provider_id`。恢复时走 U5 第 6 步，只查不发。两个窗口同时确认，`consume_schedule_claim` 的行锁只让一个进入 `accept`。

### 确认、取消、未知结果

没有 token 仍是 `缺少确认，未写入。`。取消不删除已经接受的行，也发不出撤回。未知不重发。

### 参考源的版本，以及可借与不可借的边界

DS-Bot `5e9b7da8882da3b68d8a0624342708c2150a9156` 说明可以等 SMTP 或 Gmail 发送调用返回后再标已发送。不抄它跳过确认卡的允许名单，不把它的返回当成投递回执。函数体未重读。nanoMuse 只作行为对照，不复制。

### 明确不做

不发真实邮件。不扩大 Gmail 范围。不把 mailto 链接算成已发送。不解决「响应丢失且没有任何远程编号」的查询。这个缺口保持未知，不假装有查询键。

### 验收

单元。假传输返回 `Accepted("m1")` 后，收件箱有一条 `external_success`，编号是 `m1`。假传输返回 `Rejected` 时没有 `MailAcceptance` 行。假传输返回 `Unknown(query_key=None)` 时第二次 `commit_send` 调用 `accept` 的次数仍是 1。未配置传输时不插入接受行。

形式。证明 `NoSealBeforeAccept`、`RejectInsertsNothing`、`UnknownDoesNotResend`、`SecondClickNoSecondAccept`。不覆盖真实 SMTP 对话。

运行场景。pytest 假传输。通过谓词是用户可见状态里「已发送」只在 `Accepted` 之后出现。

### 依赖

U3 的确认闸。排在 U6 后，使写路径一次只改一条。

### 风险

现有测试若断言本地 uuid 即成功，要改成断言「未配置则不成功」。这是行为修正，不是兼容保留。

### 本单元的待定点

活体用哪一种传输，见 `open.md`。不挡住状态机合入。挡住任何真实发信。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/services/mail_entry.py`、`backend/tests/test_mail_entry.py` 及邮件形式检查。不改 `gmail.py` 的只读范围。

## U8 日历回执

### 用户可见行为

日历写入在 Google 返回 event id 并已存到本地行之后，收件箱才有一条对应的外部成功。失败没有这条记录。同一确认再点不产生第二个事件。插入请求已经发出但响应丢失时，用已有的 `requestId` 去查，查到就收下那个 id，查不到保持未知，不按标题再插。

### 代码锚点（本仓库路径:行）

- `backend/app/services/calendar_google.py:1345-1354` 插入前生成 `requestId`，写入 `extendedProperties.private`，并 `arm`。
- `backend/app/services/calendar_google.py:1234-1266` `lookup_inserted_event` 用 `privateExtendedProperty=requestId=` 做 GET，不插入。
- `backend/app/api/calendar.py:498-501` `_seal_schedule_write` 只 `finish_confirm`。
- 全库调用 `note_external_success` 的产品代码只有 `mail_entry.py:453`。日历 HTTP 不在其中。
- `backend/app/agents/workflow.py:330-351` 未知写可以核对，但生产工具不填 `unknown_write`。测试以外没有赋值。

### 模块、类型、状态

不新确认表。日历创建成功的定义是本地行有非空 `external_id`，且 `note_external_success` 使用这个 id。`finish_confirm` 仍调用，但不得早于这个编号落库。

事件收件箱需要一条与这次确认对应的 receipt。若这张卡还没有 receipt，在 arm 之前用现有 `submit_event` 记一条，`delivery_id` 用确认单 token，`source` 用已允许的 `internal`。不新造 source 枚举。

### 接口

成功路径。`create_event` 返回的 Google id 写入本地 `external_id` 之后，调用 `note_external_success(..., remote_request_id=google_id)`。

丢失响应。现有 `_adopt_inserted` 已经会查。查到则走同一条成功路径。查不到则确认单保持 `unknown`，函数抛出现有的未知错误，不调用 `events.insert` 第二次。

不把日程标题、开始时间当成查询键。

### 持久化与迁移

不新迁移。`calendar_events.external_id` 已在。

### 并发、断线与重启恢复

两个点击仍由确认单消费挡住。重启落在 arm 之后、id 落库之前时，U5 只调用 `lookup_inserted_event`。GET 失败保持未知，不插入。

### 确认、取消、未知结果

未确认不插入。过期确认不插入。取消不删除已经写下的远程事件。未知不重插。拿到 event id 只说明这一次插入有了编号，不表示标题相同的另一张卡会被自动合并。

### 参考源的版本，以及可借与不可借的边界

Hermes `skills/productivity/google-workspace/scripts/google_api.py` 的 `calendar_create` 只覆盖「返回 id 后再说 created」。它没有确认单，重复 create 会再插入，不抄。函数体未重读。本仓库自己的 `requestId` 才是丢响应的查询键。

### 明确不做

不改更新、删除、RSVP 的未知核对，除非它们已经走同一条 `requestId`。本单元的验收只覆盖创建。空 403 仍不当成「一定没写上」。现有代码把明确拒绝和未知分开，本单元不放宽。

### 验收

单元。假 Google 返回 id `evt-1` 后，收件箱对该 token 只有一条 `external_success`，`external_request_id` 是 `evt-1`。假 Google 在 POST 时抛网络错误且 GET 返回空，`external_success` 行数为 0，`events.insert` 调用次数为 1。第二次点击 `events.insert` 次数仍是 1。假 Google 返回 400 且现有分类是明确拒绝时，不记外部成功。

形式。证明 `SealFollowsEventId`、`LostResponseDoesNotInsertAgain`、`TitleIsNotTheKey`。不覆盖 Google 的全部错误码。

运行场景。pytest 假 Google。通过谓词是本地事件行的 `external_id` 与收件箱编号相同，且插入次数不超过 1。

### 依赖

U7 只是为了错开写路径。逻辑依赖是现有确认单和 U3 的「写之前先停在确认」。

### 风险

`note_external_success` 要求 receipt 的 claim 已是 `written`。调用顺序必须是本地 id 落库、`finish_confirm`、再 `note_external_success`。颠倒会 409。测试要锁住这个顺序。

### 本单元的待定点

无。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/api/calendar.py`、`backend/app/services/calendar_google.py`、`backend/app/services/event_inbox.py`（只在需要绑 receipt 时）、日历测试与 `verification/` 里已有日历检查的锚点。不改邮件。

## U9 订单关联号

### 用户可见行为

下单结果能对上当初那一次确认。回包里有本次操作关联号和商家订单号时，才显示已下单。对不上、缺订单号或正文只写「已下单」时，显示未知，不再下一单。支付和登录材料不出现在回包和日志里。没有连接器时，不把浏览器当成已经接上的下单通道。

### 代码锚点（本仓库路径:行）

- `backend/app/services/errands.py:160-171` 下单前 `arm_remote_claim`，头里带武装号。
- `backend/app/services/errands.py:348-377` `outcome=placed` 且回声为空或不是冲突时，`named=True`。正文里的「已下单」另有一条更严的判断。
- `backend/app/services/errands.py:225-249` `_reconcile` 查记录或 lookup，不调用 `_place`。
- `services/errand-compose/server.py:15-27` `_public` 只返回 `outcome`、`order_id`、`told`、`clicks_after_wall`。
- `backend/app/services/errand_order.py:56-70` `claim_parts` 没有关联号。关联号是确认单上的 `remote_request_id`。
- `backend/app/services/errands.py:252-255` `_save` 不调用 `note_external_success`。

### 模块、类型、状态

公开回包增加两个字段，都不含支付材料。

- `idempotency_key`，值等于本次 arm 的编号。
- `merchant_order_id`，商家订单号。可以与现有 `order_id` 相同，但保存时两个都要有。缺一就不是已下单。

`interpret` 改成：`outcome=placed` 只有在 `idempotency_key` 等于本次武装号且 `merchant_order_id` 非空时才是 placed。回声缺失不再算 placed。正文「已下单」同样必须带这两个字段。

`_public` 返回这两个字段，继续丢掉 `password`、`pan`、`cvv`、`cookie`、`secret`、`pay`、`login`。

未知仍走 `_reconcile`。lookup 的请求带同一个武装号。lookup 回包也要回这两个字段，否则保持未知。

已下单成功后补一条 `note_external_success`，编号用 `merchant_order_id`。receipt 的 `delivery_id` 用武装号。source 用 `internal`。这不表示邮件或日历的成功。

### 接口

连接器 `place` 与 `lookup` 的 JSON 增加上述字段。现有 `Idempotency-Key` 头保留。头不是公开回包，不能代替回包字段。

没有 `MerchantConnector` 且没有通过 U10 的浏览器时，`commit_errand` 在没有连接器的情况下返回明确失败「没有可用的下单接口」，不调用 compose 去下新单。已有的未知核对若 compose 只提供 lookup，可以查，不能 place。现码在没有连接器时会 POST compose 的 place。这一条是行为变化。

### 持久化与迁移

`errand_records.order_id` 存 `merchant_order_id`。不新列，除非现有 `order_id` 被测试证明装不下。装不下再加可空 `merchant_order_id`，迁移号顺延。默认先复用 `order_id`。

### 并发、断线与重启恢复

第二次点击进入 `_reconcile`，不进入 `_place`。arm 之后崩溃，U5 只 lookup。lookup 失败保持未知。

### 确认、取消、未知结果

空 token 仍拒绝。未知不重下。取消不退款。价格超限和 App 墙沿用现有明确拒绝，前提是回包关联号一致。关联号不一致时，即使写着拒绝，也当未知，避免把别人的结果套到这张单上。

### 参考源的版本，以及可借与不可借的边界

开源报告写明，在已读范围内没有完整的订单对账模块。不把 nanoMuse 的本地 `source_key`、Agent Zero 的 websocket `correlation_id` 或 Rowboat 的注释当成方案。不虚构一个库。Poke、Vesta、Hermes Browser Use 不在本单元接入。

### 明确不做

不选浏览器依赖。不在本单元扩大 Playwright。不记录支付材料。不把 `outcome=placed` 的旧宽松判断留作兼容。

### 验收

单元。回包含一致的 `idempotency_key` 和 `merchant_order_id` 时插入一条 `ErrandRecord`。回包只有 `outcome=placed` 和「已下单」、没有关联号时，记录数为 0，place 次数为 1，第二次点击 place 次数仍是 1。回包含 `pay` 时响应和日志夹具里看不到该字段。没有连接器时 place URL 调用次数为 0。

形式。证明 `EchoRequired`、`SecondClickLooksUp`、`SecretsStayOut`、`NoConnectorDoesNotPlace`。不覆盖真实商店。

运行场景。pytest 假连接器。通过谓词是保存的 `order_id` 等于回包的 `merchant_order_id`，且武装号相等。

### 依赖

U3 的确认闸。与 U8 错开。

### 风险

收紧 `interpret` 会让旧的 compose 假成功测试失败。测试改成必须带回关联号。不在产品里保留宽松分支。

### 本单元的待定点

无。字段名在这里定成 `idempotency_key` 与 `merchant_order_id`。不再问用户选原号还是哈希。哈希会让执行端无法对上自己 arm 的值，所以用原武装号。它不是支付凭证。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/services/errands.py`、`backend/app/services/errand_order.py`、`services/errand-compose/server.py`、`services/errand-compose/decide.py`、对应测试。不改 `requirements.txt` 去加浏览器库。

## U10 浏览器选择关口

### 用户可见行为

没有新的浏览器按钮，也没有新的下单方式。连接器仍然优先。关口没通过时，用户走网页通道的请求得到「还没有可用的网页执行后端」，而不是一个未核对的代理去点击。

### 代码锚点（本仓库路径:行）

- `services/errand-compose` 已用 Playwright 做窄的下单服务。这不是自治代理。
- U9 之后，没有连接器就不再 place。
- 方案页把这块标成候选，依赖未定。

### 模块、类型、状态

本单元的产物是一份核对记录，不进入运行时状态机。记录要回答三个问题，每个都要有本仓库测试或明确的未通过。

1. 候选是否能用 Flash 的 `function` 工具表达，并且执行者能在写出之前拦住。服务端代点、执行端看不见的工具，不合格。
2. 提交订单、付款、发送这类写操作，未确认时是否一次都没有执行。只在提示里写「请先确认」不合格。
3. 结果是否是结构化字段，至少能放进 `task_steps.payload`，并带有证据引用。只有截图、没有字段，不合格。

### 接口

无产品接口。核对对象是 Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f` 里实际的 Browser Use 接法。本轮未重读该函数。实施者打开后写下路径和行号。打不开就写未核实，关口保持未通过。

### 持久化与迁移

无。

### 并发、断线与重启恢复

无关口记录就不增加运行时代码，因此没有新的恢复路径。

### 确认、取消、未知结果

写操作必须能接上现有 `ScheduleConfirmClaim`。接不上就未通过。

### 参考源的版本，以及可借与不可借的边界

Poke 案例 `https://www.browserbase.com/blog/case-study-interaction` 只说明后台浏览器的产品分工，不能推断用了 Stagehand。Vesta `https://browser-use.com/posts/vesta` 只说明有人展示过 API 加浏览器和证据。Hermes 加 Browser Use 的案例页不是本仓库已接通，也不是成功率。Playwright 不是自治 agent。现有 compose 服务不升级成这个后端。

### 明确不做

不修改 `requirements.txt`。不安装 Browser Use、Stagehand 或其他浏览器产品。不把供应商案例写成测试通过。

### 验收

单元。依赖清单与本单元开始前的字节一致。新增核对文档里三个问题都有「通过」或「未通过」，没有空着。

形式。不适用运行时状态。要证明的性质是「未通过时执行者的工具名单不含浏览器工具」。用现有 constitution 加载测试即可。

运行场景。pytest 请求一个没有连接器的网页任务。通过谓词是响应为没有可用后端，且进程环境里没有新的浏览器库导入。

### 依赖

U3。不挡住 U11。

### 风险

把关口写成已经选择了 Browser Use。文档标题必须写「未选定」。

### 本单元的待定点

三个问题里任一未通过，依赖就保持未选。这是选择关口，不是要用户现在点一个产品名。

审查合入门槛。操作者看过核对记录并说「合」才把记录留在仓库。说「合」也不等于允许加依赖。加依赖要另一次明确的合，且三个问题都已通过。

允许碰的文件。只允许新增 `docs/envisioned-product/review/ev-browser-gate.md`。不碰依赖和执行代码。

## U11 设备登记

### 用户可见行为

每一台安装包设备有自己的登记。设置里能作废一台，另一台仍可提出本机动作。凭证只显示一次。服务器不保存明文。网页登录并在请求里写 `desktop`，仍然不能执行本机工具。设备凭证不能跳过动作确认卡。

### 代码锚点（本仓库路径:行）

- `backend/app/core/config.py:186` 一个 `DESKTOP_PACKAGE_TOKEN`。
- `backend/app/agents/harness/desktop_channel.py:30-41` `recognize_package` 用共享令牌做 `hmac.compare_digest`。空令牌认不出任何包。`desktop` 和 `web` 两个字直接拒绝。
- `backend/app/agents/harness/surface.py:6-23` 服务端执行面只有 `WEB`。
- `backend/app/api/chat.py:54-59` `ChatRequest` 没有 desktop 字段。
- `backend/app/api/desktop.py:34-38` 认不出包就 403。

### 模块、类型、状态

新表 `desktop_devices`。

- `id`，`user_id`，`name`
- `token_hash`，只存 SHA-256
- `revoked_at` 可空
- `created_at`
- 唯一 `(user_id, token_hash)`

明文令牌只在创建响应里出现一次。`list` 返回 id、名字、时间、是否已作废，不返回哈希。

认证：对出示的令牌做 SHA-256，再用 `hmac.compare_digest` 与库存哈希比较。已作废的行不算通过。比较失败与旧的共享令牌失败一样返回 403，不提示哪一段不匹配。

谁可以发证。当前登录用户给自己的账户发证。不新增管理员角色。这是本设计做出的决定，避免再问一次产品方向。

共享 `DESKTOP_PACKAGE_TOKEN` 在本单元之后不再能通过 `recognize_package`。升级说明写明旧安装包要重新登记。不保留共享令牌当后门。

### 接口

- `POST /api/desktop/devices` 返回一次明文。
- `GET /api/desktop/devices` 不返回哈希和明文。
- `POST /api/desktop/devices/{id}/revoke` 只作废这一行。保存失败回滚。
- 包请求头仍是 `X-Omni-Package`。值是该设备的明文令牌，不是 `desktop`。

`PackageProof` 增加设备 id。不能用字符串构造。现有 `PackageProof("desktop")` 抛 `TypeError` 的性质保持。

### 持久化与迁移

`0020_desktop_device.py`。若 U9 用掉了 0020，号顺延。升级不复制旧共享令牌进表。

### 并发、断线与重启恢复

作废与认证同时发生时，认证事务读到 `revoked_at` 非空就拒绝。重启不改变哈希。丢了明文只能重新发证，不能从哈希还原。

### 确认、取消、未知结果

设备认证通过只表示包是登记过的。`propose` 和 `commit` 仍走确认单。作废不删除已写出的本地文件。

### 参考源的版本，以及可借与不可借的边界

OpenMausBot 机制钉 `083fd7b51405e6d0c01e189b2f65ab0322509b5a`，HEAD `35d044295697ef2b79b889461af6902c85a1b456`。借哈希保存、恒定时间比较、按 id 作废、列表不返回哈希。不借手机配对和端到端协议。函数体未重读。

Open Super Agent `3a0303559c7dc3d3b83142e59da636c872df6e65` 的按设备字符串删掉旧行，不抄。它附近的自动允许不抄。

### 明确不做

不让网页执行本地工具。不把设备凭证当成确认。不在日志里写明文令牌。

### 验收

单元。登记后出示该明文可以通过认证。数据库行里找不到明文。作废这台后认证失败，另一台仍成功。`ChatRequest` 形状仍不能表达 desktop。头的值是 `desktop` 时 403。共享旧令牌 403。

形式。证明 `HashOnly`、`RevokeIsPerId`、`WordDesktopRejected`、`ProofNotFromString`。不覆盖操作系统钥匙串。

运行场景。pytest 两个设备。通过谓词是作废请求的响应只含被作废的 id，另一 id 的后续 propose 仍能通过认证这一步并停在确认，而不是执行。

### 依赖

U1 的迁移顺序。产品上排在 U9 之后。

### 风险

已发出的安装包在升级后不能再连。发布说明必须写重新登记。不提供共享令牌兼容开关。

### 本单元的待定点

无。发证人定为当前用户。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/models/models.py`、新迁移、`backend/app/agents/harness/desktop_channel.py`、`backend/app/api/desktop.py`、`backend/app/core/config.py`、桌面认证测试。不改 `desktop/executor.py` 的执行条件以外的文件，除非头的传递必须从主进程读新令牌。允许碰 `desktop/main.js` 里保存令牌的那一处。不把令牌写进前端包体。

## U12 本机动作卡

### 用户可见行为

后台任务准备打开某个文件或运行某个程序时，用户看到一张卡，上面是设备名、路径和参数。未确认不执行。确认一次只执行这一次。执行完，同一次后台任务继续下一步。参数或设备与卡上不一致时，旧确认无效，要重新确认。网页聊天不出现可执行的本机按钮。安装包里原有的探测按钮可以留下作诊断，但不能代替这张卡。

### 代码锚点（本仓库路径:行）

- `backend/app/services/local_action.py:90-152` `propose_local` 发卡，不执行。
- `backend/app/services/local_action.py:155-206` `commit_local` 消费确认、arm，返回 `kind=armed`。服务器不打开文件。
- `backend/app/services/local_action.py:209-237` `finish_local` 只结束 `desktop.open` 或 `desktop.run`。
- `desktop/executor.py:5-22` `perform` 只在 `kind=armed` 时打开或运行。
- `backend/app/services/schedule_confirm.py:95-101` 指纹含 `local_target` 与 `local_args`。
- `backend/app/services/schedule_confirm.py:163-165` 范围是 `desktop:user:{id}`，还没有设备 id。
- `frontend/src/components/ChatWindow.tsx:238` 网页跳过 `mode === "desktop"`。
- `desktop/actions.js` 与 `desktop/preload.js` 本轮未重读。不把审查里的按钮文案写成这次又打开过的源码。

### 模块、类型、状态

确认单的 `write_parts` 增加 `device_id`。指纹因此变化。旧卡没有 `device_id` 的不能拿来执行新动作。

执行者在 U3 里遇到本机工具时已经停在确认。本单元把确认步骤的 payload 设成与 `LocalAction` 相同的字段加 `device_id`。用户确认后，安装包 `perform`，再 `finish_local`。`finish_local` 成功后把任务从 `waiting_person` 恢复为 `running`，并入队原 `executor_job_id` 的下一次循环。恢复前再核对指纹。不一致就保持等人，不执行。

`write_scope` 改成 `desktop:user:{id}:device:{device_id}`。作废设备后，该设备的未执行确认一并拒绝。

网页构建继续不渲染可执行按钮。安装包渲染这张卡。卡的字来自确认单 payload，不来自模型在确认之后新生成的参数。

### 接口

`propose`、`commit`、`finish` 的请求体增加 `device_id`。`commit` 时设备必须属于该用户且未作废，并且与指纹一致。`perform` 的 license 增加 `device_id`。执行端发现本地保存的设备 id 与 license 不一致就返回 false，不调用 `open_file` 或 `run_program`。

### 持久化与迁移

不新表。指纹字段变长仍放得下现有字符串列。若列是固定短长度，先读模型定义再加长。当前 `fingerprint` 是 `String`，没有在已读行里看到长度上限。

### 并发、断线与重启恢复

确认后、`perform` 前进程崩溃，确认单是 `unknown`。再次点击不第二次 `perform`，与现有 `commit_local` 对 `unknown` 返回 license 但不重新 arm 的行为一致。`unknown` 时 `perform` 不得再运行。现有 `executor.perform` 只接受 `armed`。保持这一点。用户要重做必须新的确认。

断线不取消已发出的确认卡。重连能看见卡。

### 确认、取消、未知结果

未确认不执行。同一 token 第二次 `perform` 返回 false。拒绝和过期不执行。取消任务后，执行者不再把结果写进已停止的任务。已经在本机跑完的进程不追回。

不抄自动允许、始终允许、记住规则后跳过卡。

### 参考源的版本，以及可借与不可借的边界

OpenClaw `069c3e2b02bcdac1f9030e4a4eb6d021e2607176` 的一次结算后再次结算返回 false，以及确认后继续同一次执行，是要借的形状。同一文件里的自动复核和 `allow-always` 不借。函数体未重读。

FullDots `e625654e779149738847ea286fa21a3d5d43b803` 的参数指纹和 consumed 之后不能再消费，是要借的形状。它的规则跳过不借。门本身不执行命令，所以执行仍用本仓库的 `perform`。

Rowboat `c37ffda3b002499da5b0d76785cc7c3275be1a7f` 只借「记下批准再继续」。不借自动允许。

### 明确不做

不让服务器替用户打开文件。不在网页开放本地工具。不把探测按钮扩展成任意路径而不经确认。不复制 OpenClaw 的自动允许。

### 验收

单元。未 `commit` 时 `perform` 返回 false，`open_file` 调用次数为 0。`commit` 后 license 的路径、参数、`device_id` 与卡一致，`perform` 一次返回 true。同一 license 再调用返回 false。改一个参数后再用旧 token，`commit` 拒绝，`perform` 次数不增加。`finish` 之后原任务状态是 `running`，且下一条步骤属于同一个 `task_run_id`。网页客户端请求执行本地工具仍被拒绝。

形式。证明 `NoRunBeforeArm`、`FingerprintMismatchRefuses`、`SecondPerformFalse`、`ResumeSameTask`、`WebStillDenied`。不覆盖真实桌面窗口管理器。

运行场景。pytest 用假的 `open_file`。通过谓词是假函数收到的路径等于卡上的 `local_target`，且任务 id 在执行前后相同。

### 依赖

U3、U5、U11。

### 风险

恢复 `running` 时若还有别的开放确认，不得把别的卡也标成已执行。只结束这张 token。

### 本单元的待定点

无。一步一张卡。一张卡一个设备、一个路径、一组参数。

审查合入门槛。操作者看过本单元代码、形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。

允许碰的文件。`backend/app/services/local_action.py`、`backend/app/services/schedule_confirm.py`、`backend/app/tasks/executor.py`、`desktop/executor.py`、`desktop/confirm_local.py`、安装包里渲染卡片的现有前端文件、网页侧只允许保持跳过 desktop 模式的测试。不把网页聊天改成能执行本地工具。

## 整体之后才做的活体

十二个单元各自的 pytest 和形式检查通过，并分别合入之后，才做方案页 E2 的整体活体。整体范围包括搜索与思考回传、邮件与日历与订单的未知不重做、设备作废、多窗口、重连、文字取消、重启恢复。整体活体仍不在本设计的实施许可里。没有另一次明确的部署请求，不部署。
