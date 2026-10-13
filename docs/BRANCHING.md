# 华海研发分支管理（Omni-Butler）

本仓按华海四类分支管理。日常只在 **`develop`** 上改代码。  
终端用 **CMD**，目录必须是 `d:\Omni-Butler\omni-butler`。

远程：`https://github.com/y4483784-png/omni-butler.git`  
首次提交（当前 `master` 起点）：`8cf0ab1`。

---

## 1. 四类分支（作用与寿命）

| 分支 | 谁用 | 从哪来 | 寿命 | 允许做什么 | 禁止做什么 |
|------|------|--------|------|------------|------------|
| **master** | 生产基线 | 仓库根 | 长期 | 只接收 **release** 或 **hotfix** 的合并 | 直接 `commit`、直接从 develop 合并上来 |
| **develop** | 研发日常 | 从 master 切出（本产品已建好） | 长期 | 在本分支上开发、`commit`、`push` | 直接合并到 master；把 release / hotfix 合进来 |
| **release** | 测试 + 上线部署 | 从 develop 切出；以后每次进测试把 develop **合并进** release | 按版本：进测试时用，上线合进 master 后可保留供下轮 | 测当前版本、修**本版本**的 bug、用本分支部署 | 加新功能；合并回 develop |
| **hotfix** | 现网紧急缺陷 | 从 master 切出 | 用完即合进 master，然后删本地/远程该 hotfix | 修线上 bug、测试、部署 | 夹带新功能；当作日常开发分支 |

华海三条硬规则（必须遵守）：

1. **develop 不能直接合并到 master**（必须经 release）。
2. **release 不能合并回 develop**。
3. **release 进入待发布后不加新功能**，只修当前功能的 bug。

因此：hotfix / release 上的修复**不会自动回到 develop**。若 develop 还需要同一处修复，在 develop 上**单独再改一次**（或 `cherry-pick` 那一次 commit），不要 `git merge release` / `git merge hotfix` 进 develop。

```
master ─────────────── 生产（禁直接提交）
  │
  ├── develop ──────── 日常开发
  │      │
  │      └── release ─ 测试 / 用本分支上线 ──merge──► master
  │
  └── hotfix ──────── 现网救急 / 用本分支上线 ──merge──► master
```

---

## 2. 本仓已经做好的初始化（不必再做）

| 步骤 | 结果 |
|------|------|
| 把原来的 `main` 改名为 `master` 并推送 | 生产基线 = 首次提交 |
| 从 `master` 切出 `develop` 并推送 | 以后开发都在这里 |
| 尚未创建 `release` / `hotfix` | 进测试、现网出问题再按下面开 |

你本地当前应在 **`develop`**。检查：

```bat
cd /d d:\Omni-Butler\omni-butler
git branch --show-current
```

应打印 `develop`。若不是：

```bat
git checkout develop
```

GitHub 网页还需要你点一次（我无法代点）：

1. 打开 https://github.com/y4483784-png/omni-butler/settings  
2. **Default branch** 改成 **`master`** 并 Update  
3. 改完后再删远程旧名 `main`（可选）：

```bat
git push origin --delete main
```

未改默认分支之前，**不要**删 `main`。

---

## 3. 每天开发（常规，人在 develop）

只做这四步。不要切到 `master` 上改。

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout develop
git pull origin develop
```

改代码，确认没有把 `.env` 加进去：

```bat
git status
git add 你改过的文件
git commit -m "说明为什么改，而不是列文件名"
git push origin develop
```

一次提交只做一类事。模型变更同时提交 `backend/alembic/versions/`。

**不要：**

```bat
git checkout master
git merge develop
git push origin master
```

这是「develop 直接进生产」，违反华海流程。

---

## 4. 常规版本：开发完毕 → 测试 → 上线 → 合进 master

### 4.1 研发：开发完毕，交给测试（从 develop 进入 release）

**本产品第一次进测试**（还没有 `release` 分支）时，在 CMD：

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout develop
git pull origin develop
git checkout -b release
git push -u origin release
```

**以后每一轮**再进测试（远程已有 `release`）：

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout develop
git pull origin develop
git checkout release
git pull origin release
git merge develop
git push origin release
```

若 `merge` 报冲突：在 release 上打开冲突文件，改完后：

```bat
git add 冲突文件
git commit -m "Merge develop into release: 解决冲突。"
git push origin release
```

告诉测试：**测 `release` 分支，不要测 develop。**

进入待发布后：

- 只在 **`release`** 上修本版本 bug（下面 4.2）。
- **不要**再往 develop 合新功能再 merge 进 release（那是下一轮版本）。
- **不要** `git merge release` 回到 develop。

### 4.2 测试期：只修当前版本的 bug（在 release 上）

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout release
git pull origin release
```

改 bug → `git add` → `git commit` → `git push origin release`。

测试人员拉最新 release 部署：

```bat
git fetch origin
git checkout release
git pull origin release
```

虚拟机上用这份代码 `docker compose` 部署（与平时相同，只是 Git 分支是 `release`）。

### 4.3 测试通过：用 release 上线

部署用的就是 **`release` 当前代码**，不是 master，也不是 develop。

上线前再确认一次：

```bat
git checkout release
git pull origin release
git log -1 --oneline
```

记下这个 commit 哈希，便于验收对照。

### 4.4 上线验收通过：release 合并进 master

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout master
git pull origin master
git merge release
git push origin master
```

然后回到开发线：

```bat
git checkout develop
```

**不要**再执行 `git merge release` 到 develop。

若验收未通过：继续停在 4.2，在 release 上修，重新部署 release，**不要**先合进 master。

---

## 5. 紧急 / 现网 BUG（hotfix）

develop 上未完成的功能不要带进 hotfix。只带线上已有代码里的那一处修复。

### 5.1 从 master 切开

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout master
git pull origin master
git checkout -b hotfix/简要说明
git push -u origin hotfix/简要说明
```

`简要说明` 用英文或拼音、不要空格，例如 `hotfix/login-cookie`。

### 5.2 在 hotfix 上修、测、部署

```bat
git checkout hotfix/简要说明
```

改代码 → `commit` → `git push origin hotfix/简要说明`。

测试与部署都针对 **这个 hotfix 分支**（流程同 4.2 / 4.3，只是分支名不同）。

### 5.3 上线验收后合进 master

```bat
git checkout master
git pull origin master
git merge hotfix/简要说明
git push origin master
```

删除已结束的 hotfix（避免以后搞混）：

```bat
git checkout develop
git branch -d hotfix/简要说明
git push origin --delete hotfix/简要说明
```

develop 若也需要同一修复：切回 `develop`，**手工改或 cherry-pick**，不要 merge hotfix：

```bat
git checkout develop
git pull origin develop
git cherry-pick <hotfix上那次commit的哈希>
git push origin develop
```

`cherry-pick` 若冲突，在 develop 上解决后再 commit。

---

## 6. 一张对照表（现在该敲哪条）

| 你的情况 | 先切到 | 然后 |
|----------|--------|------|
| 写功能、改评测、改文档 | `develop` | `add` / `commit` / `push origin develop` |
| 第一轮交给测试 | `develop` | `checkout -b release` 再 `push -u origin release` |
| 以后再交给测试 | `release` | `merge develop` 再 `push origin release` |
| 测试说这个版本有 bug | `release` | 只修 bug，push `release` |
| 测试通过并已用 release 上线、验收 OK | `master` | `merge release` 再 `push origin master` |
| 线上突然坏了 | `master` | `checkout -b hotfix/...`，修完测完部署完再 merge 回 master |
| 想把 release 合回 develop | **停止** | 华海禁止 |

---

## 7. 禁止与应急

**禁止**

- 在 `master` 上直接改文件后 commit。
- `git merge develop` 进 `master`。
- `git merge release` 或 `git merge hotfix/...` 进 `develop`。
- 待发布的 `release` 上加需求（新功能回 develop，等下一轮再 merge 进 release）。
- `git push --force` 到 `master` / `release` / `develop`。
- 把 `backend/.env` 加进任何分支。

**看错了分支、还没 commit**

```bat
git restore 文件
git checkout develop
```

**已经 commit 到 master 但还没 push**

```bat
git checkout develop
git cherry-pick <那次提交哈希>
git checkout master
git reset --hard HEAD~1
```

`reset --hard` 会丢掉 master 上那次提交的工作区改动；先确认 develop 已经 cherry-pick 成功。若已经 `push` 到 master，不要 reset，找人按 release/hotfix 补流程，不要 force push。

**合并冲突**

只在当前检出的那条分支上改冲突标记，`git add` 后再 `git commit`（merge 会自动生成说明）。不要删 `.git`。

---

## 8. 与「只有 main」时期的关系

第一次上传时分支名叫 `main`，与华海「生产叫 master」不一致。初始化已把 **同一提交** 放到 `master`，日常开发改到 `develop`。

旧的 `origin/main` 在你改完 GitHub 默认分支后可以删除（见 §2）。删除前不要在 `main` 上再提交。

本地上传、忽略规则、密钥扫描仍见 [`GIT.md`](GIT.md)。本文只补**分支怎么走**。
