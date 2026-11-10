# 预想点 → 可抄源码（产品半边）

范围：只对照 ①入口、④派活≠干活、⑥规划-执行-反思、⑨长记忆/少打扰/脉搏、⑪服务侧，以及 PA 材料里的记忆/RAG/日历邮件工具点。  
约束：不推翻「不做外接聊天总机 / 手机端 / 云手机」；不整仓换底座；模式借鉴。  
材料：`personal-agent-oss-comparison.md`（2026-11-04）、Page「开源 Personal Agent 源码对比」、`envisioned-product-complete.mmd`、本地 `oss-sources/`。  
生成：2026-11-06 Asia/Singapore。

---

## ① 入口形态

| 预想节点 | 预想行为 | 优先抄自 | 次选 | 别抄 |
|---|---|---|---|---|
| desk | 桌面安装包 = 完整产品：聊天窗 + 本机动手同一应用 | **Leon 2.0**（develop）：本机个人助手 OS；`apps/web` + Satellite 本机工具；`core/context/ARCHITECTURE.md` / `LEON.md` | Eigent：Electron 桌面壳形态（`electron/`）；OpenMausBot `electron/main.mjs` 仅「同应用内嵌本机能力」形状对照 | OpenClaw / Hermes / ZeroClaw 当「外接多聊总机」产品根；千问 Go 云手机；手机端产品；Open Interpreter 当生活助手底座 |
| web | 网页版保留但瘦：对话 / 日历确认卡 / 知识库；无本机动手 | **AnythingLLM**：`frontend`（Vite+React）+ `server` 工作区聊天；瘦能力 = 文档+对话+agent 插件，无强制本机 CUA | Khoj：`interface/web`；网页 RAG/对话侧车 | 网页遥控独立本机助手；把桌面 CUA 塞进网页；Khoj WhatsApp/Phone 客户端当主入口 |
| mailin | 邮件当一等入口；来信也可当事件 | **AnythingLLM** AIbitat 插件形状：`gmail` / `outlook`（与日历同级插件）；对照 nanobot「邮件一等入口」产品外形（Page） | OpenMausBot `server/email/host.ts`：邮件工具宿主、scope 验权、内部 MCP；Hermes「接邮件通道」仅概念 | 外接微信/Slack/TG/WhatsApp 聊天总机当产品主入口；把邮件通道当成已有确认卡日历产品 |

---

## ④ 派活 ≠ 干活

| 预想节点 | 预想行为 | 优先抄自 | 次选 | 别抄 |
|---|---|---|---|---|
| observe | 观察来意 / 事件要干什么（只理解，不执行） | **DSH SuperAgent 原型**：Router 常驻筛触发、听人与事件；自己不干活 | Leon：`core/brain/brain.ts` 入口理解；nanobot 消息总线进循环前的路由外形 | 派活层直接调日历写 / 出确认卡；Hermes 主循环一把梭调工具 |
| assign | 分派本轮任务；自己不调工具、不出确认卡 | **DSH**：Worker 按 Ticket 隔离会话/工作目录；任务带授权范围；相关通知可直通干活侧 | NanoClaw：宿主薄、干活关容器、钥匙留宿主；Eigent 仅「多助手并行」UX 对照 | DSH 的 PR/分支/裸用户进程边界当日历管家边界；整仓换 Eigent / Open Interpreter；派活层出确认卡 |

---

## ⑥ 规划-执行-反思

| 预想节点 | 预想行为 | 优先抄自 | 次选 | 别抄 |
|---|---|---|---|---|
| plan | 规划：用哪些能力、排顺序 | **Leon**：Skills→Actions→Tools→Functions；`llm-duties/react-llm-duty.ts` + `tool-manager/toolkit-registry.ts` | AnythingLLM AIbitat：`server/utils/agents/aibitat/index.js` 多 agent 图引导；Letta 子代理（general/forked/recall） | 把确认闸本身当 OSS 主抄点；满分异步 ReAct 一次抄满（预想先用简化可打断） |
| has | 还有下一步？循环闸 | **Leon** `core/turn-lifecycle/` 回合生命周期 | AnythingLLM AIbitat 轮次/`maxRounds` + abort 边界（同文件 `_aborted`） | 把「投机执行」嵌进循环（预想先不做） |
| pick | 按授权选能力 | **Leon** progressive tool loading + controlled/agent 模式；授权叠层思路（对照 DSH Mandate） | AnythingLLM：工具可见与 plugin `use()` 安装；OpenMausBot connector scope（`email/host.ts` 验权）仅次选 | 上下文变长就抬权；Hermes 默认全机权限；隐藏工具当授权 |
| reflect | 看结果够不够 | **Leon** agent 环反思；AnythingLLM tool 结果回流聊天 | Letta dreaming/sleeptime：后台巩固（偏记忆，反射思路可借） | 结果未知就盲目重放不可重放写 |
| answer | 写最终回复 | **AnythingLLM** `server/utils/chats/stream.js` 流式回复形状 | Leon 经 Socket.IO/HTTP 回客户端；Khoj conversation processor | 派活层代写最终回复并绕过干活内核 |

---

## ⑨ 长记忆 / 少打扰 / 主动脉搏

| 预想节点 | 预想行为 | 优先抄自 | 次选 | 别抄 |
|---|---|---|---|---|
| conf | 把握够高？开口前门槛 | **DSH**：主动档位 关 / 先记账给人看 / 自动，默认先记账 | Leon pulse 从拒绝中学习；AnythingLLM `request-user-input` 仅「要人动手」形状 | 低把握也刷屏草稿卡；无人确认自动写入 |
| audit | 低把握：先记账到审计账本；可读，不出草稿卡 | **DSH** 安静默认 + 记账给人看 | OpenMausBot `server/decision-log.ts`：允许/拒绝/出卡 NDJSON 审计（次选，偏授权留痕） | 用提示词恢复代替幂等账本；内存审批表当持久队列 |
| speak | 有结果或要人动手时才开口 | **Leon** 有界脉搏 + 少事/tick；DSH 少打扰 | nanobot 默认拒收陌生人消息（入口安静策略对照） | 脉搏/事件绕过确认闸开口后直接写世界 |
| mem | 可查偏好/要点档案；读写可审计；不默默抬权；变更账本 | **Leon**：分层记忆 + 策展 `OWNER.md` + QMD 检索；经验写成 `SKILL.md`（有上限） | **Letta Code**：MemFS（git 背书记忆）+ `/dream` `/sleeptime`；docs.letta.com/letta-code/memory | AnythingLLM 浅 memory（≤20/≤5 facts）当深记忆；**Letta AgentFile (.af) 已废弃**；整套第二大脑乱改记忆；Khoj 文档 RAG 冒充身份档案 |
| pulse | 主动脉搏：到点提醒/催办，经外壳推；写操作仍走确认闸 | **Leon**：`PULSE.md`，≤1 matter/tick，拒绝可学 | Letta hooks/permissions/crons/reminders；AnythingLLM `create-scheduled-job` | 脉搏绕确认闸；把 cron 当助手核或确认闸 |

---

## ⑪ 服务侧（知识库/日历/沙箱/队列等）

| 预想节点 | 预想行为 | 优先抄自 | 次选 | 别抄 |
|---|---|---|---|---|
| kb | 知识库插头（文档 RAG，非身份记忆） | **AnythingLLM**：collector + 多向量后端；MIT，活跃 | **Khoj**：`processor/content` + embeddings + pgvector（本地 `oss-sources/Khoj/docker-compose.yml`） | 整仓 fork Khoj 进专有 SaaS（**AGPL-3.0 传染**）；用 RAG 顶替 `mem` 身份档案 |
| cal | 日历插头；会前文档/会后纪要 | **AnythingLLM**：`server/utils/agents/aibitat/plugins/google-calendar/`（本地摘录：list/get/create/update 读写分列） | 同仓 `gmail` / `outlook` 插件形状；自有确认卡产品仍在 Omni-Butler | 插件级日历 = 确认卡产品；无人确认自动 `GCalCreateEvent` |
| sandbox / runner | 沙箱跑代码插头；断网、受控；跑代码小服务 | **Khoj**：docker `sandbox` = Terrarium（`KHOJ_TERRARIUM_URL`）；`processor/tools/run_code.py` | Open Interpreter 仅「沙箱/本机编码」对照，不当 PA 底座 | 默认全机 shell（Hermes）；把沙箱当授权或隐藏工具 |
| api | 主服务 | **AnythingLLM** `server` Express+Prisma 形状（对照用） | Khoj Django `server`；Leon `server/` runtime | 整仓换底座；把 Node 巨仓当 Python 管家根 |
| pg | 数据库 | Khoj/AnythingLLM：Postgres 习惯（Khoj compose 明示 `pgvector/pgvector:pg15`） | AnythingLLM Prisma 多库 | — |
| vec | 向量库 | **AnythingLLM** 默认 LanceDB，可选 PGVector 等 | Khoj pgvector 一体 | AGPL 约束下勿直接并 Khoj 进闭源发行 |
| q | 队列缓存 | AnythingLLM scheduled jobs / agent cron 形状 | Letta schedules；OpenMausBot `routines.ts` 仅次选节奏对照 | 内存队列当持久投递；事件投机执行 |
| files | 文件存储 | Khoj 文档同步卷；AnythingLLM workspace 文件 | Leon 工作区/context files | — |
| cron | 定时后台；不当助手核、不定确认闸 | **Letta** crons/reminders；**Leon** pulse 调度边界 | AnythingLLM `create-scheduled-job`；DSH 订阅直通「不定每次绕派活」 | cron 直接写日历/付钱；看门狗经 cron 改授权 |
| （邮件工具点） | 邮件读写作为工具/事件源 | AnythingLLM `gmail`/`outlook` plugins | OpenMausBot `server/email/host.ts`；nanobot 邮件通道外形 | 外接聊天总机；邮件发送无人确认 |
| （插件形状） | 统一工具插头旁的插件安装形态 | AnythingLLM AIbitat `use(plugin)` + `plugins/google-calendar` | Leon Skills 分层；Letta `~/.letta/channels/<id>/` 仅通道插件概念（产品不做外接总机） | 把 Letta/OpenClaw 多聊通道产品形态搬进来 |

---

## 缺口

1. **Leon / Letta 无本地源码摘录**：`OWNER.md`、`PULSE.md`、`turn-lifecycle/`、MemFS 路径以 comparison.md / 上游文档为准，未在 `oss-sources/` 落盘校验。  
2. **DSH 原型无本地文件**：派活≠干活、Mandate/安静档位为 Page + 公开仓概念级；编码半边需补 Router/Worker/Ticket 路径级对照。  
3. **AnythingLLM** 本地仅有 `google-calendar/index.js`、`aibitat/index.js`、`stream.js`；`gmail`/`outlook`/`memory`/`create-scheduled-job` 插件正文未摘。  
4. **确认闸（⑦）**：产品半边按任务要求不把闸当 OSS 主抄点；Pi 确认问法 / OpenMausBot decision-log 留给编码或壳子半边。  
5. **③事件驱动 / ⑩看门狗 / ②外壳**：不在本次产品半边范围；若要对齐，编码半边补 DSH 直通、OpenMausBot `turn-watchdog.ts` / `app-events.ts`。  
6. **许可落地**：Khoj AGPL 仅可作侧车/参考；闭源发行勿并代码——法务未在本文件展开。
