# Omni-Butler 预想产品实施计划

这份计划把十二个模块排成十个实现 PR。读者是稍后执行的云端 agent，以及只有说「合」才合并的操作者。

完整产品是一个桌面安装包，聊天和本机动手在同一个包里。网页留下对话、日历确认卡和知识库。

规则是在本仓原地演进。派活不干活。确认闸不归外壳。停止不撤回已发出的邮件、日程或付款。Khoj 只作侧车。

实现顺序是 H1、H2，然后 H3 与 P1，然后 H4、P2、H5，然后 P3 与 P4，最后 P5。⑫ 不单开 PR。

本文件不是开工许可。操作者明确说开工之后才执行。合并只在操作者说「合」之后。

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA.

正文是怎么做。附录解释原因并记录对照。做某一刀之前，先读该刀的小节，再读 Appendix E 里同号模块。

程序按 `pstack/skills/poteto-mode/playbooks/autopilot-stack.md` 跑。操作者合并。全部实现 PR 停在可合并。不要自行合并。

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

产品代码的主干是 `release`，不是 `main`。下面武装步骤里的 `git show origin/main:` 用来对一下 poteto 技能。本仓没有这些路径时，改读本机 Cursor 插件里的同名文件，并在决定记录里写明实际路径。

## Program checklist

### Arm the program

- [ ] 向操作者说明这份协议和 `docs/envisioned-product/PLAN.md`，然后停。只有操作者明确说开工才继续。
- [ ] 开工时从主干重读下列文件。每个周期再读一次。
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/cursor-team-kit/control-ui/SKILL.md`。写这份计划时，本机插件缓存里没有 control-ui 的 SKILL.md。找不到就改用浏览器操作 nginx 的 80 端口，并在决定记录里写明实际路径。
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/technical-writing/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/unslop/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/show-me-your-work/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/principle-prove-it-works/SKILL.md`
- [ ] 操作者说开工之后，把审计周期臂成 `/loop 1h`，使用下面的周期提示。不要靠记忆记周期。
- [ ] 周期提示原样使用。 "Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported, such as a PR opened, a code-ready head, a round launched or closed, a verdict, a merge, a stuck agent and the action taken, a blocker added or cleared, or a decision only the operator can make. Name every such change and nothing else. Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text. Either way, log this tick's row in your decision trail. The row names the items reported, or none."
- [ ] 操作者说暂停时，立刻让每个所有者停止写入。

### Spawn owners

- [ ] 每个实现 PR 一个所有者，生命周期按 `playbooks/autopilot-stack.md`。所有者做到可合并为止，不合并。
- [ ] 依赖如下。父 PR 还没在 `release` 上时，不要开子 PR，也不要叠栈。
  - [ ] H1 从 `release` 开。
  - [ ] H2 在 H1 进入 `release` 之后从 `release` 开。
  - [ ] H3 和 P1 都在 H2 进入 `release` 之后从 `release` 开。二者互不改对方的文件。
  - [ ] H4 在 H3 进入 `release` 之后从 `release` 开。
  - [ ] P2 在 P1 进入 `release` 之后从 `release` 开。
  - [ ] H5 在 H3 进入 `release` 之后从 `release` 开。它不改 H4 或 P2 的文件。
  - [ ] P3 在 H4、P2、H5 都进入 `release` 之后从 `release` 开。
  - [ ] P4 在 P2 进入 `release` 之后从 `release` 开。它不改 P3 的记忆与脉搏文件。
  - [ ] P5 在 P4 进入 `release` 之后从 `release` 开。
- [ ] 守住文件边界。H1 只动任务、意图、执行闸和工具契约字段。H2 只动确认凭据和现有确认卡的写入路径。H3 只动停止入口、代次作废和请求前检查。H4 只动事件收件箱、回执和邮件入站适配。P1 只动 `router.py` 与 `workflow.py` 的分工。P2 只动模型联网开关、旧搜索在同一轮里的退场，以及按契约重试。H5 只动看门狗进程和它对停止入口的调用。P3 只动记忆账本和脉搏入队。P4 只动桌面壳、瘦网页执行拒绝和本机工具注册。P5 只动办事连接器、服务器浏览器和定时任务的边界。
- [ ] 守住审阅门。H2、H3、H4、P2、H5、P3、P4、P5 改变交互。它们在合并前要操作者在聊天里看过截图和视频。H1 和 P1 不改交互。

### PR mechanics, for every PR

- [ ] 先定一次代码托管。默认 `gh`。若 `command -v origin` 成功且 Origin 能解析本仓，就用 `origin pr`。做不到就留在 `gh` 并记下来。不要要求 `gt`。本轮环境若提供内置 PR 工具，创建和修改走该工具。
- [ ] 按 Opening a PR 把 PR 开成可审，不要开成草稿。基线是 `release`。
- [ ] 面向 PR 的那次推送之前，在 `backend` 跑一次与该刀相关的 pytest，并在 `frontend` 跑 `npm test`，当该刀改了前端时。推送时打开 git 钩子。
- [ ] 每次提交前按 deslop 看一遍差异。送审前按 no-comments 看一遍。
- [ ] 每条 Bugbot 和安全审查意见都要分流。
- [ ] 在报告可合并之前变基到当前 `release`。修复轮次保持该合并基线。只有和 `release` 的 `git merge-tree` 冲突、由主干改动引起的 CI 失败，或同一文件上的已知并发改动落地时，才再次变基。

### Verdict and merge, for every PR

- [ ] 完整 swarm 只在代码就绪的那个 head 上跑一次。只有新行为、重做或大块新差异才再跑一轮完整 swarm。变基、解冲突或小修只在新 head 上派一个核对者。只改措辞则不派。按 `pstack/skills/swarm/SKILL.md` 跑。一条门禁 lane。该 PR 的十条 live lane。该 PR 的 perf lane。至少两条审计 lane，各自读差异和证据，不采信 PR 正文。根代理在下结论前审计这些证据。
- [ ] 按执行手册的阻断规则判断每条发现。没有阻断项才算干净。标了 Regression lane against trunk 的 lane 若失败在 trunk 已有行为上，记在 trunk 上，不阻断该 PR。新 head 要新结论。`playbooks/shipping.md` 里 patch-id 仍有效的结果可以沿用。
- [ ] 所有者不合并。根代理把可合并的 PR 交给操作者。操作者说「合」之后才合并。没说之前，任何人不再往该分支推。

### Boot recipe, for every live lane

每条 live lane 在该 PR 的 head 上使用自己的云虚拟机。网页和桌面都用 cursor-team-kit 的 `control-ui` 驱动。技能文件不在本仓时，仍用浏览器操作 nginx 的 80 端口，并把这件事写入 Appendix C 的风险记录。

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`。
- [ ] 在仓库根目录启动 `docker compose up -d`。等到 `curl -sS http://127.0.0.1/health` 成功，并且浏览器能打开登录页。桌面刀还要启动该 head 打出来的安装包。
- [ ] 只用 `control-ui` 输入。只读诊断是 `docker compose ps`、`curl -sS http://127.0.0.1/health`，以及 `docker compose logs api --since 5m`。
- [ ] 每张截图保存到 `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png`，并在报告里返回路径。


## 钉住任务代次和执行闸 H1

**Depends on.** None. 从 `release` 开。

**Files.**

- [ ] Edit `backend/app/models/models.py`。
- [ ] Edit `backend/app/agents/harness/gateway.py` 里的 `invoke`。
- [ ] Edit `backend/app/agents/tools/registry.py` 里的 `ToolSpec`。
- [ ] Edit `backend/app/agents/harness/constitution.yaml` 的 `omni.allowed_tools` 只作为白名单来源，不把记忆文本算进去。
- [ ] Create `backend/app/agents/harness/task_run.py`。
- [ ] Create `backend/alembic/versions/0011_task_generation.py`。写计划时最新修订是 `0010_schedule_confirm_claims.py`。若 `release` 已经有更后的修订，用下一个空号，不要覆盖。
- [ ] Create `backend/tests/test_task_generation.py`。
- [ ] Create `backend/tests/test_execute_gate.py`。

**Build.**

- [ ] 先读 Appendix E 的 ⑤ 和 ⑧。这一刀钉住任务、意图和副作用枚举。确认凭据表留给 H2。不要再发明一套并行状态。
- [ ] 在 `task_runs` 写入身份、表面、代次、状态和叫醒来源。状态只允许运行、等人、等资源、停止。
- [ ] 在 `intents` 写入去重键。同一用户、同一去重键，待处理行唯一。这一刀可以还没有脉搏和事件生产者。
- [ ] `ToolSpec` 增加副作用字段，取值为只读、幂等写、不可重放写，并增加允许的表面。现有 `kb` 与 `web` 先标只读。`calendar` 先标不可重放写。`sandbox` 先标只读，因为 `workflow.py` 现在传入 `network` 为 `none`。
- [ ] `invoke` 在 `spec.execute` 之前检查表面、白名单、本轮允许和任务是否已停止。网页表面拒绝本机工具名，即使注册表里看得到。
- [ ] 记忆文本不进入这道检查。`inject_system_memory` 仍只拼提示词。
- [ ] 不要在这一刀拆 `route` 和 `run_agent_workflow`。不要删智谱搜索。不要改确认卡的写入。
- [ ] 不要新增 ⑫ 里的即时通讯入口、手机端、云手机、看屏点击或第二条上网路。

**You see.**

- [ ] 现有网页聊天仍能回复，日历草稿卡仍在确认前不写库。
- [ ] api 审计日志对日历调用出现表面和代次。网页调用本机工具名时 `invoke` 返回拒绝。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_task_generation.py` 与 `backend/tests/test_execute_gate.py`。运行 `cd backend && python -m pytest tests/test_task_generation.py tests/test_execute_gate.py tests/test_workflow.py tests/test_router.py tests/test_calendar_card.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各登录，并发送「你好」。 Save `/tmp/swarm-h1/worker-1/greeting.png`. Pass when 两边都出现助手气泡，head 没有多出确认卡。
- [ ] Lane 2. 在 head 上说「明天下午三点开产品会」。 Save `/tmp/swarm-h1/worker-2/draft-card.png`. Pass when 页面出现草稿日程卡，日程列表在点确认前不增加这条。api 日志里这条日历调用带 `surface=web`。
- [ ] Lane 3. 在 head 上点确认，写下这条本地日程。 Save `/tmp/swarm-h1/worker-3/confirm-still-writes.png`. Pass when 列表里多出一条，时间与卡片一致。
- [ ] Lane 4. 在 head 上再开一个会话，发送「在吗」。 Save `/tmp/swarm-h1/worker-4/second-session.png`. Pass when 新会话有回复，上一个会话的草稿卡仍在。
- [ ] Lane 5. 在 head 上打开知识库页，不上传文件，回到聊天发送「库里有什么」。 Save `/tmp/swarm-h1/worker-5/empty-kb.png`. Pass when 页面给出回复或明确的空库说明，不出现未捕获异常页。
- [ ] Lane 6. 在 head 上点停止生成，当气泡还在增长时。 Save `/tmp/swarm-h1/worker-6/stop-visible.png`. Pass when 页面不再显示生成中。这一刀不要求服务端线程已停，那是 H3 的门。
- [ ] Lane 7. 在 head 上用侧栏退出再登录。 Save `/tmp/swarm-h1/worker-7/relogin.png`. Pass when 会话列表还在，密码错误时进不去。
- [ ] Lane 8. 在 head 上把浏览器宽度收到窄屏，再打开同一条聊天。 Save `/tmp/swarm-h1/worker-8/narrow.png`. Pass when 输入框和停止按钮仍可点。
- [ ] Lane 9. 在 head 上刷新页面。 Save `/tmp/swarm-h1/worker-9/reload.png`. Pass when 已确认的日程还在列表里，草稿卡按现有过期规则显示。
- [ ] Lane 10. 在 trunk 与 head 上各打开 `/health`。 Save `/tmp/swarm-h1/worker-10/health.png`. Pass when 两边都是成功响应。head 的聊天页仍从 nginx 的 80 端口进来。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 一次 `gateway.invoke` 调用知识库工具的 `elapsed_ms`，trunk 与 head 都量。再量 head 上被拒绝的本机工具调用的 `elapsed_ms`。
- [ ] Probe. 交错各跑 20 次固定夹具。先 trunk，再 head，再 trunk。本机拒绝只在 head 上量，并单独给出绝对耗时。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的知识库中位数不得超过 trunk 中位数 20 毫秒。本机拒绝的中位数必须低于 50 毫秒。用户等到的现有聊天首字，用架构文档里的 `time_starttransfer` 量「你好」，head 不得比 trunk 慢 300 毫秒以上。

**Review gate.** None. 这一刀不改变页面上的操作。


**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 把确认收成一张凭据 H2

**Depends on.** H1 已在 `release` 上。

**Files.**

- [ ] Edit `backend/app/services/schedule_confirm.py`。
- [ ] Edit `backend/app/api/calendar.py` 的 `create_calendar_event`、`patch_event`、`delete_event` 与 `open_confirm_claim`。
- [ ] Edit `backend/app/agents/tools/calendar_tool.py` 的 `execute_calendar`。
- [ ] Edit `backend/app/api/chat.py` 里 `_apply_regenerate` 对 `cancel_open_schedule_claims` 的调用，改为作废新凭据。
- [ ] Edit `frontend/src/components/ScheduleCard.tsx`、`FollowupCard.tsx` 与 `MeetingBriefCard.tsx`，只改提交的凭据字段，不把批准逻辑搬进外壳。
- [ ] 在 H1 的迁移之后新增确认凭据迁移。只有 `0011` 仍是头时，文件名才用 `0012_confirm_credential.py`。
- [ ] Delete `ScheduleConfirmClaim` 作为写入来源。迁移把未消费的旧 claim 作废，不自动执行。

**Build.**

- [ ] 先读 Appendix E 的 ⑦。凭据绑定动作、参数指纹、作用范围和任务代次。
- [ ] 执行前重读凭据和当前代次。参数变了必须新批。任务停止则凭据失效。
- [ ] 同一去重键只留一张待确认卡。第二次打开要么碰到已有凭据，要么先作废再发一张。
- [ ] 日历新建、改期、删除继续在确认前不写 `calendar_events`。这是 `execute_calendar` 今天的行为，保持住。
- [ ] 会后跟进若只是草稿，仍不发送。发送动作一旦存在，必须走同一张凭据。会前简报保持只读，不借这张卡写日程。
- [ ] 不要把确认框放进 `ChatWindow` 的发送按钮里。外壳只渲染卡。
- [ ] 不要用内存 Map 当队列。DS-Bot 的 `approvals.js` 只借取消和过期回答，不借它的内存表。
- [ ] 这一刀不实现办事付款和本机批准的产品界面。表结构要能装下这些动作，避免 P4 和 P5 再造一张卡。

**You see.**

- [ ] 改时间后再确认会失败，直到新卡被批准。
- [ ] 双击确认只留下一条日程。
- [ ] 重新生成助手消息时，旧的待确认凭据作废。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 运行 `cd backend && python -m pytest tests/test_schedule_confirm_claims.py tests/test_calendar_card.py tests/test_calendar.py tests/test_meeting_followup.py tests/test_calendar_google.py -q`。
- [ ] 运行 `cd frontend && npm test`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各创建一条本地日程并确认。 Save `/tmp/swarm-h2/worker-1/create.png`. Pass when 两边列表都多一条。head 的凭据表有一条已消费记录，动作、参数指纹、作用范围和代次都有值。
- [ ] Lane 2. 在 head 上改卡片时间后再确认。 Save `/tmp/swarm-h2/worker-2/reapprove.png`. Pass when 旧指纹不能写下去。新时间写入，旧凭据作废。
- [ ] Lane 3. 在 head 上打开取消卡并确认删除。 Save `/tmp/swarm-h2/worker-3/cancel-event.png`. Pass when 这条日程状态变为已取消。没有第二条删除请求。
- [ ] Lane 4. 在 head 上对同一张卡点两次确认。 Save `/tmp/swarm-h2/worker-4/double-click.png`. Pass when 只留下一条日程。第二次响应说明凭据已用过。
- [ ] Lane 5. 在 head 上让卡片过期后再点确认。 Save `/tmp/swarm-h2/worker-5/expired.png`. Pass when 不写入。页面说明要重新批。
- [ ] Lane 6. 在 head 上打开发送前的会后跟进卡，不点确认。 Save `/tmp/swarm-h2/worker-6/follow-draft.png`. Pass when 不发出邮件。页面上的草稿仍可复制。
- [ ] Lane 7. 在 head 上完成一次会前简报。 Save `/tmp/swarm-h2/worker-7/prep.png`. Pass when 简报出现。没有新建或改写日程。
- [ ] Lane 8. 在 head 上改日程标题，走已有的冲突或重名路径。 Save `/tmp/swarm-h2/worker-8/rename.png`. Pass when 未确认前远端标题不变。确认后只改这一条。
- [ ] Lane 9. 在 head 上用第二个浏览器登录同一用户，看同一张待确认卡。 Save `/tmp/swarm-h2/worker-9/two-tabs.png`. Pass when 只有一个待处理凭据。一边确认后，另一边再点不再写一次。
- [ ] Lane 10. 在 head 上拒绝或关闭卡片。 Save `/tmp/swarm-h2/worker-10/dismiss.png`. Pass when 凭据作废，日程列表没有新行。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 从确认 POST 到成功响应的服务端耗时，trunk 与 head 都量。
- [ ] Probe. 交错各确认 20 条本地日程。先 trunk，再 head，再 trunk。使用同一条固定标题夹具。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的 p95 不得超过 trunk 的 p95 加 100 毫秒。用户看到列表出现新日程的时间不得超过 trunk 的同一时间加 500 毫秒。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/h2-review-reapprove.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/h2-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 让停止在发出请求前生效 H3

**Depends on.** H2 已在 `release` 上。

**Files.**

- [ ] Edit `frontend/src/services/chatStream.ts` 的 `stopChatStream`。
- [ ] Edit `frontend/src/components/ChatWindow.tsx` 的停止按钮，让它调用服务端停止，不只调用 `AbortController`。
- [ ] Edit `backend/app/api/chat.py` 的 `chat`。今天它在 `request.is_disconnected` 时 `wf_task.cancel()`，而工作跑在 `asyncio.to_thread` 里。
- [ ] Edit `backend/app/agents/harness/gateway.py`，在真正调用外部客户端之前再查一次代次。
- [ ] Edit `backend/app/agents/harness/task_run.py`，提供唯一的停止函数。

**Build.**

- [ ] 先读 Appendix E 的 ②。停止传入子任务、排队意图，以及请求发出之前。
- [ ] `stopChatStream` 先请求服务端停止，再中止浏览器读取。
- [ ] 停止函数把该任务标为停止，作废该代次的凭据和排队意图，并让工作线程在下一次检查时退出。
- [ ] 证明只 `cancel` asyncio 任务不足以停下线程。用测试把线程里的下一步工具调用卡住，直到它看见停止。
- [ ] 已经发出的邮件、日程或付款不调用远端撤销。页面说明可能已经发出。
- [ ] 迟到结果看见旧代次就丢弃。新消息使用新代次。
- [ ] 不要在外壳里批准或拒绝确认卡。
- [ ] OpenMausBot `client.ts` 的活动检查和 DS-Bot `turns.js` 的迟到抑制是机制来源。实现前按 Appendix E 的 SHA 重读那几行。不要搬它们的群聊壳。

**You see.**

- [ ] 点停止后，后续工具调用不再发出。
- [ ] 已确认的日程还在。
- [ ] 停止后的新消息能正常回复。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_stop_propagation.py`。运行 `cd backend && python -m pytest tests/test_stop_propagation.py tests/test_execute_gate.py tests/test_workflow.py -q`。
- [ ] 运行 `cd frontend && npm test`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各发送「你好」，并在首字前点停止。 Save `/tmp/swarm-h3/worker-1/stop-early.png`. Pass when head 的任务代次增加，状态为停止。trunk 只停在页面上。两边都不再刷字。
- [ ] Lane 2. 在 head 上发起一次会走日历工具的请求，在工具发出前点停止。 Save `/tmp/swarm-h3/worker-2/stop-before-tool.png`. Pass when api 日志在 `gateway.invoke` 执行前拒绝。没有新的日程草稿从这次停止后的旧代次里冒出来。
- [ ] Lane 3. 在 head 上确认一条日程之后再点停止。 Save `/tmp/swarm-h3/worker-3/stop-after-write.png`. Pass when 已写入的日程还在。页面说明停止不会撤回已发出的日程。
- [ ] Lane 4. 在 head 上停止后，让上一次请求的迟到结果回到服务端。 Save `/tmp/swarm-h3/worker-4/late-result.png`. Pass when 旧代次结果不写回会话，也不把任务从停止改回运行。
- [ ] Lane 5. 在 head 上停止一个正在等人确认的任务。 Save `/tmp/swarm-h3/worker-5/stop-waiting.png`. Pass when 待处理凭据作废。卡片不能再确认成功。
- [ ] Lane 6. 在 head 上连续点两次停止。 Save `/tmp/swarm-h3/worker-6/stop-twice.png`. Pass when 代次只按真实停止增加，不出现两条并行的停止写。
- [ ] Lane 7. 在 head 上停止后立刻再发一条新消息。 Save `/tmp/swarm-h3/worker-7/new-after-stop.png`. Pass when 新消息使用新代次，能正常回复。
- [ ] Lane 8. 在 head 上断开浏览器网络，而不是点停止。 Save `/tmp/swarm-h3/worker-8/disconnect.png`. Pass when 服务端把这次任务标为停止，工作线程里的下一步工具调用看到停止。
- [ ] Lane 9. 在 head 上对一条只读知识库问题点停止。 Save `/tmp/swarm-h3/worker-9/stop-read.png`. Pass when 停止生效。没有把只读查询记成一次外部写入。
- [ ] Lane 10. 在 trunk 与 head 上各完成一条不点停止的「你好」。 Save `/tmp/swarm-h3/worker-10/no-stop.png`. Pass when 两边都完整回复。head 的任务状态以运行结束或按现有会话保存，不误标停止。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 从点击停止到任务行变为停止的时间。trunk 没有这行状态，所以 trunk 量的是页面停止按钮把忙碌状态清掉的时间，head 同时量页面和任务行。
- [ ] Probe. 交错各停 20 次「你好」。先 trunk 的页面时间，再 head 的页面时间和任务行时间。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的任务行必须在 500 毫秒内变为停止。head 的页面清忙碌时间不得超过 trunk 200 毫秒。不要拿任务行时间和 trunk 的页面时间做比值。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/h3-review-stop-before-tool.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/h3-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 收下事件并按投递号去重 H4

**Depends on.** H3 已在 `release` 上。

**Files.**

- [ ] Create `backend/app/services/event_inbox.py`。
- [ ] 新增事件回执迁移。只有上一刀的迁移仍是头时，文件名才用 `0013_event_receipts.py`。
- [ ] Create `backend/app/services/mail_inbound.py`。
- [ ] Edit `backend/app/services/gmail.py`，只复用已有的读信能力。`search_meeting_mail` 仍是会前检索，不要把它改成总机。
- [ ] Edit `backend/app/agents/harness/task_run.py`，让直通和排队使用 H1 的意图表。

**Build.**

- [ ] 先读 Appendix E 的 ③，以及 ① 里邮件作为事件的那一段。
- [ ] 回执分三列或三个不可混用的状态，已接收、已派活、外部成功。已派活不写成外部成功。
- [ ] 去重键是来源加投递号。保存回执和入队放在同一个事务里。事务失败则不派活。
- [ ] 已有相关任务则直通该代次，不新开派活。该代次正在等人确认时，同一目标作废旧凭据并重批一张。不同目标只排队。
- [ ] 程序规则先丢噪声。模型判断放在去重之后，避免同一封信消耗两次模型。
- [ ] 不要做投机执行。不要接微信、Telegram 或 Slack。
- [ ] OpenMausBot `routines.ts` 与 `routine-event-receipts.ts` 是回执机制来源。实现前重读 Appendix E 里的行号。不要把它的七天窗口当成永久恰好一次。

**You see.**

- [ ] 同一投递号两次进入，只派一次活。
- [ ] 等人确认时，同一目标只留下一张新卡。
- [ ] 重启后同一投递号不会再派。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_event_inbox.py`。运行 `cd backend && python -m pytest tests/test_event_inbox.py tests/test_gmail_meeting_query.py tests/test_stop_propagation.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各发一条普通聊天。 Save `/tmp/swarm-h4/worker-1/chat.png`. Pass when trunk 没有事件收件箱。head 的普通聊天仍回复，且不因空收件箱而写日程。
- [ ] Lane 2. 在 head 上向测试入口投递同一封邮件两次，投递号相同。 Save `/tmp/swarm-h4/worker-2/dedupe.png`. Pass when 收件箱只有一条。回执是已接收。没有两张确认卡。
- [ ] Lane 3. 在 head 上投递一封新邮件，当前没有相关任务。 Save `/tmp/swarm-h4/worker-3/new-job.png`. Pass when 回执先到已派活。写操作仍停在确认卡，不直接发信或改日程。
- [ ] Lane 4. 在 head 上当一条任务正在等人确认时，投递同一目标的新参数。 Save `/tmp/swarm-h4/worker-4/void-reissue.png`. Pass when 旧卡作废，只重新批一张卡。没有两套写操作并行。
- [ ] Lane 5. 在 head 上当一条任务正在等人确认时，投递另一个目标。 Save `/tmp/swarm-h4/worker-5/queue-other.png`. Pass when 新意图排队。旧卡仍是唯一待确认写。
- [ ] Lane 6. 在 head 上投递一封应被程序规则丢掉的噪声邮件。 Save `/tmp/swarm-h4/worker-6/noise.png`. Pass when 没有派活，也没有确认卡。
- [ ] Lane 7. 在 head 上把回执标成已接收之后查看页面文案。 Save `/tmp/swarm-h4/worker-7/receipt-wording.png`. Pass when 文案不把已接收写成外部已经发送成功。
- [ ] Lane 8. 在 head 上让一次外部写入结果保持未知。 Save `/tmp/swarm-h4/worker-8/unknown.png`. Pass when 回执不到外部成功。核对步骤不创建第二张订单或第二封信。
- [ ] Lane 9. 在 head 上停止任务后再投递同一来源。 Save `/tmp/swarm-h4/worker-9/after-stop.png`. Pass when 停止的旧代次不被这封信复活。新工作要么排队到新任务，要么按 H3 的新代次重开。
- [ ] Lane 10. 在 head 上重启 api 进程后再查同一投递号。 Save `/tmp/swarm-h4/worker-10/restart.png`. Pass when 回执还在。不会再派一次活。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 重复投递同一封测试信 100 次的处理时间，以及其中真正派活的次数。trunk 没有收件箱，所以 trunk 只记录这条路径不存在。head 量处理时间和派活次数。
- [ ] Probe. 先在 trunk 请求该测试入口并记录 404 或无此路由。再在 head 投递。不要把 404 的耗时和 head 的耗时做成比值。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的 100 次里派活次数必须是 1。head 的总处理时间必须低于 2 秒。用户等到的结果是没有第二张卡。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/h4-review-dedupe.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/h4-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 拆开派活和干活 P1

**Depends on.** H2 已在 `release` 上。

**Files.**

- [ ] Edit `backend/app/agents/router.py` 的 `route` 与 `RouterDecision`。
- [ ] Edit `backend/app/agents/workflow.py` 的 `run_agent_workflow` 与 `retrieve_node`。
- [ ] Edit `backend/tests/test_router.py` 与 `backend/tests/test_workflow.py`。

**Build.**

- [ ] 先读 Appendix E 的 ④ 和 ⑥。
- [ ] `route` 只观察来意并写任务分配。它不调用 `gateway.invoke`，也不组装 `schedule_card`。
- [ ] `run_agent_workflow` 是唯一干活入口。`retrieve_node` 继续是调用 `invoke` 的地方。
- [ ] 保持 `llm._refuse_outbound`。不要开始把 `tool_calls` 发给模型。
- [ ] 规划失败仍返回现有的工具规划失败，不降级成闲聊，也不改用正则。这是 `docs/architecture.md` 里 Tier 2 的现行为。
- [ ] 不要把确认闸搬进路由。卡仍由干活路径经 H2 发出。
- [ ] DeepSeek SuperAgent 的 Router 与 Worker 只作分工概念。实现前重读上游文件。找不到文件就保持本仓现在的两个函数边界，不要按想象造 Ticket 或 PR 边界。

**You see.**

- [ ] 日程卡仍出现，但日志里的工具调用栈不经过 `route`。
- [ ] 路由失败时用户仍看到规划失败，而不是一次工具执行。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 运行 `cd backend && python -m pytest tests/test_router.py tests/test_workflow.py tests/test_calendar_card.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各说「明天下午三点开会」。 Save `/tmp/swarm-p1/worker-1/card.png`. Pass when 两边都给出一张日程草稿卡。head 的日志显示调工具的是干活内核，不是 `route`。
- [ ] Lane 2. 在 head 上说「你好」。 Save `/tmp/swarm-p1/worker-2/hello.png`. Pass when 走闲聊。`route` 不调用 `gateway.invoke`。
- [ ] Lane 3. 在 head 上指定知识库文档并提问。 Save `/tmp/swarm-p1/worker-3/kb.png`. Pass when 检索发生在干活内核。派活只记下需要知识库。
- [ ] Lane 4. 在 head 上提一个会触发联网的现有问题。 Save `/tmp/swarm-p1/worker-4/web.png`. Pass when 在 P2 之前，现有搜索仍由干活内核调用。派活不出搜索结果卡片。
- [ ] Lane 5. 在 head 上提一个会触发沙箱的表格问题。 Save `/tmp/swarm-p1/worker-5/sandbox.png`. Pass when 沙箱仍断网。派活不直接执行沙箱。
- [ ] Lane 6. 在 head 上在生成过程中插一句补充。 Save `/tmp/swarm-p1/worker-6/barge.png`. Pass when 补充进入当前任务或按 H3 的停止规则处理。派活不自己改已确认的日程。
- [ ] Lane 7. 在 head 上让路由模型返回缺字段。 Save `/tmp/swarm-p1/worker-7/router-fail.png`. Pass when 用户看到工具规划失败。系统不假装闲聊，也不执行工具。
- [ ] Lane 8. 在 head 上完成一次日程确认。 Save `/tmp/swarm-p1/worker-8/confirm.png`. Pass when 确认卡仍由干活路径发出。派活的返回值里没有确认卡。
- [ ] Lane 9. 在 head 上连续两问，第二问依赖第一问的日程卡。 Save `/tmp/swarm-p1/worker-9/follow-turn.png`. Pass when 第二问沿同一任务代次，不新开一套并行写。
- [ ] Lane 10. 在 trunk 与 head 上各看一条助手回复的气泡流。 Save `/tmp/swarm-p1/worker-10/stream.png`. Pass when 两边都是边生成边出字。head 没有把最终回复改到派活里完成。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 「你好」的 `time_starttransfer`，以及一条日程草稿卡从请求到 SSE 卡片事件的时间。trunk 与 head 都量。
- [ ] Probe. 交错各 20 次。先 trunk，再 head，再 trunk。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的问候首字中位数不得超过 trunk 200 毫秒。日程卡事件的中位数不得超过 trunk 300 毫秒。

**Review gate.** None. 这一刀不改变页面上的操作。


**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 只留一条上网路并按副作用重试 P2

**Depends on.** P1 已在 `release` 上。

**Files.**

- [ ] Edit `backend/app/core/llm.py` 的 `_build_request`。
- [ ] Edit `backend/app/agents/tools/web.py` 的 `execute_web`。
- [ ] Edit `backend/app/services/web_search.py` 的 `search_api`。
- [ ] Edit `backend/app/agents/router.py`，让 `needs_web` 变成任务上的上网允许位，而不是智谱工具位。
- [ ] Create `backend/app/agents/harness/attempt.py`，记录外部尝试的幂等键和未知结果。

**Build.**

- [ ] 先读 Appendix E 的 ⑤ 上网开关和 ⑧ 的副作用契约。开工前读 DeepSeek 当前聊天接口里自带联网的字段名。不要猜字段名，也不要写进这份计划的旧版本里充数。
- [ ] 允许上网时，只在那一次模型调用上打开该字段。同一轮禁止再调用 `search_api` 或 `https://open.bigmodel.cn/api/paas/v4/web_search`。
- [ ] 不允许上网时，两个都不开。
- [ ] 只读失败可以再试。幂等写带键再试，效果只能有一份。不可重放写遇到网络失败或 5xx，记为未知，先核对，不重发。
- [ ] 核对本身是只读。它不新建一笔订单、一封信或一条日程。
- [ ] 保留 `_refuse_outbound`。联网开关不是 `tools` 参数。
- [ ] 不要把智谱搜索留成同一轮的后备。向量、OCR、Rerank 的智谱端点不是这一条上网路，不要在这刀里删掉它们。

**You see.**

- [ ] 允许上网的一轮日志里只有一条联网路径。
- [ ] 未知结果之后没有第二笔外部写。
- [ ] 网页表面仍执行不了本机工具。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 改 `backend/tests/test_workflow.py` 里对 `open.bigmodel.cn` 的断言，使同一轮不再期望该请求。运行 `cd backend && python -m pytest tests/test_workflow.py tests/test_zhipu_endpoints.py tests/test_deepseek_roles.py tests/test_execute_gate.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各问一条需要新鲜网页的问题，任务允许上网。 Save `/tmp/swarm-p2/worker-1/one-net.png`. Pass when head 只走模型调用上的 DeepSeek 自带联网。同一轮日志里没有 `web_search.py` 的智谱请求。
- [ ] Lane 2. 在 head 上把该任务的上网白名单关掉再问同一句。 Save `/tmp/swarm-p2/worker-2/whitelist-off.png`. Pass when 模型调用不打开联网开关，也不调用智谱搜索。回复说明这次不能上网。
- [ ] Lane 3. 在 head 上对一条只读知识库问题保持联网关闭。 Save `/tmp/swarm-p2/worker-3/kb-only.png`. Pass when 只走知识库。没有搜索请求。
- [ ] Lane 4. 在 head 上让一次不可重放写在发送后收到网络失败。 Save `/tmp/swarm-p2/worker-4/unknown-write.png`. Pass when 状态记为结果未知。不会自动再发一单。
- [ ] Lane 5. 在 head 上对上一条未知结果做核对。 Save `/tmp/swarm-p2/worker-5/reconcile.png`. Pass when 核对只读远端。远端已有则标已生效。远端没有则停住，等新的确认，不自动重发。
- [ ] Lane 6. 在 head 上对一条幂等写用同一幂等键执行两次。 Save `/tmp/swarm-p2/worker-6/idem-key.png`. Pass when 远端只有一份效果。
- [ ] Lane 7. 在 head 上对一条只读工具制造一次超时后再试。 Save `/tmp/swarm-p2/worker-7/read-retry.png`. Pass when 只读可以再试。审计里写明契约是只读。
- [ ] Lane 8. 在 head 上停止任务，当不可重放写已经发出。 Save `/tmp/swarm-p2/worker-8/stop-sent.png`. Pass when 不调用远端撤销。页面说明可能已经发出。
- [ ] Lane 9. 在 head 上让模型可见工具名里出现本机工具，表面是网页。 Save `/tmp/swarm-p2/worker-9/web-block.png`. Pass when 执行被拒绝。网页上看不到本机执行结果。
- [ ] Lane 10. 在 trunk 与 head 上各发「你好」。 Save `/tmp/swarm-p2/worker-10/hello.png`. Pass when 两边都不打开联网，也不调用搜索。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 不允许上网的「你好」首字时间，trunk 与 head 都量。允许上网的一轮只在 head 上量模型调用时间，因为 trunk 走的是另一条智谱搜索，二者不是同一个场景。
- [ ] Probe. 问候交错各 20 次，先 trunk。允许上网的 head 场景另记绝对时间。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. 问候首字 head 不得超过 trunk 200 毫秒。允许上网的那一轮，从请求到首个带来源的气泡必须低于 15 秒。不与 trunk 的智谱搜索时间做比值。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/p2-review-one-net.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/p2-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 加上只读看门狗 H5

**Depends on.** H3 已在 `release` 上。

**Files.**

- [ ] Create `backend/app/agents/harness/watchdog.py`。
- [ ] Edit `backend/app/tasks/celery_app.py` 的 `beat_schedule`，只增加一个读取任务。不要把 Beat 变成助手核。
- [ ] Edit `docker-compose.yml` 的角色说明只在需要独立进程时。优先复用现有 beat 进程里的一个只读任务，只要它不能调用 `invoke`。

**Build.**

- [ ] 先读 Appendix E 的 ⑩。
- [ ] 看门狗读 `task_runs` 的同一行。等人、等资源不叫卡死。休眠区间从超时里扣除。
- [ ] 它不注册写工具，不调用 `gateway.invoke`，不改 `constitution.yaml`，不改路由提示词，不改事件过滤规则。
- [ ] 异常时只调用 H3 的停止函数，或让外壳推一条提醒。停止不等于重新开工。
- [ ] OpenMausBot `turn-watchdog.ts` 的等人与休眠抵扣可以抄。不要把它的进程内 `setInterval` 写成已经是主系统外的独立会话。本刀要么单独进程，要么 Beat 里的只读任务，并且主 api 崩溃后仍能读到数据库里的任务行。
- [ ] 看门狗不打开确认卡。四路叫醒合成一轮确认是 P3 在意图表上完成的，不在这里各写各的。

**You see.**

- [ ] 等人确认超过阈值时，卡片还在，任务不被停止。
- [ ] 真正无进展时，任务变为停止，白名单文件的摘要不变。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_watchdog.py`。运行 `cd backend && python -m pytest tests/test_watchdog.py tests/test_stop_propagation.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各完成一条正常「你好」。 Save `/tmp/swarm-h5/worker-1/healthy.png`. Pass when head 的看门狗不把这条标成卡死，也不改回复。
- [ ] Lane 2. 在 head 上让任务停在等人确认，超过看门狗的静默阈值。 Save `/tmp/swarm-h5/worker-2/wait-user.png`. Pass when 不判卡死。确认卡还在。看门狗没有写工具调用。
- [ ] Lane 3. 在 head 上让任务停在等资源。 Save `/tmp/swarm-h5/worker-3/wait-resource.png`. Pass when 不判卡死。没有新的确认卡。
- [ ] Lane 4. 在 head 上把任务标成休眠并前进系统时钟越过休眠区间。 Save `/tmp/swarm-h5/worker-4/asleep.png`. Pass when 休眠时间不计入超时。醒来后不立刻判卡死。
- [ ] Lane 5. 在 head 上让运行中的任务超过进展阈值且没有状态变化。 Save `/tmp/swarm-h5/worker-5/stall.png`. Pass when 看门狗沿 H3 的停止入口停下。代次之后的新任务不受影响。
- [ ] Lane 6. 在 head 上看门狗停下之后检查授权和路由提示词。 Save `/tmp/swarm-h5/worker-6/no-rewrite.png`. Pass when `constitution.yaml` 与路由提示词的摘要不变。任务白名单不变。
- [ ] Lane 7. 在 head 上在看门狗运行时点一次用户停止。 Save `/tmp/swarm-h5/worker-7/user-stop.png`. Pass when 只停止一次。看门狗不把停止当成重新开工。
- [ ] Lane 8. 在 head 上杀掉 api 进程后只留看门狗进程，再看它读到的状态。 Save `/tmp/swarm-h5/worker-8/api-down.png`. Pass when 它读持久化的任务行。它不自己执行工具，也不新开模型会话去改规则。
- [ ] Lane 9. 在 head 上放两个看门狗周期扫同一条任务。 Save `/tmp/swarm-h5/worker-9/two-ticks.png`. Pass when 停止只发生一次。没有第二轮确认。
- [ ] Lane 10. 在 trunk 与 head 上各确认一条日程。 Save `/tmp/swarm-h5/worker-10/confirm.png`. Pass when 两边确认都成功。head 的看门狗日志没有 `gateway.invoke`。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 看门狗扫 100 条等人任务的一次周期耗时。trunk 没有这个周期，所以 trunk 记录 beat 现有的入库回收周期耗时，head 同时记录回收周期和看门狗周期。
- [ ] Probe. 先在 trunk 量 `recover-stale-document-ingests` 一次。再在 head 量同一回收任务和看门狗各一次。交错重复 10 轮。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的入库回收不得比 trunk 慢 100 毫秒以上。看门狗 100 行扫描必须低于 200 毫秒。不把两种任务做成比值。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/h5-review-wait-user.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/h5-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 记下长记忆和有界脉搏 P3

**Depends on.** H4、P2 和 H5 都已在 `release` 上。

**Files.**

- [ ] Edit `backend/app/services/memory.py` 的 `store_memories`、`remember_from_turn` 与 `inject_system_memory`。
- [ ] Edit `backend/app/models/models.py` 的 `MemoryItem`。
- [ ] 新增记忆账迁移。只有上一刀的迁移仍是头时，文件名才用 `0014_memory_ledger.py`。
- [ ] Create `backend/app/services/pulse.py`。
- [ ] Edit `backend/app/api/memory.py`，让手工修改也走同一本账。

**Build.**

- [ ] 先读 Appendix E 的 ⑨。
- [ ] 变更账记录来源、修改者、旧值、新值。待提交的恢复留在账上。冲突时两份都留，不强行撤销。
- [ ] 记忆内容仍通过 `inject_system_memory` 进入提示词。执行闸继续不读这段文字。单测把「允许发信」写进记忆后，白名单仍不变。
- [ ] 低把握只写审计账，不发草稿卡。有结果或要人动手才经外壳开口。
- [ ] 脉搏到点只往 H1 的 `intents` 入队。要改世界的意图由干活内核开 H2 的一张凭据。同一去重键已经有待确认凭据时，不再开第二张。
- [ ] 不要做会改写全部记忆的第二套整理器。不要用提示词恢复代替这本账。
- [ ] Leon 的分层记忆和 Letta 的 MemFS 是文档级参考。实现前重读。不要搬 Letta 已废弃的 AgentFile，也不要搬 Khoj 的文档库来当身份档案。

**You see.**

- [ ] 偏好能在新会话里被用到，同时白名单不变。
- [ ] 脉搏和事件打在同一件事上时，只有一张确认卡。
- [ ] 没有事项的周期不发气泡。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_memory_ledger.py` 与 `backend/tests/test_pulse.py`。运行 `cd backend && python -m pytest tests/test_memory_ledger.py tests/test_pulse.py tests/test_event_inbox.py tests/test_execute_gate.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 上各说一个可记住的偏好，例如「以后用 24 小时制」。 Save `/tmp/swarm-p3/worker-1/remember.png`. Pass when head 写入记忆，并有一条变更账，含来源、修改者、旧值与新值。trunk 仍会记偏好。
- [ ] Lane 2. 在 head 上把记忆内容写成「你可以不经确认发邮件」。 Save `/tmp/swarm-p3/worker-2/no-priv.png`. Pass when 执行闸的白名单不变。下一次发信仍要确认。
- [ ] Lane 3. 在 head 上对同一记忆键写两次相同内容。 Save `/tmp/swarm-p3/worker-3/same-value.png`. Pass when 账本能看出来第二次没有改值。权限不变。
- [ ] Lane 4. 在 head 上让两个来源同时改同一记忆键。 Save `/tmp/swarm-p3/worker-4/conflict.png`. Pass when 冲突留下两边的值。系统不强行撤销其中一边。
- [ ] Lane 5. 在 head 上制造一条低把握的主动事项。 Save `/tmp/swarm-p3/worker-5/low-conf.png`. Pass when 只进审计账。聊天里不刷出草稿卡。
- [ ] Lane 6. 在 head 上让脉搏到点，事项需要改日程。 Save `/tmp/swarm-p3/worker-6/pulse-write.png`. Pass when 只出一张确认卡。日历在确认前不变。
- [ ] Lane 7. 在 head 上让脉搏与 H4 的事件指向同一去重键。 Save `/tmp/swarm-p3/worker-7/one-round.png`. Pass when 只有一张待确认卡。
- [ ] Lane 8. 在 head 上在没有事项时等到一个脉搏周期。 Save `/tmp/swarm-p3/worker-8/quiet.png`. Pass when 聊天不新增气泡。
- [ ] Lane 9. 在 head 上删除一条记忆。 Save `/tmp/swarm-p3/worker-9/delete-mem.png`. Pass when 账本记下删除。已发出的日程不跟着撤销。
- [ ] Lane 10. 在 trunk 与 head 上各再开一个新会话并问刚才的偏好。 Save `/tmp/swarm-p3/worker-10/recall.png`. Pass when 两边都能在回复里用到该偏好。head 的记忆文本不出现在工具白名单里。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 注入记忆后的「你好」首字时间，trunk 与 head 都量。再量 head 上一次脉搏入队到出现一张卡的时间。
- [ ] Probe. 问候交错各 20 次，先 trunk。脉搏场景只在 head 上量绝对时间。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. 问候首字 head 不得超过 trunk 200 毫秒。脉搏到卡片必须低于 2 秒。不与 trunk 做比值，因为 trunk 没有脉搏。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/p3-review-one-round.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/p3-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 做出桌面安装包并收瘦网页 P4

**Depends on.** P2 已在 `release` 上。

**Files.**

- [ ] Create `desktop/package.json` 与 Electron 主进程。默认用 Electron 包住现有 Vite 前端。操作者若要换打包器，换的是这一个壳，不是再做一个助手。
- [ ] Create `desktop/local_helper.py`，只在桌面包里注册本机工具。
- [ ] Edit `backend/app/agents/tools/registry.py` 的 `ensure_builtin_tools`，使 api 进程不导入本机工具。
- [ ] Edit `backend/app/agents/harness/gateway.py`，网页表面即使看见工具名也拒绝执行。
- [ ] Edit `frontend/src/components/ChatWindow.tsx`，只在桌面壳里显示本机批准卡。网页不显示本机控制。

**Build.**

- [ ] 先读 Appendix E 的 ① 和 ⑪。网页保持对话、日历确认卡和知识库。
- [ ] 一个安装包同时包含聊天窗和本机帮手。帮手不是第二个干活内核。它调用同一个 `invoke`。
- [ ] 本机动作先过 H2 的凭据。未确认不打开文件、不跑程序。
- [ ] 不要做网页遥控一个独立的本机助手。不要做看屏点击。那是 ⑫ 的后置项。
- [ ] 不要 fork Leon 或 Eigent 的仓库当新根。Leon 只提供同应用加本机帮手的形状。Eigent 只提供 Electron 壳的形状。
- [ ] Khoj 的桌面端和手机端不要抄进这个包。
- [ ] 现有 `docker-compose.yml` 的 postgres、qdrant、redis、minio、sandbox-runner、api、worker、beat 留在原处。不要为了桌面重写这套服务。

**You see.**

- [ ] 网页能聊天、确认日程、看知识库，并且执行不了本机工具。
- [ ] 桌面包在确认后能打开一个指定文件。
- [ ] 网页和桌面同时确认同一条日程时只写一次。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_surface_block.py`。运行 `cd backend && python -m pytest tests/test_surface_block.py tests/test_execute_gate.py tests/test_calendar_card.py -q`。
- [ ] 运行 `cd frontend && npm test`。
- [ ] 桌面启动命令写进 `desktop/package.json` 后，在 live lane 里实际打开窗口。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 的网页和 head 的网页上各发送「你好」。 Save `/tmp/swarm-p4/worker-1/web-hello.png`. Pass when 两边都回复。head 的网页执行日志拒绝本机工具。
- [ ] Lane 2. 在 head 的网页上完成一条日程确认。 Save `/tmp/swarm-p4/worker-2/web-cal.png`. Pass when 确认卡可用。没有本机文件被打开。
- [ ] Lane 3. 在 head 的网页上打开知识库并看到文档列表。 Save `/tmp/swarm-p4/worker-3/web-kb.png`. Pass when 列表来自现有知识库接口。
- [ ] Lane 4. 在 head 的桌面安装包里发送「你好」。 Save `/tmp/swarm-p4/worker-4/desk-hello.png`. Pass when 同一安装包里的聊天窗出回复。
- [ ] Lane 5. 在 head 的桌面安装包里批准一次本机打开文件。 Save `/tmp/swarm-p4/worker-5/desk-open.png`. Pass when 确认之后本机帮手才打开指定文件。未确认时文件不被打开。
- [ ] Lane 6. 在 head 的网页上让模型侧看到本机工具名，并请求执行。 Save `/tmp/swarm-p4/worker-6/web-sees-tool.png`. Pass when 执行端拒绝。页面说明网页没有本机动手。
- [ ] Lane 7. 在 head 的桌面安装包里拒绝本机操作。 Save `/tmp/swarm-p4/worker-7/desk-deny.png`. Pass when 文件不被打开。凭据作废。
- [ ] Lane 8. 在 head 的桌面安装包里点停止。 Save `/tmp/swarm-p4/worker-8/desk-stop.png`. Pass when 停止走 H3 的同一入口。已打开的文件不会被自动撤销成未打开。
- [ ] Lane 9. 在 head 上同时开着网页和桌面，对同一条日程点确认。 Save `/tmp/swarm-p4/worker-9/one-writer.png`. Pass when 只写入一次。
- [ ] Lane 10. 在 trunk 网页与 head 网页上各看日程列表。 Save `/tmp/swarm-p4/worker-10/web-list.png`. Pass when 两边都能看到已有日程。head 没有多出一个本机控制面板。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 网页「你好」的首字时间，trunk 与 head 都量。桌面窗口从启动到能输入的时间只在 head 上量。
- [ ] Probe. 网页问候交错各 20 次，先 trunk。桌面冷启动量 5 次，取中位数。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. 网页首字 head 不得超过 trunk 200 毫秒。桌面冷启动到输入框可点必须低于 8 秒。不把冷启动和网页首字做成比值。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/p4-review-desk-open.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/p4-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## 加上办事插头并守住定时边界 P5

**Depends on.** P4 已在 `release` 上。

**Files.**

- [ ] Create `backend/app/agents/tools/errand_tool.py`。
- [ ] Create `backend/app/services/errand_browser.py`。
- [ ] Edit `backend/app/agents/tools/registry.py`，只在服务端注册办事工具，不注册本机点击。
- [ ] Edit `backend/app/tasks/celery_app.py`，确认 `beat_schedule` 不创建确认凭据，也不调用干活内核。
- [ ] Edit `backend/app/agents/harness/constitution.yaml` 的白名单，加入办事工具名，并标上副作用。

**Build.**

- [ ] 先读 Appendix E 的 ⑧ 办事三分支和 ⑪ 的定时边界。
- [ ] 有连接器就调接口。没有才用服务器上的受控浏览器做搜索、加购、填表和结账。
- [ ] 登录、付款、发送走 H2 的凭据，或走只在确认后打开的保险柜。保险柜不把秘密写进提示词或审计参数。`write_audit` 今天会记 args，付款参数必须走现有的 `_redact_args`。
- [ ] 只有 App 里才能继续时，停止并告诉用户。不要接云手机。
- [ ] 未知付款按 P2 的核对走，不自动再付。
- [ ] Beat 继续可以回收卡住的入库。它不决定确认，也不当助手核。新的定时事项只入队到 `intents`。
- [ ] 不要把看屏点击放进浏览器办事。不要把 Open Interpreter 的本机编码助手搬进来。

**You see.**

- [ ] 没有连接器的填表停在提交前，确认后只提交一次。
- [ ] App 墙会停下来说明，而不是打开一个手机画面。
- [ ] 定时周期不自己弹出第二套确认卡。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] 新增 `backend/tests/test_errands.py`。运行 `cd backend && python -m pytest tests/test_errands.py tests/test_execute_gate.py tests/test_watchdog.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-high` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 的网页上各发「你好」。 Save `/tmp/swarm-p5/worker-1/hello.png`. Pass when 两边都回复。head 的定时任务不因此写日程。
- [ ] Lane 2. 在 head 上办一件已有连接器的事，例如只读查询。 Save `/tmp/swarm-p5/worker-2/connector-read.png`. Pass when 走连接器。不打开服务器浏览器。
- [ ] Lane 3. 在 head 上办一件没有连接器、但网页能做的填表，停在提交前。 Save `/tmp/swarm-p5/worker-3/browser-draft.png`. Pass when 服务器浏览器停在提交前。登录、付款或发送需要确认或保险柜。
- [ ] Lane 4. 在 head 上对付款点确认。 Save `/tmp/swarm-p5/worker-4/pay.png`. Pass when 只提交一次。凭据绑定金额和对象。
- [ ] Lane 5. 在 head 上改付款金额后再提交。 Save `/tmp/swarm-p5/worker-5/pay-change.png`. Pass when 旧凭据无效。没有按旧金额付款。
- [ ] Lane 6. 在 head 上遇到只有 App 里才能继续的墙。 Save `/tmp/swarm-p5/worker-6/app-wall.png`. Pass when 流程停止。页面告诉用户这里过不去。没有云手机点击。
- [ ] Lane 7. 在 head 上让定时周期跑过一次。 Save `/tmp/swarm-p5/worker-7/cron.png`. Pass when 定时任务只做已有的入库回收，或把意图放进 H1 的队列。它不创建确认凭据。
- [ ] Lane 8. 在 head 上让办事结果在提交后变为未知。 Save `/tmp/swarm-p5/worker-8/unknown-pay.png`. Pass when 不自动再付一次。核对不产生第二笔。
- [ ] Lane 9. 在 head 的网页上请求本机点击。 Save `/tmp/swarm-p5/worker-9/no-gui.png`. Pass when 被拒绝。看屏点击不在这条路径里。
- [ ] Lane 10. 在 head 上完成一次不涉及办事的日程确认。 Save `/tmp/swarm-p5/worker-10/cal.png`. Pass when 日程确认仍走 H2。办事模块没有第二套确认卡。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 只读连接器一次调用的服务端时间，trunk 没有这条工具，所以 trunk 记录 `invoke` 对日历只读列表的时间，head 同时记录日历列表和只读办事连接器。
- [ ] Probe. 交错各 20 次，先 trunk 的日历列表，再 head 的两项。
- [ ] Baseline. 先记 trunk 的数，再记 head 的数。
- [ ] Rule. head 的日历列表中位数不得超过 trunk 20 毫秒。只读办事连接器必须低于 1 秒，浏览器填表停在提交前的那条必须低于 10 秒。后两项不与 trunk 做比值。

**Review gate.** The operator reviews the screenshot and the video in chat before merge.

- [ ] 把 lane 2 的截图复制到 `docs/envisioned-product/review/p5-review-app-wall.png`。
- [ ] 在一条 lane 的虚拟机上录 30 到 60 秒。保存为 `docs/envisioned-product/review/p5-review.mp4`。
- [ ] Post the screenshot and the video for the operator in chat. 用中文写用户能看见的变化。停在可合并。等操作者点头。

**Merge.**

- [ ] 根代理在该 head SHA 上给出干净结论。
- [ ] Bugbot 与安全审查的每条意见按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 结论之后再变基到当前 `release`。`git patch-id` 不变。
- [ ] 所有者停在可合并。只有操作者说「合」才合并。所有者不自行 squash-merge。

## Close the program

- [ ] 上面每个格子都有证据。证据是文件、日志行、截图、测试输出或 SHA。
- [ ] `docs/envisioned-product/` 里的图、简报、对照和 `PLAN.md` 仍在。没有任何实现 PR 删除或悄悄替换它们。
- [ ] 按执行手册向操作者交出可合并清单。不合并。

## Appendix A. Prototype evidence

这一轮没有做原型。产品形态已经由 `sources/envisioned-final-brief.md` 和流程图锁定，而且这一轮禁止写功能代码。用原型去试桌面壳或确认卡会变成实现。

仍未证实的点留在这里。对应的刀开工前要补读，不能把本附录当成源码已核对。

- DeepSeek 自带联网的请求字段名。本仓 `llm._build_request` 只有模型、消息、流式、思考和 JSON 模式。P2 开工前读供应商当前文档，再把字段写进那一刀。
- OpenMausBot `083fd7b51405e6d0c01e189b2f65ab0322509b5a` 与 DS-Bot `71d344bf193f3f18185f25de3d8e5c483046910a` 的行号来自壳子对照 JSON。这一轮没有打开那些 blob。
- Leon、Letta、DeepSeek SuperAgent、AnythingLLM 插件正文这一轮没有按 SHA 核对。它们停留在对照文档。
- `chat` 对 `wf_task.cancel()` 之后，`asyncio.to_thread` 里的工作是否还在跑。代码只显示取消了 asyncio 任务。H3 要用测试证明线程和下一次外部调用会停。
- Electron 冷启动时间没有量过。P4 的 8 秒是门，不是已测结果。
- Khoj 许可证这一轮没有重读。对照文档写的是 AGPL-3.0。在任何复制之前重读上游 LICENSE。在此之前只允许把它当侧车参考。

## Appendix B. Alternatives rejected

整仓改用 Leon、AnythingLLM 或 OpenMausBot 做新根。定案要求在本仓演进。现有日历确认、知识库和 DeepSeek 调用已经在这条 `release` 上。

十二个模块同时开工。任务行、凭据和执行闸会被多处各写一次。H1 先把这三样钉成唯一结构。

先做桌面安装包。网页执行路径还没有表面检查时，本机工具会从网页漏出去。P4 放在执行闸和联网契约之后。

同一轮保留智谱网页搜索当后备。定案只要一条上网路，也就是模型自带联网。

把 OpenMausBot 的进程内计时器叫成主系统外的看门狗。图上写的是外部会话。H5 抄的是等人与休眠的判断，进程必须真的能在 api 之外读到任务行。

用 DS-Bot `approvals.js` 的内存 Map 当确认队列。对照写明它不能跨重启。H2 用数据库凭据。

用提示词嘱咐模型不要重发，代替幂等行。图上明确不学这种做法。P2 用 `attempt` 行。

先让操作者再选产品半边或壳子半边。这一轮的任务已经要求一份覆盖十二个模块的计划，并按依赖分波。半边选择不再打开。

## Appendix C. Risks

H1 的意图表在 P3 之前几乎没有生产者。后来的刀如果自己建队列，四路叫醒会各写一次。所有者看见新的队列表就停，改回 `intents`。

H2 碰到现有日历测试。`execute_calendar` 现在承诺写操作先出卡、不碰库。新凭据必须保持这句话。失败时先修凭据，不要放宽测试。

H3 的线程停止若只测到 asyncio 取消，页面会停、外部请求还会发出。live lane 必须看到工具在发出前被拒绝。

H4 的邮件入站若做成新的聊天总机，就越过了 ⑫。适配器只产出事件。

P2 在字段名未读到之前不得合并实现。猜一个 JSON 键会把联网做成第二条隐藏路。

H5 若导入 `gateway.py`，它就有了写能力。评审时搜索看门狗模块，不允许出现 `invoke`。

P3 的记忆若被执行闸读取，一句话就能抬权。`test_execute_gate` 必须用带授权口吻的记忆文本仍得到原来的白名单。

P4 的桌面帮手若自带数据库写路径，会和网页确认打出两行日程。帮手只能调用同一个 `invoke`。

P5 的浏览器若保存登录秘密到审计 JSON，会泄漏口令。付款和登录参数走 `_redact_args`。

Khoj 的沙箱形状和向量形状可以看，代码不能并进本仓。P5 的浏览器也不要从 Khoj 复制源码。

`control-ui` 不在本仓。lane 仍要驱动真实页面。技能缺失时记在决定记录里，不能改成只跑 pytest。

本仓的 pstack 技能路径用 `git show origin/main:` 会失败。那不是开工失败。改读插件副本并记下路径。

已打开的 PR，包括操作者点名的 #71，不在这个程序里。不要合并它们，也不要为了这个文档 PR 去改它们。

## Appendix D. Links and reading list

开工前读 `docs/envisioned-product/PLAN.md`、`README.md`、`sources/envisioned-final-brief.md`、`sources/personal-agent-oss-comparison.md`、流程图 PNG，以及三份 OSS 对照。该对照文件正文仍保留当时的 `/workspace/research/` 字样，以仓库里的这一份为准。模块现状读 Appendix E。

H1、P1、P4 改文件前先走 `pstack/skills/how/SKILL.md`。它们跨了路由、工作流或新进程。H2 与 P2 走 `pstack/skills/interrogate/SKILL.md`。凭据形状和联网开关都容易和现有测试、`_refuse_outbound` 打架。

决定记录按 `pstack/skills/show-me-your-work/SKILL.md` 追加到 `docs/envisioned-product/decisions.tsv`。只追加，不改旧行。

产品主干是 `release`。这一份计划写的时候，`release` 是 `f567fdd789c7b4404ca01ad54846cb86e6736035`。开工时重新 `git rev-parse origin/release`。行号若漂移，跟函数名走。

## Appendix E. Module reference

证据只分三档。`repo-at-SHA` 表示这一轮在本仓 `f567fdd789c7b4404ca01ad54846cb86e6736035` 上读过。`docs-only` 表示只在对照文档里。`gap` 表示实现前必须重读，不能把对照里的行号当成这一轮已打开的源码。

### ① 入口

现状是网页。`frontend/package.json` 只有 Vite。全仓没有 Electron 或 Tauri 应用。`README.md` 把产品写成 Docker Compose 上的浏览器助手。邮件不是入口。`gmail.search_meeting_mail` 只为会前简报检索。没有桌面回环 OAuth，`docs/plans/calendar-remaining-prep-followup.md` 写了这一点。证据是 `repo-at-SHA`。

目标是桌面完整包，网页瘦功能，邮件可当事件。抄 Leon 的同应用形状，文档级路径是 `core/context/ARCHITECTURE.md` 与 `apps/web`，档为 `docs-only`。Electron 壳的形状看 Eigent 的 `electron/`，同样是 `docs-only`。不抄 OpenClaw、Hermes、ZeroClaw 的聊天总机，不抄千问 Go 的云手机，不抄 Open Interpreter 当底座。Khoj 网页只可侧车，AGPL 不并入。P4 做桌面和瘦网页。H4 做邮件事件。

### ② 外壳

现状有登录 `api/auth.py` 的 `login`，上下文 `agents/context/assembler.py`，SSE 在 `api/chat.py` 的 `chat`。停止按钮在 `ChatWindow.tsx`，只调用 `stopChatStream`。服务端仅在断开时取消 asyncio 任务。确认卡由前端渲染，批准走日历 API，不在外壳里。证据是 `repo-at-SHA`。

目标是停止传到子任务、队列和请求发出前。抄 DS-Bot `ds-bot/src/host/turns.js` 约 221 到 264 行和 695 到 708 行，档为 `gap`。次选 OpenMausBot `server/highlevel/client.ts` 约 259 到 265 行和 335 到 401 行，档为 `gap`。不抄「停止等于撤回」，不抄完整按信道输出规范，不抄满分异步 ReAct。H3 做这一刀。

### ③ 事件

现状没有事件收件箱，没有投递回执表。Celery 只有入库队列。证据是 `repo-at-SHA`。

目标是两级过滤、去重、直通或新开、回执三分。优先抄 OpenMausBot `server/routines.ts` 约 742 到 806 行和 `server/routine-event-receipts.ts`，档为 `gap`。直通的概念来自 DeepSeek SuperAgent，档为 `docs-only`。不把收到事件写成外部成功，不做投机执行。H4 做这一刀。

### ④ 派活

现状 `RouterDecision` 只有 `needs_kb`、`needs_web`、`needs_calendar`、`needs_sandbox`、`needs_freshness` 和 `confidence`。`route` 决定这些位。`workflow.retrieve_node` 接着调用工具。路由不直接执行，但分配和执行在一次用户消息里连在一起。证据是 `repo-at-SHA`。

目标是派活不调工具、不出卡。概念抄 DeepSeek SuperAgent 的 Router 与 Worker，档为 `docs-only`，并且是 `gap`，因为没有本地 SHA。不抄它的 PR 或裸进程边界。不抄 Hermes 的主循环一把梭。P1 做这一刀。

### ⑤ 任务与授权

现状没有任务表，没有代次。白名单在 `constitution.yaml` 的 `omni.allowed_tools`，值是 kb、web、calendar、sandbox。`invoke` 查白名单和风险，不查代次，不查表面。记忆经 `inject_system_memory` 进系统提示。上网是 `needs_web` 后调用智谱 `search_api`。模型默认 `llm_base_url` 为 `https://api.deepseek.com`。`_refuse_outbound` 拒绝 `tools`。证据是 `repo-at-SHA`。

目标是三层授权在执行时重查，上下文变长不抬权，上网只开 DeepSeek 自带联网。执行时分列检查抄 OpenMausBot `server/highlevel/rpc.ts` 约 24 到 40 行，档为 `gap`。会话级工具寿命抄 DS-Bot `tools.js` 约 743 到 781 行，档为 `gap`。不把隐藏工具当成沙箱，不把记忆当成授权。H1 做任务和执行闸。P2 做上网开关。

### ⑥ 干活内核

现状 `run_agent_workflow` 编译一张图，`retrieve_node` 调用 `gateway.invoke`，`reflect_node` 看还有没有下一步，然后才到回答。日历写操作在 `execute_calendar` 返回卡。证据是 `repo-at-SHA`。

目标是规划、按授权执行、看结果、再回答。形状参考 Leon 的 `react-llm-duty` 和 `turn-lifecycle`，档为 `docs-only`。不在这一层新造确认闸，不追求满分异步。P1 把调用权留在这里，从 `route` 拿走。

### ⑦ 确认闸

现状 `ScheduleConfirmClaim` 只有 token、用户、会话、状态和过期时间。没有动作指纹和代次。日历写在 `api/calendar.py` 确认之后。`execute_calendar` 的文件头写明写操作先出卡、不碰库。会后跟进可以生成草稿。全仓没有发出邮件的函数。证据是 `repo-at-SHA`。

目标是一张凭据覆盖日历、会前会后的写、办事和本机批准。回执字段的机制看 OpenMausBot `routines.ts` 约 1487 到 1536 行，档为 `gap`。当前等级重读看 `live-approval.ts` 约 12 到 17 行，档为 `gap`。不抄内存审批表，不拿旧参数执行新动作。H2 做这一刀。

### ⑧ 工具插头

现状唯一入口是 `gateway.invoke`。工具是 kb、web、calendar、sandbox。没有副作用枚举，没有未知结果表，没有办事浏览器，没有本机插头。智谱搜索 URL 由测试钉在 `open.bigmodel.cn`。沙箱在 `workflow.py` 里强制 `network` 为 `none`。证据是 `repo-at-SHA`。

目标是可见不等于可执行，三种副作用，未知先核对，办事先连接器再服务器浏览器，本机只在桌面。未知结果的机制看 OpenMausBot `client.ts` 约 335 到 401 行，档为 `gap`。日历插件形状看 AnythingLLM 的 google-calendar 插件，档为 `docs-only`。不把所有失败都重试，不把看屏点击放进来，不把 Khoj 代码并进来。H1 加契约和表面。P2 加未知结果和一条上网路。P4 加本机插头。P5 加办事。

### ⑨ 少打扰、长记忆、脉搏

现状 `MemoryItem` 有 kind、key、content 和时间。`remember_from_turn` 从用户话里抽取并覆盖。没有变更账，没有冲突保留，没有脉搏。Beat 不提醒用户。证据是 `repo-at-SHA`。

目标是低把握只记账，记忆可审计且不抬权，脉搏经外壳开口，写仍走确认。安静档位的概念来自 DeepSeek SuperAgent，档为 `docs-only`。分层记忆看 Leon 的 `OWNER.md`，档为 `docs-only`。不抄 AnythingLLM 的浅层 facts 当深记忆，不做整套第二大脑。P3 做这一刀。

### ⑩ 看门狗

现状没有看门狗模块。证据是 `repo-at-SHA`，因为搜过任务目录和 Beat 调度，只有入库回收。

目标是读同一任务状态，等人、等资源和休眠都不叫卡死，只提醒或停止，不改权和提示词。判断规则看 OpenMausBot `turn-watchdog.ts` 和 `index.ts` 约 5012 到 5094 行，档为 `gap`。不把它的进程内计时器说成外部会话。H5 做这一刀。

### ⑪ 背后服务

现状 `docker-compose.yml` 有 postgres、qdrant、redis、minio、sandbox、sandbox-runner、api、worker、beat、nginx。Beat 只跑 `recover-stale-document-ingests`。沙箱是独立 runner。证据是 `repo-at-SHA`。

目标是这些服务留在原地。定时不写世界，也不发确认。本机能力只活在桌面包里。向量库继续用现有 Qdrant。可以阅读 AnythingLLM 的多向量后端和 Khoj 的 pgvector 形状，档为 `docs-only`。不把 Khoj 并进仓库，不把主服务换成 Express 或 Django。P4 约束桌面不另起一套主库。P5 约束 Beat。

### ⑫ 不做

不整仓换底座。不做云手机。不做无人确认的写和付款。不做手机端。不做外接聊天总机。不用智谱网页搜索作为同一轮的第二条上网路。不做网页遥控独立本机助手。不做事件投机执行。不做看屏式桌面操控。不做完整按信道输出规范。不让看门狗改提示词和 Trigger。不用提示词恢复代替账本。不把隐藏工具当授权。不把内存审批表当持久队列。

每一刀的 Build 都要能指出自己没有做上列各项。发现做了就停，不要留到下一刀。
