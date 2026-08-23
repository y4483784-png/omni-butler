# Agent 评测（Tool Routing / Intent）

对标业界 Agent 评测分层（LangSmith / ToolLLM / τ-bench 等），本仓库 **离线** 可跑的指标如下。

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

评测数据集：`backend/data/eval/rag_retrieval.jsonl`（**500** 条口语化问法；seed 语料 10 篇文档）。可用 `python scripts/gen_rag_eval_500.py` 重建。

```bash
python scripts/run_rag_eval.py
python scripts/run_rag_eval.py --zhipu-rerank   # 需 LLM_API_KEY
pytest tests/test_rag_expand_rerank.py tests/test_rag_retrieval_eval.py -q
```

指标：

- **Precision@k** = 前 k 个预测文档中命中 gold 的比例（按样本平均）
- **Recall@k** = gold 文档被召回的比例
- **MRR** = 第一个正确文档排名的倒数平均

## 2. 标准指标定义

### Intent（单标签分类）

- **Accuracy** = 预测 intent 与 gold 一致的比例  
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

路径：`backend/data/eval/office_tool_routing.jsonl`（目标 **500** 条；重建：`python scripts/gen_office_eval_500.py`）

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

# 人类可读报告
python scripts/run_office_eval.py

# JSON（CI / 看板）
python scripts/run_office_eval.py --json

# 门槛（CI gate）
python scripts/run_office_eval.py --min-intent-acc 0.82 --min-tool-exact 0.78

# pytest（默认离线：mock 路由测管线 + gateway 契约；不打真实 API）
pytest tests/test_harness_office_eval.py tests/test_eval_metrics.py -q

# 真实 LLM 路由打分（500 条真实调用，显式开关才跑）
RUN_ROUTER_EVAL=1 pytest tests/test_harness_office_eval.py::test_routing_eval_meets_thresholds -q
```

路由评测走 `app.agents.router.route`（严格 LLM 判定），结果缓存在
`backend/data/eval/router_cache.json`；`run_office_eval.py --refresh-cache` 可强制重打。

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

## 7. YZ 全链路评测（upload → ingest → retrieve → answer）

**工作纪律：评测阶段只出报告，不边测边改产品。** Runner 将失败样例写入 `failures` 清单；统一修复另开一轮后再复跑同一套题库。

语料：`backend/tests/YZ测试文档/` 三份文件（产品指南 md、制度 txt、考勤 csv）。金标准：`backend/data/eval/yz_fullchain.jsonl`（**≥380** 条；重建：`python scripts/gen_yz_eval_400.py`）。

### 依赖

```bash
pip install -r requirements.txt   # 含 ragas、datasets、langchain-openai
```

Judge LLM / Embeddings 走现有智谱 OpenAI 兼容端点（`LLM_BASE_URL` + `LLM_API_KEY`）。
答案生成使用 `LLM_TIMEOUT`（默认 120s）；ragas 侧已将 `temperature` 四舍五入到 2 位小数以兼容智谱。
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

```bash
cd backend

# 默认：真实 MinIO 入库 + ragas（需 MinIO、Qdrant、LLM key）
python scripts/run_yz_eval.py

# 仅检索 + 规则指标（省 API 费用）
python scripts/run_yz_eval.py --skip-ragas

# 小子集调试
python scripts/run_yz_eval.py --limit 10 --skip-ragas

# JSON 报告 + 门槛告警（仍只告警，不改代码）
python scripts/run_yz_eval.py --json --min-recall 0.5

# 烟测（mock 入库，不断言高分）
pytest tests/test_yz_fullchain_eval.py -q
```

报告输出：`backend/reports/yz_eval_latest.json` + 终端摘要 + failures Top-N。

## 8. 依据核验忠实度评测（grounding + ragas）

**分工**：YZ（§7）测检索 / Fact Containment / 裸成稿；本评测专测生产路径 **`ground_and_repair_answer`** 的生成忠实度与规则门闩。不把 400 条口语复述全套上 ragas。

| 维度 | 本评测 | YZ 全链路 |
|------|--------|-----------|
| 成稿 | `complete_text` + **核验重写** | 裸 `complete_text` |
| 证据 | kb 真检索；web/sandbox **冻结片段** | 仅 kb 检索 |
| 主指标 | ragas **faithfulness**（+ 可选 relevancy） | faithfulness + context_* + fact containment |
| 题量 | ~48 互异题（每路 16） | ≥380 |

**工作纪律**：只出报告，不改 gold 迁就模型；不默认 live 搜索 / Docker 沙箱。

### 数据集

- 路径：`backend/data/eval/grounding_faithfulness.jsonl`
- 重建：`python scripts/gen_grounding_eval.py`
- 字段：`id` / `source`(kb|web|sandbox) / `query` / `gold_doc_keys` / `contexts` / `needs_sandbox` / `expect_unanswerable` / `tags`

### 运行

```bash
cd backend

# 成稿 + 核验重写 + ragas（需 LLM_API_KEY；kb 题需可入库）
python scripts/run_grounding_eval.py

# 对照：关闭重写（失败只贴免责）
python scripts/run_grounding_eval.py --no-repair

# 省费用：只看 repair / disclaimer / sandbox 规则
python scripts/run_grounding_eval.py --skip-ragas --limit 8

# 门槛告警（非 CI 默认）
python scripts/run_grounding_eval.py --min-faithfulness 0.75

# 量化「重写是否提高忠实度」（额外 judge 费用）
python scripts/run_grounding_eval.py --score-drafts

# 烟测（mock，不打真实 API）
pytest tests/test_grounding_eval.py -q
```

**在 Compose 虚拟机上跑**：api 镜像不挂源码，需重建后 `exec`。`backend/.dockerignore` 已放行 `scripts/` 与 `data/eval/`。

```bash
cd /mnt/hgfs/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build api
docker compose up -d --force-recreate api api2
docker compose exec -w /app api ls scripts/run_grounding_eval.py
docker compose exec -w /app api ls "tests/YZ测试文档/测试用例.md"
docker compose exec -w /app api python scripts/run_grounding_eval.py --skip-ragas --limit 8
```

kb seed / 检索走 Postgres RLS：runner 会确保 `users.id=92002`（`yz_eval_92002`）存在，再 `set_rls_context`。不调整 `users_id_seq`（`omni_app` 无 UPDATE）。若仍报 RLS / FK，确认已重建包含该改动的 api 镜像。

若暂时不想重建，可一次性挂载宿主机目录（仍需镜像里已有 `app/eval/grounding.py`）：

```bash
docker compose run --rm \
  -v /mnt/hgfs/omni-butler/backend/scripts:/app/scripts:ro \
  -v /mnt/hgfs/omni-butler/backend/data:/app/data:ro \
  -v "/mnt/hgfs/omni-butler/backend/tests/YZ测试文档:/app/tests/YZ测试文档:ro" \
  api python scripts/run_grounding_eval.py --skip-ragas --limit 8
```

报告：`backend/reports/grounding_eval_latest.json`。含按 `source` 分组的 mean faithfulness、repair_rate、disclaimer_rate、sandbox_rule_hit_rate；`failures` 为 faithfulness &lt; 0.5 或规则命中等样例。

Judge 实现：`app/eval/ragas_judge.py`（YZ 与本评测共用）。

## 9. 办公表格客观短答案评测（office_tabular）

与 grounding 冻结集分离：金标准是数字/实体，程序比对（容忍 `0.082` ≡ `8.2%`）。对标 DABstep / InfiAgent-DABench 的**闭式短答案准确率**，主看执行结果而非 SQL/代码字符串匹配。

### 数据集与重建

- 数据集：`backend/data/eval/office_tabular.jsonl`（约 **88** 题，`split=smoke` 20 题供默认 live）
- 夹具：`backend/data/eval/fixtures/`（7 张自造办公表：`employees` / `attendance` / `orders` / `returns` / `sales_regions` / `products` + 回归小表）
- 重建：`python scripts/gen_office_tabular_eval.py`（pandas 算金标准后冻结进 jsonl）

### 运行

```bash
# 默认：live 沙箱，仅 smoke（20 题，避免满量 Docker）
python scripts/run_office_tabular_eval.py

# 计划准确率（无 Docker，可跑 full 88 题）
python scripts/run_office_tabular_eval.py --dry-compile --split full

# live 全量（可选，耗时长）
python scripts/run_office_tabular_eval.py --split full
```

Compose 虚拟机（需已重建 api 镜像）：

```bash
docker compose exec -w /app api python scripts/gen_office_tabular_eval.py
docker compose exec -w /app api python scripts/run_office_tabular_eval.py
docker compose exec -w /app api python scripts/run_office_tabular_eval.py --dry-compile --split full
```

### 两级评测（Plan vs Exec）

| 阶段 | 命令 | 含义 |
|------|------|------|
| **Plan** | `--dry-compile` | IR（operation / filters / join_key / uncomputable）与 `gold_plan` 一致 |
| **Exec** | 默认 live | 沙箱 `SUMMARY_JSON.metrics` 对 `gold.value`；拒答题必须带 `missing` 指定 token |

报告：`backend/reports/office_tabular_eval_latest.json`

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
| 必过 | `--dry-compile --split smoke` → 20/20 plan |
| 必过 | 生成器幂等、join 推断、假缺列修复单测 |
| 软门禁（VM Docker） | smoke live ≥ 18/20；full `join_ok` ≥ 10/14 |

### 目标阈值（live 修复后）

- smoke exec ≥ **90%**（18/20+）
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

