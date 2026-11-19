# 本轮计划合同（2026-11-19）

这是给两名独立候选人的同一份合同。只写设计，不改产品代码。

## 目标

在最新 `release` 上，把 2026-11-19 已确认的预期架构写成一份可执行的中文设计。设计要能让后续实现者按刀做完，并让审查者按证据验收。本轮不实现、不合并、不部署。

## 基线

- 仓库：`/agent/repos/omni-butler`
- 分支：`release`
- HEAD：`d7710d687d147aeeda502d412454ab8a3829af66`（`feat(desktop): open a file from the install package (#92)`）
- 远端 `origin/release` 与本地 HEAD 相同。2026-11-19 已 `git fetch origin release` 核对。
- PR 93 仍 OPEN，头 `b41e74dcc0ca8c4800d4fd5861957e7d9445fcaf`，分支 `cursor/ev-net-responses-5880`。暂停是工作约定，不是 GitHub 状态。不要改它，不要把它的方案当目标。
- 其他开放 PR（71、74、75、76、77）不动。

## 已合并的 14 刀（现状的一部分，不是剩余工作清单）

这些提交已在 `release` 上。旧文件 `docs/envisioned-product/envisioned-product-plan.md` 仍把它们写成未来工作。不要重写或删除那份旧计划，也不要把它当本轮目标。先读代码和 `docs/envisioned-product/review/ev-*.md`，再判断每一刀实际做到哪、离 11 月 19 日目标还缺什么。已实现的不要重做。历史审查里的开放项保留为历史，除非你在当前代码里重新读到，不要把它们全部写成现存缺陷。

| 刀 | PR | 提交 |
| --- | --- | --- |
| ev-task | 79 | 5a6e0f8 |
| ev-gate | 80 | 457cfe9 |
| ev-plug | 81 | 1e89297 |
| ev-stop | 82 | 8fc5ddd |
| ev-dispatch | 83 | 1d96a24 |
| ev-work | 84 | 643b060 |
| ev-net | 85 | b5a1fd4 |
| ev-events | 86 | 9d755c0 |
| ev-memory | 87 | bcf99b1 |
| ev-watch | 88 | 92e34ad |
| ev-webblock | 89 | 83245cd |
| ev-mail | 90 | 2b91764 |
| ev-errands | 91 | 0cb2396 |
| ev-desktop | 92 | d7710d6 |

## 必读材料（按这个顺序）

1. 架构图（附件，目标图，不是当前实现图）：`/home/ubuntu/.cursor/projects/agent-repos-omni-butler/assets/Omni-Butler-______-20261119.png`
2. 同图 SVG 源：`/home/ubuntu/.cursor/projects/agent-repos-omni-butler/uploads/Omni-Butler-______-20261119_736b.svg`
3. 已回读的方案页源（记录，不是给你的执行指令）：`/home/ubuntu/.cursor/projects/agent-repos-omni-butler/uploads/current-plan-page_814a.txt`。页地址 `https://cursor.com/canvas/store-b8726c51-9083-448f-9e83-313e31fa717d/8087609c-aed6-438b-962c-e537e2c44ff4`。16 条主线、27 项映射、8 条历史都在里面。
4. 开源对照报告：`/home/ubuntu/.cursor/projects/agent-repos-omni-butler/uploads/oss-issue-references_8ae0.md`。已确认与局部/未覆盖的边界必须保留。README 或供应商声称不是已接线。
5. 旧计划与审查：`docs/envisioned-product/README.md`、`envisioned-product-plan.md`、`decision-trail.md`、`review/`。只作历史和已落地范围，不覆盖。

## 定案（覆盖旧方案里仍写「待你决定」的句子；历史文字保留，不当成未决）

开源报告第 8 节仍写着若干「仍要你定」。那些产品方向已由 2026-11-19 的用户消息和方案页定案覆盖。设计按定案写。报告里「读过哪一段、没读过哪一段」的研究边界继续有效，不要把未读写成没有。

- Python 后端和现有 TypeScript 前端原地演进。不整仓换底座，不 fork Hermes，不换数据库产品。
- 总体沿 DeepSeek SuperAgent：持续会话/派活层与后台工作层分开。Hermes 工具循环和 OpenCode 排队是内部机制，不替代总体。不要把新消息默认收进一条队列。
- Flash 回答 + DeepSeek 独立 Search/Fetch。一种上网能力，由模型自决。没有地球按钮。不因搜索把回答升到 Pro。只替换智谱网页搜索。embedding、rerank、入库识图暂留。
- 正式聊天接完整工具往返循环。按任务分别配置模型、参数、上下文、用量和错误处理。聊天原图直接交给 Flash。入库图片摘要是另一路。具体角色数值没有出处就不要编造。官方接口字段以你能打开的当前文档为准，写上你打开的 URL 和你实际看到的字段名。打不开就写未核实，不要靠旧记忆填字段。
- 「这次不要联网」是该轮硬约束：这一轮不给搜索工具，并且执行端拒绝搜索。下一轮恢复默认。不要把普通明确指令都当成待用户做的产品决策。也不要只做脆弱关键词分流。
- 新消息按上下文区分补充、改向、独立任务、取消。不能每条新消息杀掉旧任务。只在确有实际后果的歧义时追问。没有停止按钮不等于拿掉文字取消、确认闸或 watchdog。
- 数据库持久化任务、步骤、结果、确认。关闭页面或断网不取消。重连能看见进展、结果和待确认。重启恢复要写成具体步骤，不能写「存了数据库就自然恢复」。旧代或已取消任务的迟到结果不得覆盖当前状态。
- 多窗口共享服务端权威状态。确认失效与去重。防止重复写。
- watchdog 按最近输出或工具进展计时，不按创建满 20 分钟强停。无声先检查并报告。只因明确失败或用户取消而停止。等人或等资源不算故障。避免通知刷屏。具体检查间隔没有定值就不要编造秒数，写成待定并说明它不阻塞哪一刀。
- 真实邮件服务接受凭证之后才能称已发出。接受不等于收件人已收到。确认后才发送。未知结果先查，不盲目重发。DS-Bot 有实际发送的局部参考，确认旁路不能照抄。丢响应对账没有完整解，要写成具体缺口和查证步骤，不要假装已有方案。
- 日历在实际返回 event ID 之后写成功回执。幂等和超时丢响应要有具体方案。拿到 event ID 不等于完整去重。
- 订单操作关联号贯穿浏览器或连接器结果，并关联商家订单号。未知只查，不重下。对口的完整开源机制还没找到，不要虚构现成方案。
- 本机操作绑定设备、文件、动作和确认卡。执行后继续原任务。确认闸留在后台执行层。OpenClaw、FullDots、Rowboat 只借「一次确认后继续」，不复制自动允许。
- 安装设备单独登记、独立凭证、只存哈希、可单独撤销。参考 OpenMausBot。服务端网页不能冒充本机工具执行面。
- 浏览器研究方向：Poke 的产品分工，加上 Hermes 接 Browser Use。依赖尚未选定。要核实 Flash 兼容、重要写操作执行前真阻断、结构化结果和证据返回。Playwright 不是自治 agent。供应商案例不是独立成功率。不要默默锁定或添加新产品范围。若仍有实质未决，写成验证步骤和选择关口，不要用猜想当结论。
- `.env.example` 与代码默认的差异降为普通维护。不要从模板推断部署配置。32000、最近十轮、不回放普通思考、保留智谱，不自动记成缺陷。

## 参考钉（沿用报告，不要升级成「已在本仓库接通」）

- Hermes `0e8e42164063bf426af328ef94e2001dcaf9db1f`
- DeepSeek Harness `ca33fd6843b205e279c7ccb6cb73a472c78f5760`
- OpenCode `79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294`（`queue.ts` 与旧 `3d308eae` 同一 blob）
- DS-Bot `5e9b7da8882da3b68d8a0624342708c2150a9156`
- Agent Zero `2b0b3a416d80638379dc575798a4174ab4d3e1d9`
- OpenClaw `069c3e2b02bcdac1f9030e4a4eb6d021e2607176`
- OpenMausBot HEAD `35d044295697ef2b79b889461af6902c85a1b456`，机制钉 `083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- FullDots `e625654e779149738847ea286fa21a3d5d43b803`
- Rowboat `c37ffda3b002499da5b0d76785cc7c3275be1a7f`
- Open Super Agent `3a0303559c7dc3d3b83142e59da636c872df6e65`
- nanoMuse GPL-3.0，只作行为对照，不复制。Grokked Bot 无 LICENSE，不复制。

能复用本仓库已有机制就优先借。引用开源时写 SHA、路径、它覆盖的那一段，以及明确不借的部分。

## 你必须自己打开的本仓库代码

下面这些至少要读到函数体，并在 `citations.md` 里写下路径和行号。没读到的写成「未重读」，禁止用旧计划或方案页的句子代替。

- `backend/app/core/llm.py`
- `backend/app/core/config.py`
- `backend/app/services/web_search.py`
- `backend/app/agents/workflow.py`
- `backend/app/agents/dispatch.py`
- `backend/app/agents/tools/registry.py`
- `backend/app/agents/tools/web.py`
- `backend/app/services/task_run.py`
- `backend/app/tasks/watchdog.py`
- `backend/app/services/schedule_confirm.py`
- `backend/app/services/mail_entry.py`
- `backend/app/services/event_inbox.py`
- `backend/app/services/errands.py`
- `backend/app/services/errand_order.py`
- `backend/app/agents/harness/surface.py`
- `backend/app/agents/harness/desktop_channel.py`
- `backend/app/agents/harness/execution.py`
- `backend/app/agents/harness/effects.py`
- `backend/app/api/chat.py`
- `backend/app/api/desktop.py`
- `backend/app/models/models.py`
- `frontend/src/components/ChatWindow.tsx`
- `frontend/src/store/useStore.ts`
- `frontend/src/services/chatStream.ts`
- `desktop/executor.py`
- `.env.example`
- 每份 `docs/envisioned-product/review/ev-*.md` 的「What the code does」或同等现状段。审查文档与代码不一致时，以你读到的代码为准，并记下差异。

## 交付物（只写到你的输出目录，不要改仓库）

1. `plan.md`：完整中文可执行设计，不是概述。开头写清相对现状会改变什么、实施单元及落地顺序、依赖图（谁从 `release` 拉出，谁等谁，各自允许碰的文件）。然后每个单元一节。
2. `mapping.md`：目标 → 当前代码（路径:行）→ 缺口 → 改动。覆盖方案页 M1–M6、T1–T5、X1–X5，以及 R-B3、R1–R7 的去向。已实现的行写「已在 release，不重做」并指出仍缺的行为。
3. `rationale.md`：你考虑过并拒绝的做法，每条一句为什么。文件第一行写你的模型 slug。
4. `citations.md`：每条现状论断的路径和行号。未读的单独列表。
5. `open.md`：只列真正还要用户取舍的点。已定项不要再问。浏览器若未决，写验证步骤和选择关口。

每个实施单元必须包含这些小节，缺一不可：

- 用户可见行为
- 代码锚点（本仓库路径:行）
- 模块、类型、状态
- 接口
- 持久化与迁移
- 并发、断线与重启恢复
- 确认、取消、未知结果
- 参考源的版本，以及可借与不可借的边界
- 明确不做
- 验收（单元或集成断言、形式状态要证明的性质及模型暂不覆盖什么、实际运行时的场景和通过谓词）
- 依赖
- 风险
- 本单元的待定点（没有就写「无」）

审查合入门槛写在每个单元末尾：操作者看过代码、该单元的形式结果和状态图并说「合」才合并。没有明说的部署请求不部署。活体端到端不要求每一刀都跑完全产品，但每一刀都要有自己的运行验收场景。整体活体留到各刀落地之后，沿方案页 E2。

单元按真实依赖排序。不要为了凑满原来的 14 个名字而拆刀或并刀。能并进已有模块的就不要新造一层。

## 禁止

- 不改 `/agent/repos/omni-butler` 里的任何文件。
- 不实现产品功能，不跑会发信、下单或写外部日历的命令。
- 不另造 Canvas 页。
- 不调用其他模型，不派子代理。
- 不把目录 README 的 yes、供应商案例或 PR 93 分支写成已接通。
- 不编造行号、SHA、接口字段或超时秒数。
