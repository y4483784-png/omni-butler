# 现状源码与官方文档核对索引（citations）

本文件记录本次设计直接打开并读到函数体的本仓库代码锚点、官方接口文档字段核对结果、审查文档与代码的核对差异，以及未重读文件清单。未打开的文件一律标为「未重读」，不以旧计划或方案页转述替代。

基线仓库：`/agent/repos/omni-butler`，分支 `release`，HEAD `d7710d687d147aeeda502d412454ab8a3829af66`。

## 1. 本仓库已读源码锚点（按模块分组）

### 1.1 模型调用、角色配置与模板

- `backend/app/core/config.py:21-33`：定义 `llm_api_key`、`llm_base_url`（默认 `https://api.deepseek.com`）、`llm_timeout`（`120.0`）、`llm_max_retries`（`3`），以及四个角色的模型与思考开关：`chat_model="deepseek-flash"`/`chat_thinking="on"`、`planner_model="deepseek-flash"`/`planner_thinking="off"`、`router_model="deepseek-flash"`/`router_thinking="off"`、`judge_model="deepseek-v4-pro"`/`judge_thinking="on"`、`router_max_attempts=1`。
- `backend/app/core/config.py:34-40`：智谱资料链默认配置：`embedding_model="embedding-3"`、`vision_model="glm-4.6v"`、`vision_ocr_enabled=True`。
- `backend/app/core/config.py:87-105`：`max_context_turns=10`、`context_max_tokens=32000`（注释明确为软预算而非模型物理窗口）、`context_cjk_chars_per_token=1.6`。
- `backend/app/core/config.py:123-127`：`rag_rerank_enabled=True`、`rag_rerank_provider="zhipu"`、`rerank_model="rerank"`。
- `backend/app/core/config.py:130-133` 与 `164-167`：`tabular_max_rows`/`tabular_max_chars`/`tabular_batch_rows` 字段在 `Settings` 中重复定义一次。
- `backend/app/core/config.py:135-138`：现行网页搜索配置指向智谱：`web_search_base_url="https://open.bigmodel.cn/api/paas/v4/"`、`web_search_api_key=""`、`web_search_engine="search_pro_quark"`、`web_search_count=5`。
- `backend/app/core/config.py:182-186`：`errand_vault_key`、`errand_compose_url`、`errand_compose_token`、`errand_http_timeout=10.0`、`desktop_package_token`（别名 `DESKTOP_PACKAGE_TOKEN`）。
- `backend/app/core/config.py:222-228` 与 `277-284`：`ZhipuService` 包含 `EMBEDDING`、`VISION`、`RERANK`、`WEB`；`check_zhipu_endpoints` 在启动时校验启用中的智谱服务（`WEB` 不阻塞启动）。
- `.env.example:25,31,109,156-160`：示例写 `EMBEDDING_MODEL=embedding-3-pro`、`VISION_MODEL=glm-4v-flash`（与 `config.py:36,39` 默认不同）；第 109 行并发名单仍列 `embedding-3-pro`、`glm-4v-flash`、`web-search=10`；第 156-160 行仍为智谱 `WEB_SEARCH_*`。
- `backend/app/core/llm.py:130-165`：定义 `ToolsRefused`、`Role`（`CHAT`/`PLANNER`/`ROUTER`/`JUDGE`）、`RoleSpec`、`ROLE_SPECS`、`RoleCall`。
- `backend/app/core/llm.py:232-241`：`message_parts` 读取响应消息或增量里的 `content` 与 `reasoning_content`（兼容 `model_extra`）。
- `backend/app/core/llm.py:244-255`：`_refuse_outbound` 显式拒绝 `tools is not None` 或消息中含 `reasoning_content`、`role=="tool"`、`tool_calls` 的任何出站请求。
- `backend/app/core/llm.py:258-269`：`_wire_messages` 将所有消息的 `content` 强制转为 `str`，既剥离工具字段也剥离多模态数组（图片块）。
- `backend/app/core/llm.py:272-295`：`_build_request` 只设置 `model`、`messages`、`stream`、`extra_body={"thinking": ...}`、`reasoning_effort`，JSON 非思考模式额外设 `temperature=0`；未支持各角色独立的 `max_tokens`/`top_p`/`temperature` 与工具定义。
- `backend/app/core/llm.py:356-376`：`_usage_prompt_tokens` 与 `_record_call` 仅将调用次数与 `prompt_tokens` 累加在进程内存 `_STATS` 中，未落库，也未记录缓存命中、推理 token 或缺失状态。
- `backend/app/core/llm.py:411-501`：`stream_chat` 在 `usage_out` 非空时传 `stream_options={"include_usage": True}` 并解析 `prompt_tokens`/`completion_tokens`；但在 `489-494` 行遇 `is_stopped()` 时直接 `return` 而非抛出 `GenerationStopped`。
- `backend/app/core/llm.py:504-789`：`complete_text`、`complete_json`、`complete_json_object` 均先调 `_refuse_outbound` 且不落库用量。

### 1.2 联网搜索、工具注册、路由派活与后台工作图

- `backend/app/services/web_search.py:58-62,213-323,342-395`：`_web_search_url` 拼接智谱 `/web_search`；`search_api` 按智谱字段（`search_query`、`search_engine`、`search_intent`、`count`、`content_size`、`search_recency_filter`）发请求；`search_planned` 串联改写与搜索；无独立 `fetch` 客户端。
- `backend/app/agents/tools/registry.py:11-43`：`ToolSpec` 含 `name`、`risk`、`execute`、`effect`、`description`、`local=False`，缺少供模型调用的函数参数 JSON Schema；`ensure_builtin_tools` 仅注册 `calendar`、`kb`、`sandbox`、`web`。
- `backend/app/agents/tools/web.py:10-47`：`execute_web` 调 `search_planned` 并以 `name="web"`、`effect="read"` 注册。
- `backend/app/agents/tools/kb.py:10-46`、`calendar_tool.py:836-973`、`sandbox_tool.py:11-77`：现有三个内置工具均注册为 `effect="read"`；`calendar_tool` 只产草稿卡，不落库写入。
- `backend/app/agents/router.py:50-102,119-149,255-350`：`route` 与 `plan_tools` 通过 `_tier0_forced`、`_tier1_certain`（短闲聊、文档指代）和 `_llm_decide` 产出静态工具队列；`_decision_to_plan` 仅按布尔开关排 `kb`/`web`/`calendar`/`sandbox`，无本轮禁网约束传递。
- `backend/app/agents/dispatch.py:46-109`：`TurnAssignment` 与 `assign_turn` 只包装 `plan_tools`，不含四类消息意图分类（补充/修改/新任务/取消）和目标任务绑定。
- `backend/app/agents/workflow.py:290-385,388-874`：`run_agent_workflow` 运行 LangGraph 三节点（`retrieve_node` -> `reflect_node` -> `answer_node`），由服务器按 `assignment` 依次调 `gateway.invoke`，不把 `tools` 与工具结果回传模型续跑；`admit` 检查 `is_stopped()` 与 `execution_denial`；`settle_unknown`（`330-351`）调用 `decide_replay` 和 `lookup_inserted_event` 但不保存查到的事件；`reflect_node`（`526-528`）在 `direct_answer` 非空时短路后续工具；`route_intent` 定义在 `877-881` 行，无生产调用方。
- `backend/app/agents/harness/gateway.py:132-341`：`invoke` 依次执行空名拒绝、`local_tool_denial`（`161-178`）、AutoHarness 预检、本地风险/白名单、`execution_denial`（`253-273`）、`effect_denial`（`275-291`）、`spec.execute`、证据清洗与审计落盘。
- `backend/app/agents/harness/execution.py:9-42`：`effect_denial` 校验契约；`execution_denial` 在 `task_id is None` 时直接放行，否则查 `TaskRun` 状态（`stopped` 拒绝）、卡片标记拒绝及 `allowed_tools` 白名单拒绝。
- `backend/app/agents/harness/effects.py:13-56`：定义 `EFFECTS`（`read`/`idempotent_write`/`non_replayable_write`）、`decide_replay`、`call_effect`。
- `backend/app/agents/harness/types.py:13-74`：`ToolContext`（非 frozen，含 `surface: ServerSurface = ServerSurface.WEB`）、`UnknownWrite`、`ToolResult`。
- `backend/app/agents/harness/constitution.py:13-102` 与 `constitution.yaml:84-125`：`allowed_tools` 固定为 `kb`/`web`/`calendar`/`sandbox`，`tool_effect` 均为 `read`。

### 1.3 任务生命周期、聊天流、断线停止、看门狗与前端

- `backend/app/models/models.py:61-113`：`ScheduleConfirmClaim`（含局部唯一索引 `uq_schedule_confirm_open_write` 覆盖 `status IN ('open', 'unknown')`、`remote_request_id`）与 `TaskRun`（`TASK_RUN_STATUSES` 五态，仅存 `id`/`user_id`/`session_id`/`generation`/`status`/`allowed_tools`/`created_at`，无进展时间戳、步骤表、事件流表、租约或约束列）。
- `backend/app/services/task_run.py:12-74`：`_MOVES` 显式禁止任何边回到 `running`，`completed` 收到 `stop` 保持 `completed`。
- `backend/app/services/task_run.py:187-279`：`_insert_running` 默认 `stop_others=True`，导致 `begin_turn` 每次新发言自动把同会话里所有非 `card_action` 的开放代际（含普通 `running`/`waiting_person`/`waiting_resource`）置为 `stopped`；`open_alongside` 传 `stop_others=False`。
- `backend/app/core/generation_stop.py:10-35`：`bind`/`reset`/`is_stopped` 通过 `ContextVar` 每次重查数据库判断当前代际是否为 `stopped`。
- `backend/app/api/chat.py:54-59`：`ChatRequest` 只有 `session_id`、`message`、`use_kb`、`document_ids`、`regenerate_message_id`，不含聊天图片附件，不含客户端执行面字段。
- `backend/app/api/chat.py:185-188`：每次新消息无条件调用 `void_draft_schedule_cards(db, session.id)` 作废草稿卡。
- `backend/app/api/chat.py:429`：调用 `task_run.begin_turn` 停掉同会话旧普通回合。
- `backend/app/api/chat.py:493-834`：`agent_event_gen` 把后台工作与 HTTP SSE 响应绑死：`526-529`、`590-591`、`645-662`、`681-682`、`699-707` 遇客户端断连或 `CancelledError` 即调用 `task_run.stop`；`560-588` 签发确认单失败时仅置 `schedule_card=None` 而未清 `direct_answer`；`670-679` 当 `stream_chat` 因 `is_stopped()` 提前 `return` 时 `leave_stream` 仍为 `False`，会冲出 `reason_buf.finish()` 与 `answer_buf.finish()`（验证 R-B3 源码路径）；`691-696` 与 `830` 在停止后仍可能推断发 `artifact` 并固定发 `done`。
- `backend/app/tasks/watchdog.py:16-35,62-77,99-164`：`ORDINARY_STALL=20m`，`_silence` 按 `now - created_at - credit` 计算，无进展检查与去重提醒，直接对 `running` 调 `task_run.stop`；`load_runs`（`125`）全表查 `db.query(TaskRun).all()`；`_ledger`（`158`）存在进程内存中，重启即丢且不跨 worker 共享。
- `backend/app/tasks/celery_app.py:12-56`：定义 `default`/`ingest`/`maintenance` 队列与 3 条 beat 计划（`recover-stale-document-ingests`、`memory-schedule-pulse`、`watch-task-runs`）。
- `frontend/src/components/ChatWindow.tsx:24,47-55,110,121-148,238`：本地 `busy` 阻止忙时发送和 `onAsk`；忙时将发送按钮替换为常驻「停止生成」按钮并调用 `stopChatStream`；`238` 行过滤掉 `mode === "desktop"` 卡片不渲染。
- `frontend/src/store/useStore.ts:17-26`：`busy`、`thinking`、`thinkingSteps` 仅存浏览器内存。
- `frontend/src/services/chatStream.ts:9,24-26,64-95,155-298`：`abortControllers` 按会话保存 `AbortController`，`stopChatStream` 仅中止 fetch；`voidOpenDrafts`（`64-73`）发新消息即本地作废旧草稿；`startChatStream` 在 `busy` 时直接返回。
- `frontend/src/api/client.ts:300-365`：`streamChat` 只支持单轮 `POST /api/chat` 流式消费，无服务端任务订阅/事件重放接口。

### 1.4 确认闸、事件收件箱、邮件、日历、办事、记忆与桌面通道

- `backend/app/services/schedule_confirm.py:24-49,95-177,347-469,696-750`：统一确认单服务。`CONFIRM_DETAIL["unknown"]`（`46`）写死日历文案；`write_parts`/`write_scope` 覆盖日历、`mail.send`、`errand.place`、`desktop.open`/`desktop.run`（本机动作未绑定 `device_id`）；`consume_schedule_claim` 将 `open` 原子改为 `unknown`；`arm_remote_claim` 提交 `remote_request_id` 后才发起外部请求；`finish_confirm` 封口为 `written`。
- `backend/app/services/event_inbox.py:86-276,420-458`：`submit_event` 按 `(user_id, source, delivery_id)` 去重；`note_external_success`（`164-198`）校验 `ScheduleConfirmClaim.status == "written"` 且 `remote_request_id` 一致；`_route_act`（`239-248`）对点名 `completed` 代际走 `_open_task`（`430-458`），其中 `source == "mail"` 仅记 `received`/`filed`，非邮件调 `open_alongside`（核实 R2）。
- `backend/app/api/events.py:42-82`：`POST /api/events/inbox` 对 `source=="mail"` 调 `mail_entry.receive`（忽略入参 `decision`，固定按 `act` 递交）。
- `backend/app/services/mail_entry.py:143-273,397-461`：`propose_send` 签发 `mail.send` 确认单并建 `out:<fingerprint>` 回执；`commit_send` 在 `269` 行本地生成 `uuid4()`，写入 `MailAcceptance` 后调 `finish_confirm` 与 `note_external_success`，未接任何真实邮件传输（Gmail/SMTP）；`258-264` 行对已武装 `unknown` 状态直接重入 `_accept`，接入真实网络后必须改为查询而非重发。
- `backend/app/services/gmail.py:22,57-85` 与 `backend/app/services/calendar_secrets.py:41`：`GOOGLE_MAIL_SCOPES` 仅有 `gmail.readonly`，`search_meeting_mail` 只做会前只读检索。
- `backend/app/api/calendar.py:189-442,498-559,652-722`：`create_calendar_event`、`patch_event`、`delete_event`、`rsvp_google_event`、`confirm_google_title_change` 与 `_keep_found_create` 成功后只调 `_seal_schedule_write` -> `finish_confirm`，全部未调用 `event_inbox.note_external_success`；且仅 `create` 有 `_keep_found_create` 未知对账，update/delete/rsvp 无对应对账。
- `backend/app/services/calendar_google.py:95-126,1180-1202,1234-1380,1430-1448`：`_write_result_unknown` 将空体 `403` 视为明确拒绝（`unknown=False`）；`_insert_google_event` 写入 `extendedProperties.private.requestId` 并先调 `_arm_user_write(request_id)`，`lookup_inserted_event` 按 `privateExtendedProperty=requestId=...` GET 查回；`_patch_google_event` 和 `_delete_google_event_required` 武装 `event_id`。
- `backend/app/services/errands.py:131-171,200-377`：`commit_errand` 武装 `uuid4()`，通过 `Idempotency-Key` 发给连接器或 `errand-compose /tasks`；`_reconcile` 走 `decide_replay("non_replayable_write", ...)` 与 `_lookup`，不重下；`interpret`（`367-368`）在 `outcome == "placed"` 且 `echoed_key` 为空时仍返回 `named=True`。
- `backend/app/services/errand_order.py:5-31,49-110`：`_FORBIDDEN`、`Secret` 掩码类、`ErrandOrder.parse` 拒绝凭证字段入单。
- `services/errand-compose/server.py:15-27,57-79`：`_public` 只回 `outcome`/`order_id`/`told`/`clicks_after_wall`，洗掉了请求里的 `idempotency_key` 关联号。
- `services/errand-compose/driver.py:12-60` 与 `decide.py:15-43`：基于 Playwright sync API（Chromium）实现固定选择器填单与判定，非自治浏览器代理。
- `backend/app/agents/harness/surface.py:6-23`：`ServerSurface` 仅含 `WEB`；`surface_for_login()` 恒返回 `WEB`；`local_tool_denial` 拒绝一切 `local=True` 工具。
- `backend/app/agents/harness/desktop_channel.py:10-41`：`PackageProof` 无设备身份字段；`recognize_package` 仅比对单枚共享 `settings.desktop_package_token`。
- `backend/app/api/desktop.py:34-111` 与 `backend/app/services/local_action.py:90-238`：`/api/desktop/propose`、`/commit`、`/finish` 验证 `X-Omni-Package`，消费确认单并下发 `kind="armed"` 许可；未绑定 `device_id`，`finish_local` 未回填后台任务步骤或唤醒原任务继续。
- `desktop/executor.py:6-30`、`desktop/confirm_local.py:11-37,80-115`、`desktop/preload.js:3-29`、`desktop/main.js:30-73`、`desktop/actions.js:1-3`：Electron 预加载脚本只渲染两个固定探测按钮（`/tmp/ev-desktop-probe.txt` 与 `/usr/bin/true`）；`perform` 仅在 `license["kind"]=="armed"` 时执行 `xdg-open` 或 `shell=False` 子进程；主进程传共享 `DESKTOP_PACKAGE_TOKEN`。
- `backend/app/services/memory.py:586-594,641-701,862-910`：核实 R3 三处：`list_memories`（`587`）将空 `user_id` 归为 `1`；`remember_from_turn`（`657-664`）对省略 `generation` 的直接调用仍执行抽取并配随机 `source_ref`；`settle_pending`（`881-886`）在 `discarded` 时原地改写原账本行（`pending=0, decision="discarded"`），仅 `restored` 追加新行。
- `backend/app/main.py:44-68`、`backend/app/core/db.py:298-347`、`docker-compose.yml:50-400`、`backend/alembic/versions/`（最新迁移 `0017_errand.py`）：确认服务拓扑与迁移基线。

## 2. 本轮实际打开的官方接口文档与核实字段

以下 DeepSeek 官方文档均在 2026-11-19 本轮运行中通过只读抓取实际打开并核对字段名：

1. `https://api-docs.deepseek.com/guides/search.md`
   - 端点：`POST https://api.deepseek.com/v1/tools/search`、`POST https://api.deepseek.com/v1/tools/fetch`。
   - 认证头：`Authorization: Bearer <DeepSeek API Key>`、`Content-Type: application/json`。
   - Search 请求字段：`query`（必填，1–128 Unicode 字符，支持 `site:`/`-site:`/`-`/`-"..."`/`"..."`/`lang:`/`after:`/`before:`）、`count`（可选，默认 `10`，1–50）、`ori_question`（可选，1–128 字符）、`localization`（可选，默认 `""`）、`need_content`（可选，默认 `false`）。
   - Search 响应字段：`results[]`，每项含 `rerank_score`、`title`、`url`、`snippet`、`summary`、`content`、`site_name`、`site_icon`、`date`（RFC 3339 UTC 或 `null`）、`authority`（0–4，`-1` 未知）。
   - Fetch 请求字段：`url`（必填，最长 8192 字符，http/https，不得含用户名密码）。
   - Fetch 响应字段：`title`、`content`（Markdown 文本）。
   - 错误与限流：OpenAI 兼容 `error.{message,type,param,code}`；408/429/5xx 可重试，402 余额不足，其余 4xx 不重试；账号级 QPS 限额 Search 100 QPS、Fetch 25 QPS（独立于模型并发）；仅成功且有结果的请求计费（$0.75 / 1,000 次）。
2. `https://api-docs.deepseek.com/guides/thinking_mode.md`
   - OpenAI 格式控制参数：`extra_body={"thinking": {"type": "enabled" | "disabled"}}`、`reasoning_effort`（`"low" | "high" | "max"`，默认开启且 `high`）。
   - 参数生效规则：思考模式不支持 `temperature`、`presence_penalty`、`frequency_penalty`（传了不报错但无效）；`deepseek-flash` 的 `top_p` 仅在思考模式生效且下限钳制到 `0.95`（范围 `0.95`–`1.0`），非思考模式固定 `1.0`；`deepseek-v4-pro` 思考模式下忽略 `top_p`（有工具固定 `0.95`，无工具固定 `1.0`）。
   - 思考回传硬规则：请求带 `tools` 参数时，后续每次请求必须完整回传此前各轮助手消息的 `reasoning_content`（即便某轮未触发工具调用），否则 API 返回 `400`；请求不带 `tools` 时，无需回传 `reasoning_content`。
3. `https://api-docs.deepseek.com/api/create-chat-completion.md`
   - `tool_choice` 约束：`required` 与指定函数名在思考模式下不受支持，API 返回 `400`，必须先关闭思考模式或使用 `auto`。
   - `ChatCompletionRequestToolMessage` 字段：`role="tool"`、`tool_call_id`（必填）、`content`（字符串或内容块数组）。
   - `CompletionUsage` 字段：`prompt_tokens`、`completion_tokens`、`total_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`prompt_tokens_details.cached_tokens`、`completion_tokens_details.reasoning_tokens`、`completion_tokens_details.safety_tokens`、`server_tool_use`。
   - `stream_options.include_usage`：最后一个内容块携带完整 `usage`（不另发单独 usage-only chunk）。
4. `https://api-docs.deepseek.com/guides/vision.md`
   - `deepseek-flash` 支持 JPEG/PNG/GIF/WebP（按文件实际内容识别）。
   - Chat Completions 请求格式：`content` 为数组，文本项 `{"type": "text", "text": "..."}`，图片项支持 `{"type": "image_url", "image_url": {"url": "data:...;base64,..." | "https://...", "detail": "low" | "high" | "original" | "auto"}}` 或 `{"type": "file", "file_id": "file-api-..."}` / `{"type": "file", "file_data": "data:...;base64,...", "filename": "..."}`。
   - 限制：单次内联请求体 48 MiB；单图内联/外链 32 MiB、`file_id` 64 MiB；单请求最多 600 图；单边上限 8192 px（>=15 图时降为 4096 px）；每图折算上限 1024 tokens；`assistant` 消息中带图返回 `400`（仅允许 `user`/`system`/`tool`）。
5. `https://api-docs.deepseek.com/quick_start/pricing/`
   - `deepseek-flash`（版本 `DeepSeek-V4.1-Flash-1017`）：1M 上下文、384K 最大输出、支持 Vision/Tool Calls/JSON Output、并发 2500；旧别名 `deepseek-v4-flash`/`deepseek-v4-flash-vision-exp` 路由至此模型。
   - `deepseek-v4-pro`（版本 `DeepSeek-V4-Pro-0813`）：不支持 Vision、并发 500。
6. 未核实的外部接口：
   - DeepSeek 服务端请求取消接口（未查到相关公开文档，标为「未核实」，设计不承诺撤回已发送请求或停止计费）。
   - DeepSeek Responses API 文档（`https://api-docs.deepseek.com/zh-cn/guides/responses_api.md`）：本轮未打开，目标架构不采用 Responses API 路径。

## 3. 历史审查文档与当前代码核对

已逐份读取 `docs/envisioned-product/review/` 下全部 12 份 `ev-*.md`（`ev-task.md`、`ev-stop.md`、`ev-dispatch.md`、`ev-work.md`、`ev-net.md`、`ev-events.md`、`ev-memory.md`、`ev-watch.md`、`ev-webblock.md`、`ev-mail.md`、`ev-errands.md`、`ev-desktop.md`）及 `ev-gate-evidence/README.md`、`ev-plug-evidence/README.md`。

核对发现的文档与源码差异：
1. `docs/envisioned-product/review/` 目录下不存在顶层 `ev-gate.md` 与 `ev-plug.md`，这两刀的现状描述位于 `ev-gate-evidence/README.md` 与 `ev-plug-evidence/README.md`。
2. `ev-desktop.md:13` 写明「The buttons `打开测试文件` and `运行测试程序` live in `desktop/preload.js`」，代码核对一致；但审查文档未提及 `desktop/confirm_local.py:11-14` 硬编码 `_PROBES` 映射。
3. `ev-errands.md:11` 称「A page that only says `已下单` does not insert a row and does not place again」，代码核对 `errands.py:367-368` 发现：当返回体带 `outcome == "placed"` 且 `idempotency_key` 为空时（恰好是 `services/errand-compose/server.py:15-27` `_public` 洗掉关联号后的形态），`interpret` 仍会返回 `named=True` 并触发 `_save`，这与方案页 X3 的复核结论一致。
4. `ev-stop.md:15` 与后续携带项指出 `stream_chat` 提前返回可能冲出脱敏缓冲：代码核对 `llm.py:489-494` 与 `chat.py:644-679` 证实，当 `stream_chat` 因 `is_stopped()` `return` 时，循环正常退出而 `leave_stream` 仍为 `False`，随后 `reason_buf.finish()` 与 `answer_buf.finish()` 会被发送。

## 4. 未重读文件清单

下列文件或目录在本轮未打开函数体，设计中对其内部实现不作未经核实的断言，均记为「未重读」：

- `docs/envisioned-product/envisioned-product-plan.md`（历史 14 刀计划，按合同不作本轮目标，未重读）
- `backend/app/agents/context.py`（未重读；仅通过 `chat.py` 与 `workflow.py` 调用点得知输入输出形态）
- `backend/app/agents/harness/verify.py`、`backend/app/agents/harness/critique.py`、`backend/app/agents/harness/audit.py`（未重读）
- `backend/app/core/prompts.py`、`backend/app/core/moderation.py`、`backend/app/core/redis.py`（未重读）
- `backend/app/services/calendar.py`、`backend/app/services/calendar_local.py`、`backend/app/services/calendar_factory.py`、`backend/app/services/calendar_provider.py`（未重读）
- `backend/app/services/errand_vault.py`、`backend/app/services/meeting_followup.py`、`backend/app/services/meeting_prep.py`、`backend/app/services/data_analysis.py`、`backend/app/services/session.py`、`backend/app/services/artifact.py`（未重读）
- `backend/app/tasks/pulse.py`、`backend/app/tasks/ingestion.py`（未重读）
- `backend/app/rag/` 下全部文件（嵌入、重排、入库 OCR 管线，按 M4 定案保留不动，未重读）
- `frontend/src/components/FollowupCard.tsx`、`ScheduleCard.tsx`、`MailSendCard.tsx`、`ErrandCard.tsx`、`MessageActions.tsx`（未重读；`FollowupCard` 根 phase 显示问题仅引用 `ev-plug-evidence/README.md:27` 与 `ev-mail.md` 历史记录）
- `verification/` 下全部 `.tla`、`.cfg`、`check.py` 文件（未重读；其已证明性质仅引用各 `ev-*.md` 审查记录）
- `.github/workflows/` 下全部 CI 配置文件（未重读）
- `backend/alembic/versions/*.py`（仅用文件列表确认 `0001`–`0017` 编号，未重读迁移脚本正文）
- `backend/tests/` 与 `frontend/src/**/*.test.*`（未重读）
- 全部上游开源仓库源码文件（本轮未克隆或重读 Hermes、Harness、OpenCode、DS-Bot、Agent Zero、OpenClaw、OpenMausBot、FullDots、Rowboat、Open Super Agent 仓库，严格沿用 `oss-issue-references_8ae0.md` 与方案页中记录的提交 SHA、路径及已确认覆盖边界）
