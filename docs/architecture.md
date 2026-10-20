# 架构说明

怎么把服务跑起来见 [README.md](../README.md)。这篇说明代码和进程怎么分工，方便改代码时知道该看哪。

## 已经定下的选择

| 项 | 选择 |
| --- | --- |
| 模型供应商 | 智谱 GLM。用 OpenAI 兼容的 SDK，地址可配。 |
| 代码沙箱 | 自己的 Docker 容器，数据不出这台机器。 |
| 多用户 | 登录 Cookie，按用户隔开会话、知识库、记忆、日程。Postgres 上还有行级隔离；超管会绕过它，所以日常连接用 `omni_app`。 |
| 回答怎么送到页面 | 一段一段推（SSE）。 |

## 目录

```
omni-butler/
├── docker-compose.yml          # postgres / qdrant / redis / minio / sandbox / sandbox-runner / api / worker / beat / nginx
├── docker-compose.override.yml # 自动加载：api2 / worker2，nginx 按连接数转发
├── deploy/                     # nginx、巡检、备份、HTTPS 示例
├── .env.example                # 配置模板，复制为 backend/.env
├── backend/                    # FastAPI
│   ├── Dockerfile              # 构建时生成镜像里的 /entrypoint.sh
│   ├── entrypoint.sh           # 仓库里的副本，镜像并不执行这一份，见下文
│   └── app/
├── frontend/                   # React + Vite。生产构建进 nginx 镜像
└── sandbox/                    # 受限 Python 容器，镜像名 omni-sandbox
```

一份后端镜像，四种角色，由环境变量 `MODE` 决定：

| MODE | 容器 | 做什么 |
| --- | --- | --- |
| `api` | `omni-butler-api`（以及 api2） | 聊天、上传。容器内端口 8001，不映射到宿主机。 |
| `worker` | `omni-butler-worker`（以及 worker2） | 文档入库。 |
| `beat` | `omni-butler-beat` | 定时回收卡住的入库任务。全套只跑一个。 |
| `sandbox` | `omni-butler-sandbox-runner` | 只负责按请求起沙箱容器。容器内端口 8002，不映射到宿主机。 |

镜像里真正执行的入口，是 `backend/Dockerfile` 在构建时写到 **`/entrypoint.sh`** 的那一份。仓库里的 `backend/entrypoint.sh` 不会被这个镜像调用。两份大体相同；仓库那份多了一步：第一个参数是 `alembic` 时，把 `DATABASE_URL` 换成 `DATABASE_MIGRATE_URL`。镜像里的那份没有这一步。迁移仍然走属主账号，因为 `backend/alembic/env.py` 自己会优先读 `DATABASE_MIGRATE_URL`。

中间件只在 Compose 网络里。生产路径不把 5432、6379、6333、9000 映射到局域网。浏览器只访问 nginx 的 80（启用 HTTPS 后是 443）。nginx 提供前端页面，并把 `/api`、`/health`、`/health/ready` 转到 api。

默认不做：公网 HTTPS（何时要做见 [docs/deployment.md](deployment.md)）、Kubernetes、Kafka、Flower、把沙箱改成队列。

## 端口

| 服务 | 端口 | 谁能访问 |
| --- | --- | --- |
| nginx | 80 | 推荐入口。静态页面 + `/api` + `/health`。 |
| nginx HTTPS | 443 | 按部署文档打开后才有。 |
| Vite | 5173 | 只有方案 B，在跑 `npm run dev` 的那台电脑上。 |
| FastAPI | 8001 | 容器内部。方案 B 是虚拟机上的 uvicorn 直接听这个端口。Compose **不**把它映射出来。 |
| sandbox-runner | 8002 | 只在 Compose 网络里。api 通过 `SANDBOX_RUNNER_URL=http://sandbox-runner:8002` 调用。 |
| Qdrant | 6333 | 方案 B 的 `docker run` 会映射到虚拟机。Compose 不映射。 |
| MinIO | 9000、控制台 9001 | 同上。 |
| Postgres | 5432 | 同上。 |
| Redis | 6379 | 同上。 |

上传大小：应用侧 `MAX_UPLOAD_MB` 默认 20。nginx 的 `client_max_body_size` 是 25MB，略宽一点，真正卡住的是应用这道。

三层默认值不要混：

| 来源 | 数据库 | 模型地址 |
| --- | --- | --- |
| 什么配置都没有，直接跑代码 | `config.py` 默认 SQLite，模型地址默认官方 OpenAI，模型名默认 `gpt-4o-mini` | 模型名以 `glm` 开头、地址仍是官方 OpenAI 时，会自动改到智谱 |
| 复制了 `.env.example` | Postgres，地址是 `localhost` | 智谱地址，`LLM_MODEL=glm-4-air` |
| Compose 容器里 | 覆盖成 `omni_app@postgres:5432`，迁移用 `omni` | 仍来自 `backend/.env` |

`.env.example` 里的向量模型是 `embedding-3-pro`，视觉模型是 `glm-4v-flash`。`config.py` 在没配环境变量时分别是 `embedding-3` 和 `glm-4.6v`。以你复制出来的 `backend/.env` 为准。

上表的「模型地址」只指聊天。聊天走 `LLM_BASE_URL` 和 `LLM_API_KEY`。向量、OCR、Rerank 各读自己的 `EMBEDDING_*`、`VISION_*`、`RERANK_*` 地址和密钥。`config.py` 里这六项默认是空字符串，不回退到聊天那组，`glm` 自动改写也不碰它们。规则表是 `app/core/config.py` 的 `ZhipuService`：

| 服务 | 何时算在用 | 关闭开关 |
| --- | --- | --- |
| 向量 | `RAG_HYBRID_ENABLED=true` | `RAG_HYBRID_ENABLED=false` |
| OCR | `VISION_OCR_ENABLED=true` | `VISION_OCR_ENABLED=false` |
| Rerank | `RAG_RERANK_ENABLED=true` 且 provider 是 `zhipu`、`auto` 或空 | `RAG_RERANK_ENABLED=false` |

在用的服务有空地址或空密钥时，api 在 `on_startup`（`init_db` 之前）、worker 在 `worker_init` 拒绝启动，报错一次列出所有缺的变量。Beat 和 sandbox-runner 不做这项检查。评测脚本运行中会改这些开关，所以客户端每次调用都重新读地址和密钥；缺项时抛 `EndpointConfigError`。Rerank 的 HTTP 失败仍退回启发式，记一条 `error` 日志并设 `diag["rerank_fallback"]`。配置缺项不算这种失败，直接报错。

测试（pytest）强制用 SQLite，不读 `.env` 里的 Postgres。

## 数据库

业务库是 Postgres。连接池大小见 `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`。Compose 里这两项被压到 5 和 10，覆盖 `.env`。SQLite 只留给没有 Docker 的快速起步，以及 pytest。

表结构用 Alembic（`backend/alembic/`，基线 `0001_baseline`）：

```bash
cd backend
alembic upgrade head
alembic stamp head
alembic check
alembic revision --autogenerate -m "add xxx"
```

Compose 里 api 已在跑时用 `docker compose exec api alembic upgrade head`。没在跑时：

```bash
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
```

不要把迁移塞进每次 api 启动。

## 工具路由

每条用户消息都走同一套代理流程，由 `backend/app/agents/router.py` 决定用哪些工具。没有另一条「闲聊快路径」。

- **Tier 0**：用户勾选了文档，或请求里 `use_kb=true`，并且库里真有文档。直接走知识库，不调路由模型。库是空的则不会强制。
- **Tier 1**：整句精确匹配的短问候（「你好」「在吗」「hi」等，去掉空白后不超过 6 个字符）走闲聊。句子里出现「知识库」「文档里」这类明确指代、并且库里有文档，走知识库。
- **Tier 2**：其余情况让模型按固定 JSON 结构做判断。模型名是 `ROUTER_MODEL`（空则 `PLANNER_MODEL`，再空则 `LLM_MODEL`）。模型返回 json。缺字段算失败，再试一次。`ROUTER_MAX_ATTEMPTS`（默认 1）只限制限流或超时后再请求几次。失败就用 SSE 的 `error` 告诉用户「工具规划失败」，不会改用正则，也不会假装当成闲聊。

| 变量 | 作用 |
| --- | --- |
| `ROUTER_MODEL` | 路由判断用的模型。人多时用轻量模型。 |
| `ROUTER_MAX_ATTEMPTS` | 限流或超时后，路由再请求的次数上限。默认 1。非法 json 不记在这里。 |
| `CHAT_MODEL` | 闲聊回答。空则与 `LLM_MODEL` 相同。 |
| `LLM_MODEL` | 要调工具或比较复杂的回答。 |
| `PLANNER_MODEL` | 规划用。空则 `LLM_MODEL`。 |

流式事件的常见顺序是：`ack`（`path` 为 `agent`）→ 有时有 `session_title` → `status`（`phase` 为 `planning`）→ `intent` → 然后才是 `ttft` 和 `token`。`ttft` 在第一段正文之前，不是在全部 token 之后。规划较久时，中间会有注释行形式的心跳。

自测必须先登录。业务接口认 Cookie（名字是 `omni_session`）。下面方案 B 打虚拟机的 8001；Compose 没有映射 8001，把 `BASE` 换成 `http://127.0.0.1`（nginx 的 80）。

```bash
BASE=http://127.0.0.1:8001
curl -s -c /tmp/omni.cj -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"你的ADMIN_PASSWORD"}' \
  "$BASE/api/auth/login"
curl -s -b /tmp/omni.cj -c /tmp/omni.cj -H 'Content-Type: application/json' \
  -d '{}' "$BASE/api/sessions"
# 把下面的 1 换成上一步返回的 id
curl -N -b /tmp/omni.cj -H 'Content-Type: application/json' \
  -d '{"session_id":1,"message":"你好"}' \
  -w '\nTTFB=%{time_starttransfer}s\n' \
  "$BASE/api/chat"
```

问候语命中 Tier 1 时不会再调路由模型，首字仍可能花时间在别的步骤上。改了路由代码后要重启进程；Compose 用 `docker compose build api` 再 `up -d --no-build`。共享盘上可以用 `grep RouterError backend/app/agents/router.py` 确认文件已经是新的。

## Redis

Redis 是可以丢掉的加速和协调层，不是业务数据的来源。连不上时会告警并跳过缓存，聊天和检索还在。

| 用途 | 按什么做键 | 默认 |
| --- | --- | --- |
| Tier 2 路由结果 | 消息、历史、库里有没有文档或表格、模型、路由提示词指纹 | 3 天（`ROUTER_CACHE_TTL`） |
| 问题向量 | 归一化后的问题、向量模型、`EMBEDDING_BASE_URL` | 7 天（`EMBEDDING_CACHE_TTL`） |
| 联网搜索 | 查询、引擎、条数、时效参数 | 5 分钟（`WEB_CACHE_TTL`） |
| 模型并发槽 | 模型名 | `LLM_MODEL_CONCURRENCY_LIMITS`；任务结束释放，180 秒租约兜底 |
| `/api/chat` 每人限流 | 用户 id | 默认 30 次 / 60 秒，超出返回 HTTP 429 和 `Retry-After` |

只缓存完全相同的中间结果，不会因为「问题看起来像」就复用最终回答。

路由键带 `ROUTER_SYSTEM` 的指纹，和评测磁盘缓存的 `prompt_sha` 是同一种算法。改了提示词，旧决定自动作废。决定的字段结构变了，还要去加 `backend/app/agents/router.py` 里的 `_ROUTER_CACHE_VERSION`（当前是 2）。

评测进程会设置 `EVAL_SKIP_ROUTER_REDIS=1`。只要评测入口已经初始化过（`EVAL_BOOTSTRAPPED=1`），也不会读写这份路由缓存，避免完整档把 Redis 里的旧决定写进 `router_cache.json`。线上不要设这两个变量。

## 入库队列

上传和「重新解析」不在 api 进程里做。api 把文件写入 MinIO，在 Postgres 记一条 `pending`，再把文档 id 投到 Redis 的 `ingest` 队列。worker 认领后解析、分块、做向量。

大 PDF 加 OCR 可能要几分钟。任务在 Redis 里，重启 api 不会把已入队的任务弄丢。worker 崩溃后，同一个任务会再投递，已完成的会跳过。

| 角色 | 方案 B 命令 | 队列 |
| --- | --- | --- |
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8001` | 只投递 |
| Worker | `celery -A app.tasks.celery_app:celery_app worker -Q ingest,maintenance -l info` | `ingest` 入库，`maintenance` 回收超时 |
| Beat（只开一个） | `celery -A app.tasks.celery_app:celery_app beat -l info` | 默认每 5 分钟扫卡住的 pending / processing |

`CELERY_BROKER_URL` 空着就用 `REDIS_URL`。确认晚、一次只预取 1 个，入库硬超时默认 1800 秒（`CELERY_INGEST_TIME_LIMIT`）。投递失败会把文档标成 `failed`，页面上可以点重新解析。pytest 设 `CELERY_TASK_ALWAYS_EAGER=true`，不连真实队列。

Compose 里 worker 和 beat 是两个容器。不要再给 worker 加 `-B`，否则定时任务会投两次。第二份入库进程是 `worker2`，不要 `--scale worker=N`。

## 用户文件

原文件只存 MinIO。数据库 `documents.stored_path` 只存对象键，例如 `u1/{uuid}_差旅.pdf`。不在项目目录或宿主机目录里长期保存原文件。

| 变量 | 方案 B / 模板 | Compose 容器内 |
| --- | --- | --- |
| `S3_ENDPOINT` | `http://127.0.0.1:9000` | `http://minio:9000`（编排文件覆盖） |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `minioadmin` | 与 MinIO root 一致。编排文件里 root 写死为 `minioadmin` |
| `S3_BUCKET` | `omni-uploads` | 同左 |

解析和沙箱需要读文件时，先下载到临时文件，用完即删。Compose 把临时目录放在 `/var/omni-tmp`，并挂给 api 和 sandbox-runner，这样宿主机上的 Docker 才能把同一路径挂进沙箱。方案 B 把 `SANDBOX_TMP_DIR` 留空。

表格（csv / xlsx）按全表文本分块，默认最多 `TABULAR_MAX_ROWS=5000` 行、`TABULAR_MAX_CHARS=800000` 字符。查某个单元格走检索；汇总和画图走沙箱读原文件。改了上限之后，旧文档要点「重新解析」。

数据分析可以这样确认沙箱是通的：

1. 知识库上传 `.csv` 或 `.xlsx`，等到状态 **ready**。
2. 勾选该文档。
3. 让它汇总或画图。意图应是数据分析，右侧出现图，消息上可以点「查看图表」再打开。
4. 若提示沙箱镜像不可用：在仓库根目录 `docker compose build sandbox`，并 `docker image inspect omni-sandbox:latest`。

Qdrant 可以这样确认：

1. 方案 B：`curl` 能访问 `QDRANT_URL`。Compose：在 api 容器里能访问 `http://qdrant:6333`，宿主机上没有 6333。
2. 重启后端或重新创建 api 容器。
3. 对目标文档点「重新解析」。
4. 警告里不再说 Qdrant 不可达。Qdrant 不可达时，检索会退回关键词，聊天不会因此停掉。

## 上下文

页面上的历史是全量的。送给模型的那一截由 `backend/app/agents/context/` 组装，和档案不是同一份。

- Postgres 或 SQLite 里的 `messages` 始终全留。
- 默认把最近 `MAX_CONTEXT_TURNS`（10）轮原文放进模型。更早的轮次写进 `sessions.context_summary`。
- `sessions.working_state` 放证据指针、最近文档、沙箱摘要。`pending_calendar` 仍是单独一列。
- `CONTEXT_MAX_TOKENS` 是软上限，不是模型的 128K 物理窗口。`CONTEXT_RESERVE_RATIO` 给输出和依据核验留位置。超过水位后，先收缩证据，再整轮丢掉，最后紧急截断。
- `CONTEXT_MANAGER_ENABLED=false` 时，退回「只切最近 N 轮」。

相关变量都在 `.env.example` 的 `CONTEXT_*`。

长期记忆是单独一条：`MemoryItem`，从对话里抽取再写回系统提示。开关是 `MEMORY_ENABLED`、`MEMORY_USE_LLM`、`MEMORY_MAX_CHARS`、`MEMORY_MAX_ITEMS`。

## 多用户

- 登录：`POST /api/auth/login`，HttpOnly Cookie。管理员由 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 在第一次启动时写入。密码空着则只有警告，没有密码哈希，登录会被拒绝。库里已经有密码之后，再改 `ADMIN_PASSWORD` 不会覆盖；改密码走侧栏「改密」。
- 业务接口都要登录。会话、知识库、记忆、日程按 `user_id` 隔开。客户端不要传 `user_id`。
- 生产库的迁移里包含行级隔离策略。SQLite 测试跳过。
- 管理员在侧栏「用户」里给同事建账号。

## 路线图里已经落地的部分

- 对话：流式输出、会话的增删改、自动标题、Markdown、图表等产物。
- 检索：解析、OCR、按结构分块；关键词加 Qdrant 向量；邻接块扩展和重排序；入库有阶段进度。
- 工具：计划 → 检索 → 反思 → 回答。知识库、联网、日程、沙箱走同一出口。数据分析是 Docker 沙箱。
- 长期记忆和上下文管理：见上面两节。
- 多用户登录和数据隔离已经落地。费用算到人、LDAP / OIDC、部门共享知识库还没做。日程有对话里的卡片；独立的日程面板还没有。不打算把仓库换成另一套代理框架或图谱检索。

## 使用中见过的现象

| 现象 | 说明 |
| --- | --- |
| 生成过程中换了会话，回答好像没了 | 流式内容按会话记在前端。换回来还能看到已生成的部分。侧栏「·」表示这个会话还在生成。 |
| 普通闲聊首字很慢 | 看上面的路由。常见原因是进程没重启，或路由模型超时后在重试。Tier 1 问候不会调路由模型。 |
| 沙箱读不到刚上传的文件 | MinIO 要可访问。文件是临时下载后再挂进容器的，结束就删。 |
