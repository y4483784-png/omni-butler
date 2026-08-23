# Omni-Butler

基于 LLM + Agent 的统一办公助手（PRD v1.0）的可运行骨架。

## 已确认的技术决策

| 项       | 决策                             |
| ------- | ------------------------------ |
| LLM 供应商 | 智谱 GLM（openai SDK + 可配置 base_url，ADR-005 修订） |
| 代码沙箱    | 自托管 Docker（数据不出域，ADR-004）      |
| 多用户隔离  | 登录会话 Cookie + 按 user_id 隔离（会话/知识库/记忆/日程）；Postgres RLS 加固 |
| 实时通道    | SSE 流式（PRD 指定）                 |

## 目录结构

```
omni-butler/
├── docker-compose.yml     # postgres / qdrant / redis / minio / sandbox-runner / api / worker / beat / nginx
├── docker-compose.override.yml  # 自动加载：api2 / worker2 + nginx least_conn
├── deploy/
│   ├── nginx.conf         # SPA + /api SSE 反代（默认仅 80）
│   ├── nginx-scale.conf   # 两个 api 的 upstream
│   ├── nginx-tls.conf.example
│   ├── certs/             # 启用 TLS 时放证书，勿提交私钥
│   ├── health-watch.sh    # 磁盘 / 容器 / 沙箱镜像 / ready 巡检（cron）
│   ├── backup.sh          # pg_dump + Qdrant snapshot + MinIO 对象（cron）
│   └── crontab.example    # 巡检 + 夜间备份的 crontab 样例
├── .env.example           # 配置模板
├── backend/               # FastAPI + SQLAlchemy（LLM 走 openai SDK + 可配置 base_url）
│   ├── Dockerfile
│   ├── entrypoint.sh      # MODE=api|worker|beat|sandbox
│   ├── app/{api,core,models,agents,rag,services,sandbox,tasks}
│   └── requirements.txt
├── frontend/              # React + Vite + TS；生产打进 nginx 镜像
│   └── Dockerfile
└── sandbox/               # 受限 Python 执行容器（omni-sandbox）
```

## 日常启动与关闭（Compose 已装好之后）

栈已经在虚拟机里跑过一次后，**每天只做下面两件事**。不要每天 `--build`、不要每天 `alembic`、不要再开 Windows 的 `npm run dev`。

浏览器入口：`http://<虚拟机IP>/`（IP 用 `ip -4 addr show ens33`，不要用 `172.17` 那种 Docker 桥）。探活：`http://<虚拟机IP>/health`。依赖探活（Postgres/Redis）：`http://<虚拟机IP>/health/ready`（不要把它配成 api 容器的 Docker healthcheck）。

在虚拟机执行（项目目录以你实际为准，现在是 `/mnt/hgfs/omni-butler`）：

### 启动

```bash
cd /mnt/hgfs/omni-butler
ls /mnt/hgfs/omni-butler || mount -a          # 仅当共享盘没挂上
nmcli networking on                           # 仅当 Windows ping 不通
docker compose start                          # 启动已有容器，不重建镜像
docker compose ps                             # 含 override 时应 11 个 Up：… / api / api2 / worker / worker2 / nginx
curl -s http://127.0.0.1/health
curl -s http://127.0.0.1/health/ready
```

虚拟机若是正常关机且容器设了 `restart: unless-stopped`，开机后栈可能已经在跑，这时 `docker compose ps` 全绿即可，不必再 start。

改过代码、要重新打镜像时才用：`export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0` 然后 `docker compose up -d --build`。改过数据库模型时才再跑：`docker compose exec api alembic upgrade head`（api 未在跑时：`docker compose run --rm --name omni-butler-migrate api alembic upgrade head`）。

### 关闭

下班或暂停服务（**数据卷保留**，明天 `start` 就能起来）：

```bash
cd /mnt/hgfs/omni-butler
docker compose stop
```

不要用 `docker compose down -v`，那会删掉 Postgres / MinIO / Qdrant 的数据。

只拆容器、保留数据：`docker compose down`（无 `-v`）。第二天要用 `docker compose up -d` 再创建容器，而不是 `start`。

### 巡检与备份（建议装一次 cron）

Compose 已给所有服务加了日志轮转（默认约 30MB/容器，api/worker 约 100MB）。容器崩了会 `restart: unless-stopped`，**不会喊人**。在虚拟机装最低限度巡检和夜间备份：

```bash
chmod +x /mnt/hgfs/omni-butler/deploy/health-watch.sh /mnt/hgfs/omni-butler/deploy/backup.sh
mkdir -p /var/omni-backups
crontab -e
# 样例见 deploy/crontab.example，加入：
# */5 * * * * COMPOSE_DIR=/mnt/hgfs/omni-butler /mnt/hgfs/omni-butler/deploy/health-watch.sh
# 15 2 * * * COMPOSE_DIR=/mnt/hgfs/omni-butler BACKUP_DIR=/var/omni-backups /mnt/hgfs/omni-butler/deploy/backup.sh
```

巡检：根分区磁盘 ≥80%、长期运行的 compose 服务是否 running/unhealthy（跳过一次性的 `sandbox` 镜像构建容器）、宿主机是否有 `omni-sandbox:latest`、`/health/ready` 是否 200。异常写 syslog（`omni-butler-health`），并打到 stderr（cron 若配了 `MAILTO` 会寄信）。

备份脚本对齐常见做法：Postgres 用容器内 `pg_dump -Fc`（cron 里用 `docker exec`，不要 `compose exec` 以免抢 TTY）；Qdrant 走官方 [Snapshots API](https://qdrant.tech/documentation/concepts/snapshots/)（`POST /snapshots` 后下载再删除，经 api 容器访问内网 `qdrant:6333`，宿主机不映射 6333）；MinIO 用与线上相同的 boto3 把 `S3_BUCKET` 打成 tar（等价 `mc mirror`，官方 server 镜像不含 `mc`）。默认保留 7 天（`BACKUP_KEEP_DAYS`）。

手动确认沙箱镜像（巡检也会查）：

```bash
docker compose build sandbox
docker image inspect omni-sandbox:latest
```

### TLS 触发线

默认只开 **80、明文 HTTP**。内网、防火墙只给可信网段时可以接受。

**一旦 80 对公网、访客 Wi‑Fi 或不可信办公网开放，必须先上 TLS，再对外提供服务。** 浏览器到 nginx 的会话令牌、聊天、上传文件都会明文经过 80；`LLM_API_KEY` 仍留在 api 容器、出站智谱走 HTTPS，但用户数据已经裸奔。

启用方式（不强制公网 Let's Encrypt；内网 CA / mkcert / 已有证书均可）：

```bash
cp deploy/nginx-tls.conf.example deploy/nginx-tls.conf
# 把 fullchain.pem、privkey.pem 放到 deploy/certs/
# 按 docker-compose.yml 里 nginx 服务的注释打开 443 端口和证书挂载
docker compose up -d --build nginx
```

### 生产口令与隔离

Compose 运行时用非超管 `omni_app` 连库（`omni` 只做迁移），这样 Postgres RLS 才会对业务连接生效。侧栏可「改密」；管理员可看「审计」。日志与响应带 `X-Request-ID`。默认 `SECRET_KEY` / MinIO `minioadmin` 会在启动时告警；生产设 `ENFORCE_SECURE_SECRETS=true` 则拒绝启动。

**轮换口令（虚机上手做，本仓库不会改你正在跑的 `backend/.env`）**

| 项 | 注意 |
| --- | --- |
| `SECRET_KEY` | 改 `backend/.env` 后重建 api/worker/beat。已登录 Cookie 会全部失效，需重新登录。 |
| Postgres `omni` / `omni_app` | 数据卷若已初始化，改 compose 的 `POSTGRES_PASSWORD` **不会**自动改库内密码。先 `docker exec omni-butler-postgres psql -U omni -c "ALTER USER ..."`，再把同级 `.env` 的 `POSTGRES_PASSWORD` / `POSTGRES_APP_PASSWORD` 改成新值并 `up -d` api/worker。 |
| MinIO | 已有 `minio_data` 卷时，改 `MINIO_ROOT_*` 往往不生效。需要按 MinIO 文档改 root 凭据，并同步 `backend/.env` 的 `S3_ACCESS_KEY` / `S3_SECRET_KEY`。 |
| Redis | 默认无 `requirepass`。要加口令必须同时改 `REDIS_URL`（`redis://:password@redis:6379/0`）并重建 api/worker/beat，否则入库队列会全部失败；内网单机可后置。 |
| 强制检查 | 口令改完后再设 `ENFORCE_SECURE_SECRETS=true`。 |

改库口令时同时改 `POSTGRES_PASSWORD` / `POSTGRES_APP_PASSWORD`（写在 `docker-compose.yml` 同级 `.env`）。

本轮升级后虚拟机需要跑一次迁移并重建前端镜像（改密 / 审计 UI）：

```bash
cd /mnt/hgfs/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose exec api alembic upgrade head
docker compose build sandbox
docker compose up -d --no-build
```

若 api 报 `role omni_app does not exist`，先用属主连接建角色再起服务：

```bash
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
docker compose up -d
```

### 扩容（仍在同一台虚拟机，按需）

聊天画图走 `sandbox-runner`，api **不**挂 Docker 套接字。

`docker-compose.override.yml` 会**自动**加上第二聊天进程 `api2`、第二入库进程 `worker2`，nginx 走 `deploy/nginx-scale.conf`（`least_conn`）。`docker compose up` / `stop` / `ps` 都管全套，不必再写 `-f`。

容器名一律是 `omni-butler-<服务名>`，不再带 Compose 默认的 `-1` 副本后缀：`omni-butler-api`、`omni-butler-api2`、`omni-butler-worker`、`omni-butler-worker2`，以及 postgres / qdrant / redis / minio / sandbox-runner / beat / nginx。

不要用 `--scale worker=2`：那会造出同服务副本 `omni-butler-worker-2`，和 `worker2` 不是一回事；现在 worker 有固定 `container_name`，`--scale` 会直接报错。

从旧名字（`api-1`、`api2-1`、`worker-2`）切过来（**不要**加 `-v`，数据卷保留）：

```bash
cd /mnt/hgfs/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose down --remove-orphans
docker rm -f omni-butler-worker-2 2>/dev/null || true
docker compose up -d --build
docker compose ps
```

只要基础 9 服务（内存不够时）：`docker compose -f docker-compose.yml up -d`（显式 `-f` 时**不会**加载 override）。同时说话仍受智谱槽位限制（约 50–100 路）。

---

## 架构与部署

单台 Linux 用 **Docker Compose** 起全栈。浏览器只访问 `http://<VM>/`（nginx 托管前端静态，并把 `/api`、`/health` 反代到 api）。

一份后端镜像、四种角色：`api`（聊天/上传）、`worker`（Celery 入库）、`beat`（定时回收卡住的 pending/processing）、`sandbox-runner`（只负责起沙箱容器）。中间件只走内部网络，**不把 5432/6379/6333/9000 打到局域网**。

**默认不做**：公网 HTTPS（见上方 TLS 触发线）、K8s、Kafka、Flower、把沙箱改成队列。开发仍可用方案 B（Windows `npm run dev` + 虚拟机 venv）。

### 首次拉起（只需一次）

虚拟机需要 **Compose V2 插件**（子命令是 `docker compose`，中间是空格）。没有插件时，`docker` 会把 `-d` 当成自己的参数，报 `unknown shorthand flag: 'd'`。先检查：

```bash
docker compose version    # 期望 Docker Compose version v2.x
```

若失败，在虚拟机安装插件（x86_64）：

```bash
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

`uname -m` 若是 `aarch64`，把文件名里的 `linux-x86_64` 改成 `linux-aarch64`。也可用发行版包：`dnf install docker-compose-plugin`（需已配置 Docker CE 源）。

**不要**用旧的 `docker-compose`（带连字符）硬套本仓库的 `docker-compose.yml`（Compose V2 语法）。

先停掉旧的 `docker run` 容器（`omni-postgres` / `omni-redis` / `omni-minio` / `qdrant`），避免两套库并存。Compose 卷名与旧 `docker run` 不同，默认是新库。

```bash
cd /mnt/hgfs/omni-butler
# backend/.env 填 LLM key；Compose 会把 DB/Redis/Qdrant/MinIO 主机名改成服务名
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build sandbox
docker compose up -d postgres qdrant redis minio
# HGFS 上 BuildKit 单独 COPY 文件会报 checksum / not found，先关掉
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
# 沙箱由 sandbox-runner 挂宿主机 docker.sock（不要从 download.docker.com 下 CLI）
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
# 会新建 beat 容器（与 worker 拆开）；已有栈用 up -d --build 即可，不必 down
docker compose up -d --build
curl http://127.0.0.1/health
curl http://127.0.0.1/health/ready
# 浏览器：http://<虚拟机IP>/
```

若 `COPY ... not found` 仍出现，共享盘对 Docker 守护进程不可靠，先拷到本地盘再构建：

```bash
rsync -a --delete \
  --exclude .venv --exclude node_modules --exclude .git --exclude '*.db' \
  /mnt/hgfs/omni-butler/ /var/tmp/omni-butler/
cd /var/tmp/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
```

迁移**不要**塞进每个 api 启动。api 已在跑时用 `docker compose exec api alembic upgrade head`；未在跑时用上面的 `compose run --name omni-butler-migrate`（避免和固定容器名 `omni-butler-api` 冲突）。日常开关见文首「日常启动与关闭」。

### 备份

| 数据 | 位置 | 建议 |
|------|------|------|
| Postgres | `pgdata` 卷 | 要备份。用 [`deploy/backup.sh`](deploy/backup.sh)（容器内 `pg_dump -Fc`） |
| Qdrant | `qdata` 卷 | 要备份。同一脚本走 REST snapshot（不映射 6333） |
| MinIO | `minio_data` 卷 | 要备份。同一脚本按 bucket 拉对象打 tar（等价 `mc mirror`） |
| Redis | `redisdata` 卷 | 可再生，不必备份；AOF 只减少在途任务丢失 |

产出目录默认 `/var/omni-backups/<UTC时间戳>/`，含 `postgres.dump`、`qdrant.snapshot`、`minio.tgz`、`MANIFEST.txt`。保留天数 `BACKUP_KEEP_DAYS`（默认 7）。crontab 样例：[`deploy/crontab.example`](deploy/crontab.example)。

`.env` 不入库。生产请按上文「生产口令与隔离」改掉默认 Postgres/MinIO/`SECRET_KEY`，不要把默认口令带进生产。

---

## 首次安装（一次性）

### 后端依赖

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate
pip install -r requirements.txt
copy ..\.env.example .env          # Linux/macOS: cp ../.env.example .env
# 编辑 backend/.env：填入 LLM_API_KEY（或 OPENAI_API_KEY）+ LLM_BASE_URL（智谱见 .env.example）
```

> 未配置 API Key 时聊天走 mock 流式回复，骨架仍可端到端跑通。

### 前端依赖

```bash
cd frontend
npm install
```

### ⚠️ 依赖注意（已踩坑）

`requirements.txt` 已将 `openai` 锁定为 `>=2.0,<3`，**请勿降级到 1.x**。原因：`openai 1.x` 与 `httpx 0.28+` 不兼容，会抛出 `TypeError: AsyncClient.__init__() got an unexpected keyword argument 'proxies'`（前端一直「生成中」），并可能在客户端析构时报 `AttributeError: 'AsyncHttpxClientWrapper' object has no attribute '_state'`。升级到 `openai 2.x` 即可。若手动改过依赖后报上述错误，执行 `pip install -r requirements.txt` 把 openai 升回 2.x。

> `AsyncOpenAI` 按 **运行中的事件循环** 绑定并复用 httpx 连接池（见 `llm.py` 中 `WeakKeyDictionary`），避免每次请求重复 TCP/TLS；不要在模块级创建跨请求共享的单例 client（旧版会挂死事件循环）。

---

## 端口约定

| 服务 | 端口 | 说明 |
|------|------|------|
| nginx（生产入口） | `80` | 静态前端 + `/api` + `/health`；默认明文。对不可信网络暴露时改 443，见「TLS 触发线」 |
| 前端 Vite（开发） | `5173` | 方案 B：Windows 浏览器入口 |
| 后端 FastAPI（开发） | `8001` | 方案 B：与 `frontend/vite.config.ts` 中 `/api` 代理一致 |
| Qdrant | `6333` | 仅开发/旧 docker run 映射；Compose 生产不对外 |

生产推荐拓扑：

| 组件 | 跑在哪 | 示例地址 |
|------|--------|----------|
| 全栈 Compose | Linux 虚拟机 | `http://192.168.88.129/`（以 `ip -4 addr show ens33` 为准） |
| 代码（共享盘，用于构建镜像） | Windows ↔ `/mnt/hgfs/omni-butler` | VMware 共享名 `omni-butler` |

开发备选（方案 B）：虚拟机 venv 跑 uvicorn + Celery，Windows `npm run dev` 代理到 `:8001`。

> **不推荐**：后端在 Windows、Docker 只在虚拟机。当前实现会把 Windows 路径传给 `docker -v`，远程 Linux 看不到该路径，沙箱会失败。

---

## 方案 B 前置：VMware 共享文件夹（代码进虚拟机）

虚拟机里若 `find` 不到 `omni-butler`，多半是共享未挂载（`/mnt/hgfs` 为空），不是代码被删。

### 1. 宿主机（VMware）

1. 虚拟机 → **设置** → **选项** → **共享文件夹** → 启用  
2. 添加文件夹，指向宿主机项目根（例如 `d:\Omni-Butler\omni-butler` 或上一级，以你实际共享名为准）  
3. 共享名示例：`omni-butler`

### 2. 虚拟机：手动挂载并确认

```bash
# 应能列出共享名，例如 omni-butler
vmware-hgfsclient

mkdir -p /mnt/hgfs
mount -t fuse.vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
ls /mnt/hgfs
# 期望：omni-butler

ls /mnt/hgfs/omni-butler
# 期望：backend  frontend  sandbox  docker-compose.yml  README.md
```

> 文档里的 `/path/to/omni-butler` 只是占位符。本环境真实路径是 **`/mnt/hgfs/omni-butler`**。

### 3. 开机自动挂载（`/etc/fstab`）

部分 fuse 版本**不接受** `nofail`（会报 `fuse: unknown option(s): '-o nofail'`）。使用：

```fstab
.host:/  /mnt/hgfs  fuse.vmhgfs-fuse  allow_other,defaults,_netdev  0  0
```

写入后验证（须先离开挂载点再卸载）：

```bash
systemctl daemon-reload
cd /
umount /mnt/hgfs
mount -a
ls /mnt/hgfs/omni-butler
```

若 `umount` 报 busy：先 `cd /`，关掉占用该目录的 shell，再卸载。

---

## 开发备选（方案 B：venv + Windows 前端）

仅在**不用 Compose 全栈**时使用。日常开关请看文首「日常启动与关闭」。

项目路径：`/mnt/hgfs/omni-butler`。中间件可用 **`docker run` / `docker start`**。用户原文件只存 MinIO。

### 方案 B 每天复制

```bash
# —— 虚拟机 ——
nmcli networking on                         # 仅当 Windows ping 不通时
ls /mnt/hgfs/omni-butler || mount -a

docker start qdrant                         # 首次见下方「首次准备」
docker start omni-minio                     # 首次见下方「首次准备」
docker start omni-postgres                  # 首次见下方「首次准备」
docker start omni-redis                     # 缓存 / 集中限流
curl -s http://127.0.0.1:6333/ | head
curl -I http://127.0.0.1:9000/minio/health/live
docker exec omni-postgres pg_isready -U omni
docker exec omni-redis redis-cli ping

cd /mnt/hgfs/omni-butler/backend
source /root/omni-venv/bin/activate
# 新增依赖后（如 MinIO / Celery）：pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
# 文档入库 worker（与 API、Beat 分进程；API 重启不会丢掉已入队任务）
celery -A app.tasks.celery_app:celery_app worker -Q ingest,maintenance -l info
# 另开终端：定时回收卡住的入库（Compose 生产已拆成独立 beat 容器，不要再给 worker 加 -B）
celery -A app.tasks.celery_app:celery_app beat -l info

# —— Windows（另开终端）——
cd <本机-omni-butler>\frontend
npm run dev
# 浏览器 http://localhost:5173
```

健康检查（Windows，IP 换成你的虚拟机 `ens33`）：

```powershell
ping 192.168.88.129
curl http://192.168.88.129:8001/health
curl http://192.168.88.129:6333/
```

结束：前后端 `Ctrl+C`；需要时 `docker stop omni-minio qdrant omni-postgres omni-redis`。Celery worker 也要 `Ctrl+C`。

---

### 方案 B 首次准备（只需做一次）

#### 1. 网络与共享盘

主网卡一般为 `ens33`，不要用 Docker 桥（`172.17` / `172.18`）当虚拟机 IP。

```bash
nmcli networking on
ip -4 addr show ens33
ls /mnt/hgfs/omni-butler || mount -a
docker version
```

#### 2. 沙箱镜像

```bash
cd /mnt/hgfs/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build sandbox
docker run --rm -i --network=none --read-only --tmpfs /tmp omni-sandbox \
  python -c "import pandas,matplotlib; print('ok')"
```

> 无 BuildKit 时不要加 `DOCKER_BUILDKIT=1`。

#### 3. 首次创建 Qdrant / MinIO / Postgres / Redis

```bash
# Qdrant
docker run -d --name qdrant --restart unless-stopped \
  -p 6333:6333 -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant:latest

# Postgres（Phase 0 起为业务主库；SQLite 仅保留给本地快速起步与测试）
docker volume create omni_pgdata
docker run -d --name omni-postgres --restart unless-stopped \
  -p 5432:5432 \
  -e POSTGRES_USER=omni -e POSTGRES_PASSWORD=omni -e POSTGRES_DB=omni_butler \
  -v omni_pgdata:/var/lib/postgresql/data \
  postgres:16

# Redis（Phase 1：精确缓存、模型并发控制、每用户聊天限流；AOF 为后续队列做准备）
docker volume create omni_redisdata
docker run -d --name omni-redis --restart unless-stopped \
  -p 6379:6379 \
  -v omni_redisdata:/data \
  redis:7 redis-server --appendonly yes

# 建表（二选一）：启动后端自动 create_all；或用 Alembic（推荐，后续改表都走它）
cd /mnt/hgfs/omni-butler/backend
pip install -r requirements.txt        # 含 psycopg / alembic
alembic upgrade head                   # 新库直接建到最新
# 若库已由后端 create_all 建好，只需登记版本：alembic stamp head

# MinIO（上传文件唯一持久化位置）
docker volume create omni_minio_data
docker run -d --name omni-minio --restart unless-stopped \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -v omni_minio_data:/data \
  minio/minio:latest server /data --console-address ":9001"
```

可选放行端口（方案 B 开发）：

```bash
firewall-cmd --permanent --add-port=6333/tcp
firewall-cmd --permanent --add-port=9000/tcp
firewall-cmd --permanent --add-port=9001/tcp
firewall-cmd --permanent --add-port=8001/tcp
firewall-cmd --reload
```

Compose 生产只放行 80（TLS 启用后再放行 443）：

```bash
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --reload
```

#### 4. `backend/.env`（与中间件同机用回环）

```env
QDRANT_URL=http://127.0.0.1:6333
REDIS_URL=redis://127.0.0.1:6379/0
S3_ENDPOINT=http://127.0.0.1:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=omni-uploads
SANDBOX_ENABLED=true
SANDBOX_IMAGE=omni-sandbox
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

MinIO 控制台：`http://<虚拟机IP>:9001`（账号/密码均为 `minioadmin`）。

---

### 备选：本机 Docker Desktop（全在 Windows）

仅当你不用虚拟机、本机已装 Compose 插件时：

```bash
cd <本机-omni-butler根目录>
docker compose up -d minio qdrant
docker compose build sandbox

cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8001

cd ..\frontend
npm run dev
```

---

### 数据分析使用检查

1. 前端 → 知识库上传 `.csv` / `.xlsx`，等到状态 **ready**  
2. 勾选该文档  
3. 提问做汇总 / 画图；期望意图为「数据分析」，右侧 Artifact 出图，消息可点「查看图表」重开  
4. 若提示沙箱镜像不可用：在 **宿主机项目目录** 执行 `docker compose build sandbox`，确认 `docker image inspect omni-sandbox:latest` 成功  

**表格入库**：csv/xlsx 按全表文本分块（默认最多 `TABULAR_MAX_ROWS=5000` 行 / `TABULAR_MAX_CHARS=800000` 字符）。查单元格走 RAG；汇总/画图走沙箱读原文件。旧文档需点「重新解析」。相关配置见 `.env.example`：`SANDBOX_*`、`TABULAR_*`。

### Qdrant 就绪验证

1. `curl` 能访问 `QDRANT_URL`  
2. 重启后端  
3. 知识库对目标文档点「重新解析」  
4. warning 中不再提示 Qdrant 不可达  

### 排障速查

| 现象 | 处理 |
|------|------|
| Windows ping 不通虚拟机；`ens33` 无 IPv4 或全是「未托管」 | `nmcli networking on`；再看 `ip -4 addr show ens33` |
| 登录横幅只有 `172.17/172.18` | 那是 Docker 桥，改用 `ens33` 的 `192.168.x.x` |
| `/mnt/hgfs` 为空、找不到项目 | `vmware-hgfsclient` → `mount -a` 或手动 `mount -t fuse.vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other` |
| `fstab` 报 `unknown option nofail` | 去掉 `nofail`，用 `allow_other,defaults,_netdev` |
| `systemctl enable network` 失败 | openEuler 无 `network.service`，只用 NetworkManager |
| 沙箱读不到上传文件 | 确认 MinIO 可访问；对象会临时下载后挂载，执行结束自动删除临时文件 |
| 生成中切换会话后回答消失 | 已修复：流式状态按 session 缓存在前端，切回即可看到 partial/完整内容；侧栏「·」表示该会话仍在生成 |
| 普通闲聊首字很慢（TTFB > 1.5s） | 见下方 **工具路由与 TTFB**；常见原因：未重启 uvicorn、路由 LLM 超时重试 |

### 工具路由与 TTFB

**快路径已下线**：所有消息统一走 Agent workflow，由 `app/agents/router.py` 做严格路由：

- **Tier 0（确定信号）**：用户勾选文档 / `use_kb=true` → 直接 KB，不调 LLM
- **Tier 1（极简确定性词）**：精确匹配的问候语（「你好」「在吗」等）→ chat；明确文档指代（「知识库」「文档里」等，且库中有文档）→ KB
- **Tier 2（LLM 判定）**：schema 约束 JSON 输出（`ROUTER_MODEL`，重试 `ROUTER_MAX_ATTEMPTS` 次）；**失败直接以 SSE `error` 上报「工具规划失败」，不做正则兜底、不回退闲聊**

| 配置 | 说明 |
|------|------|
| `ROUTER_MODEL` | 路由判定模型（空则 `PLANNER_MODEL` → `LLM_MODEL`；建议高并发轻量模型） |
| `ROUTER_MAX_ATTEMPTS` | schema 校验失败的重试上限（默认 2） |
| `CHAT_MODEL` | 闲聊回答模型（空则与 `LLM_MODEL` 相同） |
| `LLM_MODEL` | 工具路径 / 复杂回答 |
| `PLANNER_MODEL` | 规划 JSON 用模型（空则 `LLM_MODEL`） |

**自测（虚拟机到智谱）**：

```bash
# 替换 SESSION_ID；需已存在会话
curl -N -w "\nTTFB=%{time_starttransfer}s\n" \
  -H "Content-Type: application/json" \
  -d '{"session_id":1,"message":"你好"}' \
  http://127.0.0.1:8001/api/chat
```

期望 SSE 顺序：`ack`（`path: agent`）→ `status`（`planning`）→ `intent`（`chat`）→ `token`… → `ttft` → `done`。问候语命中 Tier 1 不调路由 LLM，TTFB 仍然很快。

改代码后 **必须重启 uvicorn**；HGFS 同步可 `grep RouterError /mnt/hgfs/omni-butler/backend/app/agents/router.py` 确认。

## 数据库（Postgres）

业务主库为 **Postgres**（`DATABASE_URL=postgresql+psycopg://omni:omni@localhost:5432/omni_butler`），多进程 API / 后续队列 worker 共用连接池（`DB_POOL_SIZE` / `DB_MAX_OVERFLOW`）。SQLite 仅保留两个场景：无 Docker 的本地快速起步、pytest（`tests/conftest.py` 强制 `sqlite:///./omni_butler.db`，与 `.env` 无关）。

Schema 用 **Alembic** 管理（`backend/alembic/`，基线 `0001_baseline`）：

```bash
cd backend
alembic upgrade head        # 新库建表 / 升级到最新
alembic stamp head          # 已由 create_all 建好的库：只登记版本
alembic check               # 校验 models 与迁移无漂移
alembic revision --autogenerate -m "add xxx"   # 之后每次改 models
```

## Redis（缓存与集中限流）

Redis 是可丢失的加速/协调层，不是业务数据源；不可达时系统会告警并**降级直通**，聊天、RAG 不因此中断。

| 用途 | key 依据 | 默认 TTL / 限制 |
|------|----------|-----------------|
| Tier 2 路由决策精确缓存 | 消息 + 历史 + KB/表格可用性 + 路由模型/版本 | 3 天 |
| query embedding 精确缓存 | 归一化问题 + embedding 模型 + provider 地址 | 7 天 |
| 联网搜索结果缓存 | query + 引擎 + 数量 + 时效/内容参数 | 5 分钟 |
| 模型分布式并发槽 | 模型名 | `.env` 的供应商并发上限；任务结束释放，180 秒租约兜底 |
| `/api/chat` 每用户滑动窗口 | user id | 默认 30 次/60 秒，超限返回 HTTP 429 + `Retry-After` |

只缓存精确相同的中间结果，不做高风险的「相似问题直接复用最终答案」。主要配置见 `.env.example`：`ROUTER_CACHE_TTL`、`EMBEDDING_CACHE_TTL`、`WEB_CACHE_TTL`、`LLM_MODEL_CONCURRENCY_LIMITS`、`CHAT_RATE_LIMIT`。修改路由提示词/结构后应递增 `app/agents/router.py` 的 `_ROUTER_CACHE_VERSION` 使旧决策自然失效。

## Celery（文档入库队列）

上传 / 重新解析不再在 API 进程里用 `BackgroundTasks` 跑解析。API 只负责把文件写入 MinIO、在 Postgres 记一条 `pending` 文档，然后把 `doc_id` 投递到 Redis 上的 **`ingest` 队列**；独立 worker 进程认领任务、解析、分块、向量化。

这样做的原因：大 PDF + OCR 可能跑几分钟。任务放在 Redis 里，**重启 uvicorn 不会丢任务**；worker 崩溃后同一 `task_id` 会重新投递并幂等跳过或续跑。

| 角色 | 命令 | 队列 |
|------|------|------|
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8001` | 只投递 |
| Worker | `celery -A app.tasks.celery_app:celery_app worker -Q ingest,maintenance -l info` | `ingest` 入库；`maintenance` 超时回收 |
| Beat（恰好一个） | `celery -A app.tasks.celery_app:celery_app beat -l info` | 每 5 分钟扫描卡住的 pending/processing |

配置：`CELERY_BROKER_URL` 空则复用 `REDIS_URL`；`acks_late=True`、`prefetch=1`、入库硬超时默认 1800 秒。投递失败会把文档标为 `failed`（前端可点重新解析）。pytest 设 `CELERY_TASK_ALWAYS_EAGER=true`，不连真实 broker。

Compose 生产里 `worker` 与 `beat` 已拆成两个容器；第二入库进程用 `worker2`，不要 `--scale worker=N`。**不要** 给 worker 再加 `-B`，否则定时任务会重复投递。方案 B（venv）同样分两个进程，不要用 `worker -B`。

开发（venv）升级 schema：

```bash
cd backend
alembic upgrade head    # 含 documents.ingest_task_id / ingest_started_at
```

Compose：栈已在跑用 `docker compose exec api alembic upgrade head`；否则 `docker compose run --rm --name omni-butler-migrate api alembic upgrade head`

## 用户文件存储

用户上传原文件 **只存 MinIO**，不在项目或宿主机目录持久化。数据库 `documents.stored_path` 只存 object key（如 `u1/{uuid}_差旅.pdf`）。

| 配置 | 说明 |
|------|------|
| `S3_ENDPOINT` | 默认 `http://127.0.0.1:9000`（与 MinIO 同机） |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 默认 `minioadmin` |
| `S3_BUCKET` | 默认 `omni-uploads` |
| 启动 | Compose：`docker compose up -d`；开发可用 `docker start omni-minio` |

旧本地上传迁移到 MinIO：

```bash
cd /mnt/hgfs/omni-butler/backend
source /root/omni-venv/bin/activate
python scripts/migrate_uploads_out.py --dry-run
python scripts/migrate_uploads_out.py --delete-old
```

解析 / 沙箱经 `materialize` 下载到系统临时文件，结束后立即删除。

## 路线图（对应架构方案）

- Phase 1 对话内核（已完成：SSE、会话 CRUD、自动命名/重命名、Markdown、Artifacts）
- Phase 2 RAG（解析增强 + OCR + 结构分块；**关键词 + Qdrant embedding 混合检索**；**retrieval-resume：邻接块扩展 + 智谱 Rerank**；入库阶段进度条）
- Phase 3 Agentic 工具（**LangGraph Scheme B** `plan → retrieve → reflect → answer` + **AutoHarness Standard 同构 gateway**：Tool Registry / constitution / verify / audit；kb / web / calendar / sandbox 均经唯一出口；数据分析为 Docker 沙箱 MVP）
- Phase 4 长期记忆（**MVP 已落地**：`MemoryItem` + extract→upsert→system 注入；配置 `MEMORY_ENABLED` / `MEMORY_USE_LLM` / `MEMORY_MAX_CHARS`；对标 LangGraph Store 的 user 命名空间 + key）
- Phase 5 生产化（**多用户登录与数据隔离已落地**；成本归因细化等仍待办）

**多用户隔离（Phase 5）**

- 登录：`POST /api/auth/login`（HttpOnly Cookie）；管理员 `ADMIN_USERNAME` / `ADMIN_PASSWORD`（见 `.env.example`）
- 业务 API 均需登录；会话/知识库/记忆/日程按 `user_id` 隔离；禁止客户端传 `user_id`
- Postgres 生产库：`alembic upgrade head` 含 RLS 策略（SQLite 测试跳过）
- 管理员可在 UI 侧栏「用户」创建同事账号

**仍待办 / 延后**：日程独立面板；LDAP/OIDC；部门共享知识库；不整仓换 OpenHands / deepagents / LightRAG 图谱。

### 办公路由评测（标准指标）

指标与跑法见 [`backend/app/eval/README.md`](backend/app/eval/README.md)。金标准数据集：`backend/data/eval/office_tool_routing.jsonl`（**500** 条口语化用例；可用 `python scripts/gen_office_eval_500.py` 重建）。

```bash
cd backend
python scripts/run_office_eval.py            # 真实 LLM 路由（结果缓存 router_cache.json；--refresh-cache 重打）
# Intent Accuracy / Macro-F1 · Tool Exact-Match · Micro/Macro P/R/F1 · Hamming · Recall@k
pytest tests/test_harness_office_eval.py tests/test_eval_metrics.py -q   # 离线（mock 路由）
# RUN_ROUTER_EVAL=1 时上述 pytest 额外跑 500 条真实 LLM 打分门槛
```

### RAG 检索评测

金标准：`backend/data/eval/rag_retrieval.jsonl`（**500** 条；可用 `python scripts/gen_rag_eval_500.py` 重建）。指标：Precision@k / Recall@k / MRR，见 [`backend/app/eval/README.md`](backend/app/eval/README.md) §1b。

```bash
cd backend
python scripts/run_rag_eval.py
# 可选：--zhipu-rerank（需 API Key）
pytest tests/test_rag_expand_rerank.py tests/test_rag_retrieval_eval.py -q
```

配置见 `.env.example`：`RAG_CANDIDATE_K` / `RAG_EXPAND_WINDOW` / `RAG_RERANK_PROVIDER=zhipu` / `RERANK_MODEL=rerank`。

### YZ 全链路评测（真实入库 + ragas）

基于 `backend/tests/YZ测试文档/` 三份样例文档，跑通 upload → MinIO → ingest → retrieve → answer。金标准：`backend/data/eval/yz_fullchain.jsonl`（`python scripts/gen_yz_eval_400.py` 重建）。**先出报告、后统一修产品** — 详见 [`backend/app/eval/README.md`](backend/app/eval/README.md) §7。

```bash
cd backend
pip install -r requirements.txt   # 含 ragas
python scripts/run_yz_eval.py              # 默认含 ragas（需 MinIO + LLM key）
python scripts/run_yz_eval.py --skip-ragas  # 仅检索 + Fact Containment 等规则指标
pytest tests/test_yz_fullchain_eval.py -q
```

### 依据核验忠实度评测（grounding + ragas）

固定题集测生产路径「成稿 → 核验 → 至多一次重写」。金标准：`backend/data/eval/grounding_faithfulness.jsonl`（`python scripts/gen_grounding_eval.py` 重建，kb/web/sandbox 各 16 条）。详见 [`backend/app/eval/README.md`](backend/app/eval/README.md) §8。

```bash
cd backend
python scripts/run_grounding_eval.py
python scripts/run_grounding_eval.py --no-repair --skip-ragas --limit 8
pytest tests/test_grounding_eval.py -q
```

办公回归（契约）：`test_harness_office_eval.py`（gateway / verify）+ `test_memory.py` + `test_harness_gateway.py`。
