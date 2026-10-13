# 从 GitHub 拿掉 YZ 测试夹具里不该公开的文件

给「已经 push 上去、仓库还曾公开」用。终端用 **CMD**，目录必须是：

```bat
cd /d d:\Omni-Butler\omni-butler
```

## 0. 这件事要解决什么

`backend/tests/YZ测试文档/` 里现在有多种文件。对外（GitHub）**最多只留**：

- `测试用例.md`（虚构产品指南，评测要用）
- `测试用例.txt`（虚构制度样例，评测要用）

**必须从 GitHub 上消失（含历史）的：**

| 文件 | 为什么不能公开 |
|------|----------------|
| `测试用例.pdf` | 信通院 2026 年研究报告，有版权声明，不是测试样例 |
| `测试用例.xlsx` | 销售明细表，评测代码不用 |
| `测试用例.csv` | 工号/姓名/考勤，即使像合成数据也不再对外 |

本机硬盘上 csv/xlsx/pdf **可以继续留着**（改完 Git 不会自动删你磁盘上的文件），只是以后 `git add` 加不进去。

---

## 为什么不能只「删文件再 push 一次」

Git 像相册：最新一张照片撕掉，旧相册里还有。

PDF 是在第一次提交 `8cf0ab1` 里上去的。你只在现在的 `develop` 里删除，别人仍可以：

1. 打开 GitHub → Commits → 点最初那次提交
2. 或 `git clone` 之后 `git checkout 8cf0ab1`

所以必须做两件事：

1. **改成 Private**：正在公开的网页立刻锁上  
2. **改写历史再强推**：让 GitHub 上的旧提交里也没有这些文件  

日常禁止 `git push --force` 到 `master`。这次是误传版权材料和名单，属于例外。

---

## 第 1 步：网页改成私有（先做，1 分钟）

**意思：** 没登录的人不能再打开仓库、不能再下载 PDF。不改这一步就强推，改写过程中旧地址短暂仍可能被拉。

1. 浏览器打开 https://github.com/y4483784-png/omni-butler/settings  
2. 拉到最下面 **Danger Zone**  
3. **Change repository visibility** → **Private** → 确认  
4. 看仓库页右上角应有一把锁  

做完再做第 4 步强推。第 2、第 3 步可以先在本机做。

---

## 第 2 步：告诉 Git「以后不要再跟踪这些文件」（已在仓库里改好）

**意思：** `.gitignore` 是一份黑名单。列出的文件以后 `git add -A` 也不会提交。

已写入：

- `backend/tests/YZ测试文档/*.csv`
- `backend/tests/YZ测试文档/*.xlsx`
- `backend/tests/YZ测试文档/*.pdf`
- `backend/tests/YZ测试文档/*.docx`

`*.docx` 本来就在总 ignore 里；这里再写一层，避免有人 `git add -f`。

`.gitignore` **管不了已经提交过的文件**，所以还要第 3、第 4 步。

---

## 第 3 步：从「当前分支的快照」里取消跟踪（不删你电脑上的文件）

**意思：** `git rm --cached` = 只从 Git 索引拿掉，磁盘上的 csv/pdf/xlsx 还在，本机评测仍能读 csv。

```bat
cd /d d:\Omni-Butler\omni-butler
git checkout develop
git pull origin develop

git rm --cached "backend/tests/YZ测试文档/测试用例.pdf"
git rm --cached "backend/tests/YZ测试文档/测试用例.xlsx"
git rm --cached "backend/tests/YZ测试文档/测试用例.csv"

git add .gitignore docs/YZ_GITHUB.md backend/app/eval/README.md
git status
```

`git status` 里这三项应显示为 **deleted**（对仓库而言），你的资源管理器里文件还在。

```bat
git commit -m "Stop tracking YZ csv/xlsx/pdf; GitHub keeps md and txt only."
```

**这一步还没有清掉第一次提交里的 PDF。** 必须做第 4 步。

---

## 第 4 步：改写全部历史（把旧提交里的文件也挖掉）

**意思：** 从每一个曾经存在的 commit 里删除那三个路径，等于重写相册里每一页。提交哈希会变（`8cf0ab1` 不再是原来那个）。

先确认当前在仓库根：

```bat
cd /d d:\Omni-Butler\omni-butler
```

下面这条会改本地所有分支的历史，跑完之前不要关窗口：

```bat
git filter-branch --force --index-filter "git rm --cached --ignore-unmatch \"backend/tests/YZ测试文档/测试用例.pdf\" \"backend/tests/YZ测试文档/测试用例.xlsx\" \"backend/tests/YZ测试文档/测试用例.csv\"" --prune-empty --tag-name-filter cat -- --all
```

拆开看：

| 片段 | 意思 |
|------|------|
| `filter-branch` | 对历史里每一个提交重做一遍索引 |
| `--index-filter` | 不检出工作区，只改暂存清单，比较快 |
| `git rm --cached --ignore-unmatch ...` | 每个提交里若有这三个文件就拿掉；没有也不报错 |
| `--prune-empty` | 若某次提交删完后变成空的，丢掉该提交 |
| `-- --all` | 本地每一个分支都处理（`master` 和 `develop`） |

若提示 `Cannot create a new backup` 或已跑过一次，加上：

```bat
git filter-branch -f --index-filter "git rm --cached --ignore-unmatch \"backend/tests/YZ测试文档/测试用例.pdf\" \"backend/tests/YZ测试文档/测试用例.xlsx\" \"backend/tests/YZ测试文档/测试用例.csv\"" --prune-empty --tag-name-filter cat -- --all
```

跑完检查（不应再打印 pdf/xlsx/csv）：

```bat
git log --all --name-only --pretty=format: -- "backend/tests/YZ测试文档"
```

应只看到 `测试用例.md` 和 `测试用例.txt`（以及你后来改的 ignore/文档）。

再检查忽略是否生效：

```bat
git check-ignore -v "backend/tests/YZ测试文档/测试用例.pdf"
git check-ignore -v "backend/tests/YZ测试文档/测试用例.csv"
```

应有输出。`测试用例.md` 执行同一命令应**没有**输出。

---

## 第 5 步：强推到 GitHub（覆盖远程旧历史）

**前提：** 第 1 步已经是 Private。

**意思：** 普通 `git push` 会被拒绝，因为本地历史和 GitHub 对不上。`--force` 表示「用我改写后的分支覆盖网上的同名分支」。

```bat
git checkout develop
git push --force origin develop
git push --force origin master
```

若远程还有 `main`：

```bat
git push origin --delete main
```

（须已在网页把 Default branch 改成 `master`，否则删不掉默认分支。）

---

## 第 6 步：验收

浏览器打开（须已登录）：

https://github.com/y4483784-png/omni-butler/tree/develop/backend/tests/YZ测试文档

只应看到 md、txt。点仓库 **Commits** 里最早一次，进去同样不应能下载 pdf/xlsx/csv。

本机：

```bat
dir "backend\tests\YZ测试文档"
```

你自己电脑上仍可能有 csv/pdf，这是正常的，只是不会再被提交。

---

## 不要做的

- 仓库还是 Public 时就 `--force`  
- 用另一份正版报告重新命名成 `测试用例.pdf` 再提交  
- `git add -f` 把 csv/pdf 加回去  
- 为这事去改 LLM key（这次泄漏的不是 `.env`）

日常开发仍在 `develop` 上改代码，见 [`BRANCHING.md`](BRANCHING.md)。
