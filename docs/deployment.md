# 部署与运维

推荐路径的第一次启动、每天开关，见仓库根目录 [README.md](../README.md)。这里是跑起来之后才需要的事：日志、巡检、备份、HTTPS、口令、多开一份进程，以及从旧环境迁过来的一次性步骤。

命令都在仓库根目录执行。下面用 `/path/to/omni-butler` 代替。VMware 共享盘上常见是 `/mnt/hgfs/omni-butler`。

## 日志

Compose 给每个服务加了日志轮转，避免容器日志把磁盘写满。

| 服务 | 单文件 | 份数 | 大约上限 |
| --- | --- | --- | --- |
| 大多数服务 | 10MB | 3 | 30MB |
| api、worker，以及扩出来的 api2、worker2 | 20MB | 5 | 100MB |

容器异常退出会按 `restart: unless-stopped` 自己拉起，**不会通知人**。要知道它挂了，靠下面的巡检。

## 巡检与备份

装一次 cron 即可。脚本：

- [`deploy/health-watch.sh`](../deploy/health-watch.sh)：根分区使用率是否到 80%、长期运行的服务是否还在跑或变成 unhealthy、宿主机有没有 `omni-sandbox:latest`、`http://127.0.0.1/health/ready` 是否 200。一次性的 `sandbox` 容器会退出，脚本默认跳过它（`HEALTH_SKIP_SERVICES=sandbox`）。
- [`deploy/backup.sh`](../deploy/backup.sh)：备份 Postgres、Qdrant、MinIO。
- 样例 [`deploy/crontab.example`](../deploy/crontab.example) 里的路径是共享盘。目录不同就改 `COMPOSE_DIR`。

```bash
chmod +x /path/to/omni-butler/deploy/health-watch.sh /path/to/omni-butler/deploy/backup.sh
mkdir -p /var/omni-backups
crontab -e
```

加入（共享盘示例；目录不同就改路径）：

```cron
# MAILTO=root
*/5 * * * * COMPOSE_DIR=/mnt/hgfs/omni-butler /mnt/hgfs/omni-butler/deploy/health-watch.sh
15 2 * * * COMPOSE_DIR=/mnt/hgfs/omni-butler BACKUP_DIR=/var/omni-backups /mnt/hgfs/omni-butler/deploy/backup.sh
```

巡检有异常时写 syslog，标识是 `omni-butler-health`，同时打到 stderr。cron 配了 `MAILTO` 就会寄信。

备份对齐常见做法：

| 数据 | `docker volume ls` 里的名字 | 怎么备 |
| --- | --- | --- |
| Postgres | `omni-butler_pgdata`（编排里的卷名是 `pgdata`） | 容器里 `pg_dump -Fc`。cron 里用 `docker exec`，不用 `docker compose exec`，避免抢终端。 |
| Qdrant | `omni-butler_qdata`（`qdata`） | 官方 Snapshots API：创建快照、下载、再删掉。经 api 容器访问内部地址 `qdrant:6333`。宿主机不映射 6333。 |
| MinIO | `omni-butler_minio_data`（`minio_data`） | 用和线上相同的 boto3 把 `S3_BUCKET` 打成 tar，效果等同 `mc mirror`。官方 server 镜像里没有 `mc`。 |
| Redis | `omni-butler_redisdata`（`redisdata`） | 里面是缓存和排队任务，丢了可以再生，脚本不备它。AOF 只减少正在排队的任务丢失。 |

产出在 `/var/omni-backups/<UTC时间戳>/`：`postgres.dump`、`qdrant.snapshot`、`minio.tgz`、`MANIFEST.txt`。保留天数看 `BACKUP_KEEP_DAYS`，默认 7。

恢复时可以参考脚本头部的注释：Postgres 用 `pg_restore`，Qdrant 用快照恢复接口，MinIO 把 tar 里的对象传回桶。

手动确认沙箱镜像（巡检也会查）：

```bash
docker compose build sandbox
docker image inspect omni-sandbox:latest
```

镜像能跑的话：

```bash
docker run --rm -i --network=none --read-only --tmpfs /tmp omni-sandbox \
  python -c "import pandas,matplotlib; print('ok')"
```

## HTTPS

默认只开 **80**，明文。机器只在可信内网、防火墙也只给可信网段时，可以先这样。

一旦 80 对公网、访客 Wi-Fi 或不可信办公网开放，先上 TLS 再让别人访问。浏览器到 nginx 的登录令牌、聊天、上传文件都会明文经过 80。`LLM_API_KEY` 仍留在 api 容器里，出站访问智谱走 HTTPS，但用户数据在这一跳上没有加密。

不强制公网 Let's Encrypt。内网 CA、mkcert、已有证书都可以。

```bash
cp deploy/nginx-tls.conf.example deploy/nginx-tls.conf
# 把 fullchain.pem、privkey.pem 放到 deploy/certs/（私钥不要提交）
```

然后按 `docker-compose.yml` 里 nginx 服务的注释，打开 `443:443` 和证书挂载。证书是挂进容器的，不必为了换证书重做前端镜像：

```bash
docker compose up -d nginx
```

`deploy/nginx-tls.conf.example` 头部写的是 `docker compose up -d --build nginx`。那会把前端镜像再打一遍。共享盘上若真要 `--build`，先按 README 关掉 BuildKit。

HTTPS 起来之后，在 `backend/.env` 设 `SESSION_COOKIE_SECURE=true`，让登录 Cookie 只走加密连接，然后 `docker compose up -d --no-build` 让 api 读到新值。防火墙再放行 443：

```bash
firewall-cmd --permanent --add-port=443/tcp
firewall-cmd --reload
```

## 口令与隔离

Compose 运行时用非超管 `omni_app` 连库，`omni` 只做迁移和建角色。Postgres 的行级隔离对超管不生效，所以业务连接必须是 `omni_app`。

侧栏可以「改密」。管理员可以看「审计」。日志和响应带 `X-Request-ID`。

默认的 `SECRET_KEY`、MinIO 的 `minioadmin`、数据库口令 `omni` 会在启动时打警告。`ENFORCE_SECURE_SECRETS=true` 时，下面任何一条还在，进程就拒绝启动：

- `SECRET_KEY` 仍是空或 `change-me-in-production`
- `S3_ACCESS_KEY` / `S3_SECRET_KEY` 仍是 `minioadmin` / `minioadmin`
- 运行时数据库 URL 里仍是 `omni:omni` 或 `omni_app:omni`
- 填了 `GOOGLE_OAUTH_CLIENT_ID`，但 `CALENDAR_TOKEN_KEY` 是空的

所以要先把口令换完，再打开强制检查。只改 `SECRET_KEY` 不够：Compose 里 MinIO 仍是字面量 `minioadmin`，数据库口令默认仍是 `omni`。

本仓库不会改你正在跑的 `backend/.env`。下面在虚拟机上手做。

### `SECRET_KEY`

改 `backend/.env`，然后让读这份配置的进程重新创建。已经登录的 Cookie 全部失效，需要重新登录。

```bash
docker compose up -d --no-build --force-recreate api api2 worker worker2 beat
```

没有加载 override 时，命令里去掉 `api2` 和 `worker2`。

### Postgres

口令写在 **`docker-compose.yml` 同级** 的 `.env`，不是只改 `backend/.env`：

```env
POSTGRES_PASSWORD=给omni的新口令
POSTGRES_APP_PASSWORD=给omni_app的新口令
```

数据卷如果已经初始化过，改这个文件**不会**改库里已经存在的密码。先在库里改，再改文件，再让业务容器起来：

```bash
docker exec omni-butler-postgres psql -U omni -d omni_butler -c "ALTER USER omni PASSWORD '给omni的新口令';"
docker exec omni-butler-postgres psql -U omni -d omni_butler -c "ALTER USER omni_app PASSWORD '给omni_app的新口令';"
docker compose up -d --no-build --force-recreate api api2 worker worker2 beat
```

`docker-compose.yml` 会用这两个变量拼 `DATABASE_URL`（`omni_app`）和 `DATABASE_MIGRATE_URL`（`omni`），并覆盖 `backend/.env` 里的同名地址。没有加载 override 时，上面的命令去掉 `api2` 和 `worker2`。

### MinIO

`MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD` 写死在 `docker-compose.yml` 里，都是 `minioadmin`，不是从 `.env` 读的。已有 `minio_data` 卷时，改这两行也不会改掉卷里已经初始化的 root。

要换的话：按 MinIO 文档改 root 凭据，同时改 `docker-compose.yml` 里的这两行，并让 `backend/.env` 的 `S3_ACCESS_KEY` / `S3_SECRET_KEY` 与新凭据一致。只改 `backend/.env` 会让上传对不上。

### Redis

默认没有口令。Compose 把容器里的 `REDIS_URL` 写成 `redis://redis:6379/0`，写在 `docker-compose.yml` 的公共环境里，会覆盖 `backend/.env`。

要加口令：给 redis 服务加上 `requirepass`，并把那处 `REDIS_URL` 改成 `redis://:密码@redis:6379/0`，然后重新创建 api、worker、beat。只改 `backend/.env` 不会生效，队列会连不上。一台机器、只在内网时可以先不加。

### 打开强制检查

上面几项都换完之后，再在 `backend/.env` 设 `ENFORCE_SECURE_SECRETS=true`，并重新创建 api。

## 同一台机器上多开一份进程

聊天画图走 `sandbox-runner`。api **不**挂 Docker 套接字。

`docker-compose.override.yml` 会自动加上第二聊天进程 `api2`、第二入库进程 `worker2`。nginx 在 `NGINX_SCALE=1` 时改用 [`deploy/nginx-scale.conf`](../deploy/nginx-scale.conf)，按连接数较少的那台转发。`docker compose up` / `stop` / `ps` 都管全套，不必再写 `-f`。

长期运行的容器名是固定的：`omni-butler-api`、`omni-butler-api2`、`omni-butler-worker`、`omni-butler-worker2`，以及 postgres、qdrant、redis、minio、sandbox-runner、beat、nginx。

`sandbox` 没有固定容器名。它跑完就退出，名字一般是 `omni-butler-sandbox-1`，状态 Exited 是正常的。

不要用 `--scale worker=2`。那会去造同一服务的副本，和单独的 `worker2` 不是一回事；worker 已经写了固定 `container_name`，`--scale` 会直接报错。

内存不够、只要基础 9 个服务时：

```bash
docker compose -f docker-compose.yml up -d --no-build
```

显式写出 `-f` 时不会加载 override。同时说话的人数仍受智谱并发槽位限制（配置里大约几十到一百路，见 `LLM_MODEL_CONCURRENCY_LIMITS`）。

改后端时只构建一次 api 镜像，再 `docker compose up -d --no-build`。沙箱是另一份小镜像，单独 `docker compose build sandbox`。不要和五个后端角色一起 `--build`。

## 历史迁移

新装机器按 [README](../README.md) 做即可，不必做本节。下面是以前环境留下的一次性步骤。

### 从旧的 `docker run` 容器换到 Compose

以前方案 B 用过这些容器名：`omni-postgres`、`omni-redis`、`omni-minio`、`qdrant`。Compose 的容器名是 `omni-butler-` 前缀。卷也不是同一套：旧的是 `omni_pgdata`、`omni_redisdata`、`omni_minio_data`、`qdrant_storage`；Compose 在 `docker volume ls` 里是 `omni-butler_pgdata`、`omni-butler_redisdata`、`omni-butler_minio_data`、`omni-butler_qdata`。两套同时跑会有两个库。切到 Compose 前先停掉旧容器。默认会得到一个新库，旧卷里的数据不会自动跟过来。

```bash
docker stop omni-postgres omni-redis omni-minio qdrant
```

确认不再需要旧数据之后，才删除旧容器。不要对 Compose 项目使用 `docker compose down -v`。

### 从带 `-1` 后缀的容器名换过来

旧名字像 `api-1`、`api2-1`、`worker-2`。现在长期服务用固定名，没有这份后缀。**不要**加 `-v`，数据卷留着：

```bash
cd /path/to/omni-butler
# 共享盘上构建才需要：
# export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose down --remove-orphans
docker rm -f omni-butler-worker-2 2>/dev/null || true
docker compose build sandbox
docker compose build api
docker compose build nginx
docker compose up -d --no-build
docker compose ps
```

这里分开构建，是为了避免 `up --build` 在 BuildKit 关掉时把磁盘写满。旧说明里的 `up -d --build` 容易踩这个坑。

### 加上改密和审计界面的那一次升级

当时的界面改动需要新的数据库迁移，以及新的前端镜像。新装已经包含这些功能，不必再做。

若你的镜像还是那次改动之前打的，正确顺序是构建会变的镜像，再迁移，再启动。只 `build sandbox` 再 `up -d --no-build` **不会**更新前端；前端在 nginx 镜像里。

```bash
cd /path/to/omni-butler
# 共享盘上才需要：
# export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build api
docker compose build nginx
docker compose build sandbox
docker compose exec api alembic upgrade head
docker compose up -d --no-build
```

api 当时没在跑，就把 `exec` 换成：

```bash
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
```

### 报 `role omni_app does not exist`

新装在 api 第一次启动时会用属主账号 `omni` 创建 `omni_app`。旧库上如果业务容器抢先用 `omni_app` 去连、角色还没有，就会看到这句。先用属主连接做迁移（`alembic` 走 `DATABASE_MIGRATE_URL`），再起服务：

```bash
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
docker compose up -d --no-build
```

迁移命令要用 `--name omni-butler-migrate`，避免和固定容器名 `omni-butler-api` 冲突。

### 把旧的本地上传文件搬进 MinIO

现在用户原文件只进 MinIO。更早的版本可能把文件留在后端目录。在曾经跑过那套本地目录的机器上：

```bash
cd /path/to/omni-butler/backend
source /root/omni-venv/bin/activate
python scripts/migrate_uploads_out.py --dry-run
python scripts/migrate_uploads_out.py --delete-old
```

`--dry-run` 先看会动哪些文件。确认后再加 `--delete-old`。虚拟环境路径按你机器上的实际位置来，`/root/omni-venv` 只是当时的例子。
