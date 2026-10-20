# 把聊天模型从智谱换到 DeepSeek

用户看到的回答改由 DeepSeek 生成，回答上方有一块可折叠的「思考」。维护者只留一个回答模型设置。嵌入、重排和识图仍走智谱，但各自带地址和密钥。

聊天、规划、路由、判分不再回退到智谱或 OpenAI。`glm` 和 `gpt-4o-mini` 的默认值删掉。思考开关按角色单独配置。

三个 PR 按顺序做。`ds-1` 拆开智谱地址。`ds-2` 换成 `json_object` 加校验。`ds-3` 切换模型并展示思考。操作者从下往上合并。本文件所在的 PR 只交计划，不合并。

## How to read this

One box is one unit of work. Every box names the evidence that checks it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator lands `ds-1`, `ds-2`, and `ds-3`. This plan PR stays unmerged.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

一个勾是一份工作。证据要能打开。嵌套勾是上一勾的子步骤。正文是操作步骤。附录解释原因和对照表。

本仓库的产品主干是 `release`，仓库里没有 `pstack`。执行时读插件里的同名手册。路径写在附录 D。

## Program checklist

### Arm the program

- [ ] 先把本计划和执行约定交给操作者，然后停下。操作者明确说开始之后，才执行。
- [ ] 程序开始时读下面这些文件。每一轮复查都重读。
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/control-ui/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/show-me-your-work/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-full.md`
- [ ] 操作者说开始之后，用 `/loop 1h` 挂上复查。不要靠记忆记间隔。
- [ ] 复查提示词用下面这一段，一个字不改。`Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported, such as a PR opened, a code-ready head, a round launched or closed, a verdict, a merge, a stuck agent and the action taken, a blocker added or cleared, or a decision only the operator can make. Name every such change and nothing else. Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text. Either way, log this tick's row in your decision trail. The row names the items reported, or none.`
- [ ] 操作者说暂停时，立刻通知每个 PR 的负责人停止写入。

### Spawn owners

- [ ] 每个 PR 一个负责人，生命周期按 `autopilot-stack` 走。负责人报告可以叠进栈，自己不合并。
- [ ] 依赖按下面这条链。后一个从父分支拉出，不从 `release` 另起。
  - [ ] `ds-1` 从 `release` 拉出，没有父 PR。
  - [ ] `ds-2` 在 `ds-1` 之后，从 `ds-1` 的分支拉出。
  - [ ] `ds-3` 在 `ds-2` 之后，从 `ds-2` 的分支拉出。
- [ ] 守住文件边界。`ds-1` 只改嵌入、识图、重排和它们的配置。`ds-2` 只改 JSON 调用和对应提示词。`ds-3` 改模型、思考、前端和评测判分。三个 PR 都会碰到 `backend/app/core/config.py`，所以必须叠成一条链，不能并行改这个文件。
- [ ] 守住审阅门。`ds-3` 改了聊天页面上的交互。合并前操作者要在聊天里看到截图和视频。`ds-1` 和 `ds-2` 不改页面交互。

### PR mechanics, for every PR

- [ ] 先确定用哪个代码托管。默认 `gh`。如果 `command -v origin` 成功且 Origin 能解析这个仓库，所有 PR 操作都用 `origin pr`。记下任何退回 `gh` 的情况。不要求安装 `gt`。
- [ ] PR 打开时就是可审阅状态，不要开成草稿。这个运行环境有内置 PR 工具时用它，否则按上面的选择用 `origin pr create --status open --base <base-branch>` 或 `gh pr create --base <base-branch>`。栈里的子 PR 指向父分支，不指向 `release`。
- [ ] 面向 PR 的那次推送之前，在 `backend` 跑一次 `python -m pytest tests -q`。推送时打开 git 钩子。
- [ ] 每次提交前按 `/deslop` 清理文案。送审前按 `/no-comments` 检查注释。
- [ ] Bugbot 和安全审查的每条评论都按 `pstack/skills/poteto-mode/references/bugbot-triage.md` 分流。
- [ ] 报告可以叠栈之前，先变基到当时的父分支。修复轮次保持这个合并基线。只有准备合并、`git merge-tree` 与主干冲突、或 CI 失败来自主干上的新提交时，才再次变基。

### Verdict and merge, for every PR

- [ ] 完整的十路现场检查只在代码就绪的那个提交上跑一次。只有新行为、改设计或大块新差异才再跑一轮。变基、解冲突或小修补只派一个核对者看新提交。现场车道和性能车道用本计划里该 PR 的盒子。另外至少两路只读审计，各自盯一个重点，读差异和证据，不信 PR 正文。根负责人在下结论前核对证据。
- [ ] 每路都是 `PASS` 才算干净。某路写成备注的缺陷也退回负责人。新提交要重新下结论。`playbooks/shipping.md` 里补丁号没变的结果可以沿用。
- [ ] 负责人不合并。根负责人按依赖把 PR 的基线改到父分支。操作者从 `ds-1` 往上合并。补丁号规则以 `playbooks/shipping.md` 为准。本计划 PR 不在这条链里。

### Boot recipe, for every live lane

每路现场检查在自己的云虚拟机上跑该 PR 的提交。这个仓库里没有 `control-ui`。页面用浏览器打开，命令行用仓库自己的 `eval.py`。缺口记在附录 C。

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`。
- [ ] 按 README 拉起 Compose。`curl -s http://127.0.0.1/health` 返回 `{"status":"ok","app":"Omni-Butler"}` 再开始。需要登录页时用 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`。
- [ ] 输入只通过页面或 `backend/scripts/eval.py`。日志只用 `docker compose logs api` 查看，不改数据。
- [ ] 每张截图存到 `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png`，报告里带回路径。
- [ ] 现场车道需要真密钥。没有 DeepSeek 或智谱密钥时该路失败，不能记成通过。空密钥时的演示回复不算模型已经换好。

## 拆开嵌入、重排和识图的智谱地址 (ds-1)

**Depends on.** None.

**Files.**

- [ ] 改 `backend/app/core/config.py`。
- [ ] 改 `backend/app/core/embeddings.py`。
- [ ] 改 `backend/app/rag/vision_ocr.py`。
- [ ] 改 `backend/app/rag/rerank.py`。
- [ ] 改 `backend/app/eval/ragas_judge.py` 里的嵌入客户端，只改地址和密钥，不改判分模型。
- [ ] 改 `.env.example`。
- [ ] 改 `docker-compose.yml` 里 `env_file` 旁边的注释。
- [ ] 改 `README.md` 和 `docs/architecture.md` 里关于这三项仍共用 `LLM_BASE_URL` 的句子。
- [ ] 新增 `backend/tests/test_zhipu_endpoints.py`。

**Build.**

- [ ] 在 `config.py` 增加 `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`VISION_BASE_URL`、`VISION_API_KEY`、`RERANK_BASE_URL`、`RERANK_API_KEY`。不要给它们默认成 `LLM_BASE_URL` 或 `LLM_API_KEY`。
- [ ] `embed_texts` 只用嵌入这组。`ocr_image_bytes` 只用识图这组。`ZhipuReranker` 只用重排这组。`ragas_judge` 里的 `OpenAIEmbeddings` 也只用嵌入这组。
- [ ] 嵌入模型、识图模型、重排模型的名字保持不动。用户机器上是 `embedding-3`、`glm-4.6v`、`rerank`。模板里是 `embedding-3-pro` 和 `glm-4v-flash`。这一 PR 不统一这两个名字，避免悄悄换掉向量空间。
- [ ] `VISION_OCR_ENABLED` 为假，或 `RAG_RERANK_ENABLED` 为假时，对应的地址和密钥可以空着。开着却空着，进程启动时报错，错误文本里写上变量名。
- [ ] `resolve_reranker` 在重排开着但密钥缺失时不要再静默换成 `HeuristicReranker`。启动已经失败的话，运行期走不到这里。
- [ ] `rerank_hits` 在 HTTP 失败时仍可退回规则排序，但日志从 `warning` 改成 `error`，并继续设置 `diag["rerank_fallback"]`。评测里这条已经会把运行标成降级。不要让生产日志看起来像正常排序。
- [ ] 聊天仍然用原来的 `LLM_BASE_URL` 和 `LLM_API_KEY`。这一 PR 不改 `llm.py` 的聊天方法，不改模型名。

**You see.**

- [ ] 启动日志在重排密钥缺失时出现 `RERANK_API_KEY`，进程起不来。
- [ ] 重排 HTTP 失败时，api 日志有一条 `error`，文本里有 `rerank failed`。
- [ ] 同一轮聊天的回答仍然出现。嵌入请求的主机是 `EMBEDDING_BASE_URL`，不是聊天地址。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `backend/tests/test_zhipu_endpoints.py` 断言三个客户端的请求地址来自各自的设置，缺失密钥时启动检查失败，重排异常写 `error` 且 `rerank_fallback` 为真。运行 `cd backend && python -m pytest tests/test_zhipu_endpoints.py tests/test_rag_expand_rerank.py tests/test_rag_retrieval_eval.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-xhigh-fast` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 在 trunk 和 head 各发一句「明天下午三点开会」。Save `ds1-lane1-calendar.png`. Pass when 两边都出现日程卡片，且 head 的重排主机仍是智谱地址。
- [ ] Lane 2. 打开聊天页发送「你好」。Save `ds1-lane2-hello.png`. Pass when 出现回答，且该请求的主机仍是 `LLM_BASE_URL`。
- [ ] Lane 3. 对一份已入库文档提问。Save `ds1-lane3-kb.png`. Pass when 回答带引用，且嵌入请求主机等于 `EMBEDDING_BASE_URL`。
- [ ] Lane 4. 打开重排但留空 `RERANK_API_KEY` 后重启 api。Save `ds1-lane4-missing-key.png`. Pass when 进程没有进入就绪，日志里有 `RERANK_API_KEY`。
- [ ] Lane 5. 把 `RERANK_BASE_URL` 指到一个会返回 500 的地址。Save `ds1-lane5-rerank-error.png`. Pass when 日志级别是 `error`，并且检索诊断里 `rerank_fallback` 为真。
- [ ] Lane 6. 上传一页扫描件触发识图。Save `ds1-lane6-ocr.png`. Pass when 识图请求主机等于 `VISION_BASE_URL`。
- [ ] Lane 7. 留空 `EMBEDDING_BASE_URL` 且嵌入会被用到时重启。Save `ds1-lane7-embedding.png`. Pass when 启动失败，日志里有 `EMBEDDING_BASE_URL`。
- [ ] Lane 8. 设置 `RAG_RERANK_ENABLED=false` 后提问。Save `ds1-lane8-rerank-off.png`. Pass when 进程就绪，且日志里没有发往重排地址的请求。
- [ ] Lane 9. 设置 `VISION_OCR_ENABLED=false` 且留空识图密钥。Save `ds1-lane9-vision-off.png`. Pass when 进程就绪。
- [ ] Lane 10. 打开 `http://127.0.0.1/health/ready`。Save `ds1-lane10-ready.png`. Pass when `checks.postgres` 和 `checks.redis` 都是 `ok`。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 同一条知识库问题从发起到回答显示的毫秒数，trunk 和 head 都测。另计这一次请求里重排调用的毫秒数。
- [ ] Probe. 先 trunk，再 head，再 trunk。两边都打出这两个毫秒数。问题用同一句已入库文档的提问。
- [ ] Baseline. 先记下 trunk 的回答毫秒数和重排毫秒数。
- [ ] Rule. head 的回答均值超过 trunk 的 1.2 倍即失败。head 的重排均值超过 trunk 的 1.2 倍也失败。地址换成同值的新变量，不应该变慢。

**Review gate.** None. ds-1 is not review-gated.

**Merge.**

- [ ] 根负责人在该提交上给出干净结论。
- [ ] Bugbot 已分流。
- [ ] 结论之后变基到当时的 `release`，补丁号不变。
- [ ] 负责人不合并。操作者在整条链就绪后先合并 `ds-1`。

## 用 json_object 加校验替换严格 schema (ds-2)

**Depends on.** ds-1.

**Files.**

- [ ] 改 `backend/app/core/llm.py`。
- [ ] 改 `backend/app/agents/router.py`。
- [ ] 改 `backend/app/agents/harness/critique.py`。
- [ ] 改路由和核验的系统提示，使其中含有小写单词 `json` 和一个一行示例。
- [ ] 改 `backend/tests/test_router.py`、`backend/tests/test_workflow.py`、`backend/tests/conftest.py`。
- [ ] 新增 `backend/tests/test_json_object.py`。
- [ ] 删除对 `complete_json_schema` 的全部引用。

**Build.**

- [ ] 删除 `complete_json_schema`。删除没有调用方的 `complete_json_async`。
- [ ] 新增一个函数，接收消息、Pydantic 模型和模型名。请求体用 `response_format` 的 `json_object`。不用 `json_schema`，也不要 `strict`。
- [ ] 返回内容为空、只有空白、或 `finish_reason` 为 `length` 时，算失败，不要把空串当成成功。
- [ ] 校验失败或内容为空时，把错误和示例再发给模型一次。第二次仍失败就抛出 `LLMStructuredError`。不要第三次。
- [ ] `router.py` 的 `_llm_decide` 改为调用这个函数，并用 `RouterDecision` 校验。外层不要再乘一轮重试。`ROUTER_MAX_ATTEMPTS` 保留为 1 的含义写进 `.env.example`，避免和函数内部的一次重试叠成四次。
- [ ] `critique_draft` 改为同一个函数，用现有的核验结果模型校验。删掉 `_CRITIQUE_SCHEMA` 那份手写 schema。
- [ ] 路由提示和核验提示里写上小写 `json`，并给一个一行示例。DeepSeek 在缺少这个单词时会一直输出空白，直到打满 token。智谱这一步先跑通，是为了在换供应商之前证明校验本身。
- [ ] 不改模型名，不改 `LLM_BASE_URL`，不改前端。

**You see.**

- [ ] 问一句需要工具的话，页面不出现「工具规划失败」，除非模型两次都给了非法 JSON。
- [ ] 非法 JSON 时，用户看到明确的「工具规划失败」，而不是一直转圈。
- [ ] 日志里同一次用户消息的路由补全最多两次。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `backend/tests/test_json_object.py` 覆盖合法 JSON、空内容、第一次非法第二次合法、两次都非法。断言请求体是 `json_object`，并且没有 `json_schema`。运行 `cd backend && python -m pytest tests/test_json_object.py tests/test_router.py tests/test_workflow.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-xhigh-fast` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. 对同一份已入库文档问同一句事实题。Save `ds2-lane1-kb.png`. Pass when head 的意图和 trunk 一致，回答都不是空的。
- [ ] Lane 2. 发送「明天下午三点开会」。Save `ds2-lane2-card.png`. Pass when 出现日程卡片，开始时刻是 15:00，且没有「工具规划失败」。
- [ ] Lane 3. 发送「查一下明天的日程」。Save `ds2-lane3-list.png`. Pass when 出现列表而不是新建卡片。
- [ ] Lane 4. 发送「明天有空吗」。Save `ds2-lane4-free.png`. Pass when 回答没有编造一场新会议。
- [ ] Lane 5. 问一个需要知识库的问题。Save `ds2-lane5-ground.png`. Pass when 回答正文里没有裸 JSON，也没有 schema 报错文本。
- [ ] Lane 6. 看这一轮的 api 日志。Save `ds2-lane6-attempts.png`. Pass when 路由补全次数不超过 2。
- [ ] Lane 7. 发送一句普通闲聊。Save `ds2-lane7-chat.png`. Pass when 气泡里有文字，且不是只有空白。
- [ ] Lane 8. 刷新页面。Save `ds2-lane8-reload.png`. Pass when 上一条助手消息还在，内容与刷新前一致。
- [ ] Lane 9. 运行 `cd backend && python scripts/eval.py run routing --profile smoke`。Save `ds2-lane9-routing.png`. Pass when 命令退出码是 0，且报告里没有把空内容记成正确路由。
- [ ] Lane 10. 运行 `cd backend && python scripts/eval.py run calendar --profile smoke`。Save `ds2-lane10-calendar.png`. Pass when 退出码是 0。限流行记在 `infra_error`，不进通过率分母。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 一句「明天下午三点开会」从发起到卡片出现的毫秒数。trunk 和 head 都测。另计日志里这次路由补全的耗时。
- [ ] Probe. 先 trunk，再 head，再 trunk。两边都打出毫秒数。
- [ ] Baseline. 先记下 trunk 的卡片毫秒数。
- [ ] Rule. head 的卡片均值超过 trunk 的 1.5 倍即失败。单次路由补全超过 60 秒即失败。60 秒是现在 `complete_json_schema` 的超时上限，空白死循环不能比这更久。

**Review gate.** None. ds-2 is not review-gated.

**Merge.**

- [ ] 根负责人在该提交上给出干净结论。
- [ ] Bugbot 已分流。
- [ ] 结论之后变基到当时的 `ds-1` 分支，补丁号不变。
- [ ] 负责人不合并。操作者在 `ds-1` 已合并后合并 `ds-2`。

## 把回答链路换到 DeepSeek 并展示思考 (ds-3)

**Depends on.** ds-2.

**Files.**

- [ ] 改 `backend/app/core/config.py` 和 `backend/app/core/llm.py`。
- [ ] 改 `backend/app/api/chat.py`。
- [ ] 改 `backend/app/agents/workflow.py` 里 `run_agent_workflow` 的草稿调用。
- [ ] 改 `backend/app/agents/harness/critique.py` 里 `ground_and_repair_answer`。
- [ ] 改 `backend/app/agents/tools/calendar_tool.py` 里 `_planning_advice`。
- [ ] 改 `backend/app/agents/router.py` 的缓存版本。
- [ ] 改 `backend/app/eval/agent_e2e.py`、`grounding.py`、`yz_fullchain.py`、`context_replay.py`、`ragas_judge.py`。
- [ ] 改 `frontend/src/api/client.ts`、`frontend/src/services/chatStream.ts`、`frontend/src/components/ChatWindow.tsx`。
- [ ] 新增一条 Alembic 修订，在 `messages` 上加可空列 `reasoning`。当前头是 `0008_calendar_google_connection`。
- [ ] 改 `.env.example`、`README.md`、`docs/architecture.md`、`docs/deployment.md`、`backend/app/eval/README.md`。
- [ ] 新增 `backend/tests/test_deepseek_roles.py`。

**Build.**

- [ ] 删除 `LLM_MODEL`。回答只用 `CHAT_MODEL`，默认 `deepseek-flash`。`stream_chat`、`complete_text` 和 `complete_text_with_usage` 在调用方不传模型时都用它。现在 `complete_text` 默认的是 `settings.llm_model`，和 `stream_chat` 的 `resolved_chat_model()` 不是同一个值，合并时要收成一个。
- [ ] `PLANNER_MODEL` 默认 `deepseek-flash`。`ROUTER_MODEL` 默认 `deepseek-flash`。`JUDGE_MODEL` 默认 `deepseek-v4-pro`。三个都不再在空字符串时回退到回答模型。
- [ ] 删掉 `gpt-4o-mini` 和 `glm-4-air` 默认值。删掉 `config.py` 里把 `glm` 模型悄悄改写到智谱地址的那段。删掉 `OPENAI_API_KEY` 这个别名，避免旧密钥被送到 DeepSeek。
- [ ] `LLM_BASE_URL` 改为 `https://api.deepseek.com`。`LLM_API_KEY` 只表示 DeepSeek 的密钥。
- [ ] 增加四个开关，默认值如下。`CHAT_THINKING=on`，`PLANNER_THINKING=off`，`ROUTER_THINKING=off`，`JUDGE_THINKING=on`。开时请求带 `extra_body` 的 `thinking.type=enabled` 和 `reasoning_effort=high`。关时带 `thinking.type=disabled` 和 `reasoning_effort=none`。不要再做一个全局开关。思考力度不做成环境变量。
- [ ] 思考开着时不要发送 `temperature`，官方文档写明它不起作用。不要把现有的 `llm_max_tokens=2048` 传给思考请求。不传时，思考模式的官方默认输出上限是 64K。`finish_reason=length` 视为失败。
- [ ] 从安装的 OpenAI SDK 2.x 里用 `getattr` 读取 `reasoning_content`。2.54.0 上这个字段在 `model_extra`，不在声明字段里。`content` 为 `None` 时不要拼接。官方流式示例用「有思考就不要内容」的写法，遇到只有角色的块会把 `None` 加进字符串。
- [ ] `stream_chat` 把思考增量和回答增量分成两种产出。`chat.py` 新增 SSE 类型 `reasoning`。不要复用现有的 `thinking`，那是「正在搜索」这类状态句。思考增量不要追加进 `acc`。落库的 `content` 只有回答。
- [ ] 非流式路径只把 `content` 交回调用方。覆盖 `run_agent_workflow` 第 529 行的草稿、`ground_and_repair_answer` 第 485 行的重写、`_planning_advice`、以及 `agent_e2e`、`grounding`、`yz_fullchain`、`context_replay` 的作答。这些函数名已对过代码。日历第 389 行是 `extract_event`，第 536 行是 `extract_free_slots`，数据分析第 361 行是 `_plan_analysis_spec`，记忆第 256 行是 `extract_memories`。它们走规划角色，思考关闭。
- [ ] 若某次非流式调用的结果会直接显示给用户，例如日程建议，先发一条 `reasoning` 事件，再发回答。回答文本里仍然没有思考。
- [ ] `messages.reasoning` 存思考全文，刷新后仍能展开。组装下一轮模型输入时只用 `content`。当前代码不传 `tools`。调用方若传入 `tools`，客户端直接抛错，不要发出请求。官方文档写明，带 `tools` 时必须把 `reasoning_content` 原样回传，否则接口返回 400。
- [ ] 前端在回答上方放一块可折叠的「思考」。思考 token 到达时展开。第一个回答 token 到达后折叠。用户可以再打开。状态步骤列表保持原样，不要和思考正文混在同一个列表里。
- [ ] `ragas_judge.py` 的聊天模型从 `settings.llm_model` 改为 `JUDGE_MODEL`，思考按 `JUDGE_THINKING`。去掉这里的 `max_tokens=2048`。嵌入客户端仍用 `ds-1` 的嵌入地址。报告里的 `judge_model` 和 `answer_model` 必须不同，相同则 `self_judged` 为真，该档数字不能当结论。
- [ ] `router_cache_key` 已经包含 `model`。把 `_ROUTER_CACHE_VERSION` 从 2 调到 3。部署时清掉 Redis 里前缀为路由缓存的键。评测继续尊重 `EVAL_SKIP_ROUTER_REDIS`。
- [ ] `is_llm_infra_exception` 继续把 HTTP 429 和限流异常算作基础设施失败。响应能正常返回但 `finish_reason` 是 `insufficient_system_resource` 或 `aborted` 时，也抛 `LLMInfraError`。评测不得把这些行算成答错。
- [ ] `gen_*.py` 只生成冻结题集，不调用模型。沙箱代码不调用模型。这一 PR 不改它们。负责人用检索确认没有遗漏的调用后再勾掉。
- [ ] `.env.example` 的 `LLM_MODEL_CONCURRENCY_LIMITS` 改成包含 `deepseek-flash` 和 `deepseek-v4-pro`。起始槽位用 `deepseek-flash=50,deepseek-v4-pro=10`。这是保护账户的上限，不是测出来的最优值。官方并发上限远高于此。
- [ ] `docker-compose.yml` 的 `environment` 映射不要写入这些密钥。该映射会盖住 `env_file`。只在 `env_file` 旁用注释列出 `backend/.env` 必须有的名字。

**You see.**

- [ ] 发送「用一句话介绍你自己」后，回答上方先出现「思考」，随后出现回答。折叠后回答仍在。
- [ ] 刷新页面，回答正文里没有思考段落，展开「思考」仍能看到刚才的内容。
- [ ] 日程卡片仍能出现。该次 `complete_json` 的请求体里思考是关的。

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `backend/tests/test_deepseek_roles.py` 用假的 SDK 响应断言四类角色的请求体、思考与回答分离、空内容失败、带 `tools` 时拒绝发送、缓存键随模型变化、429 和 `insufficient_system_resource` 变成 `LLMInfraError`。运行 `cd backend && python -m pytest tests/test_deepseek_roles.py tests/test_router.py tests/test_context_eval_gen.py -q`。

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-xhigh-fast` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. trunk 没有「思考」块。记下这一点。同一句「用一句话介绍你自己」在两边各发一次。Save `ds3-lane1-hello.png`. Pass when head 有可折叠「思考」且回答已显示，刷新后回答正文不含思考文本，trunk 仍有回答且没有这块新区域。
- [ ] Lane 2. 在 head 观察思考 token 先到、回答后到。Save `ds3-lane2-collapse.png`. Pass when 思考在到达时是展开的，回答一开始后变为折叠，点击后能再打开。
- [ ] Lane 3. 发送「明天下午三点开会」。Save `ds3-lane3-card.png`. Pass when 卡片开始时刻是 15:00，且该次规划请求的日志里思考为关。
- [ ] Lane 4. 发送「查一下明天的日程」。Save `ds3-lane4-list.png`. Pass when 出现列表。路由请求的日志里思考为关。
- [ ] Lane 5. 问一句需要知识库的事实。Save `ds3-lane5-kb.png`. Pass when 回答有引用，且重排主机不是 `api.deepseek.com`。
- [ ] Lane 6. 再问一句促使嵌入的问题。Save `ds3-lane6-embed.png`. Pass when 嵌入主机是 `EMBEDDING_BASE_URL`，不是 DeepSeek。
- [ ] Lane 7. 刷新第 2 路的会话。Save `ds3-lane7-reload.png`. Pass when 回答正文与折叠前的回答一致，思考只出现在「思考」块里。
- [ ] Lane 8. 在 head 上跑 `cd backend && python scripts/eval.py run calendar --profile smoke`，并对照 trunk 的同命令。Save `ds3-lane8-calendar.png`. Pass when 限流行在两边都记为 `infra_error`。head 没有因模型名仍是 `glm` 而失败。
- [ ] Lane 9. 在 head 上跑 `cd backend && python scripts/eval.py run agent --profile smoke`。Save `ds3-lane9-agent.png`. Pass when 报告里的作答模型是 `deepseek-flash`。若出现 429，该行是 `infra_error` 而不是答错。
- [ ] Lane 10. 部署后发第一句需要路由的话，并看 Redis。Save `ds3-lane10-cache.png`. Pass when 这一次是缓存未命中。旧的 `glm` 路由结果没有被当成这次的决定。

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. 两边都测「用一句话介绍你自己」从发起到第一个回答 token 的毫秒数，以及回答全文显示的毫秒数。head 另外测从发起到第一条 `reasoning` 事件的毫秒数。trunk 没有这条事件，不要拿它和 trunk 的首 token 做比值。
- [ ] Probe. 先 trunk，再 head，再 trunk。同一句提示词。两边都打出首个回答 token 的毫秒数和全文毫秒数。head 再打出第一条思考事件的毫秒数。
- [ ] Baseline. 先记下 trunk 的首个回答 token 毫秒数。
- [ ] Rule. head 第一条思考事件超过 20000 毫秒即失败。head 第一个回答 token 超过 90000 毫秒即失败。这两个数是预算，不是已经测到的 DeepSeek 延迟。回答全文超过 90000 毫秒也失败。失败时不要把思考等待除以 trunk 的首 token 时间。

**Review gate.** The operator reviews before merge.

- [ ] 把第 2 路的截图复制到 `docs/plans/media/ds-3-review-thinking.png`。
- [ ] 在车道虚拟机上录 30 到 60 秒，内容是思考展开、折叠、再打开。保存为 `docs/plans/media/ds-3-review.mp4`。
- [ ] 把 screenshot 和 video 发到聊天里给 operator。停在可合并。等 operator 点头后再合并。

**Merge.**

- [ ] 根负责人在该提交上给出干净结论。
- [ ] Bugbot 已分流。
- [ ] 结论之后变基到当时的 `ds-2` 分支，补丁号不变。
- [ ] 负责人不合并。操作者在 `ds-2` 已合并、并且看过截图和视频之后，最后合并 `ds-3`。

## Close the program

- [ ] 上面每个勾都有证据。
- [ ] 按 `autopilot-stack` 的收尾回复操作者。链的根是 `ds-1`，尖是 `ds-3`。本计划 PR 仍然不合并。

## Appendix A. Prototype evidence

这一节记录动笔前已经看过的事实，以及还没证明的事。

SDK 探针在这台机器的 `/tmp/ds-probe-py`，没有提交进仓库。操作者要求本 PR 只有计划，所以没有分支，也没有提交号。安装的是 `openai` 2.54.0，与 `backend/requirements.txt` 里的 `openai>=2.0,<3` 相符。

观察到的结果如下。`ChatCompletionMessage` 的声明字段里没有 `reasoning_content`。用带这个字段的 JSON 去做 `model_validate` 之后，`getattr` 能读到思考文本，`model_extra` 里也有它。流式块上同样如此。只有思考的块，`content` 是 `None`。`Completions.create` 的签名里有 `extra_body` 和 `reasoning_effort`。空内容加 `finish_reason=length` 时，思考文本仍在，回答是 `None`。

没有做实时接口调用。这台机器的环境里没有 `DEEPSEEK_API_KEY`，也没有智谱密钥。思考块先于回答块、以及小写 `json` 是否区分大小写，都还没在真接口上见过。计划里的步骤按官方文档来写，真接口要在 `ds-3` 的现场车道上第一次看见。

思考块的版式没有做原型。操作者已经定了可折叠、放在回答上方。执行时采用的细节是，token 到达时展开，回答一开始就折叠。这是可改的界面决定，不是测出来的。

20000 毫秒和 90000 毫秒是预算，不是测量值。第一次现场跑完之后，如果预算和真实延迟差得很远，改计划里的数字，不要为了过门而改提示词。

## Appendix B. Alternatives rejected

回答模型保留 `LLM_MODEL` 这个名字。否掉了。拆开之后它不再表示「所有调用共用的那个模型」。留下这个名字会让人以为嵌入也还走它。留下 `CHAT_MODEL`，删掉 `LLM_MODEL`。

聊天链路保留智谱或 OpenAI 作为失败回退。否掉了。操作者明确不要回退。回退会把配错的 DeepSeek 密钥藏起来。

四个角色分四个 PR 轮流改 `LLM_BASE_URL`。否掉了。它们共用一个地址。先改地址、模型名还是 `glm` 的那个提交，在 `release` 上是坏的。JSON 契约可以先在智谱上证明，所以单独成 `ds-2`。供应商切换必须一次做完，所以是 `ds-3`。

用一个代理给 DeepSeek 补上 `json_schema`。否掉了。官方聊天补全只接受 `text` 和 `json_object`。代理是一个新服务。

用一个全局思考开关。否掉了。回答要开，规划和路由要关。开关必须跟着角色走。

判分关闭思考，以便 `temperature=0` 生效。否掉了。官方文档写明思考模式下 `temperature` 没有作用。选 `deepseek-v4-pro` 就是为了它默认开着的思考。判分结果要和作答模型分开记录。比较换模型前后的回答时，用 head 上的判分模型给两边的回答打分，不要拿 trunk 上智谱判分的分数和 head 上 Pro 判分的分数直接比。

把模型思考塞进现有的 `thinking` 状态列表。否掉了。那个列表是「正在搜索」这类状态。混在一起之后，用户无法单独折叠推理。新事件叫 `reasoning`。

每一轮都把 `reasoning_content` 回传给接口。否掉了。官方思考模式文档写明，请求不带 `tools` 时，回传的思考会被忽略。当前仓库的聊天补全不传 `tools`。回传只会把思考写进下一轮上下文的风险留下。带 `tools` 的调用现在直接拒绝，等真的要做工具调用时再按文档回传。

使用已经退役的模型名 `deepseek-v4-flash`。否掉了。2026-10-20 读到的官方文档把可用模型写成 `deepseek-flash` 和 `deepseek-v4-pro`。旧名字仍会被接到 V4.1 Flash，但是退役名。

## Appendix C. Risks

`ds-3` 的思考会推迟第一个回答 token。用户能先看见「思考」在增长。性能门用绝对预算，不用和 trunk 的比值。预算未经过真接口测量。

`ds-3` 拒绝带 `tools` 的请求。今天没有这种调用。以后若接上工具却删掉这道拒绝，DeepSeek 会因缺少回传的 `reasoning_content` 返回 400。负责人看这道单测还在。

`ds-3` 的路由缓存。键里已经有模型名。仍要把版本从 2 调到 3，并在部署时清掉旧键。不清的话，只有在有人把模型名改回旧字符串时才会命中旧决定。清掉是为了部署步骤本身可见。

`ds-3` 的 Ragas。现有代码把 `max_tokens` 设成 2048，注释说默认上限会让生成停不下来。思考模式下 2048 会先被思考吃掉，分数 JSON 变成空。这一 PR 去掉该上限，并把 `length` 当成失败。

`ds-1` 必须先合并。否则 `ds-3` 一把换掉 `LLM_BASE_URL` 时，嵌入、重排和识图会打到 DeepSeek。重排失败今天会退回规则排序。`ds-1` 把缺密钥变成启动错误，把 HTTP 失败的日志改成 `error`。

`ds-2` 的 `json_object` 在提示词没有小写 `json` 时会长时间输出空白。超时沿用 60 秒。现场车道如果看到接近 60 秒的路由，先检查提示词，不要加大超时。

官方流式示例在 `content` 为 `None` 时会抛类型错误。`ds-3` 的单测要覆盖这种块。

这个检出里没有 `control-ui`，也没有 `control-cli`。现场车道用浏览器和 `eval.py`。截图路径仍按启动步骤保存。

DeepSeek Flash 官方支持识图，Pro 不支持。这一计划不把识图迁过去。操作者已经定了识图留在智谱。

官方价格页写明工作日高峰是 UTC 01:00 到 04:00 以及 06:00 到 10:00，高峰价格是低峰的两倍。全量评测避开这段时间。贵不是模型答错。

`gen_*.py` 和沙箱没有模型调用。如果负责人的检索发现有，停下来把调用点补进 `ds-3` 的文件列表，不要在计划外顺手改。

比较日历和 agent 全量结果时，429 和 `insufficient_system_resource` 只计入 `infra_error_rate`。这个比率现有门是 0.05。超过就当基础设施失败，不当成 DeepSeek 比智谱笨。

## Appendix D. Links and reading list

动笔改代码前读这些页。模型名和参数以这些页为准，不要以转述为准。

- [Your First API Call](https://api-docs.deepseek.com/) 写明 `deepseek-flash` 与 `deepseek-v4-pro`，以及 `base_url` 为 `https://api.deepseek.com`。
- [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion) 写明思考默认开启、`reasoning_content` 与 `content` 并列、`json_object` 必须在提示里要求 JSON，以及不传 `max_tokens` 时的默认输出上限。
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode) 写明 `extra_body` 的写法、`temperature` 在思考模式下无效、以及有没有 `tools` 时回传思考的差别。
- [Models and Pricing](https://api-docs.deepseek.com/quick_start/pricing) 写明 Flash 的服务版本是 DeepSeek-V4.1-Flash-1017，Pro 是 DeepSeek-V4-Pro-0813，以及高峰时段。

`ds-2` 和 `ds-3` 在改请求形状之前读 `pstack/skills/how/SKILL.md`。`ds-3` 的思考展示在合并前读 `pstack/skills/interrogate/SKILL.md`。

决策记录按 `pstack/skills/show-me-your-work/SKILL.md` 写在执行分支的 TSV 里。本 PR 只有这一份计划，不另附 TSV。

仓库里已经核对过的调用点见下一节。

## Appendix E. Call sites

下面是 2026-10-20 在 `release` 的 `e4c6038` 上对过的调用。行号会漂，函数名以这一节为准。

回答角色，思考默认开，模型只读 `CHAT_MODEL`。

- `backend/app/api/chat.py` 约第 539 行，`stream_chat`。这是用户看见的流。思考不要进入 `acc`。
- `backend/app/agents/workflow.py` 的 `run_agent_workflow`，约第 529 行，`complete_text` 草稿。现在不传模型，因此用的是 `llm_model`，不是 `CHAT_MODEL`。
- `backend/app/agents/harness/critique.py` 的 `ground_and_repair_answer`，约第 485 行，重写用的 `complete_text`。
- `backend/app/agents/tools/calendar_tool.py` 的 `_planning_advice`，约第 362 行，`complete_text`。
- `backend/app/eval/agent_e2e.py` 约第 266 行，`complete_text_with_usage`，模型是 `resolved_chat_model()`。
- `backend/app/eval/grounding.py` 约第 276 行，`complete_text`。
- `backend/app/eval/yz_fullchain.py` 约第 299 行，`complete_text`。
- `backend/app/eval/context_replay.py` 约第 199 行，作答的 `complete_text_with_usage`。

规划角色，思考默认关，模型只读 `PLANNER_MODEL`。`complete_json` 在不传模型时已经走 `resolved_planner_model()`。

- `backend/app/services/calendar.py` 的 `classify_calendar_action`，约第 210 行。
- `backend/app/services/calendar.py` 的 `extract_event`，约第 389 行。这是预填，不是第 389 行附近另一个函数。
- `backend/app/services/calendar.py` 的 `extract_list_day`，约第 483 行。
- `backend/app/services/calendar.py` 的 `extract_free_slots`，约第 536 行。
- `backend/app/services/data_analysis.py` 的 `_plan_analysis_spec`，约第 361 行。
- `backend/app/services/web_search.py` 的 `_llm_rewrite_queries`，约第 111 行，由 `rewrite_queries` 调用。
- `backend/app/services/memory.py` 的 `extract_memories`，约第 256 行。
- `backend/app/services/session.py` 的 `generate_session_title`，约第 61 行，已经传入 `resolved_planner_model()`。
- `backend/app/agents/context/summary.py` 的 `summarize_incremental`，约第 64 行，已经传入 `resolved_planner_model()`。

路由角色，思考默认关，模型只读 `ROUTER_MODEL`。

- `backend/app/agents/router.py` 的 `_llm_decide`，约第 193 行，`complete_json_schema` 加 `ROUTER_JSON_SCHEMA`。
- `backend/app/agents/harness/critique.py` 的 `critique_draft`，约第 324 行，`complete_json_schema` 加 `_CRITIQUE_SCHEMA`，模型是 `resolved_router_model()`。

判分角色，思考默认开，模型只读 `JUDGE_MODEL`。

- `backend/app/eval/context_replay.py` 的 `resolved_judge_model`，约第 443 行。空字符串时现在会回到回答模型。`ds-3` 删掉这个回退。约第 210 行的 `complete_json` 用判分模型。
- `backend/app/eval/ragas_judge.py` 约第 62 行，现在用 `settings.llm_model`。`ds-3` 改成 `JUDGE_MODEL`。

不改的地方。

- `backend/scripts/gen_*.py` 生成题集，不调用模型。
- 沙箱目录里没有聊天补全调用。
- `backend/app/core/llm.py` 的 `complete_json_async` 没有调用方。`ds-2` 删掉。
- 路由缓存键在 `router_cache_key`，约第 253 行，字典里已有 `model`。版本常量 `_ROUTER_CACHE_VERSION` 现在是 2。
