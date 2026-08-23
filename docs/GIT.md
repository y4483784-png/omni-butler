# Omni-Butler Git + GitHub 操作方案

适用范围：`omni-butler/` 产品目录（Windows `d:\Omni-Butler\omni-butler`，虚拟机 `/mnt/hgfs/omni-butler`）。

默认终端：**CMD**（提示符形如 `d:\Omni-Butler\omni-butler>`）。`Test-Path` 是 PowerShell 命令，在 CMD 里会报「不是内部或外部命令」。

目标终态：

1. 本地唯一仓库根在 **内层** `omni-butler/`。
2. 远程是 **GitHub 私有仓**（`y4483784-png/omni-butler`）。
3. `backend/.env`、证书、`.venv`、`node_modules` 永不上传。
4. 分支按华海流程：生产 `master`、日常 `develop`、测试 `release`、现网 `hotfix`。逐步命令见 [`BRANCHING.md`](BRANCHING.md)。

---

## 0. 目标与禁止

**做**

- 内层 `git init` → 扫描 → 首次 commit → 建 GitHub **Private** 空仓 → `git push`。
- 用已有个人身份提交：`chenyuzhuo2005` / `1711582727@qq.com`（不要改全局，除非 GitHub 上邮箱对不上，见 §5.2）。

**不要做**

- 不要在外层 `d:\Omni-Butler` 点「Initialize Repository」或「Publish to GitHub」（会把 PRD、计划、错误根目录一起推上去）。
- 不要把仓库建成 **Public**。本仓是内部办公助手 + 评测数据 + 架构信息。
- 不要用 GitHub **登录密码** 当 `git push` 密码（已废弃）。必须用 **Personal Access Token** 或 SSH。
- 不要 `git add -f` 任何 `.env` / `.pem` / `.key`。
- 不要 `git push --force` 到 `master` / `develop` / `release`。
- 不要在 `master` 上直接开发；日常只在 `develop`（见 [`BRANCHING.md`](BRANCHING.md)）。
- 不要同时再绑一个 Gitee `origin`（一个仓只保留一个 `origin`）。

---

## 1. 仓库根

| 角色 | 路径 | 是否仓库根 |
|------|------|------------|
| Cursor 当前工作区 | `d:\Omni-Butler` | **否** |
| 产品代码 | `d:\Omni-Butler\omni-butler` | **是** |
| 虚拟机 | `/mnt/hgfs/omni-butler` | 与上一行同一目录 |

外层误建 `.git` 时按 §10 删除后再从 §6 重来。

---

## 2. 入库边界

**上传**

- `backend/app/`、`frontend/src/`、`sandbox/`、`deploy/`（无真实证书）、`docker-compose*.yml`
- `.env.example`（不是 `.env`）
- 评测黄金数据：`backend/data/eval/*.jsonl` 与 fixtures 里的正式 CSV
- `backend/tests/`、`backend/scripts/`、`backend/alembic/`、`README.md`、`docs/`

**不上传**（`.gitignore`）

| 类别 | 路径 |
|------|------|
| 密钥 | `backend/.env`、任意 `.env` |
| 证书 | `deploy/certs/*.pem`、`*.key`、`deploy/nginx-tls.conf` |
| 依赖 | `.venv/`、`node_modules/` |
| 运行 | `*.db`、`uploads/`、`*.log` |
| 评测产物 | `backend/reports/*.json`、`*_eval_latest.json`、`_plan_join_*.csv`、`data/harness/` |
| 内部 Word | `*.docx` |

外层的 PRD、部署方案、`新建 文本文档.md` **不在本仓内**，不会被这次 push 带上。

---

## 3. 全流程总览

```
GitHub 网页：验证邮箱 → 加 QQ 邮箱 → 建 Private 空仓 → 建 PAT
        ↓
CMD 本地：init → add → 扫描 → commit
        ↓
CMD：remote add → branch -M main → push
        ↓
网页：确认无 .env，Visibility = Private
```

下面 §4–§8 **按顺序做，不要跳**。

---

## 4. GitHub 账号（只做一次）

记下你的 **GitHub 用户名**（新建账号的那个，下面写成 `GITHUB_USER`）。仓库名建议：`omni-butler`。

### 4.1 验证登录邮箱

GitHub → 右上角头像 → **Settings** → **Emails**。

- 注册邮箱必须显示 Verified。
- **把 `1711582727@qq.com` 加进去并点验证邮件**。  
  否则网页上看不到你的头像/贡献（commit 作者是 QQ 邮箱，账号对不上）。

可选：勾选 Keep my email addresses private，再用 GitHub 提供的 `noreply` 邮箱（见 §5.2）。首次上传可以先加 QQ 邮箱，不改 `user.email`。

### 4.2 建私有空仓库

1. 打开 https://github.com/new
2. **Repository name**：`omni-butler`
3. **Private**（必须勾选，不要 Public）
4. **不要**勾选 Add a README / .gitignore / License（本地已有历史，勾了第一次 push 会冲突）
5. Create repository
6. 页面给出的地址形如：`https://github.com/GITHUB_USER/omni-butler.git`  
   先复制保存，§8 要用。

此时远程是空的，没有 commit，这是预期。

### 4.3 建 Personal Access Token（推送密码）

GitHub 已不允许用账户密码 `git push`。

1. 打开 https://github.com/settings/tokens?type=beta  
   （Settings → Developer settings → Personal access tokens → **Fine-grained tokens**）
2. Generate new token
3. Token name：`omni-butler-push`
4. Expiration：建议 90 天（到期再续，不要设无限期除非你清楚风险）
5. Repository access：**Only select repositories** → 只选 `omni-butler`
6. Permissions → Repository permissions：
   - **Contents**: Read and write
   - **Metadata**: Read（通常自动带上）
7. Generate token
8. **立刻复制** `ghp_` 开头的字符串。关掉页面就再也看不到，只能作废重建。
9. 不要把 token 写进仓库、README、聊天记录、`.env`。

Classic token 备选：Settings → Tokens (classic) → `repo` 权限。新账号优先用上面的 fine-grained。

---

## 5. 本机 Git 身份

### 5.1 沿用现有全局身份（推荐）

CMD：

```bat
git config --global --get user.name
git config --global --get user.email
```

本机已有：

- `user.name=chenyuzhuo2005`
- `user.email=1711582727@qq.com`

**不要改 `--global`。** 本地 commit 继续用这套。Gitee 的 credential 与 GitHub 分开，互不影响。

### 5.2 仅当 GitHub 贡献对不上时

若网页 commit 旁没有你的账号，说明邮箱未加入 GitHub。按 §4.1 加邮箱即可，一般不必改 `user.email`。

若你改用 GitHub noreply（Settings → Emails 里那一串）：

```bat
cd /d d:\Omni-Butler\omni-butler
git config user.email "数字+用户名@users.noreply.github.com"
```

只改**本仓库**，不要 `--global`。

---

## 6. 本地建仓（CMD）

```bat
cd /d d:\Omni-Butler\omni-butler
cd
if exist docker-compose.yml echo COMPOSE=YES
if exist .git echo INNER_GIT=YES
if exist ..\.git echo OUTER_GIT=YES
```

期望：路径以 `\omni-butler` 结尾，`COMPOSE=YES`，后两行无输出。若 `OUTER_GIT=YES`，先做 §10。

```bat
git init
git status
git check-ignore -v backend\.env
```

期望：

- `No commits yet`
- `backend/.env` **不**在 Untracked 列表
- `check-ignore` 有输出（证明被忽略）
- `.venv/`、`node_modules/` 不出现
- `.env.example`、`README.md`、`backend/app/` 出现

若 `.env` 出现在 Untracked：**停止**，不要 `git add`。

---

## 7. 首次提交前扫描

```bat
cd /d d:\Omni-Butler\omni-butler
git add -A
git status
git diff --cached --stat
git diff --cached --name-only | findstr /I /E ".env"
git diff --cached --name-only | findstr /I "node_modules .venv .pem .key"
```

- 第一行 `findstr /E ".env"`：只匹配**文件名以 `.env` 结尾**的路径。出现 `backend/.env` 才要 `git reset`。
- **`.env.example` 不算危险**，必须提交。旧命令 `findstr \.env` 会误伤它；若因此已经 `git reset`，再执行一次本节的 `git add -A` 即可，仓库没有坏。
- 第二行有 `node_modules` / `.venv` / `.pem` / `.key` → `git reset`。
- 两行都无输出（或提示找不到）→ 正常，继续。
- `--stat` 末行若到数百 MB → 依赖被加进去了，`git reset`。

`CRLF will be replaced by LF` 是换行符提示，**不是错误**，忽略即可。

```bat
git commit -m "Initial commit: Omni-Butler application tree."
git log -1 --format=full
git ls-files | findstr /I /E ".env"
git ls-files | findstr /I "node_modules .venv"
```

以 `.env` 结尾的 `ls-files` 必须无匹配（`.env.example` 出现在仓库里是对的）。作者应是 `chenyuzhuo2005 <1711582727@qq.com>`。

---

## 8. 连接 GitHub 并推送

把 `GITHUB_USER` 换成你的 GitHub 用户名。

```bat
cd /d d:\Omni-Butler\omni-butler
git remote -v
git remote add origin https://github.com/GITHUB_USER/omni-butler.git
git remote -v
git branch -M main
git push -u origin main
```

弹出登录时：

| 字段 | 填什么 |
|------|--------|
| Username | GitHub **用户名**（不是 QQ，也不是 Gitee） |
| Password | §4.3 的 **PAT**（`ghp_...`），不是 GitHub 登录密码 |

Windows 可能弹出浏览器「Authorize Git Credential Manager」：用浏览器登录 GitHub 授权也可以，效果与 PAT 相同。

成功标志：末尾有 `main -> main`，且 `Branch 'main' set up to track 'origin/main'`。

失败对照：

| 现象 | 处理 |
|------|------|
| `remote origin already exists` | `git remote -v` 看地址。错了则 `git remote remove origin` 再 `add` |
| `Authentication failed` | 密码栏用了登录密码；重建 PAT，Username 用 GitHub 用户名 |
| `Repository not found` | 仓库名/用户名拼错，或仓是别人的、PAT 没选这个仓 |
| `failed to push some refs` / 有 README 冲突 | 网页建仓时勾了 README。不要 `--force`。应删网页仓重建为空仓，或先问后再 `pull --rebase` |
| 推送体积过大 | 多半带上了 `.venv`；`git ls-files` 检查后按 §10 思路重建（尚未 push 成功时可删 `.git` 重来） |

---

## 9. 推送后验收（必须做）

浏览器打开 `https://github.com/GITHUB_USER/omni-butler`：

1. 右上角锁图标 / 齿轮 → 确认 **Private**。
2. 搜索或进 `backend/`：**不能有 `.env`**，应有 `.env.example`。
3. 不能有 `node_modules/`、`.venv/`、`*.docx`。
4. 应有 `README.md`、`docker-compose.yml`、`docs/GIT.md`。

本机：

```bat
git remote -v
git status
git check-ignore -v backend\.env
```

`status` 应为 clean（或仅 ignore 内的本地文件）。`origin` 指向你的 GitHub HTTPS 地址。

Cursor：用 **打开文件夹** `d:\Omni-Butler\omni-butler`，源码管理应显示与 GitHub 同步，而不是外层的「Initialize Repository」。

虚拟机（共享盘已挂上）：

```bash
cd /mnt/hgfs/omni-butler
test -d .git && git remote -v && git log -1 --oneline
```

---

## 10. 误操作恢复

**误在外层 init**（且还没有有价值的 commit）：

```bat
rmdir /s /q d:\Omni-Butler\.git
```

然后从 §6 在内层重做。

**已经 push 了 `.env`（紧急）**

1. GitHub 网页立刻把仓改为 Private（若当时是 Public）。
2. 轮换 `backend/.env` 里所有密钥（LLM、数据库、MinIO、SECRET_KEY），旧 key 作废。
3. 历史里仍有泄密，需要改写历史或删仓重建；不要只靠再提交一个「删除 .env」。  
   未对外公开且刚推上去时：删 GitHub 仓、本地 `rmdir /s /q .git`、改密钥、从 §6 重做最快。

**Token 泄露**：GitHub → Settings → tokens → Revoke，再按 §4.3 新建。

---

## 11. 日常（本地 + GitHub）

日常在 **`develop`** 上操作（完整分支流程见 [`BRANCHING.md`](BRANCHING.md)）：

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout develop
git pull origin develop
git status
git add 具体文件
git commit -m "说明为什么改"
git push origin develop
```

- 先 commit 再 push。不要把未扫描的 `git add -A` 直接推。
- 一次提交一类事。
- 模型变更同时提交 `alembic/versions/`。
- 不要 force push `master` / `develop` / `release`。
- 不要在 `master` 上直接开发。

回退未提交修改：`git restore path\to\file`  
只看历史：`git log --oneline -20` 然后 `git show <hash>`

---

## 12. 与 Gitee 的关系

本机仍记得 Gitee 凭据，**本仓不要再 `remote add` Gitee**。一个 `origin` 只指向 GitHub。

旧 Gitee 项目与本仓无关，不必登录 Gitee 才能推 GitHub。

---

## 13. 验收清单

- [ ] GitHub 仓为 **Private**，且建仓时无 README
- [ ] `1711582727@qq.com` 已加入 GitHub Emails 并已验证
- [ ] PAT 只授权这一个仓的 Contents 读写，未写入任何文件
- [ ] `.git` 在 `omni-butler/`，不在 `d:\Omni-Butler\`
- [ ] `git check-ignore -v backend\.env` 有输出
- [ ] `git ls-files` 无 `.env` / `.venv` / `node_modules`
- [ ] `origin` = `https://github.com/GITHUB_USER/omni-butler.git`
- [ ] 网页上看不到 `.env`，能看到 `.env.example`
- [ ] 虚拟机能看到同一 `.git` 与同一 `origin`
