# Omni-Butler 预想图 × OSS 机制对照（harness/任务边界）

时间：2026-11-06T11:43:48.002168+08:00（Asia/Singapore / UTC+8）

结论：继续嵌入 Omni-Butler 的既有产品与工具接口；借机制，不换根仓、不整仓搬。当前图已包含这些机制，本文件只补来源与证据边界。

## 范围与材料

- **timezone**：Asia/Singapore (UTC+8)
- **mode**：静态分析；仅创建本 MD/JSON，不运行仓库源码、clone、修改仓库、发送消息、发布、提交或改 Page
- **target_mmd**：/workspace/flow-diagrams/envisioned-product-complete.mmd
- **prior_report**：/workspace/research/personal-agent-oss-comparison.md
- **page**：{"access": "ListCanvases 精确解析短 ID 后 ReadCanvas 只读", "canvas_id": "f17af6db-4dcd-40e8-aa63-992120e9db87", "title": "开源 Personal Agent 源码对比", "url": "https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87"}
- **input_sha256**：{"/workspace/flow-diagrams/envisioned-product-complete.mmd": "941303efcb989941b83efcb3079844e52213cf38e06bd2e2d1f494f569bc1df2", "/workspace/research/personal-agent-oss-comparison.md": "46b351d292679e7e0c1d054b00abe972deae9eacd70e058307e2411befc368af"}
- **requested_numbers**：["②", "③", "⑤", "⑦", "⑧", "⑩"]
- **numbering**：六个请求编号与当前 mmd 分区一致；⑤可见≠执行的文案实际在⑧ mcp，保留⑤并注明跨区。
- **current_diagram_note**：当前图已经写入取消链、持久回执、确认凭据、副作用契约与共享任务状态。本次只准备来源对照，不把这些说成本次新增或 Omni-Butler 已实现。
- **integration_principle**：保留 Omni-Butler 产品与栈；借状态/协议/执行边界，不整仓搬；跨仓组合属于自行实现建议。
- **evidence_levels**：{"code": "此前本会话实际读过的固定 SHA 源码；本轮沿用，无运行验证", "prior": "本轮读到的旧报告/Page，未复核项目源码；只作为下一步阅读/架构参考", "proposal": "映射到 Omni-Butler 的集成做法为设计建议，不声称上游或 Omni-Butler 已实现"}
- **excluded_prior_product_items**：Page 中千问 Go、Alma、Muse 为产品形态对照而非源码；本次不以其充当实现来源。

## 六节点映射

“优先/次选”是对这条机制的参考优先级，不是对整仓的排名。已实现证据只限列出的源码范围；集成建议不是上游实现事实。

### ② 外壳：收发与可打断交互（简化要做）（02-shell）

- **预想节点**：请求 ② → `SHELL`；mmd L41–L49；实际节点：`duplex`, `stream`, `chan`, `shellnote`。 请求编号与 mmd 分区编号一致；跨区关联保留原编号。
  - 跨区映射：⑤ task：运行代次/旧结果隔离；⑧ contract：请求前取消检查
- **预想行为**：桌面/网页/邮件共用可打断任务会话；Stop 向子任务、排队项与请求前传播，旧结果不得复活；外壳只负责交互，不替确认闸批准。
- **优先抄自**：
  - 来源 [D-CANCEL](#source-d-cancel), [D-LATE](#source-d-late)；机制：子任务追踪、取消传播、清掉排队通知、抑制迟到完成。 集成：在 Omni-Butler 任务层实现父子 run 关联与取消代次；外壳 Stop 调同一取消入口，不引入 DS-Bot 群聊。 证据边界：证明了回合/子 agent 层处理，不证明所有外部工具在途请求可中止或撤销。
- **次选**：
  - 来源 [O-CANCEL](#source-o-cancel), [O-HTTP](#source-o-http)；机制：每次工具调用绑定活动检查；等待后和真正发请求前再次检查。 集成：统一插头接受任务有效性检查，在退避/限流/异步准备后复查；取消和 HTTP 超时分开。 证据边界：该 client 的 fetch 使用超时 signal；不是完整链路 AbortSignal 传播。
  - 来源 [PA-PI](#source-pa-pi), [PA-LETTA](#source-pa-letta), [PA-NANOBOT](#source-pa-nanobot), [PA-OPENCLAW](#source-pa-openclaw), [PA-EIGENT](#source-pa-eigent)；机制：壳子/通道补充：Pi 会话嵌入、Letta CLI/App Server 与通道分层、nanobot 邮件总线、OpenClaw 收发/执行分离、Eigent 桌面壳形态。 集成：只读这些先前机制线索，映射到现有桌面壳+瘦网页+邮件 adapter；是否采用包另行评估。 证据边界：全部为先前报告/Page 二手证据；未复核取消 API，也不能作为群聊总机或整仓换底座理由。
- **明确别抄**：
  - Stop=撤销已发送请求/外部动作。
  - 把外壳确认框当全产品确认闸，或让外壳绕过授权。
  - 完整群聊总机、跨 App 聊天入口、满分全双工/异步 ReAct；本版仍简化可打断。

### ③ 事件驱动主动（要做）（03-events）

- **预想节点**：请求 ③ → `TRIGGER`；mmd L51–L62；实际节点：`src`, `f1`, `f2`, `inbox`, `route`, `direct`, `newjob`, `receipt`, `tnote`。 请求编号与 mmd 分区编号一致；跨区关联保留原编号。
- **预想行为**：邮件/日程等订阅事件经两级过滤后进入事件收件箱；按来源和投递号取得持久回执，关联事件直达正在执行任务；接收、派活和外部成功分别记账，写操作仍过闸。
- **优先抄自**：
  - 来源 [O-EVENT](#source-o-event), [O-RECEIPT](#source-o-receipt)；机制：事件键→run 回执；run/回执同次保存，保存失败回滚，成功才调度；有容量与保留窗口。 集成：在现有后端用事件表/唯一键+事务/outbox 实现同等语义；回执与业务外部效果账分开。 证据边界：源码证明 webhook/app 事件去重与本地保存，不证明跨进程数据库事务、邮件投递语义或外部动作 exactly-once。
- **次选**：
  - 来源 [PA-DSH](#source-pa-dsh), [PA-NANOBOT](#source-pa-nanobot), [PA-HERMES](#source-pa-hermes), [PA-LETTA](#source-pa-letta)；机制：DeepSeek 原型的关联事件直通；nanobot/Hermes 的邮件入口；Letta 通道配对/路由/访问控制。 集成：把邮件当 typed event adapter；保留来源/信任级别/关联任务/投递键；只借通道适配，不建设聊天总机。 证据边界：二手材料未证明持久事件回执、两级过滤具体实现或邮件 OAuth 产品闭环。
- **明确别抄**：
  - 收到 webhook 或派活成功就显示外部动作已成功。
  - 去重窗口=永远 exactly-once；事件去重代替业务副作用幂等。
  - 模型投机执行或通道消息直接触发写操作；外部文本不能自带授权。

### ⑤ 这一轮任务与授权（05-auth）

- **预想节点**：请求 ⑤ → `AUTH`；mmd L70–L76；实际节点：`task`, `stack`, `note`, `webflag`。 请求编号与 mmd 分区编号一致；跨区关联保留原编号。
  - 跨区映射：⑧ mcp：工具可见 ≠ 有执行权限；执行端独立验权；⑦ go：作用范围/代次的确认凭据
- **预想行为**：任务带本轮代次与身份→工具白名单→本轮允许；工具列表只是展示/模型可见集合，执行端独立验权；上下文增长和委派都不能抬权。
- **优先抄自**：
  - 来源 [O-RPC](#source-o-rpc)；机制：tools/list 与 tools/call 分别按策略判定，执行入口另查任务仍有效。 集成：把三层授权合成执行时 policy；每个工具入口都验主体、白名单、本轮范围和取消状态。 证据边界：已读的是单个 HighLevel 适配器，不能宣称该仓或 Omni-Butler 已全面覆盖三层授权。
- **次选**：
  - 来源 [D-SCOPE](#source-d-scope), [D-GUARD](#source-d-guard)；机制：Session 级工具注册/注销；可见性过滤之外追加执行 guard。 集成：服务端/桌面分别装配能力集合；生命周期结束解除工具，执行端仍必须 fail closed。 证据边界：guard 的已读具体范围是提问/委派工具，不能泛化成万能安全沙箱。
  - 来源 [PA-DSH](#source-pa-dsh), [PA-NANOCLAW](#source-pa-nanoclaw), [PA-OPENCLAW](#source-pa-openclaw), [PA-ZEROCLAW](#source-pa-zeroclaw)；机制：先前 PA：任务授权范围、薄宿主/执行隔离、配对信任、自治档位与操作留痕。 集成：以 DeepSeek 原型作为角色分离概念参考；隔离与证据链作为执行边界设计输入。 证据边界：均未复核源码；无证据证明它们与当前三层授权严格等价或提供 OS 级安全保证。
- **明确别抄**：
  - 隐藏工具=禁止执行，或 deny 列表=OS 沙箱。
  - 把上下文/agent 消息/群管理员角色当作用户授权。
  - 把容器/薄宿主设计照搬成本机独立遥控助手；本机能力仍只在桌面安装包内。

### ⑦ 确认闸（嵌在干活里）（07-gate）

- **预想节点**：请求 ⑦ → `GATE`；mmd L87–L93；实际节点：`side`, `card`, `instances`, `go`。 请求编号与 mmd 分区编号一致；跨区关联保留原编号。
- **预想行为**：有副作用先给人看；确认凭据绑定动作、参数指纹、作用范围、任务代次；执行前复验，改参数重批，停止失效；日历/邮件/办事/本机共用一闸。
- **优先抄自**：
  - 来源 [O-CONFIRM](#source-o-confirm), [O-LIVE](#source-o-live)；机制：routine 回执绑定动作/消息/bot/线程/内容指纹；审批读取当前保存等级；提交失败回滚。 集成：在现有确认卡后端增加受信任凭据与消费回执；执行时按最新权限复验，改参数不复用旧批准。 证据边界：不是完整可移植凭据协议：此处只核验 routine 回执字段；用户图中的作用范围/任务代次/停止作废需在 Omni-Butler 自己实现并测试。
- **次选**：
  - 来源 [D-APPROVAL](#source-d-approval)；机制：审批请求有归属、细节、取消与 stale-answer 处理。 集成：确认等待纳入任务生命周期；取消令 UI 卡与后端 pending 同时失效；持久状态另建。 证据边界：pending 是内存 Map；本文件未提供跨重启审批恢复。
  - 来源 [PA-PI](#source-pa-pi), [PA-GOOSE](#source-pa-goose), [PA-ZEROCLAW](#source-pa-zeroclaw)；机制：Pi 扩展确认问法、Goose 工具审批、ZeroClaw 操作留痕作为产品/接口参考。 集成：只借确认交互与操作摘要表达，不把交互组件当授权协议。 证据边界：先前 Page 二手证据，未复核凭据、原子消费或持久化。
- **明确别抄**：
  - 内存审批表当持久确认队列。
  - Pi 示例 confirm 弹窗=全局安全闸；静态等级=永久批准。
  - 拿已批准的旧参数执行新动作，或进程重启自动恢复批准执行。

### ⑧ 统一工具插头与能力（08-bus）

- **预想节点**：请求 ⑧ → `BUS`；mmd L95–L111；实际节点：`mcp`, `contract`, `unknown`, `cal`, `errands`, `err_api`, `err_web`, `err_stop`, `local`, `web_limit`, `dsknet`。 请求编号与 mmd 分区编号一致；跨区关联保留原编号。
- **预想行为**：自有工具统一入口，执行端验权与取消检查；声明只读/幂等写/不可重放写并按契约重试；结果未知先核对，停止不等于撤销；接口优先、App墙停、本机仅桌面。
- **优先抄自**：
  - 来源 [O-RPC](#source-o-rpc), [O-HTTP](#source-o-http), [O-CANCEL](#source-o-cancel)；机制：API 插头集中参数校验/授权/活动检查；非可重放写在网络失败/5xx 后给 unknown outcome，而非盲重发。 集成：为自有工具定义副作用与 replay/reconcile 能力；记录外部请求/结果标识，未知结果转核对流程，不能只凭模型判断重试。 证据边界：三类统一 contract 是图的目标设计，不能说该仓已经实现全产品三分类框架；这里只核验 HighLevel 的 repeatable 分支。
- **次选**：
  - 来源 [D-SCOPE](#source-d-scope), [D-GUARD](#source-d-guard)；机制：按会话安装/卸载工具与执行 guard，防止无归属工具泄露。 集成：把注册生命周期、可见性与调用授权分为三件事；桌面工具不在网页服务注册。 证据边界：不是操作系统级隔离；也未证明某个工具可绕开 App 墙。
  - 来源 [PA-ANYTHING](#source-pa-anything), [PA-LEON](#source-pa-leon), [PA-GOOSE](#source-pa-goose), [PA-NANOCLAW](#source-pa-nanoclaw)；机制：AnythingLLM 邮件/日历插件形状；Leon Skills→Actions→Tools→Functions；Goose 标准扩展；NanoClaw 薄宿主/执行分离。 集成：借领域 adapter 与分层结构，嵌入 Omni-Butler 自有工具总线；不替换业务后端。 证据边界：报告仅插件目录/入口或架构文档；未复核发送/改日历实现、幂等或确认保证。
- **明确别抄**：
  - 所有失败都自动重试；不可重放写超时直接重发。
  - 普通失败有限重试+提示词检查旧动作=可靠幂等账本。
  - 照搬全套工具/裸命令权限；把 DeepSeek 自带联网当另一个旧搜索插头或同轮开两条联网路。
  - 扩展云手机/手机端/网页遥控独立本机助手，或把看屏操控拉进本版主链。

### ⑩ 看门狗（简化要做）（10-watch）

- **预想节点**：请求 ⑩ → `WATCH`；mmd L124–L130；实际节点：`wd`, `wd_check`, `wd_act`, `wd_note`。 请求编号与 mmd 分区编号一致；跨区关联保留原编号。
  - 跨区映射：⑤ task：复用运行/等人/等资源/停止状态；② duplex / ⑧ contract：统一取消链与执行前检查
- **预想行为**：监测同一任务的实质进展；等待人/资源和机器休眠不误判；异常提醒或沿取消链安全停，不改授权、提示词或 Trigger 规则。当前图还要求主系统外独立会话/定时叫醒。
- **优先抄自**：
  - 来源 [O-WATCH](#source-o-watch), [O-STALL](#source-o-stall)；机制：按事件更新时间与工具运行集合；区分等待状态与工具超时，抵扣休眠；卡死撤销能力、interrupt，按代次释放资源。 集成：将监测器做成任务状态只读消费者+受限 stop 接口；停止前后以任务代次防误伤新工作，复用授权系统不越权。 证据边界：已核验是进程内 class/setInterval 与 host 接线，不是图中“主系统外的独立会话”；也不等于主进程崩溃时仍可工作。
- **次选**：
  - 来源 [O-RETRY](#source-o-retry)；机制：失败恢复明确区分 busy/owner/final/retry；用户停止/拒绝/cap 不自动重试。 集成：把安全停止与可恢复临时故障分开；看门狗终止不自动变成重新开工授权。 证据边界：该恢复器含有限自动重试与提示词防重复，不能整段当业务幂等策略。
  - 来源 [PA-DSH](#source-pa-dsh), [PA-NANOBOT](#source-pa-nanobot), [PA-LEON](#source-pa-leon), [PA-LETTA](#source-pa-letta)；机制：先前 PA 的任务隔离、定时/脉搏/心跳只作调度概念对照。 集成：如保留独立监测需求，用宿主/外部 supervisor 读持久任务状态；不必让另一个 LLM 会话介入每次巡检。 证据边界：这是设计建议；二手材料不证明独立安全看门狗。Leon pulse/Letta dreaming 不等于进展监控。
- **明确别抄**：
  - 把 OpenMausBot 进程内 timer 写成已经实现的外部独立看门狗。
  - 无输出就 kill；等人/等资源算卡死；睡眠后马上判超时。
  - 安全停后自动重启危险动作、自动抬权/改提示词/Trigger 规则；旧任务释放新代次资源。

## 来源锚点（与 JSON sources 一一对应）

<a id="source-o-rpc"></a>
### O-RPC · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/highlevel/rpc.ts:24–40](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/highlevel/rpc.ts#L24-L40)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：tools/list 按 authorize 过滤；tools/call 独立校验参数、授权、调用前后任务有效性。

<a id="source-o-cancel"></a>
### O-CANCEL · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/highlevel/client.ts:259–265](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/highlevel/client.ts#L259-L265)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：AsyncLocalStorage 为每次工具调用绑定活动检查；等待结束再次检查。

<a id="source-o-http"></a>
### O-HTTP · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/highlevel/client.ts:335–401](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/highlevel/client.ts#L335-L401)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：实际 fetch 前检查任务有效；不可重放写在网络失败/5xx 后返回结果未知；在途 fetch 使用超时 signal。

<a id="source-o-event"></a>
### O-EVENT · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/routines.ts:742–806](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/routines.ts#L742-L806)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：来源/投递编号查回执；run 与回执同次保存、失败回滚，保存成功后调度；容量满拒收。

<a id="source-o-receipt"></a>
### O-RECEIPT · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/routine-event-receipts.ts（文件级锚点，未伪造行号）](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/routine-event-receipts.ts)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：保存七天事件回执；加载时可从近期 run 补回丢失的回执。

<a id="source-o-confirm"></a>
### O-CONFIRM · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/routines.ts:1487–1536](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/routines.ts#L1487-L1536)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：routine 请求回执校验动作、消息、bot、线程、指纹与指纹版本；提交失败恢复内存快照。

<a id="source-o-live"></a>
### O-LIVE · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/host/live-approval.ts:12–17](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/host/live-approval.ts#L12-L17)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：每次审批决策读取会话当前保存等级，不依赖开任务时的旧副本。

<a id="source-o-watch"></a>
### O-WATCH · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/turn-watchdog.ts（文件级锚点，未伪造行号）](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/turn-watchdog.ts)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：进程内定时器；运行工具与普通静默分阈值；等人/等电脑不算卡死；休眠间隔抵扣。

<a id="source-o-stall"></a>
### O-STALL · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/index.ts:5012–5094](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/index.ts#L5012-L5094)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：卡死后撤销内部能力、interrupt；按代次检查后释放资源；事件驱动更新等待/完成状态。

<a id="source-o-retry"></a>
### O-RETRY · milind-soni/OpenMausBot@083fd7b51405e6d0c01e189b2f65ab0322509b5a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[server/routine-recovery.ts（文件级锚点，未伪造行号）](https://github.com/milind-soni/OpenMausBot/blob/083fd7b51405e6d0c01e189b2f65ab0322509b5a/server/routine-recovery.ts)
- repo：`milind-soni/OpenMausBot`；commit：`083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- 机制／阅读线索：区分 busy/owner/final/retry；用户停止、拒绝、cap 等不重试；普通失败有限重试，附避免重复的提示词。

<a id="source-d-cancel"></a>
### D-CANCEL · Fei2411/DS-Bot@71d344bf193f3f18185f25de3d8e5c483046910a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[ds-bot/src/host/turns.js:221–264](https://github.com/Fei2411/DS-Bot/blob/71d344bf193f3f18185f25de3d8e5c483046910a/ds-bot/src/host/turns.js#L221-L264)
- repo：`Fei2411/DS-Bot`；commit：`71d344bf193f3f18185f25de3d8e5c483046910a`
- 机制／阅读线索：停止子 agent、清理父任务待处理完成通知；按 exchangeId 停止当前关联回合或清理排队消息。

<a id="source-d-late"></a>
### D-LATE · Fei2411/DS-Bot@71d344bf193f3f18185f25de3d8e5c483046910a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[ds-bot/src/host/turns.js:695–708](https://github.com/Fei2411/DS-Bot/blob/71d344bf193f3f18185f25de3d8e5c483046910a/ds-bot/src/host/turns.js#L695-L708)
- repo：`Fei2411/DS-Bot`；commit：`71d344bf193f3f18185f25de3d8e5c483046910a`
- 机制／阅读线索：被停止父/子任务的迟到完成消息会被抑制，不能重新唤醒工作；真实新消息可清除停止标记。

<a id="source-d-scope"></a>
### D-SCOPE · Fei2411/DS-Bot@71d344bf193f3f18185f25de3d8e5c483046910a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[ds-bot/src/host/tools.js:743–781](https://github.com/Fei2411/DS-Bot/blob/71d344bf193f3f18185f25de3d8e5c483046910a/ds-bot/src/host/tools.js#L743-L781)
- repo：`Fei2411/DS-Bot`；commit：`71d344bf193f3f18185f25de3d8e5c483046910a`
- 机制／阅读线索：工具按 Session 注册；归属消失、重载或卸载时 dispose；不是全局无差别暴露。

<a id="source-d-guard"></a>
### D-GUARD · Fei2411/DS-Bot@71d344bf193f3f18185f25de3d8e5c483046910a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[ds-bot/src/host/tools.js:834–915](https://github.com/Fei2411/DS-Bot/blob/71d344bf193f3f18185f25de3d8e5c483046910a/ds-bot/src/host/tools.js#L834-L915)
- repo：`Fei2411/DS-Bot`；commit：`71d344bf193f3f18185f25de3d8e5c483046910a`
- 机制／阅读线索：模型工具列表过滤之外，还有 restrict 与执行 guard；这里 guard 具体针对提问/委派工具。

<a id="source-d-approval"></a>
### D-APPROVAL · Fei2411/DS-Bot@71d344bf193f3f18185f25de3d8e5c483046910a

- 证据级别：此前本会话已静态核验源码；本轮沿用固定提交；未运行验证
- 源码：[ds-bot/src/host/approvals.js（文件级锚点，未伪造行号）](https://github.com/Fei2411/DS-Bot/blob/71d344bf193f3f18185f25de3d8e5c483046910a/ds-bot/src/host/approvals.js)
- repo：`Fei2411/DS-Bot`；commit：`71d344bf193f3f18185f25de3d8e5c483046910a`
- 机制／阅读线索：审批关联 Session/bot/room 与请求细节；abort/退出归属取消；拒绝过期回答；pending 为内存 Map。

<a id="source-pa-pi"></a>
### PA-PI · Pi

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/earendil-works/pi
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="pi"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：可嵌会话壳与扩展确认问法；没有现成个人助手入口、长期记忆和心跳。

<a id="source-pa-dsh"></a>
### PA-DSH · DeepSeek SuperAgent 原型

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/deepseek-ai/dsh-superagent-prototype
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="dsh"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：派活≠干活、任务授权范围、关联事件直通；研究原型，不等于持久队列或严格沙箱。

<a id="source-pa-nanobot"></a>
### PA-NANOBOT · nanobot

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/HKUDS/nanobot
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="nanobot"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：消息总线→助手循环→执行器，邮件一等入口、默认陌生人拒收；只借入口适配边界。

<a id="source-pa-openclaw"></a>
### PA-OPENCLAW · OpenClaw

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/openclaw/openclaw
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="openclaw"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：收发/定时/助手分层、设备配对与危险操作限制；不采纳聊天总机产品范围。

<a id="source-pa-nanoclaw"></a>
### PA-NANOCLAW · NanoClaw

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/nanocoai/nanoclaw
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="nanoclaw"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：薄宿主收发与容器执行分开，凭据留宿主；不是已核验安全保证。

<a id="source-pa-zeroclaw"></a>
### PA-ZEROCLAW · ZeroClaw

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/zeroclaw-labs/zeroclaw
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="zeroclaw"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：自治档位与工具操作留痕；未核验其凭证结构或持久化语义。

<a id="source-pa-hermes"></a>
### PA-HERMES · Hermes Agent

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/NousResearch/hermes-agent
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="hermes"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：邮件通道与网关信任参考；默认本机命令权限大的形态不照搬。

<a id="source-pa-goose"></a>
### PA-GOOSE · Goose

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/aaif-goose/goose
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="goose"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：此前 Page：标准工具扩展与审批，偏桌面工具代理；不能据此证明本轮确认凭据协议。

<a id="source-pa-letta"></a>
### PA-LETTA · Letta Code

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/letta-ai/letta-code
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="letta"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 本地报告：[/workspace/research/personal-agent-oss-comparison.md:L94–L124](file:///workspace/research/personal-agent-oss-comparison.md#L94-L124)
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：报告路径 src/channels/README.md：通道配对/路由/访问控制；CLI/App Server 分层。MemFS 仅作状态留痕旁证。

<a id="source-pa-anything"></a>
### PA-ANYTHING · AnythingLLM

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/Mintplex-Labs/anything-llm
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="anythingllm"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 本地报告：[/workspace/research/personal-agent-oss-comparison.md:L143–L174](file:///workspace/research/personal-agent-oss-comparison.md#L143-L174)
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：报告路径 server/utils/agents/ephemeral.js 与 server/utils/agents/aibitat/plugins/（目录列表）：Gmail/Calendar/Outlook 工具形状；未复核各插件实现。

<a id="source-pa-leon"></a>
### PA-LEON · Leon 2.0

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/leon-ai/leon
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="leon"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 本地报告：[/workspace/research/personal-agent-oss-comparison.md:L195–L228](file:///workspace/research/personal-agent-oss-comparison.md#L195-L228)
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：报告路径 core/context/ARCHITECTURE.md、core/context/LEON.md：Skills→Actions→Tools→Functions 与 turn-lifecycle 的架构线索；不是本轮已读生命周期源码。

<a id="source-pa-khoj"></a>
### PA-KHOJ · Khoj

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/khoj-ai/khoj
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="khoj"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 本地报告：[/workspace/research/personal-agent-oss-comparison.md:L38–L77](file:///workspace/research/personal-agent-oss-comparison.md#L38-L77)
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：报告 src/khoj/interface/email 与 processor/tools 的目录线索；仅作邮件/工具/RAG边界对照，不是这六节点的优先实现参考。

<a id="source-pa-eigent"></a>
### PA-EIGENT · Eigent

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/eigent-ai/eigent
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="eigent"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 本地报告：[/workspace/research/personal-agent-oss-comparison.md:L232–L246](file:///workspace/research/personal-agent-oss-comparison.md#L232-L246)
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：报告仅 README 与 electron/backend/server/src 顶层布局；桌面壳形态可对照，无取消链证明。

<a id="source-pa-oi"></a>
### PA-OI · Open Interpreter

- 证据级别：二手报告/架构说明，未复核源码
- 仓库定位（不是已核验源码）：https://github.com/openinterpreter/openinterpreter
- commit：缺失，不补造。
- Page：[开源 Personal Agent 源码对比](https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/f17af6db-4dcd-40e8-aa63-992120e9db87)；定位 `AGENTS[id="oi"]`。ReadCanvas 已读取 Page 源码；selector 是记录定位，不是仓库源码路径。
- 本地报告：[/workspace/research/personal-agent-oss-comparison.md:L250–L264](file:///workspace/research/personal-agent-oss-comparison.md#L250-L264)
- 限制：本轮未复核该仓源码；无固定 commit SHA，不作为代码已实现的证明。
- 机制／阅读线索：报告仅 README；本机工具/编码助手对照，不是个人助手根底座，也不证明沙箱或取消语义。

## 明确缺口

- **G-OMNI / 全部六节点**：未对 Omni-Butler 当前源码、部署、授权路径做核验；此文是目标图的机制对照，不是现状缺陷清单。
- **G-SECONDARY / 先前 Personal Agent 参考**：本地报告与 Page 均存在并已读；先前 PA 项目无本次固定 SHA/代码行级核验，部分仅 README/目录/架构说明。仅按二手材料使用；Page 的产品现状与许可概括未核验，不作为选型或法律结论。
- **G-SHELL / ②**：未证明完整流式中断/暂停恢复/更改方向均已实现；已发出的 HTTP/外部动作不可因 Stop 保证撤销。两 bot 的机制覆盖不同层，需自行打通。
- **G-EVENT / ③**：两级过滤+订阅直通+邮件持久回执的完整闭环无单一已核验实现。mmd 把 inbox 放在 f2 后：若要在模型判断前避免重复消耗，可另设计 ingress 去重，但本次不改图且不声称已有实现。
- **G-GATE / ⑤/⑦/⑧**：尚无统一“身份×工具×本轮范围×参数×代次”的全栈凭据及原子消费证据；routine 本地提交回执不能确保外部服务副作用原子/恰好一次。
- **G-WATCH / ⑩ wd**：当前图写“主系统外的独立会话”；优先参考 OpenMausBot 实际为进程内 setInterval。外部 supervisor、主进程崩溃恢复、状态持久性需另证；监测规则不必由独立 LLM 执行。
- **G-NET / ⑤ webflag / ⑧ dsknet、err_stop、local**：本次未核验 DeepSeek 自带联网、App墙判断或桌面包本机能力的具体代码。保留预想边界，不用第三方通道/电脑工具反向扩张范围。
- **G-MEM / ⑨关联但非本次六条**：此前 DS-Bot 记忆提交/恢复/审计证据仍有效，但本次不扩成第七条；不拿记忆事务推导外部动作事务。

## 核验说明

- 六个请求编号均与 mmd 分区精确匹配；⑤授权与⑧执行验权交叉已注明。
- JSON 为主数据，MD 由同一数据生成；六条的行为、优先来源、次选与别抄均逐项呈现。
- 来源代码 URL 固定 SHA；旧 PA 明确为二手证据并锚定本地报告行号或 Page 的 AGENTS 记录；未声称本轮复核旧 PA 源码。
- 只验证 JSON 结构、引用完整性、节点覆盖与文件一致性；未执行源码、测试或网络链接可达性检查。
- 未编辑 mmd、旧报告、Page 或任何仓库文件。
