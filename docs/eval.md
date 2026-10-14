# 评测怎么跑

指标含义、每一套和线上路径差在哪，以这两份为准：

- [backend/app/eval/README.md](../backend/app/eval/README.md)
- [docs/评测系统白话说明.md](评测系统白话说明.md)

入口是 `backend/scripts/eval.py`。日常 `docker compose up` 不会拉起 `eval` 这个服务，要用 `--profile eval`。

引用任何数字之前，先记住三条：

- **门禁只认完整档**。冒烟档不代表生产路径：路由冒烟是缓存重放，表格冒烟只编译计划、不进沙箱。
- **端到端套件的门禁看 `core_completion_rate`**，不含闲聊、日程、拒答这类几乎必过的题。`infra_error_rate` 超过 5% 直接算失败。
- **降级运行不参与判定**。检索退回关键词，或表格的大模型规划失败，报告会标 `degraded`，入口直接判失败，不给一个还能看的分数。

`yz` 已经从登记册拿掉。不要再 `run yz`，也不要引用它的分数。

```bash
cd backend
pip install -r requirements-dev.txt

python scripts/eval.py list
python scripts/lint_eval_datasets.py --card

# 冒烟。虚拟环境里自动用 SQLite、关掉 Redis，不连 Compose 的中间件。
python scripts/eval.py run pytest --profile smoke
python scripts/eval.py run rag --profile smoke
python scripts/eval.py run tabular --profile smoke
python scripts/eval.py run context --profile smoke

# 需要 MinIO、Qdrant、Postgres 的套件：Compose 的 eval 服务。
# 先按仓库根目录 README 把栈拉起来。
docker compose --profile eval run --rm eval python scripts/eval.py run agent --profile smoke
docker compose --profile eval run --rm eval python scripts/eval.py run grounding --profile full

# 检索的完整档要走正式入库和混合检索。
# 本机虚拟环境会在预检直接退出（退出码 2），不会改成关键词模式还记一笔完整档成绩。
# 关键词对照就是冒烟档。
docker compose --profile eval run --rm eval python scripts/eval.py run rag --profile full

# 最近一次报告按档分开。冒烟不会覆盖完整档的 latest.json。
python scripts/eval.py last rag --profile smoke
python scripts/eval.py diff agent --profile full
```

报告在 `backend/reports/<套件>/latest-<档>.json`，总索引是 `backend/reports/index.jsonl`。

办公场景的约定仍由 pytest 覆盖。单个文件可以：

```bash
python -m pytest tests/test_harness_gateway.py -q
```

评测容器里没有 Docker 套接字。表格完整档通过 `sandbox-runner:8002` 跑沙箱，不要在 eval 容器里找 `omni-sandbox` 镜像。`omni-butler-sandbox-1` 显示 Exited 是占位容器正常退出。
