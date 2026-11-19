# 现状引文

行号属于 `/agent/repos/omni-butler`，HEAD `d7710d687d147aeeda502d412454ab8a3829af66`。只列这次读到函数体或读到该行的论断。

## 模型与搜索

- `backend/app/core/llm.py:134-153` 角色 chat、planner、router、judge，以及各自的配置字段名。
- `backend/app/core/llm.py:244-256` 出站拒绝 `tools`、`reasoning_content`、`role=tool`、`tool_calls`。
- `backend/app/core/llm.py:258-268` 发给模型的消息只留字符串 `role` 与 `content`。
- `backend/app/core/llm.py:272-295` 请求体含 `model`、`messages`、`stream`、`extra_body.thinking`、`reasoning_effort`。JSON 且非思考时才写 `temperature=0`。
- `backend/app/core/llm.py:356-368` 用量只读 `prompt_tokens` 或 `input_tokens`，没有就返回 None。
- `backend/app/core/llm.py:399-408` 发送前读停止位。
- `backend/app/core/llm.py:411-424` `stream_chat` 同样先拒绝工具。
- `backend/app/core/config.py:25-32` 默认模型与思考开关。聊天、规划、路由是 `deepseek-flash`，核验是 `deepseek-v4-pro`。
- `backend/app/core/config.py:36` 嵌入默认 `embedding-3`。
- `backend/app/core/config.py:39` 识图默认 `glm-4.6v`。
- `backend/app/core/config.py:87` 最近十轮。
- `backend/app/core/config.py:90` 32000，注释写明不是模型物理窗口。
- `backend/app/core/config.py:124` 重排提供方默认 `zhipu`。
- `backend/app/core/config.py:135-138` 网页搜索基址、引擎 `search_pro_quark`、条数 5。
- `backend/app/core/config.py:186` `DESKTOP_PACKAGE_TOKEN`。
- `.env.example:8-15` 模板里的四个模型名。
- `.env.example:25` `EMBEDDING_MODEL=embedding-3-pro`。
- `.env.example:31` `VISION_MODEL=glm-4v-flash`。
- `.env.example:109` 并发示例含 `embedding-3-pro` 与 `glm-4v-flash`。
- `.env.example:120` 与 `:123` 十轮和 32000。
- `backend/app/services/web_search.py:58-62` 空基址落到智谱 `/web_search`。
- `backend/app/services/web_search.py:213-219` `search_api` 声明自己不调模型。
- `backend/app/services/web_search.py:255-262` 智谱请求字段。
- `backend/app/agents/tools/web.py:10-36` 工具执行调 `search_planned` 并带回 `url`。
- `backend/app/agents/tools/registry.py:36-43` 内置工具注册 kb、日历、沙箱、web。
- `backend/app/rag/vision_ocr.py:17-46` 入库识图用智谱和 `image_url`。

## 任务、派活、聊天

- `backend/app/models/models.py:87-93` 五态。
- `backend/app/models/models.py:96-112` `TaskRun` 列。唯一键是用户加代际。
- `backend/app/services/task_run.py:15-46` 迁移表。`stopped` 上 `finish` 为 reject。`completed` 上 `stop` 为 keep。
- `backend/app/services/task_run.py:127-150` 条件更新。
- `backend/app/services/task_run.py:166-177` 确认单标记。
- `backend/app/services/task_run.py:201-205` 插入时停掉同会话其他开着的非确认单回合。
- `backend/app/services/task_run.py:253-259` `open_alongside` 不停止其他回合。
- `backend/app/services/task_run.py:262-279` `begin_turn` 走会停止其他回合的插入。
- `backend/app/agents/dispatch.py:87-109` `assign_turn` 只产生工具分配，不调用网关。
- `backend/app/agents/workflow.py:290-311` `admit` 先看停止和允许名单，日历走确认。
- `backend/app/agents/workflow.py:318-319` 只有结果自己带 `unknown_write` 才进入未知处理。
- `backend/app/agents/workflow.py:474` 服务器调用 `gateway.invoke`。
- `backend/app/agents/workflow.py:877-881` `route_intent` 的定义。全库搜索只有这一处出现调用形式，没有调用方。
- `backend/app/agents/harness/constitution.yaml:84-88` 允许名单四个名字。
- `backend/app/agents/harness/execution.py:17-42` 停止、确认单标记、不在名单都拒绝。
- `backend/app/agents/harness/effects.py:18-36` 读可再发，不可重放的写只核对。
- `backend/app/agents/harness/surface.py:6-23` 执行面只有 web。任意其他对象都被收成 web。
- `backend/app/api/chat.py:54-59` 聊天请求字段。
- `backend/app/api/chat.py:429` 每条聊天 `begin_turn`。
- `backend/app/api/chat.py:526-528` 断开则 `stop` 并取消工作任务。
- `backend/app/api/chat.py:642-647` 流式循环里再次把断开当成停止。
- `backend/app/api/chat.py:691-696` 可能从已累积文本推断 artifact。
- `backend/app/api/chat.py:719-830` 保存已累积文本，并总是写出 `done`。
- `frontend/src/components/ChatWindow.tsx:47-49` 忙则不发送。
- `frontend/src/components/ChatWindow.tsx:140-142` 停止按钮。
- `frontend/src/components/ChatWindow.tsx:238` 网页不把 desktop 模式交给日程卡。
- `frontend/src/services/chatStream.ts:24-26` 停止是本页 abort。
- `frontend/src/services/chatStream.ts:72-94` 发送时把本页 `busy` 设为真。
- `frontend/src/store/useStore.ts:17-25` `busy` 存在本页状态里。

## 看门狗

- `backend/app/tasks/watchdog.py:16` 60 秒。
- `backend/app/tasks/watchdog.py:19` 20 分钟。
- `backend/app/tasks/watchdog.py:29-35` 只有 running 会停。
- `backend/app/tasks/watchdog.py:99-116` 沉默用 `created_at`。
- `backend/app/tasks/watchdog.py:122-126` 读全部 `TaskRun`。
- `backend/app/tasks/watchdog.py:158-164` 账本是模块全局。
- `backend/app/tasks/celery_app.py:50-53` beat `watch-task-runs` 的 schedule 是 60，任务名 `app.tasks.watchdog.sweep_task`。
- `backend/app/tasks/celery_app.py:66-81` worker 启动只检查智谱端点并确保应用角色，没有任务恢复。

## 确认、邮件、日历、订单、桌面

- `backend/app/models/models.py:61-84` 确认单列，含 `remote_request_id`。
- `backend/app/models/models.py:287-300` `MailAcceptance`。
- `backend/app/models/models.py:303-313` `ErrandRecord` 以用户加武装号唯一。
- `backend/app/services/schedule_confirm.py:95-101` 本机动作的指纹材料。
- `backend/app/services/schedule_confirm.py:158-177` 指纹与范围。桌面范围没有设备 id。
- `backend/app/services/schedule_confirm.py:347-410` 消费把 open 改成 unknown。过期、不符、没有活任务都拒绝。
- `backend/app/services/schedule_confirm.py:413-436` arm 在出站前提交远程编号。
- `backend/app/services/schedule_confirm.py:462-469` `finish_confirm` 把 unknown 或 written 收成 written。
- `backend/app/services/mail_entry.py:226-273` 确认后生成 `uuid4`，接受并封口。
- `backend/app/services/mail_entry.py:397-418` 插入接受行。
- `backend/app/services/mail_entry.py:451-459` 封口调用 `note_external_success`。
- `backend/app/services/calendar_secrets.py:41` Gmail 范围只有 `gmail.readonly`。
- `backend/app/services/event_inbox.py:164-194` 外部成功的条件。
- `backend/app/services/event_inbox.py:239-248` completed 进入 `_open_task`。
- `backend/app/services/event_inbox.py:420-458` 邮件只归档。其他来源 `open_alongside`。
- `backend/app/services/calendar_google.py:1234-1266` 按 request id 查询，不插入。
- `backend/app/services/calendar_google.py:1332-1354` 插入前写 private requestId 并 arm。
- `backend/app/api/calendar.py:498-501` 日程封口只 `finish_confirm`。
- `backend/app/api/calendar.py:519-558` 未知创建可以按已有编号收下，仍不调用 `note_external_success`。
- `backend/app/services/errands.py:131-171` 空 token 拒绝。arm 之后才 place。
- `backend/app/services/errands.py:225-249` 核对不调用 place。
- `backend/app/services/errands.py:348-377` 回声为空时 `outcome=placed` 仍可以成为 placed。
- `services/errand-compose/server.py:11-27` 公开回包字段，以及被丢掉的敏感键。
- `backend/app/services/errand_order.py:13-30` `Secret` 的字符串形式是空的。
- `backend/app/agents/harness/desktop_channel.py:30-41` 共享令牌比对。字面量 desktop 与 web 拒绝。
- `backend/app/api/desktop.py:34-38` 与 `:49-111` 认不出包就 403。propose、commit、finish 都要包证明。
- `backend/app/services/local_action.py:90-206` 先发卡，消费并 arm 后才返回 armed。服务器不打开文件。
- `backend/app/services/local_action.py:209-237` finish 只接受本机两类动作和匹配的 request id。
- `desktop/executor.py:5-22` 只有 `kind=armed` 才打开或运行。

## 审查和代码的差异

- `docs/envisioned-product/review/ev-task.md` 没有「What the code does」标题。开头两段与 `task_run.py` 的五态、唯一键一致。
- `ev-work.md:7-13` 描述的未知写路径在 `workflow.py` 里还在。生产工具没有给 `unknown_write` 赋值。赋值只出现在 `backend/app/agents/harness/types.py:73` 和 `backend/tests/test_work_loop.py`。
- `ev-net.md:19-21` 与当前智谱搜索、`_refuse_outbound` 一致。
- `ev-watch.md:7-13` 与 `created_at`、20 分钟、全表读取一致。beat 的 60 秒在 `celery_app.py:50-53`，与审查一致。
- `ev-mail.md:7-19` 说接受行就是提供方。`mail_entry.py:269` 仍是本地 uuid。审查与代码一致，都还不是邮箱服务。
- `ev-events.md:9-21` 与 `_route_act`、`_open_task` 一致。
- `ev-errands.md:7-17` 与 arm、公开边界、未知不重下一致。`interpret` 对空回声的宽松判断比审查摘要更具体，以 `errands.py:367-368` 为准。
- `ev-desktop.md:9-21` 与共享令牌、网页不执行一致。`desktop/actions.js` 与 `preload.js` 未重读，探测按钮文案不在这次的代码论断里。
- `ev-webblock.md:7-19` 与 `ServerSurface`、`ChatRequest` 无 desktop 字段一致。
- `ev-memory.md:7-17` 只读了审查。`memory.py` 未重读，不用审查行号当现状。
- `ev-dispatch.md:7-15` 与 `assign_turn` 不调用网关一致。`gateway.py` 函数体未重读。
- `ev-stop.md:7-17` 与断开写停止、迟到块不再进入循环一致。`finally` 仍保存已累积文本，这一点以 `chat.py` 为准。
- `docs/envisioned-product/review/ev-gate.md` 与 `ev-plug.md` 不存在。现状改从代码读。`ev-gate-evidence/README.md:24-36` 与 `ev-plug-evidence/README.md:30-36` 只作历史，其中「日历 HTTP 都已消费确认」这次没有逐个路由重读。

## 当天打开的外部文档

- `https://api-docs.deepseek.com/guides/search.md` 看到 Search `query`、`count`、`need_content`，Fetch `url`，响应 `results[].title/url/snippet/summary/content/date` 与 Fetch 的 `title/content`。
- `https://api-docs.deepseek.com/guides/thinking_mode` 看到带 `tools` 时必须回传 `reasoning_content`，不带 `tools` 时可以不回传。
- `https://api-docs.deepseek.com/api/create-chat-completion` 看到用户 `content` 可以是 `image_url` 数组，`tools` 含 function，思考模式下 `tool_choice` 的 `required` 和指定工具名会 400，流式用量在最后一块。
- `https://api-docs.deepseek.com/guides/vision` 看到 `deepseek-flash` 接受 JPEG、PNG、GIF、WebP，`image_url.url` 为 data URL 或 http(s)。`detail` 可选。
- `https://api-docs.deepseek.com/quick_start/pricing` 看到 Flash 有 Vision，`deepseek-v4-pro` 没有。旧名 `deepseek-v4-flash` 仍被接到 Flash。
- `https://api-docs.deepseek.com/zh-cn/guides/responses_api` 看到 Responses 的 `web_search` 仅 `deepseek-v4-pro` 执行，`deepseek-flash` 忽略。

## 未重读

这些文件或函数没有读完，或没有打开。设计不把它们的内容写成这次的发现。

- `backend/app/core/llm.py` 的 `complete_json` 与 `complete_text` 后半，从约第 525 行起到文件末尾，未逐行读完。
- `backend/app/agents/workflow.py` 约 529 到 849 行的图后半，未读完。`route_intent` 只读了 877 到 881。
- `backend/app/agents/harness/gateway.py` 函数体未读。只确认了 constitution 的允许名单行。
- `backend/app/services/schedule_confirm.py` 从 `finish_confirm` 之后未读完。
- `backend/app/services/event_inbox.py` 从 `_open_task` 之后的辅助函数未读完。`_target` 未读完。
- `backend/app/services/errands.py` 从 `interpret` 之后未读完。
- `backend/app/services/mail_entry.py` 的 `receive` 与 `propose_send` 前半未按设计需要逐行引用。`propose_send` 读了 143 到 224。
- `backend/app/services/calendar.py` 的 `create_event` 函数体未读。读的是 Google 实现和 HTTP 封口。
- `backend/app/services/gmail.py` 未读完。只确认了只读范围的引用。
- `backend/app/services/memory.py` 未打开。
- `backend/app/api/calendar.py` 除 480 到 565 附近未逐路由读完。
- `frontend/src/services/chatStream.ts` 从约 180 行起未读完。
- `frontend/src/components/ChatWindow.tsx` 从约 160 行起未读完。238 行的 desktop 判断是单独搜到并读到的。
- `frontend/src/store/useStore.ts` 从约 80 行起未读完。
- `desktop/actions.js`、`desktop/preload.js`、`desktop/main.js`、`desktop/confirm_local.py` 未读。
- `docs/envisioned-product/envisioned-product-plan.md` 与 `decision-trail.md` 未读。按合同只作历史，不覆盖本设计。
- 各 `ev-*.md` 里形式证明、性能数字和未关闭清单的后半，除已引用的现状段外未读完。
- Hermes、Harness、OpenCode、DS-Bot、OpenClaw、FullDots、Rowboat、OpenMausBot、Open Super Agent、Agent Zero 的函数体这次都未打开。引用只沿合同里的 SHA 和开源报告已写明的边界。
- PR 93 的代码未读。
- 生产环境变量未读。不能从 `.env.example` 推断线上模型名。
