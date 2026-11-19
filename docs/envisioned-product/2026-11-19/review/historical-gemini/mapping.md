# 现状到 2026-11-19 目标架构逐项映射（mapping）

基线：`release` 分支 `d7710d687d147aeeda502d412454ab8a3829af66`。本映射覆盖方案页全部 16 条主线（`M1`–`M6`、`T1`–`T5`、`X1`–`X5`）、8 条历史项（`R-B3`、`R1`–`R7`）、14 刀已合并现状，以及原 27 项去向表。凡已在 `release` 上落地的基座能力均标注「已在 release，不重做」并只列仍缺行为。

## 1. 14 刀已合并现状盘点（不重做清单）

| 原刀 | 提交 | 状态与已在 release 的能力（不重做） | 距 2026-11-19 目标仍缺的行为 | 承接单元 |
| --- | --- | --- | --- | --- |
| `ev-task` | `5a6e0f8` | **已在 release，不重做**：`TaskRun` 五态表（`models.py:87-113`）、`_MOVES` 转移表与防回流 `running` 校验（`task_run.py:12-74`）、RLS 隔离 | 缺持久化步骤/事件表、缺进展时间列 `last_progress_at`、缺并发不杀旧任务默认行为（`begin_turn` 仍 `stop_others=True`） | `U3`、`U4` |
| `ev-gate` | `457cfe9` | **已在 release，不重做**：`ScheduleConfirmClaim`（`models.py:61-85`）、`(user_id, action, fingerprint)` 局部唯一索引、单次消费 `consume_schedule_claim`、读卡续发 `continue_from_card`（`schedule_confirm.py:229-410`） | 缺本机动作 `device_id` 绑定、缺按动作区分的 `unknown` 提示语（`schedule_confirm.py:46` 仍写死日历） | `U5`、`U7`、`U9` |
| `ev-plug` | `1e89297` | **已在 release，不重做**：三态副作用契约 `EFFECTS`/`decide_replay`（`effects.py:13-56`）、`execution_denial`（`execution.py:17-42`）、`arm_remote_claim`/`finish_confirm`（`schedule_confirm.py:413-469`） | 日历仅 `create` 有未知对账，update/delete/rsvp 缺对账；生产工具未构造 `UnknownWrite`；空 403 仍作明确拒绝 | `U5`、`U6`、`U7` |
| `ev-stop` | `8fc5ddd` | **已在 release，不重做**：`generation_stop.bind`/`is_stopped`（`generation_stop.py:22-35`）、请求前停发与停后不写记忆（`llm.py:399-408`、`chat.py:751-774`） | 断连即 `task_run.stop`（`chat.py:526-529,645-662,699-700`）需改为仅退订；`stream_chat` 提前返回可能冲出脱敏缓冲（`llm.py:489-494`、`chat.py:670-679`） | `U3` |
| `ev-dispatch` | `1d96a24` | **已在 release，不重做**：`assign_turn` 与 `TurnAssignment` 边界（`dispatch.py:46-109`），派活不调网关、不签发确认单 | 缺 SuperAgent 持续交互四类归类（补充/修改/新任务/取消）与目标任务关联，缺非阻塞即时回复 | `U3` |
| `ev-work` | `643b060` | **已在 release，不重做**：`admit`、`classify_tool_result`、`settle_unknown`（`workflow.py:290-351`）在执行前校验停止与允许列表 | 仍为服务器编排的固定三节点图，未接模型原生工具往返循环（`tools` + `reasoning_content` 回传 + 工具结果续写）；`reflect_node:526-528` 存在 `direct_answer` 短路 | `U2`、`U3` |
| `ev-net` | `b5a1fd4` | **已在 release，不重做**：保留单一网页搜索路径与 `ToolsRefused` 守门（`llm.py:244-255`），未合 PR 93 | 仍调用智谱 `/paas/v4/web_search`（`web_search.py:58-62,213-323`），未切到 DeepSeek 独立 Search/Fetch，缺本轮禁网硬约束 | `U2` |
| `ev-events` | `9d755c0` | **已在 release，不重做**：`EventReceipt` 表（`models.py:220-285`）、`(user_id, source, delivery_id)` 去重、噪声过滤、`waiting_person` 换卡一次（`event_inbox.py:86-355`） | 日历确认 HTTP 路径未调用 `note_external_success`（`calendar.py:498-501`）；R2 完成代际再开任务待结合多任务复核 | `U5`、`U3` |
| `ev-memory` | `bcf99b1` | **已在 release，不重做**：`MemoryLedger` 账本（`models.py:356-422`）、`decide_apply` 保护人写值、`completed` 才抽取、脉搏推送防重（`memory.py:273-286,657-664`） | R3 三处遗留细节（`list_memories:587`、`remember_from_turn:657` 省略 generation、`settle_pending:881` 放弃原地改行）按需收口 | 保留（R3 见第 3 节） |
| `ev-watch` | `92e34ad` | **已在 release，不重做**：`watch-task-runs` Celery beat 入口与 `_EFFECT` 保护非 `running` 状态（`watchdog.py:29-55`、`celery_app.py:50-54`） | 仍以 `created_at` 满 20 分钟直接强停；`load_runs` 全表扫 `TaskRun`；`SweepLedger` 在进程内存重启即丢；缺无进展先检查与去重报告 | `U4` |
| `ev-webblock` | `83245cd` | **已在 release，不重做**：`ServerSurface.WEB`、`surface_for_login()`、`local_tool_denial` 在网关执行前拒本地工具（`surface.py:6-23`、`gateway.py:161-178`） | `ToolContext.surface` 仍允许外部赋字符串字段（虽判定过 `coerce_surface`）；R5 边界审计待收口 | `U8` |
| `ev-mail` | `2b91764` | **已在 release，不重做**：入站直递建 `EventReceipt`、`propose_send`/`MailSendCard` 确认流、`mailto` 保持纯链接（`mail_entry.py:79-223`） | `commit_send`（`mail_entry.py:269`）用本地 `uuid4()` 伪造接受并写 `MailAcceptance`，未接真实 Gmail/SMTP，缺未知先查 | `U6` |
| `ev-errands` | `0cb2396` | **已在 release，不重做**：`ErrandOrder`/`Secret` 防凭证入单（`errand_order.py:14-110`）、连接器优先与 `errand-compose` 独立容器（`errands.py:262-308`）、`_reconcile` 未知不重下 | `server.py:15-27` `_public` 洗掉关联号；`errands.py:367-368` 对空回声的 `outcome=="placed"` 仍判成功；Browser Use 候选未做核对关口 | `U7` |
| `ev-desktop` | `d7710d6` | **已在 release，不重做**：Electron 壳与 `desktop/executor.py` `perform`（仅 `kind=="armed"` 执行，`shell=False`）、`/api/desktop/*` 路由、`PackageProof` 拒绝字符串伪造 | 仍用单枚共享 `DESKTOP_PACKAGE_TOKEN`（`desktop_channel.py:30-41`）；安装包仅两个固定探测按钮（`preload.js:3-29`、`confirm_local.py:11-14`）；完成后未回原任务继续 | `U8`、`U9` |

## 2. 主线 16 项逐项映射（M1–M6、T1–T5、X1–X5）

| 编号 | 2026-11-19 目标 | 当前代码（路径:行） | 缺口 | 改动与目标单元 |
| --- | --- | --- | --- | --- |
| **M1** | Flash 回答 + DeepSeek 独立 Search/Fetch；默认具备搜索能力，无地球按钮，不升到 Pro，PR 93 不合 | `backend/app/services/web_search.py:58-62,213-323`（调智谱 `/web_search`）；`backend/app/agents/tools/web.py:10-47`；`backend/app/core/config.py:135-138` | 搜索仍连智谱；无独立 `fetch` 客户端与工具；搜索仍由静态路由前置执行而非 Flash 自主决策调用 | **`U2`**：将 `web_search.py` 改为调用 `POST {LLM_BASE_URL}/v1/tools/search` 与 `/v1/tools/fetch`（按已核实字段）；注册 `web_search` 与 `web_fetch` 函数工具供 Flash 自主调用；更新 `ev-net` 锁定测试与锚点 |
| **M2** | 正式后台执行接完整模型工具往返循环（选工具 -> 存消息 -> 校验执行 -> 存结果 -> 回填续写），工具轮完整回传 `reasoning_content`，普通轮不回放，确认写前停住 | `backend/app/core/llm.py:244-269,272-295,411-501`（`_refuse_outbound` 拒工具/思考，`_wire_messages` 压扁字段）；`backend/app/agents/workflow.py:388-874`；`backend/app/agents/tools/registry.py:11-18` | `_refuse_outbound` 阻止生产带工具请求；`ToolSpec` 无参数 JSON Schema；无「先落库 assistant(tool_calls+reasoning_content) 再执行工具」循环；`tool_choice=required`/指定名在思考模式下未防护 400 | **`U2`**：在 `llm.py` 新增受控工具往返入口（保留非工具入口拒绝策略）；按 `thinking_mode.md` 在含 `tools` 的轨迹完整回传 `reasoning_content`；`ToolSpec` 补 Schema；后台执行循环改为持久化步骤后经 `gateway.invoke` 执行并回填 `role="tool"` |
| **M3** | 按任务角色分别配置模型/参数/上下文/用量；聊天原图直接交给 Flash（不先交智谱转文字）；真实记录用量，缺失标未知不填零 | `backend/app/core/config.py:21-33,87-105`；`backend/app/core/llm.py:134-219,258-295,356-376,460-475`；`backend/app/api/chat.py:54-59` | 仅 4 角色模型与思考开关，缺角色级可选参数槽；`_wire_messages` 强转 `str` 无法传 `image_url` 多模态数组；`ChatRequest` 无图片输入；用量仅放进程 `_STATS` 且无缓存/推理 token 与 `unknown` 标记 | **`U1`**：扩展 `RoleSpec`/`RoleCall` 支持角色级可选参数（未填省略走供应商默认，不编造数值）；`_wire_messages` 支持 `text`+`image_url` 数组（按 `vision.md`）；聊天上传图片直传 Flash（与资料入库 OCR 分路）；新增 `llm_usage_records` 持久化真实 `CompletionUsage` 与 `usage_status` |
| **M4** | 只替换智谱网页搜索并清死引用；智谱嵌入、重排、入库识图保留不动 | `backend/app/core/config.py:34-40,123-127,135-138,222-228`；`backend/app/services/web_search.py:1-407` | 智谱网页搜索代码与 `ZhipuService.WEB` / `WEB_SEARCH_*` 仍在 | **`U2`**（清理智谱网页搜索与 `ZhipuService.WEB`）+ **`U1`**（明确保护 `EMBEDDING`/`VISION`/`RERANK` 零改动回归验收） |
| **M5** | 顺手维护 `.env.example` 与 `config.py` 默认说明一致，写清覆盖规则，不迁索引 | `.env.example:25,31,109,156-160`；`backend/app/core/config.py:36,39,130-133,164-167` | `.env.example` 写 `embedding-3-pro`/`glm-4v-flash` 与 `config.py` 默认 `embedding-3`/`glm-4.6v` 不一致；`config.py` 有重复的 `tabular_*` 字段 | **`U1`**：对齐 `.env.example` 注释与默认说明、去重 `config.py` 重复字段定义；不改现有默认模型名、不迁向量索引 |
| **M6** | 「这次不要联网」作为该轮硬约束：本轮不向模型暴露搜索工具且执行层拒绝搜索，下轮恢复默认；无常驻联网开关 | `backend/app/agents/router.py:82-102`；`backend/app/agents/harness/execution.py:17-42`；`backend/app/services/task_run.py:207-216` | 无该轮禁网约束表示；允许列表仅从全局 constitution 快照，未在出站 `tools` 与执行闸同步剔除搜索工具 | **`U2`**：在任务约束/本轮 `allowed_tools` 表达 `no_web`（单轮有效，由派活/任务上下文结构化传递，非全量关键词硬路由）；构造模型 `tools` 时剔除 `web_search`/`web_fetch`，且 `execution_denial` 对该轮双保险拒绝 |
| **T1** | SuperAgent 持续交互派活与后台干活分离；进行中可继续聊；新消息不自动杀旧任务；去掉常驻停止按钮；明确文字取消仍有效 | `backend/app/services/task_run.py:187-219,262-279`（`begin_turn` 默认 `stop_others=True`）；`backend/app/api/chat.py:185-188,429`；`frontend/src/components/ChatWindow.tsx:24,47-55,140-148`；`frontend/src/services/chatStream.ts:64-73,286-298` | 前端 `busy` 拦发且展示常驻「停止生成」按钮；后端 `begin_turn` 自动停旧普通回合、`void_draft_schedule_cards` 清旧草稿卡；执行与 SSE 请求绑死 | **`U3`**：后端 `begin_turn` 取消默认停旧任务、不再粗暴作废无关任务草稿卡；交互派活与独立后台执行者解耦；前端移除常驻停止按钮和 `busy` 发送锁，支持任意时刻继续对话 |
| **T2** | 持续交互派活层按上下文分清补充、修改、新任务、取消并关联正确任务；只在确有实际后果的歧义时追问；取消不误伤其他任务或卡；写操作确认仍在后台执行层 | `backend/app/agents/dispatch.py:46-109`；`backend/app/services/task_run.py:200-206`（按 `card_action` 局部豁免，普通 `waiting_person` 仍会被停）；`backend/app/api/chat.py:429` | 无四类交互判定器与目标任务绑定；无文字取消入口；普通 `waiting_person` 仍被新消息停掉 | **`U3`**：在 `dispatch.py` 实现 `TurnDirective`（`supplement`/`modify`/`new_task`/`cancel`/`clarify`）与目标 `task_id` 绑定；统一 `waiting_person` 不被无关新话中止；文字取消带明确作用域并在后台执行层执行停止，歧义影响动作时返回简短追问 |
| **T3** | 任务/步骤/工具结果/确认/状态持久化在服务端数据库；关闭网页或断网不取消；重连读服务端状态；具体重启恢复步骤；旧代/已取消任务迟到结果不得覆盖新状态；诚实记录用量 | `backend/app/api/chat.py:526-529,645-682,699-707`（断连即 `task_run.stop`）；`backend/app/models/models.py:96-113`（无步骤/事件/租约表）；`backend/app/core/llm.py:489-494` | 断线取消后台任务；无持久化步骤与事件日志；无服务端重启恢复机制；缺多订阅者 SSE 流；`stream_chat` 提前返回可能冲出脱敏缓冲 | **`U3`**：新增 `task_steps`、`task_events`、`task_inbox_messages` 表与 `TaskRun` 租约列；后台执行转由独立工作器驱动并落盘每步状态；SSE 断连只做 `detach` 不停任务；重连按 `after_event_id` 补放状态；启动/维护扫描过期租约执行确定性恢复；代际栅栏丢弃迟到片段并禁止覆盖新状态 |
| **T4** | 按最近真实输出或工具进展计时；不按创建满 20 分钟强停；无进展先检查并去重报告；等人确认与外部等待分开记录；明确失败或用户取消再结束 | `backend/app/tasks/watchdog.py:16-35,62-77,99-164`；`backend/app/models/models.py:96-113` | 时钟用 `TaskRun.created_at` 满 20 分钟强停；`load_runs` 全表扫；`SweepLedger` 在进程内存中重启即丢；无检查报告去重机制；未细分等待类型 | **`U4`**：`TaskRun` 增加 `last_progress_at`、`waiting_kind`、`stall_reported_at`、`last_checked_at`；看门狗改为索引过滤查询与持久化 `watchdog_state`；无进展先检查并向聊天发一条去重进展报告，不再按创建时间强停；具体间隔做成配置待定项 |
| **T5** | 同会话多窗口共享服务端权威状态；不互相打断；过期确认拒绝、同操作单次执行；单窗断线不影响另一窗 | `frontend/src/store/useStore.ts:17-26`；`frontend/src/services/chatStream.ts:9,24-26,155-298`；`backend/app/api/chat.py:429` | 忙碌状态仅存浏览器内存；第二窗口发消息停掉第一窗口任务；无多订阅者共享事件流 | **`U3`**：前端订阅会话级服务端事件流并按权威状态渲染任务列表/进度/待确认卡；多窗口发消息统一经派活层路由；确认单消费继续依赖数据库行锁与局部唯一索引保证至多一次执行 |
| **X1** | 真实邮件服务接受凭证之后才能称已发送（接受 ≠ 对方已收到）；强制确认；未知结果先查不重发；明确记录丢响应对账缺口 | `backend/app/services/mail_entry.py:226-273,397-461`；`backend/app/services/calendar_secrets.py:41`；`backend/app/services/gmail.py:22,57-85` | `commit_send:269` 用本地 `uuid4()` 写 `MailAcceptance` 冒充发送；`commit_send:258-264` 对 `unknown` 直接重入 `_accept`；缺真实 Gmail/SMTP 传输和按关联头查证逻辑 | **`U6`**：引入 `MailTransport` 抽象与真实传输适配（Gmail API 或 SMTP，待 `open.md` 定选），发送前落盘 `client_message_id` 武装号，仅在拿到服务商真实接受凭据后写 `MailAcceptance` 与 `note_external_success`；超时转 `unknown` 后仅查不重发，SMTP 无远程查单能力时显式保持 `unknown` 并报缺口 |
| **X2** | 日历真实写入返回 event ID 后写 `external_success` 回执；同一确认不重复建事件；重入按 ID 查证，补齐 update/delete/rsvp 未知对账 | `backend/app/api/calendar.py:189-442,498-559,652-722`；`backend/app/services/calendar_google.py:95-126,1234-1380`；`backend/app/services/event_inbox.py:164-198` | `calendar.py` 全部确认路由只调 `finish_confirm`，从未调 `note_external_success`；update/delete/rsvp 无未知对账；空 403 误当明确拒绝；`FollowupCard` 根 phase 刷新后可能误显可点 | **`U5`**：在 `calendar.py` 全部确认写入成功拿到真实 `external_id` 后绑定并调用 `note_external_success`；扩展 `ScheduleConfirmClaim` 记录 `provider_event_id`；补齐 update/delete/rsvp 未知对账；空体 403 改按未知处理；修复 `FollowupCard` 逐项 phase 展示 |
| **X3** | 订单操作关联号贯穿请求与回包，服务返回商家订单号便于查单；敏感凭证持续过滤；未知只查不重下；连接器优先；浏览器后端 `Hermes + Browser Use` 保持候选并立验证关口 | `backend/app/services/errands.py:131-171,225-377`；`backend/app/services/errand_order.py:5-110`；`services/errand-compose/server.py:15-27`；`services/errand-compose/driver.py:12-60`；`services/errand-compose/decide.py:15-43` | `_public` 洗掉关联号；`interpret:367-368` 允许 `outcome=="placed"` 在空回声下落单；`CONFIRM_DETAIL["unknown"]` 仍说核对日历；Browser Use 未经 Flash 兼容与写前拦截核对 | **`U7`**：Part A 立即落地关联句柄（`correlation_id` = `sha256(idempotency_key)` 或非敏感关联号）贯通 `_public`/`decide`/`interpret`，严格要求 `placed`/`already` 同时具备匹配关联句柄与非空 `order_id`，补按动作分流的未知文案；Part B 设立 `Hermes + Browser Use` 4 项验证关口（不锁依赖、不新加服务） |
| **X4** | 后台规划具体本机步骤 -> 生成绑定设备/文件/参数的确认卡 -> 单次确认消费执行一次 -> 结果回同一后台任务继续下一步；参数变了重确认；禁自动允许 | `backend/app/services/local_action.py:90-238`；`backend/app/api/desktop.py:49-111`；`desktop/preload.js:3-29`；`desktop/confirm_local.py:11-37,80-115`；`desktop/executor.py:6-30`；`frontend/src/components/ChatWindow.tsx:238` | 安装包仅固定探测按钮（`/tmp/ev-desktop-probe.txt`、`/usr/bin/true`）；后台执行循环无法产出步骤级本机确认卡；`finish_local` 未回填 `TaskStep` 或唤醒原任务；确认单未绑定 `device_id` | **`U9`**：在 `write_parts` 为 `desktop.open`/`desktop.run` 纳入 `device_id`；后台执行者在安装包会话中产出真实步骤动作卡并挂起为 `waiting_person`；Electron 端按卡渲染设备/目标/参数并逐次确认执行；`finish_local` 持久化步骤结果并唤醒同一 `TaskRun` 继续下一步；严禁任何自动允许或记住规则 |
| **X5** | 每台桌面设备单独登记、独立凭证、只存 SHA-256 哈希、恒定时间比对、按设备 ID 单独撤销；网页端自报 desktop 继续拒绝；凭证不代替动作确认卡 | `backend/app/agents/harness/desktop_channel.py:10-41`；`backend/app/core/config.py:186`；`backend/app/api/desktop.py:34-43`；`backend/app/agents/harness/surface.py:6-23` | 仅单枚共享 `DESKTOP_PACKAGE_TOKEN`；无法单台撤销；无设备表与管理接口 | **`U8`**：新增 `desktop_devices` 表（仅存 `token_hash`、RLS 隔离）；`recognize_package` 改查激活设备表并用 `hmac.compare_digest` 比对 SHA-256 哈希，返回带 `device_id`/`user_id` 的不可伪造 `PackageProof`；新增设备登记/列表（不含哈希）/单设备撤销 API 与设置页；保持网页端拒绝本地工具 |

## 3. 历史项（R-B3、R1–R7）源码复核与去向

| 历史编号 | 原编号 | 本轮核对状态（附实读代码行） | 结论与去向 |
| --- | --- | --- | --- |
| **R-B3** | B3 偶发 SSE | **本轮已重读源码**：`backend/app/core/llm.py:489-494`（`is_stopped()` 时 `return` 而非抛异常）与 `backend/app/api/chat.py:644-679,691-696,830`（`leave_stream` 保持 `False` 导致 `reason_buf.finish()`/`answer_buf.finish()` 冲出，且停后仍推断发 `artifact` 和固定发 `done`） | **并入 `U3` 修复**：停止检查改为显式代际栅栏，停止后丢弃全部未发送缓冲片段，禁止发送迟到 `artifact` 或把已取消回合继续推给客户端 |
| **R1** | D1 | **验证脚本正文未重读**；通过全部 `ev-*.md` 确认 10+ 个 `verification/*/check.py` 均未进 GitHub Actions，`.gitignore` 缺 `*_TTrace_*` | **作为跨单元工程纪律落实**：每单元新增或更新的 `verification/<name>/check.py` 同步纳入 CI 工作流与 `.gitignore`；历史旧验证脚本补进 CI 单独列出，不阻塞单刀功能设计 |
| **R2** | D2 | **本轮已重读源码**：`backend/app/services/event_inbox.py:239-248,420-458` 确认点名 `completed` 代际时，非邮件事件调 `_open_task -> open_alongside` 新开 `running`，邮件来源记 `received`/`filed` | **记录在 `open.md` 第 6 节并与 `U3` 协同审视**：在多任务 SuperAgent 模型下这是产品语义取舍而非直接缺陷，默认保留现行行为，待操作者定夺是否改由派活层评估 |
| **R3** | D3 | **本轮已重读源码**：`backend/app/services/memory.py:587`（`uid = int(user_id or 1)`）、`memory.py:657-664`（省略 `generation` 仍抽取）、`memory.py:881-886`（`discarded` 原地改写原账本行，仅 `restored` 追加新行） | **记录在 `open.md` 第 6 节**：确认三处行为在 `release` 均存在；不占用核心主线，可在 `U3` 顺手或独立小维护 PR 收口（`list_memories` 拒绝 `<1`、强制要求 `generation`、决定放弃是否改为追加行） |
| **R4** | D4 其余 | **本轮已重读源码**：`backend/app/tasks/watchdog.py:122-138`（`db.query(TaskRun).all()` 全表扫）与 `watchdog.py:158-164`（进程全局 `_ledger` 重启即丢） | **并入 `U4` 完整解决**：改为索引过滤 SQL 查询并将看门狗游标/休眠抵扣/提醒记录持久化到 Postgres |
| **R5** | D5 | **本轮已重读核心源码**：`surface.py:6-23`、`types.py:13-31`（`ToolContext` 非 frozen，允许外部给 `surface` 赋字符串）、`gateway.py:161-178`（均经 `coerce_surface`）、`workflow.py:877-881`（`route_intent` 无生产调用方）；边界测试文件未重读 | **并入 `U8` 收口**：将 `ToolContext.surface` 收紧为只接受 `ServerSurface` 的属性/校验，核查全部直接构造点并清理或标记 `route_intent` |
| **R6** | D6 | 非代码缺陷（旧流程图表达、分支回结束、字体一致性） | **保留为文档卫生项**：不覆盖桌面 01–14 已审查流程图文件夹，新设计图仅作目标讨论与各刀评审附图 |
| **R7** | D7 | **本轮已重读源码**：`calendar_google.py:95-126`（空 403 仍作明确拒绝）、`calendar.py:519-559`（仅 create 有未知对账）、`workflow.py:314-351`（`UnknownWrite` 仅测试构造）、`chat.py:560-588`（确认单签发失败丢卡但未清 `direct_answer`） | **拆分并入 `U2`/`U3`/`U5`/`U6`/`U7`**：`U5` 处理日历空 403 与 update/delete/rsvp 对账；`U2`/`U3` 修复确认单签发失败时同步撤回确认句并打通工具层 `UnknownWrite`；`U6`/`U7` 各自守住邮件与订单未知态 |

## 4. 原 27 项去向总览（追溯方案页 MAP 表）

| 原编号 | 新主线/历史编号 | 承接实施单元 | 状态说明 |
| --- | --- | --- | --- |
| A1 | M1 | `U2` | 已定案 Flash + 独立 Search/Fetch；PR 93 不合 |
| A2 | M2 | `U2` | 生产后台工具往返循环 |
| A3 | M3 | `U1` | 已定案分任务模型/参数/用量；具体默认数值不编造 |
| A4 | M2；合理现状 | `U2` | 工具轮完整回传 `reasoning_content`；普通轮不回放保持现状 |
| A5 | M3；合理现状 | `U1` | 32000/十轮为应用裁剪；聊天原图直传 Flash 已定，入库摘要另路保留 |
| A6 | M5 | `U1` | 模板差异顺手维护，不据此迁索引或推断生产配置 |
| A7 | M4 | `U2`（清搜索）、`U1`（保资料链） | 只换网页搜索，智谱嵌入/重排/入库识图保留 |
| A8 | M6 | `U2` | 正常工具约束规则，无常驻联网开关 |
| B1 | T1 | `U3` | 沿用 SuperAgent 交互派活与后台执行分离 |
| B2 | T2 | `U3` | 四类消息判定、目标关联、歧义追问与文字取消 |
| B3 | T3；R-B3 | `U3` | 服务端持久化、断线继续、重启恢复、缓冲栅栏 |
| B4 | T5 | `U3` | 同会话多窗口共享权威状态、不互相打断 |
| B5 | T4 | `U4` | 按最近真实进展看护任务、去重报告 |
| C1 | X2 | `U5` | 日历真实事件 ID 写 `external_success` 与幂等对账 |
| C2 | X1 | `U6` | 真实邮件服务接受凭据与未知查证 |
| C3 | X3 | `U7` | 订单关联号贯通回包 + 商家单号对账 + Browser Use 核对关口 |
| C4 | X4 | `U9` | 本机步骤动态动作卡、确认一次执行一次并回原任务 |
| C5 | X5 | `U8` | 单设备独立登记、SHA-256 哈希凭证与按 ID 撤销 |
| D1 | R1 | 各单元 + CI | 形式检查进 Actions 与 `.gitignore` 清理 |
| D2 | R2 | `U3` / `open.md` | 已复核源码，留产品语义确认 |
| D3 | R3 | `open.md` / 维护 | 已复核三处源码，低优收口 |
| D4 | T4；R4 | `U4` | 时钟改为进展时间、扫全表改索引、看门狗状态落库 |
| D5 | R5 | `U8` | 执行面类型收紧与 `route_intent` 清理 |
| D6 | R6 | 文档卫生 | 图表达问题，不改旧 01–14 文件夹 |
| D7 | R7 | `U2`/`U3`/`U5`/`U6`/`U7` | 空 403、确认句泄露、未知写路径逐项落地 |
| E1 | 工作约定 | 全部单元 | 一次一刀、单测+形式检查+状态图+独立审查，操作者说「合」才合并，不部署 |
| E2 | 验证范围 | 全部单元 + 终期活体 | 每刀自备单测、形式检查与独立运行验收场景；全产品活体端到端留整体落地后统一执行 |
