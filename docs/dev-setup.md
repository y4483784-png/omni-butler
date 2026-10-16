# 开发备选与 VMware 共享盘

日常使用请走仓库根目录 [README.md](../README.md) 的 Compose 全栈：浏览器开虚拟机的 80 端口，不必再开 Windows 的 `npm run dev`。

这篇是两条备选：

- **方案 B**：虚拟机里用 Python 虚拟环境跑后端和入库进程，Windows 上只跑前端。中间件仍用 Docker。
- **Docker Desktop**：不用虚拟机，Windows 本机把中间件和沙箱跑起来，后端仍用虚拟环境。

不要把后端放在 Windows、Docker 只放在虚拟机。当前实现会把 Windows 路径交给 `docker -v`，远程 Linux 看不到那个路径，沙箱会失败。

## VMware 共享文件夹

虚拟机里找不到项目，多半是共享没挂上（`/mnt/hgfs` 是空的），不是代码被删了。本环境挂上之后的路径是 **`/mnt/hgfs/omni-butler`**。文档里的 `/path/to/omni-butler` 是占位符。

### 宿主机（VMware）

1. 虚拟机 → **设置** → **选项** → **共享文件夹** → 启用。
2. 添加文件夹，指向宿主机上的仓库根（例如 `d:\Omni-Butler\omni-butler`，以你实际共享的那一层为准）。
3. 共享名示例：`omni-butler`。

### 虚拟机里手动挂上

```bash
vmware-hgfsclient

mkdir -p /mnt/hgfs
mount -t fuse.vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
ls /mnt/hgfs
ls /mnt/hgfs/omni-butler
```

`vmware-hgfsclient` 应列出共享名。`ls /mnt/hgfs/omni-butler` 应能看见 `backend`、`frontend`、`sandbox`、`docker-compose.yml`、`README.md`。

### 开机自动挂载

有的 fuse 版本不接受 `nofail`，会报 `fuse: unknown option(s): '-o nofail'`。`/etc/fstab` 用这一行：

```fstab
.host:/  /mnt/hgfs  fuse.vmhgfs-fuse  allow_other,defaults,_netdev  0  0
```

写入后先离开挂载点再卸载，然后验证：

```bash
systemctl daemon-reload
cd /
umount /mnt/hgfs
mount -a
ls /mnt/hgfs/omni-butler
```

`umount` 说设备忙：先 `cd /`，关掉还停在该目录里的 shell，再卸载。

openEuler 没有 `network.service`。`systemctl enable network` 会失败。网卡用 NetworkManager：`nmcli networking on`。主网卡一般是 `ens33`，IP 用 `ip -4 addr show ens33`。不要把 Docker 网桥（`172.17` / `172.18`）当成虚拟机 IP。

### 共享盘上构建镜像

Docker 的 BuildKit 按文件做校验。共享盘（HGFS）上单独复制 `package.json` 这类文件时，常报 checksum 或 `not found`，即使 `ls` 能看见。前端镜像仍会单独复制这两个文件；后端镜像已经改成整目录复制，但在共享盘上仍建议一起关掉：

```bash
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
```

不要设 `DOCKER_BUILDKIT=1`。本地磁盘不需要这两行。

关掉之后仍然失败，就先同步到本地盘再构建：

```bash
rsync -a --delete \
  --exclude .venv --exclude node_modules --exclude .git --exclude '*.db' \
  /mnt/hgfs/omni-butler/ /var/tmp/omni-butler/
cd /var/tmp/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build sandbox
docker compose build api
docker compose build nginx
docker compose run --rm --name omni-butler-migrate api alembic upgrade head
docker compose up -d --no-build
```

同步过去的是一份副本。之后改代码若仍在共享盘上编辑，要再同步，或改到在 `/var/tmp/omni-butler` 里构建。

## 方案 B：虚拟环境 + Windows 前端

只在不用 Compose 全栈时用。项目在虚拟机里的路径按上一节。用户原文件只存 MinIO。

### 每天

```bash
# —— 虚拟机 ——
nmcli networking on                         # 仅当 Windows ping 不通时
ls /mnt/hgfs/omni-butler || mount -a

docker start qdrant
docker start omni-minio
docker start omni-postgres
docker start omni-redis
curl -s http://127.0.0.1:6333/ | head
curl -I http://127.0.0.1:9000/minio/health/live
docker exec omni-postgres pg_isready -U omni
docker exec omni-redis redis-cli ping

cd /mnt/hgfs/omni-butler/backend
source /root/omni-venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

入库和定时任务另开终端，不要加 `-B`（加了会和单独的 beat 各投一遍定时任务）：

```bash
cd /mnt/hgfs/omni-butler/backend
source /root/omni-venv/bin/activate
celery -A app.tasks.celery_app:celery_app worker -Q ingest,maintenance -l info
```

```bash
celery -A app.tasks.celery_app:celery_app beat -l info
```

Windows 上：

```powershell
cd <本机-omni-butler>\frontend
$env:VITE_API_PROXY = "http://<虚拟机IP>:8001"
npm run dev
```

浏览器开 `http://localhost:5173`。

`frontend/vite.config.ts` 在没设 `VITE_API_PROXY` 时，会把 `/api` 代理到 `http://192.168.88.136:8001`。这是某台虚拟机的旧地址，不是你的机器。以前的说明里还出现过 `192.168.88.129`。每次都用 `ip -4 addr show ens33` 的结果设 `VITE_API_PROXY`。

从 Windows 看虚拟机是否通（把 IP 换成你的）：

```powershell
ping <虚拟机IP>
curl http://<虚拟机IP>:8001/health
curl http://<虚拟机IP>:6333/
```

结束：前后端和两个 Celery 进程都 `Ctrl+C`。需要时 `docker stop omni-minio qdrant omni-postgres omni-redis`。

### 第一次准备

网络和共享盘：

```bash
nmcli networking on
ip -4 addr show ens33
ls /mnt/hgfs/omni-butler || mount -a
docker version
```

沙箱镜像（在仓库根目录）：

```bash
cd /mnt/hgfs/omni-butler
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose build sandbox
docker run --rm -i --network=none --read-only --tmpfs /tmp omni-sandbox \
  python -c "import pandas,matplotlib; print('ok')"
```

中间件（容器名是方案 B 的旧名字，和 Compose 的 `omni-butler-*` 不是同一套）：

```bash
docker run -d --name qdrant --restart unless-stopped \
  -p 6333:6333 -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant:latest

docker volume create omni_pgdata
docker run -d --name omni-postgres --restart unless-stopped \
  -p 5432:5432 \
  -e POSTGRES_USER=omni -e POSTGRES_PASSWORD=omni -e POSTGRES_DB=omni_butler \
  -v omni_pgdata:/var/lib/postgresql/data \
  postgres:16

docker volume create omni_redisdata
docker run -d --name omni-redis --restart unless-stopped \
  -p 6379:6379 \
  -v omni_redisdata:/data \
  redis:7 redis-server --appendonly yes

docker volume create omni_minio_data
docker run -d --name omni-minio --restart unless-stopped \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -v omni_minio_data:/data \
  minio/minio:latest server /data --console-address ":9001"
```

建表在后端目录做。新库用 Alembic。若这个库已经被后端启动时自动建过表，只登记版本：`alembic stamp head`。

```bash
cd /mnt/hgfs/omni-butler/backend
pip install -r requirements.txt
alembic upgrade head
```

从 Windows 访问这些端口时，在虚拟机上放行（Postgres 的 5432 和 Redis 的 6379 只给虚拟机本机用，不必对 Windows 打开）：

```bash
firewall-cmd --permanent --add-port=6333/tcp
firewall-cmd --permanent --add-port=9000/tcp
firewall-cmd --permanent --add-port=9001/tcp
firewall-cmd --permanent --add-port=8001/tcp
firewall-cmd --reload
```

`backend/.env` 与中间件在同一台机器上时用回环地址。由 `.env.example` 复制后再改这几项即可。模板里的 `CORS_ORIGINS` 只有 `http://localhost:5173`。浏览器若用 `http://127.0.0.1:5173` 打开，再补上这个源：

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

`SANDBOX_RUNNER_URL` 留空。方案 B 由本机的 `docker` 直接起沙箱，不经过 Compose 里的 sandbox-runner。`SANDBOX_TMP_DIR` 也留空，临时文件用系统临时目录。

MinIO 控制台：`http://<虚拟机IP>:9001`，账号和密码都是 `minioadmin`。

登录账号仍是 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，和 Compose 一样。Google 日历四个变量见 [README 的配置一节](../README.md)。方案 B 的回调用模板里的 `http://localhost:5173/api/calendar/google/callback`（浏览器开的是这个源时）。

### 依赖

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env            # Windows: copy ..\.env.example .env
```

```bash
cd frontend
npm install
```

没配 `LLM_API_KEY` 时，聊天是演示用的流式回复，这条路径仍能走通。

`requirements.txt` 把 `openai` 锁在 `>=2.0,<3`。不要降到 1.x。`openai` 1.x 和 `httpx` 0.28 及更新版本合不来，会报 `TypeError: AsyncClient.__init__() got an unexpected keyword argument 'proxies'`，页面一直停在「生成中」，客户端销毁时还可能报 `AttributeError: 'AsyncHttpxClientWrapper' object has no attribute '_state'`。手动改过依赖后如果看到这两句，重新 `pip install -r requirements.txt`。

调用模型时，客户端按当前这条事件循环复用连接（实现在 `backend/app/core/llm.py`）。不要在模块加载时建一个跨请求共用的客户端，旧写法会把事件循环挂死。

## 本机 Docker Desktop

不用虚拟机、本机已经有 Docker Desktop 时，可以只在 Windows 上跑中间件和沙箱，后端仍用虚拟环境。浏览器仍走 Vite 的 5173。全栈进 nginx 的路径以 Linux 上的 Compose 为准，见 [README](../README.md)。

不要用 `docker compose up -d minio qdrant` 配合本机 uvicorn。当前 `docker-compose.yml` 把 Postgres、Redis、Qdrant、MinIO 的端口映射注释掉了，只在容器网络里能访问。Windows 上的虚拟环境连不上 `localhost:5432` 或 `localhost:9000`。

中间件用上一节「第一次准备」里那四条 `docker run -p ...`（Qdrant、Postgres、Redis、MinIO），在 Docker Desktop 里执行即可。它们会把端口映射到本机。然后再：

```bash
cd <本机-omni-butler根目录>
docker compose build sandbox

cd backend
.venv\Scripts\activate
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

```powershell
cd <本机-omni-butler>\frontend
$env:VITE_API_PROXY = "http://127.0.0.1:8001"
npm run dev
```

`backend/.env` 用上一节的回环地址。`vite.config.ts` 在没设 `VITE_API_PROXY` 时会指向一台虚拟机的旧 IP，本机后端必须自己设成 `127.0.0.1:8001`。

## 方案 B 故障

| 现象 | 处理 |
| --- | --- |
| Windows ping 不通；`ens33` 没有 IPv4，或全是「未托管」 | `nmcli networking on`，再看 `ip -4 addr show ens33`。 |
| 登录页或说明里只有 `172.17` / `172.18` | 那是 Docker 网桥，改用 `ens33` 的局域网地址。 |
| `/mnt/hgfs` 是空的 | `vmware-hgfsclient`，然后 `mount -a`，或手动 `mount -t fuse.vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other`。 |
| fstab 报 `unknown option nofail` | 去掉 `nofail`，用 `allow_other,defaults,_netdev`。 |
| `systemctl enable network` 失败 | openEuler 没有 `network.service`，用 NetworkManager。 |
| 前端能开，请求打到别人的虚拟机 | 设 `VITE_API_PROXY`。默认值在 `frontend/vite.config.ts`。 |
| 沙箱读不到上传的文件 | 确认 MinIO 能访问。对象会临时下载后再挂进沙箱，跑完就删临时文件。 |
| 页面一直「生成中」，日志里有 `proxies` | `openai` 被降到了 1.x。按上面「依赖」一节装回 2.x。 |
