# Omni-Butler

一台 Linux 上用 Docker Compose 跑起来的办公助手。浏览器只访问这台机器的 80 端口，数据库、文件和模型调用都留在本机。

下面这条是推荐路径。Windows 上另开前端、或虚拟机里用 Python 虚拟环境，写在 [docs/dev-setup.md](docs/dev-setup.md)。

## 开始之前

- 一台装了 Docker 的 Linux。命令是 `docker compose`（中间是空格），不是带连字符的 `docker-compose`。
- 项目目录。后面的命令都在**仓库根目录**执行（能看见 `docker-compose.yml` 的那一层）。
- 能用浏览器打开这台机器的 80 端口。

看 Compose 是否可用：

```bash
docker compose version
```

应显示 `Docker Compose version v2.x`。若提示找不到命令，或 `docker compose up -d` 报 `unknown shorthand flag: 'd'`，先装插件。x86_64：

```bash
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

`uname -m` 若是 `aarch64`，把文件名里的 `linux-x86_64` 换成 `linux-aarch64`。已经配好 Docker 源时，也可以 `dnf install docker-compose-plugin`。

虚拟机经 VMware 共享盘挂项目时，目录多半是 `/mnt/hgfs/omni-butler`。挂载、开机自动挂上、共享盘上构建镜像的限制，见 [docs/dev-setup.md](docs/dev-setup.md)。普通 Linux 把仓库放在本地盘即可，不必做那一节。

## 配置

Compose 读的是 **`backend/.env`**。仓库根的 `.env.example` 只是模板，复制过去再改：

```bash
cd /path/to/omni-butler
cp .env.example backend/.env
```

Windows 上复制用 `copy .env.example backend\.env`。这个文件不要提交。

仓库根如果另有一个 `.env`，Compose 只用它来替换 `docker-compose.yml` 里的 `${变量}`（数据库口令）。它**不会**把模型密钥送进容器。第一次启动不必建这个根目录文件。

### 不填就进不了门

| 变量 | 写在哪 | 说明 |
| --- | --- | --- |
| `ADMIN_USERNAME` | `backend/.env` | 默认 `admin`。登录页的用户名。 |
| `ADMIN_PASSWORD` | `backend/.env` | 登录页的密码。留空时服务仍会起来，但管理员没有密码，登录会被拒绝。只在还没有密码时写入一次；已经登录过之后要改密码，用侧栏「改密」。再改这一项不会覆盖库里已有的密码。 |

### 想真的对话再填

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` | 智谱的密钥。留空时聊天是演示回复，页面仍能走通。旧名字 `OPENAI_API_KEY` 也认。 |
| `LLM_BASE_URL` | 模板里已是智谱地址 `https://open.bigmodel.cn/api/paas/v4/`。 |
| `LLM_MODEL` | 模板里是 `glm-4-air`。 |

### 保持模板原样即可

`DATABASE_URL`、`REDIS_URL`、`QDRANT_URL`、`S3_ENDPOINT` 在模板里指向本机端口。Compose 启动容器时会改成内部服务名（`postgres`、`redis`、`qdrant`、`minio`），不用手改。改成服务名之后，[方案 B](docs/dev-setup.md) 的本机进程会连不上。

`SECRET_KEY` 模板值是 `change-me-in-production`。第一次可以先不动。准备长期使用时，连同数据库和 MinIO 口令一起换，见 [docs/deployment.md](docs/deployment.md)。`ENFORCE_SECURE_SECRETS` 保持 `false`，直到那些口令都换完，否则进程会拒绝启动。

### Google 日历（可选）

四个都空着时，日程只写在本机数据库里，不影响登录和聊天。

| 变量 | 何时要填 |
| --- | --- |
| `GOOGLE_OAUTH_CLIENT_ID` | 要连 Google 日历时。Web 应用的 OAuth 客户端。同意屏幕保持测试模式，并把要用的 Google 账号加为测试用户。 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 同上。 |
| `GOOGLE_OAUTH_REDIRECT_URI` | 必须和 Google Cloud 控制台登记的回调一致，路径是 `/api/calendar/google/callback`。 |
| `CALENDAR_TOKEN_KEY` | 用来加密保存的日历令牌。不能用 `SECRET_KEY` 代替。 |

回调按**浏览器实际打开的地址**来写：

- 推荐路径（Compose，浏览器开 80 端口）：`http://<这台机器的局域网IP>/api/calendar/google/callback`
- 模板里的 `http://localhost:5173/api/calendar/google/callback` 只给 Windows 上 `npm run dev` 用。Compose 不要留这个值。

生成 `CALENDAR_TOKEN_KEY`：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

容器里没有这份命令时，在已安装依赖的开发机上生成，再贴进 `backend/.env`。配了 `GOOGLE_OAUTH_CLIENT_ID` 却把这项空着：没开强制检查时，点「连接 Google 日历」会失败；`ENFORCE_SECURE_SECRETS=true` 时，进程直接拒绝启动。

范围只用日历事件和读取日历时区。手测步骤见 [docs/plans/calendar-google-manual-acceptance.md](docs/plans/calendar-google-manual-acceptance.md)。

其余开关在 `.env.example` 里有注释，默认就能跑。含义见 [docs/architecture.md](docs/architecture.md)。

## 第一次启动

在仓库根目录执行。共享盘上构建镜像之前，先关掉 BuildKit（本地盘可以跳过这两行）：

```bash
cd /path/to/omni-butler
# 仅 VMware / HGFS 共享盘需要。本地磁盘不要设。
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
```

共享盘上若仍报 `COPY ... not found` 或 checksum 错误，先把仓库同步到本地目录再构建，步骤见 [docs/dev-setup.md](docs/dev-setup.md)。

然后构建三个镜像，再迁移数据库，最后拉起全套。不要用一次 `docker compose up -d --build` 把所有服务一起构建：BuildKit 关掉时，每个带构建步骤的服务会各装一遍依赖，容易把磁盘写满。

```bash
cd /path/to/omni-butler
docker compose build sandbox
docker compose build api
docker compose build nginx
docker compose up -d postgres qdrant redis minio
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
docker compose up -d --no-build
```

`api` 这一个镜像会被聊天进程、入库进程、定时任务和沙箱运行器共用。数据库迁移只做这一次，不要写进每次启动。

`docker-compose.override.yml` 会自动多开一份聊天进程（`api2`）和一份入库进程（`worker2`）。内存紧时不要加载它，改成：

```bash
docker compose -f docker-compose.yml up -d --no-build
```

写出 `-f docker-compose.yml` 时，Compose 不会再读 override。取舍见 [docs/deployment.md](docs/deployment.md)。

## 怎样算跑起来了

```bash
docker compose ps
curl -s http://127.0.0.1/health
curl -s http://127.0.0.1/health/ready
```

- 带 override 时，应有 **11** 个一直运行的容器：postgres、qdrant、redis、minio、sandbox-runner、api、api2、worker、worker2、beat、nginx。只要基础栈则是 9 个（没有 api2、worker2）。
- `sandbox` 是用来确认沙箱镜像的一次性容器，跑完就退出。`docker compose ps -a` 里看到它 Exited，是正常的。
- `/health` 返回 `{"status":"ok","app":"Omni-Butler"}`。
- `/health/ready` 里 `checks.postgres` 和 `checks.redis` 都是 `ok`。这个地址会去连数据库，不要拿它当 api 容器的 Docker 健康检查；api 容器只查 `/health`。

浏览器打开 `http://<这台机器的局域网IP>/`。IP 用 `ip -4 addr` 看，选用局域网地址。`172.17` 和 `172.18` 是 Docker 自己的网桥，别的电脑访问不到。

登录页用户名是 `ADMIN_USERNAME`（默认 `admin`），密码是 `ADMIN_PASSWORD`。登录后侧栏可以「改密」；管理员还能看到「用户」和「审计」。

另一台电脑打不开页面时，在这台 Linux 上放行 80 端口，例如：

```bash
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --reload
```

## 每天开关

数据在 Docker 卷里。停服务用 `stop`，第二天 `start` 即可。虚拟机正常关机、容器又是 `restart: unless-stopped` 时，开机后可能已经在跑，`docker compose ps` 全是 Up 就不必再 start。

```bash
cd /path/to/omni-butler
docker compose start
docker compose ps
```

暂停（卷还在）：

```bash
docker compose stop
```

`docker compose down -v` 会删掉 Postgres、MinIO、Qdrant 的数据。只拆容器、保留数据用 `docker compose down`（不要加 `-v`）。那样做了之后，第二天用 `docker compose up -d --no-build` 重新创建容器，而不是 `start`。

改了代码再构建，仍然按改动的部分来，不要整栈 `--build`：

```bash
# 共享盘上才需要：
# export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build api       # 改了后端。worker、beat、sandbox-runner、api2、worker2 共用这一份镜像
docker compose build nginx     # 改了前端
docker compose build sandbox   # 改了沙箱
docker compose up -d --no-build
```

改了数据库模型时再迁移。api 已在跑：

```bash
docker compose exec api alembic upgrade head
```

api 没在跑（避免和固定容器名 `omni-butler-api` 撞车）：

```bash
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
```

巡检、备份、HTTPS、换口令见 [docs/deployment.md](docs/deployment.md)。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| `unknown shorthand flag: 'd'` | 没有 Compose V2 插件，按上面「开始之前」安装。 |
| 使用了 `docker-compose`（带连字符） | 改用 `docker compose`。本仓库的编排文件是 V2 写法。 |
| `env file backend/.env not found` | 先 `cp .env.example backend/.env`，并写上 `ADMIN_PASSWORD`。 |
| 构建报 `COPY ... not found` 或 checksum | 共享盘上先 `export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0`。仍失败就同步到本地盘，见 [docs/dev-setup.md](docs/dev-setup.md)。 |
| 构建把磁盘写满 | 不要 `docker compose up -d --build`。按上一节分开 `build api`、`build nginx`、`build sandbox`，再 `up -d --no-build`。 |
| 登录失败 | 看 `backend/.env` 的 `ADMIN_PASSWORD` 是否为空。空密码只在第一次写入：`docker compose up -d --no-build --force-recreate api api2`（没有 api2 就只写 api）。库里已经有密码时，用侧栏「改密」。 |
| 浏览器里只有 `172.17` / `172.18` | 那是 Docker 网桥。改用 `ip -4 addr` 里的局域网地址。 |
| `/health` 正常，`/health/ready` 不是 200 | `docker compose ps` 看 postgres、redis 是否 Up；`docker compose logs postgres redis api`。 |
| 页面能开，聊天一直是演示回复 | `LLM_API_KEY` 还空着。填进 `backend/.env` 后让进程重新读配置（没有 api2、worker2 时从命令里去掉这两项）：`docker compose up -d --no-build --force-recreate api api2 worker worker2 beat` |
| 画图提示沙箱镜像不可用 | `docker compose build sandbox`，再 `docker image inspect omni-sandbox:latest`。 |
| Windows ping 不通、共享盘是空的 | [docs/dev-setup.md](docs/dev-setup.md)。 |

## 其他文档

| 文档 | 内容 |
| --- | --- |
| [docs/deployment.md](docs/deployment.md) | 巡检、备份、HTTPS、换口令、多开一份进程、以前版本的一次性迁移 |
| [docs/dev-setup.md](docs/dev-setup.md) | 方案 B（虚拟机虚拟环境 + Windows 前端）、VMware 共享盘、本机 Docker Desktop |
| [docs/architecture.md](docs/architecture.md) | 目录、端口、路由、Redis、入库队列、上下文、文件存在哪 |
| [docs/eval.md](docs/eval.md) | 评测怎么跑；指标口径在 [backend/app/eval/README.md](backend/app/eval/README.md) 和 [docs/评测系统白话说明.md](docs/评测系统白话说明.md) |
| [docs/GIT.md](docs/GIT.md)、[docs/BRANCHING.md](docs/BRANCHING.md) | 提交和分支 |
