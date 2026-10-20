# Agent 评测（Tool Routing / Intent）

对标业界 Agent 评测分层（LangSmith / ToolLLM / τ-bench 等），本仓库 **离线** 可跑的指标如下。

## 0. 统一入口（必读）

所有套件经 `backend/scripts/eval.py` 调度。目标优先级：`--target` > 环境变量 `EVAL_TARGET` > 套件默认。venv 目标强制 SQLite + 关 Redis；Compose `eval` 服务会注入 `EVAL_TARGET=services`（日常 `up/start/ps` 不会拉起该服务）。`rag` / `routing` / `context` / `tabular` / `calendar` 即使 target=services 也把应用库指到 scratch SQLite，避免往线上 Postgres 灌评测行、踩 FORCE RLS。`agent` / `yz` / `grounding` 仍用真实库并 `set_rls_context`。`tabular` full 在 services 下探 `sandbox-runner /health`，不在 eval 容器里查 `omni-sandbox` 镜像。对外汇报的 L2 主指标是 **`agent` 任务完成率**；`yz` 已弃用，仅作对照。

Compose `eval` 服务已设 `EVAL_TARGET=services`。**不要**在 eval 容器里查本机 `omni-sandbox` 镜像——live 表格走 `sandbox-runner:8002`。`omni-butler-sandbox-1 Exited` 是镜像占位容器正常退出，不是故障。

## 0b. 各套件与生产路径的差异登记（引用数字前先看这里）

只有一部分套件是"原样跑生产路径"。下表把差异登记在册，**引用任何数字前先确认它属于哪一栏**。

| 套件 / 档位 | 是否生产路径 | 差异点 | 数字该怎么读 |
|---|---|---|---|
| `routing` 全部档位 | 部分 | 调用生产 `plan_tools`，但上下文由评测自己组装（生产走 `plan_node`，入参来源不同） | "规划函数的表现"，不是端到端路由表现 |
| `rag` smoke | ❌ | 语料按空行切段、纯关键词检索、关闭混合与向量；结果按文档去重 | 关键词对照基线，**不代表生产检索** |
| `rag` full | ✅ | 走 `ingest_document` 生产分块 + 混合检索；报告里 `mode=production_hybrid`、`degraded=false` | 可代表生产检索；`degraded=true` 的运行直接判失败 |
| `tabular` smoke | ❌ | `--dry-compile`：只编译计划、不执行沙箱；报告里 `plan_arm=heuristic` | "启发式规划臂的计划编译率"，**不代表生产规划能力** |
| `tabular` full | ✅ | 大模型规划（`spec_source=llm`）+ 沙箱执行；规划失败的行标 `spec_source=heuristic_fallback` 并计入 `planner_error_rate` | `plan_accuracy` 按 `spec_source` 拆分看；`planner_error_rate` 必须为 0 |
| `grounding` 全部档位 | 部分 | 调用生产 `ground_and_repair_answer`，但证据用冻结上下文，不经过真实检索 | "依据核验组件的忠实度" |
| `context` 全部档位 | 部分 | 上下文按生产逻辑组装，但历史是脚本化回放；多臂对照（legacy/window/full）是评测专有 | 上下文策略对照；live 档判分模型见 `judge_model` |
| `agent` 全部档位 | ✅ | 真实 `run_agent_workflow`；仅联网工具是确定性桩 | 唯一可对外汇报的 L2 指标 |
| `calendar` 全部档位 | 部分 | 真实 `complete_json` / `complete_text` 抽槽，假 Google；确认走本地接口 | 模型抽出的卡片和确认结果对不对，不是线上 Google |
| `yz` | ❌ 已下线 | 旁路实现（upload→ingest→retrieve→answer），不经过工作流 | 套件已从注册表移除，只保留文件作历史对照；不要引用 |

两条补充规则：

- **降级运行不参与判定**：`rag` 退回关键词模式、`tabular` 出现规划失败时，报告标 `degraded=true`，完整档直接判失败而不是给分数（冒烟档本来就是确定性回放，不在其列）。
- **判分模型**：`context` live 档判分用 `JUDGE_MODEL`（默认 `deepseek-v4-pro`），作答用 `CHAT_MODEL`（默认 `deepseek-flash`）。报告记录 `judge_model` / `answer_model` / `self_judged`。两个模型名相同才是"自己判自己"。默认两者不同，所以默认一次运行不是自评。

| 套件 | smoke | full | 默认 target |
|------|-------|------|-------------|
| `pytest` | health/router/gateway/metrics + 评测基础设施测试 | `tests/` 全量 | venv |
| `routing` | `--require-cache` | `--refresh-cache` + 统一 gates | venv |
| `rag` | 关键词分段对照 | `--production` ingest + hybrid（services） | venv |
| `agent` | `--limit 30` | 全量 `run_agent_workflow` | **services** |
| `grounding` | `--skip-ragas --limit 24` | `--score-drafts`（repair Δ） | **services** |
| `tabular` | `--dry-compile` heuristic | live `--planner llm` | venv |
| `context` | extractive legacy vs full + gate | 同 smoke，384 条；live 需 `RUN_CTX_EVAL=1` | venv |
| `calendar` | `--require-cache` 重放真实返回 | `--refresh-cache`，假 Google | venv |

```bash
cd backend
pip install -r requirements-dev.txt

python scripts/eval.py list
python scripts/eval.py run pytest --profile smoke
python scripts/eval.py run rag --profile smoke
python scripts/eval.py last rag --profile smoke

# Compose 栈已在跑时（端到端）：
docker compose --profile eval run --rm eval python scripts/eval.py run agent --profile smoke
python scripts/eval.py diff agent --profile full
```

### 报告在哪

```
backend/reports/
  index.jsonl
  <suite>/latest.json              # 最近一次 full（smoke 不覆盖）
  <suite>/latest-{profile}.json
  <suite>/latest.md
  <suite>/runs/<YYYYMMDD-HHMMSS>_<profile>/
      report.json
      samples.jsonl
      summary.md
      stdout.log
      junit.xml          # 仅 pytest
```

`report.json` 含 `meta`（suite/profile/target/argv/duration/git/dataset sha）与 `metrics` / `failures`。旧路径 `*_eval_latest.json` 已废弃。

路由缓存 / Context 基线仍在 `data/eval/`（不是报告）：`router_cache.json`、`calendar_model_cache.json`、`context_eval_baseline.json`。日历缓存只收真实 `complete_json` / `complete_text` 的返回，不手写金标准。

### 题集质量检查

所有正式题集由 `data/eval/seeds/*.jsonl` 的冻结问法或可复算 CSV 事实确定性生成，不调用 LLM、不用前后缀循环凑数。提交题集变更前运行：

```bash
python scripts/lint_eval_datasets.py --card
python -m pytest tests/test_eval_datasets_contract.py -q
```

题量、分布和契约状态见 `data/eval/DATASETS.md`。校验会拦截归一化重复、schema 漂移、gold 指向不存在语料及类别失衡。

---

## 1. 评测对象

| 层级 | 测什么 | 输入 | 金标准 |
|------|--------|------|--------|
| **Intent** | 单标签意图 | 用户消息 + context | `gold.intent` |
| **Tool set** | 多标签工具队列 | 同上 | `gold.tools`（集合） |
| **Trajectory** | 有序队列召回 | 预测 `pending_tools` | `recall@k` |
| **Gateway** | 治理契约 | 工具名 + 参数 | deny/allow（单独契约测） |

RAG 检索层（可选）：`precision@k` / `recall@k` / `MRR` — 见 `metrics.retrieval_report()`，需在 JSONL 中标注 `gold.doc_ids`。

## 1b. RAG 检索增强（retrieval-resume）

管线：`keyword + vector → RRF（candidate_k）→ 邻接扩展（±window）→ 智谱 Rerank → top_k`。

| 配置 | 含义 |
|------|------|
| `RAG_CANDIDATE_K` | 融合后候选数（默认 20） |
| `RAG_EXPAND_WINDOW` | 同文档 `chunk_index` 邻接窗（默认 1） |
| `RAG_RERANK_PROVIDER` | `zhipu`（默认）/ `none`（启发式） |
| `RERANK_MODEL` | 智谱模型名，默认 `rerank` |

评测数据集：`backend/data/eval/rag_retrieval.jsonl`（**400** 条：300 单文档、60 多文档、40 难负例；语料 16 篇文档）。问法冻结在 `data/eval/seeds/rag_queries.jsonl`，语料在 `data/eval/corpus/rag/`；用 `python scripts/gen_rag_eval.py` 重建。

```bash
python scripts/eval.py run rag --profile smoke
python scripts/eval.py run rag --profile full          # --zhipu-rerank
# 兼容旧入口：python scripts/run_rag_eval.py
```

指标：

- **Hit@k** = 前 k 个结果中是否至少有一个 gold（单文档题首看）
- **Precision@k** = 前 k 个预测文档中命中 gold 的比例（按样本平均）
- **Recall@k** = gold 文档被召回的比例
- **MRR** = 第一个正确文档排名的倒数平均

无 retrieval gold 的 chat / refusal 样本不进入这些均值；报告同时记录 `n_scored`。单文档 gold 下 Precision@k 的上限受 k 影响，比较检索可用性时优先看 Hit@k / MRR。

确定性 smoke 在 2026-09-17 的重建基线为 Recall@k **0.9888**、MRR **0.9322**；同日智谱 rerank full 为 Recall@k **0.9912**、MRR **0.9796**。回归门槛取较低基线减约 0.05，即 Recall@k ≥ **0.93**、MRR ≥ **0.88**；full 使用相同 Recall 门槛。

难负例切片（`hard_negative`，n=40）在同一关键词基线上是 Recall@k **1.0**、MRR **0.875**（命中还在，排序更靠后）。只看整体均值时，这一切片掉 0.17 的 MRR 只会把总 MRR 拉低约 0.018，仍能过 0.88。因此 smoke/full 另设切片门槛：Recall@k ≥ **0.95**、MRR ≥ **0.82**（约低于该切片基线 0.05）。整体门槛不另收紧，它们已经落在已公布基线减 0.05 附近。

## 2. 标准指标定义

### Intent（单标签分类）

- **Accuracy** = 预测 intent 与 gold 一致的比例。限流、超时等基础设施失败不进入分母，并单独报告 `infra_error_rate`；schema 规划失败仍记为 `error`，留在分母里。全是基础设施失败时准确率为 0，不能靠空分母记成 100%。
- **Macro-F1** = 各类别 F1 的算术平均（关注长尾 intent）  
- **Confusion matrix** = 混淆矩阵，定位「calendar 被判成 chat」等

### Tool set（多标签，与 ToolLLM / ACL tool-selection 一致）

- **Exact-match accuracy（CSR）** = 预测工具集合与 gold 完全一致的比例  
- **Micro Precision / Recall / F1** = 把所有 (样本×工具) 摊平后计算  
- **Macro Precision / Recall / F1** = 每个工具单独算 P/R/F1 再平均  
- **Hamming loss** = 错标标签占比（越低越好）  
- **Per-label P/R/F1** = 单工具维度（kb / web / calendar / sandbox）

### Trajectory

- **Recall@k** = gold 工具中有多少出现在预测队列前 k 步（Scheme B 单步执行时 k=1 最重要）

## 3. 数据集格式（JSONL）

路径：`backend/data/eval/office_tool_routing.jsonl`（**400** 条；含 50 条多工具、至少 80 条多轮和 24 条日历续填；重建：`python scripts/gen_office_eval.py`）

```json
{
  "id": "kb_01",
  "message": "出差住宿标准是多少？按咱们库里的差旅制度说",
  "history": [],
  "context": {
    "has_kb_docs": true,
    "has_tabular_docs": false,
    "forced_kb": false,
    "use_kb": false,
    "pending_calendar": null
  },
  "gold": {
    "intent": "rag",
    "tools": ["kb"]
  },
  "tags": ["kb", "policy"]
}
```

**原则**：消息用口语化表述；失败时改路由提示词 / few-shot，不要改 gold 去迁就模型。

## 4. 运行

```bash
cd backend

python scripts/eval.py run routing --profile smoke
python scripts/eval.py run routing --profile full
# 兼容：python scripts/run_office_eval.py --json
```

路由评测走生产 `plan_tools`（与 `workflow.plan_node` 相同，含 `context_line` / `pending_calendar` 短路）。结果缓存在
`backend/data/eval/router_cache.json`；`--refresh-cache` 可强制重打。

400 条重建题集在 2026-09-17 使用 `glm-4-air` 的首轮 full 基线为 Intent Accuracy **0.9225**、Tool Exact **0.9275**。门槛按基线下留约 0.05 回归预算，二者均设为 **0.87**；换模型后应记录新基线再调整，不得改 gold 追分。

## 5. 扩展数据集

1. 从线上失败 case / 用户反馈复制真实话术  
2. 人工标注 `intent` + `tools` + `context`  
3. 跑 eval，看 `failures` 列表与 per-label F1  
4. 优先修 **recall 低** 的工具（漏调）和 **precision 低** 的工具（误调）

## 6. 与生产评测的关系

| 本仓库离线集 | 生产 / LLM 路由 |
|--------------|-------------------|
| mock 路由测评测管线本身 | `RUN_ROUTER_EVAL=1` 跑真实 LLM 路由打分（需 API key，非 CI 默认） |
| 不调 Docker / 外部 API | 端到端任务成功率、pass^k 需另建 E2E 集 |
| YZ 全链路集见 §7 | 检索 + Fact Containment；裸成稿上的 ragas |
| 依据核验忠实度见 §8 | 生产 `ground_and_repair` + 固定题 faithfulness |

参考：LangSmith classification eval、Berkeley Function Calling Leaderboard、ToolLLM (ICLR) tool-selection 表、agent-eval-harness（intent + retrieval P@k/R@k/MRR）。

## 6b. 端到端 Agent 评测（主 L2 指标）

题集：`data/eval/agent_tasks.jsonl`（**174** 题；`python scripts/gen_agent_eval.py`）。每题走真实 `run_agent_workflow`；web 为确定性桩；「今天」冻结为 `EVAL_TODAY`。

每题都带 `context`，由题集决定、不再由执行器写死：

| 字段 | 含义 | 题数 |
|------|------|------|
| `has_kb_docs` / `has_tabular_docs` | 可用性开关（`_apply_availability` 据此裁剪工具） | 各 4 道反例 |
| `pending_calendar` | 待续日程；命中 `plan_node` 短路，不经过路由 | 6 |
| `document_keys` | 限定文档范围（夹具键名，入库后解析成编号） | 5 |

三类新标签：`availability_gate`（8）、`slot_fill`（6）、`doc_scope`（5）。新增题型 `no_tool`：工作区提供不了所需工具时，唯一正确行为是"不执行任何工具并说明数据不可用"；判分要求工具集合为空，答案命中 `answer_spec` 或包含"数据/表格不可用"的说明。

三层分别计分，失败归到第一层：

| 层 | 指标 |
|----|------|
| 路由 | 实际执行工具集合 vs `route_gold.tools` |
| 证据 | gold 文档引用 / sandbox 是否执行（限定文档范围的题必须引用限定文档） |
| 完成 | `judges.judge_completion(task_type, answer_spec)` |

指标：`task_completion_rate`（附 bootstrap 95% CI）对外报数；**门禁只看 `core_completion_rate`**（`fact_qa` + `compute` + `multi_tool` + `no_tool`），把 `chat` / `calendar` / `refuse` 这类近乎白送的部分排除在外。另设 `infra_error_rate ≤ 0.05`，避免大模型大面积报错时靠幸存题目过门槛。路由阶段的限流、超时和同类传输失败记为 `infra_error`（与作答路径的 `LLMInfraError` 相同），不进入 `core_completion_rate` 分母；schema 校验失败仍是 `workflow_error`。拒答题不带 `gold_doc_keys`；考勤汇总标 `sandbox`。

```bash
docker compose --profile eval run --rm eval python scripts/eval.py run agent --profile smoke
docker compose --profile eval run --rm eval python scripts/eval.py run agent --profile full
```

## 7. YZ 全链路评测（已弃用，请用 agent）

**工作纪律：评测阶段只出报告，不边测边改产品。** Runner 将失败样例写入 `failures` 清单；统一修复另开一轮后再复跑同一套题库。

语料：`backend/tests/YZ测试文档/`。**GitHub 只跟踪** `测试用例.md`、`测试用例.txt`。考勤 `测试用例.csv` 只放本机（已 gitignore），克隆公开仓后没有该文件，YZ 全链路评测需自行准备。金标准：`backend/data/eval/yz_fullchain.jsonl`（**400** 条；130 policy + 100 product + 100 attendance + 30 multi-doc + 20 unanswerable + 20 chat；重建：`python scripts/gen_yz_eval.py`）。100 条考勤 gold 已冻结在 `seeds/yz_attendance.jsonl`；本机 CSV 变化时显式加 `--refresh-attendance-seed` 复算。

### 依赖

```bash
pip install -r requirements.txt   # 含 ragas、datasets、langchain-openai
```

Judge LLM 走聊天端点（`LLM_BASE_URL` + `LLM_API_KEY`）。ragas 的 Embeddings 走 `EMBEDDING_BASE_URL` + `EMBEDDING_API_KEY`，不跟随聊天那组；留空时评测直接报错并写出变量名。
答案生成和判分使用 `LLM_TIMEOUT`（默认 120s）。判分思考默认开启，请求不送 `temperature`。
生成阶段较慢，可用 `--limit N` 先小跑，或 `--skip-ragas` 只测检索+规则指标。

### 指标

| 层级 | 指标 | 来源 |
|------|------|------|
| 检索 | Hit@k / Recall@k / MRR / P@k | 自有 `metrics.retrieval_report` |
| 生成 | faithfulness / answer_relevancy | **ragas** |
| 检索+生成 | context_precision / context_recall | **ragas**（需 `ground_truth`） |
| 辅助 | Fact Containment | `gold_facts` 子串命中 |
| 辅助 | Citation Hit | 角标引用是否命中 `gold_doc_keys` |
| 路由 | Intent / Tool exact | 含 `gold.intent` 的子集 |

### 运行

`yz` 已从 `scripts/eval.py` 注册表移除，**不要**再执行 `python scripts/eval.py run yz`。对照请跑 `agent`。历史旁路脚本 `scripts/run_yz_eval.py` 仍在仓库里，但不进门禁、也不要当现行入口。

报告若手工跑过历史脚本，旧文件在 `backend/reports/yz/`（该目录已被 gitignore）。

## 8. 依据核验忠实度评测（grounding + ragas）

**分工**：YZ（§7）测检索 / Fact Containment / 裸成稿；本评测专测生产路径 **`ground_and_repair_answer`** 的生成忠实度与规则门闩。不把 400 条口语复述全套上 ragas。

| 维度 | 本评测 | YZ 全链路 |
|------|--------|-----------|
| 成稿 | `complete_text` + **核验重写** | 裸 `complete_text` |
| 证据 | kb 真检索；web/sandbox **冻结片段** | 仅 kb 检索 |
| 主指标 | ragas **faithfulness**（+ 可选 relevancy） | faithfulness + context_* + fact containment |
| 题量 | 120 题（kb/web/sandbox 各 40） | 400 |

**工作纪律**：只出报告，不改 gold 迁就模型；不默认 live 搜索 / Docker 沙箱。

### 数据集

- 路径：`backend/data/eval/grounding_faithfulness.jsonl`
- 重建：`python scripts/gen_grounding_eval.py`
- 字段：`id` / `source`(kb|web|sandbox) / `query` / `gold_doc_keys` / `contexts` / `needs_sandbox` / `expect_unanswerable` / `tags`

### 运行

```bash
cd backend

python scripts/eval.py run grounding --profile smoke
python scripts/eval.py run grounding --profile full
```

**在 Compose 虚拟机上跑**：

```bash
cd /mnt/hgfs/omni-butler
docker compose --profile eval run --rm eval python scripts/eval.py run grounding --profile smoke
```

报告：`backend/reports/grounding/latest.json`。含按 `source` 分组的 mean faithfulness、repair_rate、disclaimer_rate、sandbox_rule_hit_rate。

冒烟档固定取文件前 24 条（kb / web / sandbox 各 8），这 24 条全是可答题，`n_unanswerable=0`，`refusal_accuracy` 没有分母。这时跳过该门禁，**不算失败**。冒烟报告里一旦真有拒答率，仍要 ≥ 0.40。完整档不受影响：`faithfulness_mean` ≥ 0.70，`refusal_accuracy` ≥ 0.60。缺了拒答率（完整档题集里有不可答题）仍然失败。

## 9. 办公表格客观短答案评测（office_tabular）

与 grounding 冻结集分离：金标准是数字/实体，程序比对（容忍 `0.082` ≡ `8.2%`）。对标 DABstep / InfiAgent-DABench 的**闭式短答案准确率**，主看执行结果而非 SQL/代码字符串匹配。

### 数据集与重建

- 数据集：`backend/data/eval/office_tabular.jsonl`（**200** 题，`split=smoke` 30 题）
- 夹具：`backend/data/eval/fixtures/`（9 张自造办公表：原有员工/考勤/订单/退货/销售/产品，加费用、项目、库存）
- 重建：`python scripts/gen_office_tabular_eval.py`（pandas 算金标准后冻结进 jsonl）

### 冒烟档为什么只放简单题（重要）

冒烟档计划分走的是**启发式臂**（`plan_arm=heuristic`）。实测该臂的规划通过率：

| 切片 | 通过率 | 原因 |
|---|---|---|
| 简单难度 | 40/52 | 问句里直接出现列名，规则能认 |
| **中等难度** | **5/94** | 中等题全是"按列的值过滤"（部门＝产品/研发、报销状态＝已通过）。**规则只能从问句里认列名，认不出列的值**，所以过滤条件生成不出来 |
| 困难 · 关联 | 9/14 | 关联分支部分可用 |
| 困难 · 比率 | 3/4 | — |

因此冒烟档**刻意只收简单题**，中难题交给完整档（走大模型规划器）。这不是数据挑选，而是一条真实能力边界：**启发式规划器无法处理"按数据值过滤"的题**，一旦大模型规划不可用，这类题没有可用的降级方案。

`contracts._tabular_check` 会强制这条组成规则（冒烟档必须含比率题、拒答题、脏数据题，且不得含中等难度与关联题），避免有人重新平衡切片时把它悄悄改掉。

计划打分的口径由报告顶层字段给出：`plan_arm`（`heuristic` / `llm` / `llm_with_heuristic_fallback`）、`plan_arm_note`，以及逐题的 `spec_source`。

### 运行

```bash
python scripts/eval.py run tabular --profile smoke   # dry-compile
python scripts/eval.py run tabular --profile full    # live sandbox
```

Compose 虚拟机（live full）：

```bash
docker compose --profile eval run --rm eval python scripts/eval.py run tabular --profile full
```

报告：`backend/reports/tabular/latest.json`

### 主指标与切片

- **Accuracy** / **Smoke Accuracy** / **Full Accuracy**
- **Plan Accuracy** / **Plan∧Exec**
- **Easy / Hard Accuracy**（`difficulty`）
- 按 **tags** 切片：`sum` `count` `filter` `median` `rate` `join_ok` `join_refuse` `missing` `followup` 等
- **Join Success** 与 **Join Refuse** 分开统计
- **Refuse Recall / Precision**（拒答质量）
- 失败桶：`sandbox_error` `wrong_number` `join_wrong_measure` `join_empty` `silent_substitute` `join_skipped` 等
- **capability_breakdown**（`concepts` 字段）、**top_failures**、**plan_exec_gap_by_tag**
- Markdown 摘要：`python scripts/summarize_office_tabular_report.py`

### CI / 回归门禁（本地 pytest）

| 门禁 | 条件 |
|------|------|
| 必过 | `--dry-compile --split smoke` → 30/30 plan |
| 必过 | 生成器幂等、join 推断、假缺列修复单测 |
| 软门禁（VM Docker） | smoke live ≥ 27/30；full `join_ok` ≥ 10/14 |

### 目标阈值（live 修复后）

- smoke exec ≥ **90%**（27/30+）
- full exec ≥ **80%**
- `rate` / `dirty` tag exec 应接近 plan（非 0%）

### 与生产的衔接

- 启发式 join：`analysis_ir.infer_join_from_message` → 填 `join_left` / `join_right` / `join_key`
- 无 DB 入口：`data_analysis.analyze_local_tables(message, files)` — 评测与生产共用 join/拒答/沙箱路径
- 夹具会先拷到 `SANDBOX_TMP_DIR`（`/var/omni-tmp`），避免 api 容器内路径被 docker.sock 挂成目录

### 边界

- **不要**把 grounding_eval 默认改成 live Docker；两条评测线互不替代
- 拒答题必须带 `missing` 原因词（如「年终奖」「join」）；沙箱崩溃、metrics 为空不算通过
- 本集**不用** ragas faithfulness 做主分

## 10. 上下文管理评测（ContextManager）

对标 LongMemEval / NIAH / AgentMemBench：固定脚本化历史（教师强制），只变上下文策略；CI 用确定性指标，夜间可跑真实 LLM。

### 分层

| 层 | 内容 | 命令 |
|----|------|------|
| L0 | 不变式（system+当前 user、无半轮、角标⊆included、legacy 等价、token MAPE） | `python -m pytest tests/test_context_invariants.py -q` |
| L1 | 回放 + extractive 摘要；needle_presence / pending_retention | `python scripts/eval.py run context --profile smoke` |
| 故障 | 摘要失败、估算漂移、异步追赶、regenerate、600 字裁剪 xfail | `python -m pytest tests/test_context_faults.py -q` |
| L2 | 真实摘要 + 作答 + 裁判（需 API） | `RUN_CTX_EVAL=1 python scripts/run_context_eval.py --live --split full --gate` |
| L3 | 日志聚合 + 线上会话导出回流 | `python scripts/ctx_log_report.py --log ...` / `python scripts/export_context_case.py --session-id N --question ... --gold-keys ...` |

### 数据集

- 生成：`python scripts/gen_context_eval.py`
- 路径：`data/eval/context_recall.jsonl`（384 条；8 类型 × 4 桶 × 4 深度 × 3 种子；smoke 32）
- 字段：`history`（脚本回复）/ `pending_calendar` / `evidence_pool` / `probe{question,gold_keys,judge_mode}`
- 校准：`data/eval/token_calibration.jsonl`

### 主对照（默认只跑两臂）

| arm | 含义 |
|-----|------|
| `legacy` | `CONTEXT_MANAGER_ENABLED=false` |
| `full` | 完整 ContextManager |

默认 smoke/full 都只比较 `legacy` 与 `full`，对应「完全没做」和「完整实现」。`window`、`window_summary`、`oracle` 仍保留为排障用的可选臂，不进入默认完整测试。

### 门禁

`needle_presence` 只写进报告，不设门禁。abstention 跌幅没有实现，不要把它当成已生效的门槛。

实际会失败的条件：

- 冒烟与完整档都硬卡：`invariant_violations==0`；`pending_retention==1.0`
- 只有完整档硬卡 `arms.full.overall.compaction_rate>=0.05`（只把 `compact` / `emergency` 算进压缩率，`warn` 不算）
- 冒烟档是短桶探针（只要 12/30 轮），和完整档不可比。这些对话到不了压缩阈值，产品正常时 `compaction_rate` 也是 0，所以不把压缩率当冒烟硬门禁。压缩有没有发生，看完整档。
- 基线：`data/eval/context_eval_baseline.json`（进 git；`--update-baseline` 更新）。报告的 `split` 必须和基线一致，smoke 对 full 基线直接失败，不拿两组数字互比
- live 相对基线：`fact_recall` 跌超过 0.05；`actual_tokens_mean` 涨超过 10%；`summary_fail_rate>5%` → exit 1
- 报告：`reports/context/latest.json`（gitignore 整个 `reports/`）

### 提交前建议

```bash
cd backend
python scripts/eval.py run context --profile smoke
python -m pytest tests/test_context_manager.py tests/test_context_invariants.py tests/test_context_faults.py tests/test_context_eval_gen.py -q
```

### 回流

线上失忆 → `export_context_case.py` 写出 `data/eval/context_replays/*.json` → 同一 `run_context_eval.py --dataset` 回归。

