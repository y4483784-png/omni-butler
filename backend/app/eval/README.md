# Agent 评测（Tool Routing / Intent）

对标业界 Agent 评测分层（LangSmith / ToolLLM / τ-bench 等），本仓库 **离线** 可跑的指标如下。

## 0. 统一入口（必读）

所有套件经 `backend/scripts/eval.py` 调度。目标优先级：`--target` > 环境变量 `EVAL_TARGET` > 套件默认。venv 目标强制 SQLite + 关 Redis；Compose `eval` 服务会注入 `EVAL_TARGET=services`（日常 `up/start/ps` 不会拉起该服务）。`rag` / `routing` / `context` / `tabular` 即使 target=services 也把应用库指到 scratch SQLite，避免往线上 Postgres 灌评测行、踩 FORCE RLS。`yz` / `grounding` 仍用真实库并 `set_rls_context`。`tabular` full 在 services 下探 `sandbox-runner /health`，不在 eval 容器里查 `omni-sandbox` 镜像。

目标优先级：`--target` > 环境变量 `EVAL_TARGET` > 套件默认。Compose `eval` 服务已设 `EVAL_TARGET=services`，**不要**在 eval 容器里查本机 `omni-sandbox` 镜像——live 表格走 `sandbox-runner:8002`。`omni-butler-sandbox-1 Exited` 是镜像占位容器正常退出，不是故障。

| 套件 | smoke | full | 默认 target |
|------|-------|------|-------------|
| `pytest` | health/router/gateway/metrics | `tests/` 全量 | venv |
| `routing` | 缓存命中即可 | `--refresh-cache` + 门槛 | venv |
| `rag` | 启发式 rerank | `--zhipu-rerank` | venv |
| `yz` | `--limit 10 --skip-ragas` | 全量 + zhipu rerank | **services** |
| `grounding` | `--skip-ragas --limit 8` | `--score-drafts` | **services** |
| `tabular` | `--dry-compile --split smoke` | live `--split full` | venv |
| `context` | extractive smoke + gate | 五臂 full + gate | venv |

```bash
cd backend
pip install -r requirements-dev.txt

python scripts/eval.py list
python scripts/eval.py run pytest --profile smoke
python scripts/eval.py run rag --profile smoke
python scripts/eval.py last rag

# Compose 栈已在跑时：
docker compose --profile eval run --rm eval python scripts/eval.py run yz --profile smoke
```

### 报告在哪

```
backend/reports/
  index.jsonl
  <suite>/latest.json
  <suite>/latest.md
  <suite>/runs/<YYYYMMDD-HHMMSS>_<profile>/
      report.json
      samples.jsonl
      summary.md
      stdout.log
      junit.xml          # 仅 pytest
```

`report.json` 含 `meta`（suite/profile/target/argv/duration/git/dataset sha）与 `metrics` / `failures`。旧路径 `*_eval_latest.json` 已废弃。

路由缓存 / Context 基线仍在 `data/eval/`（不是报告）：`router_cache.json`、`context_eval_baseline.json`。

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

评测数据集：`backend/data/eval/rag_retrieval.jsonl`（**500** 条口语化问法；seed 语料 10 篇文档）。可用 `python scripts/gen_rag_eval_500.py` 重建。

```bash
python scripts/eval.py run rag --profile smoke
python scripts/eval.py run rag --profile full          # --zhipu-rerank
# 兼容旧入口：python scripts/run_rag_eval.py
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

python scripts/eval.py run routing --profile smoke
python scripts/eval.py run routing --profile full
# 兼容：python scripts/run_office_eval.py --json
```

路由评测走 `app.agents.router.route`（严格 LLM 判定），结果缓存在
`backend/data/eval/router_cache.json`；`--refresh-cache` 可强制重打。

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

语料：`backend/tests/YZ测试文档/`。**GitHub 只跟踪** `测试用例.md`、`测试用例.txt`。考勤 `测试用例.csv` 只放本机（已 gitignore），克隆公开仓后没有该文件，YZ 全链路评测需自行准备。金标准：`backend/data/eval/yz_fullchain.jsonl`（**≥380** 条；重建：`python scripts/gen_yz_eval_400.py`）。

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

python scripts/eval.py run yz --profile smoke    # 需 Compose eval 服务
python scripts/eval.py run yz --profile full
# 兼容旧入口仍可用；报告写入 reports/yz/...
```

报告输出：`backend/reports/yz/latest.json` + `runs/<ts>/` + 终端摘要。

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

python scripts/eval.py run grounding --profile smoke
python scripts/eval.py run grounding --profile full
```

**在 Compose 虚拟机上跑**：

```bash
cd /mnt/hgfs/omni-butler
docker compose --profile eval run --rm eval python scripts/eval.py run grounding --profile smoke
```

报告：`backend/reports/grounding/latest.json`。含按 `source` 分组的 mean faithfulness、repair_rate、disclaimer_rate、sandbox_rule_hit_rate。

## 9. 办公表格客观短答案评测（office_tabular）

与 grounding 冻结集分离：金标准是数字/实体，程序比对（容忍 `0.082` ≡ `8.2%`）。对标 DABstep / InfiAgent-DABench 的**闭式短答案准确率**，主看执行结果而非 SQL/代码字符串匹配。

### 数据集与重建

- 数据集：`backend/data/eval/office_tabular.jsonl`（约 **88** 题，`split=smoke` 20 题供默认 live）
- 夹具：`backend/data/eval/fixtures/`（7 张自造办公表：`employees` / `attendance` / `orders` / `returns` / `sales_regions` / `products` + 回归小表）
- 重建：`python scripts/gen_office_tabular_eval.py`（pandas 算金标准后冻结进 jsonl）

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
- 路径：`data/eval/context_recall.jsonl`（192 条；smoke 24）
- 字段：`history`（脚本回复）/ `pending_calendar` / `evidence_pool` / `probe{question,gold_keys,judge_mode}`
- 校准：`data/eval/token_calibration.jsonl`

### 五臂对照

| arm | 含义 |
|-----|------|
| `legacy` | `CONTEXT_MANAGER_ENABLED=false` |
| `window` | 仅窗口 |
| `window_summary` | 窗口+摘要 |
| `full` | 完整 ContextManager |
| `oracle` | 近乎不压缩（est>128k 标 skipped） |

### 门禁

- 硬：`invariant_violations==0`；每个桶 `full.needle_presence >= legacy`；`pending_retention==1.0`
- 相对基线：`data/eval/context_eval_baseline.json`（进 git；`--update-baseline` 更新）
- 跌幅：needle>0.03 / fact_recall>0.05 / abstention>0.05；token 涨>10%；summary_fail_rate>5% → exit 1
- 报告：`reports/context/latest.json`（gitignore 整个 `reports/`）

### 提交前建议

```bash
cd backend
python scripts/eval.py run context --profile smoke
python -m pytest tests/test_context_manager.py tests/test_context_invariants.py tests/test_context_faults.py tests/test_context_eval_gen.py -q
```

### 回流

线上失忆 → `export_context_case.py` 写出 `data/eval/context_replays/*.json` → 同一 `run_context_eval.py --dataset` 回归。

