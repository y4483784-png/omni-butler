# 日历评分套件（150 题）

本文只定计划。不改产品代码，不写 150 条题目，不改页面脚本。对应 PR 只含本文。

读者是把日历接进现有评分体系的工程师。主路径是正式评测套件：登记、题集契约、统一入口、分数、门禁。页面手测不是分母。

已经定过的，本文照做：

- 套件风格与现有 `kind="eval"` 套件相同，出分数，不把手工走查当主路径。
- 题量正好 **150**。
- 计分套件里用假 Google，不连真实 Google Calendar。
- [`calendar-release-scripts-100.md`](calendar-release-scripts-100.md) 和 [`fixtures/calendar-release-scripts-100.csv`](fixtures/calendar-release-scripts-100.csv) 留作可选抽查，不进分母。
- [`calendar-hard-gates.md`](calendar-hard-gates.md) 的 pytest 硬门禁已经落地。本套是评分层，不把那五条重写成新的 pytest。
- 过去的墙钟可以，只要结果合理。不要求在账户当地 `09:00–11:00` 开跑。
- 模型钟点对不对付不对题，不主导分数。硬检查是：确认前不写、会议和时间块字段、按 id 改期和撤销、空档不把忙时说成有空、备注写入 `description`。

`agent` 套件里 `task_type=calendar` 只看有没有走到日历工具（`backend/app/eval/judges.py`），完成率门禁还把日历题排除在 `core_completion_rate` 之外。路由套件只看 intent / 工具集合。两套都不判卡片、确认和假 Google。本套不往 `agent_tasks.jsonl` 或 `office_tool_routing.jsonl` 加题。

## 1. 接进现有评测系统

登记册是唯一入口。`python scripts/eval.py list` 和 `run` 都读 `backend/app/eval/suites.py` 的 `SUITES`。套件模块必须提供 `build_parser()` 和 `run(args) -> SuiteResult`（`backend/scripts/eval.py` 的 `_run_eval_suite`）。报告写到 `backend/reports/<套件>/`，总索引是 `backend/reports/index.jsonl`。门禁在 `backend/app/eval/gates.py` 的 `GATES`，由 `eval.py` 在 `run()` 返回之后统一判定，套件自己不把退出码设成失败。

落地时同一 PR 改这些文件。只登记、不交题集，会让下面三处同时变红，所以不要拆开提交。

| 接哪里 | 文件 | 落地时做什么 |
| --- | --- | --- |
| 登记 | `backend/app/eval/suites.py` | 增加 id `calendar`，见第 1.1 节 |
| 题集契约 | `backend/app/eval/contracts.py` | `CalendarCase`、`CONTRACTS["calendar"]`、`TAG_VOCAB["calendar"]`、配额检查 |
| 对齐测试 | `backend/tests/test_eval_datasets_contract.py` | `test_contracts_cover_registered_eval_datasets` 要求每个 `kind="eval"` 套件都有契约。不用改测试逻辑 |
| 契约入口 | `backend/scripts/lint_eval_datasets.py` | 已对 `CONTRACTS` 全量校验。不用新入口 |
| 每次 push 的 CI | `.github/workflows/eval.yml` 的 `offline` job | 已经跑 `python scripts/lint_eval_datasets.py` 和 `python -m pytest tests -q`。题集一进 `CONTRACTS`，形状和配额就在每次 push / PR 上被卡住 |
| 门禁表 | `backend/app/eval/gates.py` | `("calendar", "full")` 必须有门禁。`backend/tests/test_eval_gates.py` 的 `test_eval_suites_declare_gates_for_full` 会查 |
| 冒烟档也写门禁 | 同上 | `("calendar", "smoke")` 用同一组地板。该测试只强制 full，但本套两个档都是确定性回放，地板相同 |
| 跑分模块 | `backend/scripts/run_calendar_eval.py` | `build_parser` / `run`。`DATASET` 指向下面的 jsonl |
| 判分 | `backend/app/eval/calendar_behavior.py` | 单题检查与汇总。对标 `backend/app/eval/office_tabular.py` |
| 题集 | `backend/data/eval/calendar_behavior.jsonl` | 正好 150 行。本计划不写这文件 |
| 离线冒烟 workflow | `.github/workflows/eval.yml` 的 `eval-smoke` job | 在 rag / tabular / context 旁边加一行 `python scripts/eval.py run calendar --profile smoke`。该 job 只在 `workflow_dispatch` 跑 |
| 路径差异说明 | `docs/eval.md`、`backend/app/eval/README.md` 第 0b 节 | 加一行：这是给定模型 JSON 之后的产品行为分，不是抽槽准确率，也不是线上 Google |
| 题卡 | `backend/data/eval/DATASETS.md` | 用 `python scripts/lint_eval_datasets.py --card` 生成，不要手改 |
| 隔离库断言 | `backend/tests/test_eval_runner.py` 的 `test_hermetic_suites_isolate_app_db` | 断言 `get_suite("calendar").isolate_app_db is True` |

不放进每次 push 的 `python -m pytest tests -q`。那条已经跑 Plan 2 的日历测试。150 题的分数跑在 `eval.py`，不塞进 pytest 套件。

不新增 `gen_calendar_eval.py`。检索和路由题是种子展开的；本套每一题的模型 JSON 和期望是写死的判断，jsonl 就是源。配额由契约卡住，不靠生成器凑数。

### 1.1 登记项

```python
"calendar": SuiteSpec(
    id="calendar",
    title="日历行为评分",
    kind="eval",
    description=(
        "150 题。假 Google、冻结模型 JSON，走 execute_calendar 和确认接口。"
        "硬检查：确认前不写、会议/时间块、按 id 改删、空档诚实、备注进说明。"
    ),
    requires=(),
    targets=("venv", "services"),
    key_metrics=(
        "raw.hard_pass_rate",
        "raw.gated_pass_rate",
        "raw.slices.confirm_before_write.pass_rate",
        "raw.slices.meeting_block.pass_rate",
        "raw.slices.id_mutate.pass_rate",
        "raw.slices.free_busy.pass_rate",
        "raw.slices.notes_description.pass_rate",
        "raw.slices.error_paths.pass_rate",
        "raw.clock_reasonable_rate",
    ),
    profiles={
        "smoke": ["--split", "smoke"],
        "full": ["--split", "full"],
    },
    module="scripts.run_calendar_eval",
    default_target="venv",
    needs_blob_stub=True,
    isolate_app_db=True,
)
```

`requires` 为空：预检不要求 `LLM_API_KEY`，也不要求 MinIO / Qdrant / sandbox。`isolate_app_db=True` 与 rag、tabular、context 相同，scratch SQLite，不碰线上库。`needs_blob_stub=True` 只为 venv 引导和那三套对齐；本套不读对象存储。

两个档都是确定性回放。报告里 `degraded` 必须是 `false`。`eval.py` 的 `_degradation_reason` 看到 full 档 `degraded=true` 会直接判失败。本套没有「退回关键词」或「规划失败改启发式」这种旁路，不要设这个标志。

`run()` 自己的 `exit_code` 保持 0，和 `backend/scripts/run_office_tabular_eval.py` 一样。过不过由 `GATES` 决定。

命令：

```bash
cd backend
python scripts/eval.py run calendar --profile smoke
python scripts/eval.py run calendar --profile full
python scripts/eval.py last calendar --profile full
```

不需要 `docker compose --profile eval`。venv 目标即可。

## 2. 题的形状

契约用 `contracts.py` 里的 `StrictModel`（`extra="forbid"`）。缺字段、多字段、枚举写错，`lint_eval_datasets.py` 失败。`text_field` 用 `None`：同一句配不同种子是合法的（发布脚本里「把 CRS 周会改到后天三点」就重复过）。唯一性看 `id`，再加自定义检查：`(utterance 串, connection, fault, 种子 key 列表)` 不得重复。

`id` 为 `CAL-001` … `CAL-150`，三位数字，稳定不重排。不要用发布脚本的 `CRS-*`。

`split` 只有 `smoke` 和 `full`。冒烟档只跑 `split=smoke`。完整档跑全部 150，包含冒烟行。这和表格套件「full 含 smoke」一致。

### 2.1 字段

| 字段 | 取值 |
| --- | --- |
| `id` | `CAL-001` … `CAL-150` |
| `split` | `smoke` 或 `full` |
| `category` | 第 3 节 12 个枚举之一。一题一个，配额按这个字段算 |
| `tags` | 只许 `TAG_VOCAB` 里的词。类别名各写一次，另可加 `past_clock`、`disconnected`、`multi_turn`、`all_day` |
| `now` | 无偏移墙钟，缺省整套用 `2026-10-15T10:00:00`。跑分把 `calendar_now` 钉在这一刻，不读进程时钟 |
| `zone` | 缺省 `Asia/Singapore`。已连接时写进账户时区；未连接不发明时区 |
| `connection` | `connected` 或 `disconnected` |
| `fault` | `none`、`blackhole`、`revoke`、`insert_500` |
| `seeds` | 数组，可空。每条含 `key`、`title`、`start_at`、`end_at`、`description`、`location`、`all_day`、`participants`、`external_id`、`local` |
| `turns` | 1–3 轮。每轮含 `utterance` 和 `model` |
| `expect` | 第 2.3 节 |

种子的 `start_at` / `end_at` 是无偏移墙钟。`local=true` 时另插本地行；已连接且 `local=false` 时只出现在假 Google 的 `events.list`。未连接的种子必须 `local=true`，因为没有 Google 可读。

`fault` 只驱动假客户端，对应 `backend/tests/test_calendar_google.py` 里 `FakeGoogle` 已经分得开的旋钮：`blackhole` 是时区 GET 或列表的传输错误 / 5xx（`CalendarTransientError`）；`revoke` 是 401/403 或刷新 400（`CalendarTokenError`）；`insert_500` 是 `events.insert` 返回 500，确认接口仍是现有的写入失败（502，详情含「未保存」），不要说成前两句。

### 2.2 冻结的模型 JSON

一题的 `turns[].model` 四个键都要出现，值可以是 `null`。`null` 表示这一轮不许打到对应调用；打到了，该题记 `unexpected_model_call`，算失败，不算跳过。

| 键 | 喂给 | `null` 的含义 |
| --- | --- | --- |
| `extract_create` | `extract_event(..., existing_event=False)` 看到的 JSON | 这一轮不应走创建抽槽 |
| `extract_update` | `extract_event(..., existing_event=True)` 看到的 JSON | 这一轮不应走改期/撤销抽槽 |
| `list_day` | `extract_list_day` 的 `{"day": str}`。空字符串是合法答案（七天窗口） | 这一轮不应问日期 |
| `free_slots` | `extract_free_slots` 的 `{"slots":[{"start_at","end_at"}],"note"}`。`slots: []` 是合法答案 | 这一轮不应问空档 |
| `advice` | `complete_text` 的建议句 | 不判措辞。列表建议失败时产品会说「暂时给不出建议」，本套不因为这句扣分 |

创建抽槽的键，与 `backend/app/services/calendar.py` 里 `_extract_system_prompt` 的创建格式一致：`title`、`start_at`、`end_at`、`participants`、`missing_fields`、`action`、`day`、`kind`、`description`、`location`。

改期/撤销抽槽多 `change_end`、`change_notes`（布尔），没有 `action` / `day`。`change_end` / `change_notes` 缺省、`false`、空都表示用户没要求改。题里要改时长或备注时必须显式 `true`。模型把结束时间或缩短的备注填上、但旗标不是 `true`，产品会丢掉，题按丢掉之后的卡片判。

`kind` 只许 `""`、`meeting`、`block`。空按会议，这是现有 `_write_kind`。

跑分不调用 `complete_json` / `complete_text` 的真实实现。按系统提示分发：创建/改期提示以「你是日程信息提取器」开头，列表日提示含 `{"day":str}`，空档提示含 `{"slots"`，建议走 `complete_text`。分发对不上该轮的非空键，记 `unexpected_model_call`。

抽槽之后的产品代码照跑，包括 `extract_event` 里的补全（没说结束则开始加 60 分钟、`change_end` 为假则保留原时长）。题的通过条件写在补全之后的卡片和确认结果上，不写在「这句话应该被理解成几点」上。硬检查不得依赖正则把一个空的模型字段补成正确答案；模型 JSON 本身就要够用。

### 2.3 期望与单题怎么算过

`expect` 的键固定如下。没列出的键不检查。

| 键 | 取值 |
| --- | --- |
| `confirm` | `do_not_confirm`、`confirm_create`、`confirm_update`、`confirm_cancel` |
| `before_confirm_google` | 固定 `none` |
| `card.type` | `meeting_create`、`block_create`、`meeting_update`、`block_update`、`cancel`、`list`、`none` |
| `card.phase` | `draft`、`info`、`absent` |
| `card.fields` | 只比较写出的键：`title`、`start_at`、`end_at`、`duration_minutes`、`participants`、`location`、`description`、`kind`、`external_id` |
| `card.suggested_slots` | `absent`、`present_ok`、`non_overlapping` |
| `card.must_not_overlap` | 建议时段不得与这些区间相交。端点相接不算相交 |
| `card.must_list_titles` | 列表卡必须有的标题 |
| `google.op` | `none`、`create`、`update`、`delete` |
| `google.external_id` | `absent`、`new`、`seed:<key>` |
| `google.match` | 确认之后假 Google 体上必须成立的键，允许的键同 `card.fields`，另加 `attendees` |
| `google.unchanged` | 必须原样留下的种子 `key` |
| `http_status` | 确认接口的状态码。不确认的题不写这一键 |
| `copy_must` | 用户看得到的句子里必须有的子串。只用于错误文案，不用于建议好不好 |
| `copy_must_not` | 不得出现的子串 |
| `local_row` | `unchanged`、`inserted`、`updated`、`absent` |

`external_id` 的 `absent` 是卡片上的空字符串。`seed:<key>` 等于该种子的 Google id。`new` 是确认后插入的 id。

一题 **通过**，当且仅当 `expect` 里每一条都成立，并且每一轮在点确认之前 insert / patch / delete 都是空的、本地行的标题、起止、`status` 没变。没有半分。失败样例写入 `samples.jsonl`，带上 `id`、`category`、`failed_checks`。

观察点只有三处，都在进程里：

1. `execute_calendar` 的 `schedule_card` 和 `direct_answer`。
2. 假 Google 的 insert / patch / delete 调用：有没有、打到哪个 id、JSON 里有没有 `attendees`、`description` 是什么。
3. 确认接口的状态码和本地行。已连接的创建是 `POST /api/calendar`，已连接的改期和撤销是 `POST /api/calendar/google/confirm`，未连接是 `PATCH` / `DELETE /api/calendar/{id}`。

不观察页面控件、按钮文案、气泡顺序。那些留在 Plan 1 和已经进门禁的 `frontend/tests/scheduleCard.test.mjs`。

### 2.4 钟点怎么判，以及它为什么不进门禁

钟点只在 `category=wall_clock` 上计算 `clock_reasonable_rate`。分母是这 4 题（冒烟是其中 2 题）。其他类别的卡片时间按 `expect.card.fields` 比较，那些字段来自冻结的模型 JSON 和已经写明的补全规则，不是「这句话的标准答案钟点」。

一题在钟点上合理，当且仅当：

- 卡片上的起止是无偏移墙钟，字符串里没有 `+`，没有 `Z`。
- 创建：结束等于模型的 `end_at`；模型没给结束时，结束是开始加 60 分钟。
- 改期：`change_end` 不为真时，时长等于种子时长，不改成默认一小时。
- 开始时间早于该题的 `now` 时，卡片仍保留这个开始时间。过去不是丢掉、改写或拒绝确认的理由。

不算失败：模型给出的钟点和执行人自己对「明天三点」的理解不一致；开跑时刻不在 `09:00–11:00`；题目用了 `2026-10-14` 这种已经过去的日期。

`clock_reasonable_rate` 写入报告，**不进 `GATES`**。`pass_rate`（150 题）也不进门禁，这样钟点题失败不会单独把套件打红。硬检查和其余产品行为走第 4 节的地板。

## 3. 配额

正好 **150**。下表之和是 150。冒烟列之和是 **30**。

硬检查五类进 `hard_pass_rate`（96 题）。`error_paths` 单独有切片门禁，因为它就是 Plan 2 的 G5，题量小，不能被总均值吞掉。`create_*`、`list_schedule`、`conflict`、`attendees` 算进 `gated_pass_rate`，没有自己的切片门禁。`wall_clock` 只进第 2.4 节的钟点率。

| category | 全量 | 冒烟 | 锁什么 | 进哪一个率 |
| --- | ---: | ---: | --- | --- |
| `confirm_before_write` | 20 | 4 | 聊天创建、改期、撤销、列表、空档都不写；下一句作废草稿；未连接也不写 Google | `hard_pass_rate` |
| `meeting_block` | 18 | 3 | block 无参与人、无地点；空 `kind` 按 meeting；撤销卡不按 kind 换成时间块 | `hard_pass_rate` |
| `id_mutate` | 22 | 4 | 唯一命中预填 `external_id`；确认打到这个 id；同标题无 id 为 409；未知 id 为 409 | `hard_pass_rate` |
| `free_busy` | 20 | 4 | 先列出当天；重叠段丢掉；端点相接留下；午夜段留下；全天不算有空；读失败不编空闲 | `hard_pass_rate` |
| `notes_description` | 16 | 3 | 备注写入 Google `description` 和本地行；空备注可确认；没要求改备注就不截短；撤销确认不改 description | `hard_pass_rate` |
| `create_meeting` | 10 | 2 | 确认后假 Google 与卡片墙钟一致；来宾为空；参与人只在本地 | `gated_pass_rate` |
| `create_block` | 10 | 2 | 确认后时长与卡片一致；无参与人无地点；来宾为空 | `gated_pass_rate` |
| `list_schedule` | 8 | 2 | 只读列表卡，`phase=info`，不写 | `gated_pass_rate` |
| `conflict` | 8 | 1 | 重叠留在卡上，不插入；建议空隙不与已占用相交；端点相接不算冲突 | `gated_pass_rate` |
| `attendees` | 6 | 1 | 会议卡留参与人；时间块清空；Google 体没有 `attendees` | `gated_pass_rate` |
| `error_paths` | 8 | 2 | 瞬时错误与授权失效文案分开；确认 502 / 401 都不写；insert 500 仍是「未保存」 | 切片门禁，也进 `gated_pass_rate` |
| `wall_clock` | 4 | 2 | 无偏移；过去的墙钟仍留在卡上；不要求 09:00–11:00 | 只进 `clock_reasonable_rate` |
| 合计 | **150** | **30** |  |  |

契约检查这些整数，容差是 0。冒烟行必须盖住每一个进门禁的类别，避免某一类在冒烟档分母为 0、切片率缺失被当成通过。

五类硬检查的题，契约还要拒绝「没写对应断言」的行：

| category | `expect` 里必须能看出 |
| --- | --- |
| `confirm_before_write` | `before_confirm_google=none`，并且 `confirm=do_not_confirm` 或确认前检查覆盖每一轮 |
| `meeting_block` | `card.type` 是会议或时间块；block 的 `participants` 与 `location` 被检查 |
| `id_mutate` | `google.external_id` 或 409 的 `http_status` |
| `free_busy` | `card.must_list_titles` 或读失败文案，加上 `suggested_slots` / `must_not_overlap` / `copy_must` 里至少一项 |
| `notes_description` | `card.fields.description` 或 `google.match.description` |

出题时可以借用发布脚本里的场景（同标题两条、全天、空备注），但期望按本节写，不按页面控件写。不要把 CRS 原句整表复制进来充数。

## 4. 分数和门禁

单题二分，和表格套件的 `exec_ok` 一样。汇总进套件报告的顶层（与 tabular、context 相同，门禁的点路径直接读这些键）：

| 键 | 含义 |
| --- | --- |
| `n` | 本档题数。冒烟 30，完整 150 |
| `passed` | 通过题数 |
| `pass_rate` | `passed / n`。报告用，不设门禁 |
| `hard_n` / `hard_pass_rate` | 五类硬检查。完整档分母 96 |
| `gated_n` / `gated_pass_rate` | 除 `wall_clock` 以外的题。完整档分母 146 |
| `n_clock` / `clock_reasonable_rate` | 只含 `wall_clock`。不设门禁 |
| `slices.<category>.n` / `passed` / `pass_rate` | 每一类都要写出，包括该档题数为 0 时也要写出 `n: 0`。冒烟档不会为 0 |
| `degraded` | `false` |
| `split` | `smoke` 或 `full` |

`key_metrics` 用 `raw.` 前缀，因为 `backend/app/eval/report.py` 把套件报告放在 `raw` 下。`GATES` 的路径不加 `raw.`，`evaluate_gates` 读的是 `run()` 返回的那份字典。

地板是 **1.0**，`max_drop=None`。模型不在被测范围里，掉一题就是产品退步，不是抽样波动。不要为了让红的一轮变绿去把地板改成 0.95。检索套件用「基线减 0.05」是因为那里在测模型和检索；本套对标的是 context 的 `invariants.violations` 必须为 0。

`("calendar", "smoke")` 和 `("calendar", "full")` 都写这些门禁：

| metric | min |
| --- | --- |
| `hard_pass_rate` | 1.0 |
| `gated_pass_rate` | 1.0 |
| `slices.confirm_before_write.pass_rate` | 1.0 |
| `slices.meeting_block.pass_rate` | 1.0 |
| `slices.id_mutate.pass_rate` | 1.0 |
| `slices.free_busy.pass_rate` | 1.0 |
| `slices.notes_description.pass_rate` | 1.0 |
| `slices.error_paths.pass_rate` | 1.0 |

总地板已经是 1.0 时，切片门禁看起来重复。仍然要写上。检索套件的教训是：只看总均值时，一小类塌掉仍能过线。以后若有人把 `gated_pass_rate` 放松，五类硬检查和 `error_paths` 还在。放松总地板之前，必须先给 `create_meeting`、`create_block`、`list_schedule`、`conflict`、`attendees` 各自补上 1.0 的切片门禁。

不设门禁的键：`pass_rate`、`clock_reasonable_rate`。也不要给钟点率配 `allow_missing`。冒烟档有 2 道钟点题，率必须出现在报告里。

失败样例进 `SuiteResult.samples`，报告的 failures 列表能点到 `CAL-*` 和 `failed_checks`。修的是产品或写错的题，不是把这题从分母里拿掉。题量必须保持 150；删题就要补题。

## 5. 什么是假的，什么是真的

| | 本套 |
| --- | --- |
| 假 | Google HTTP。跑分替换 `app.services.calendar_secrets._http_request`。行为对齐测试里的 `FakeGoogle`（列表、insert、patch、delete、时区、刷新、500/401）。评测代码不要 import `backend/tests/test_calendar_google.py` |
| 假 | 模型。`complete_json` 和 `complete_text` 按第 2.2 节返回该轮 JSON。不读 `LLM_API_KEY`，不打模型 |
| 真 | `execute_calendar`（`backend/app/agents/tools/calendar_tool.py`） |
| 真 | 抽槽之后的补全、会议/时间块卡片、`_without_busy_overlap`、`_replace_unreliable_free_note` |
| 真 | 确认接口：`POST /api/calendar`、`POST /api/calendar/google/confirm`、未连接的 `PATCH` / `DELETE /api/calendar/{id}` |
| 真 | scratch SQLite 里的本地行和连接行。用户由评测自己建，用完删 |
| 不跑 | 真实 OAuth、真实 Google、Playwright、`run_agent_workflow`、路由 `plan_tools` |

时钟：每题开始把 `calendar_now` 换成该题的 `now`（缺省 `2026-10-15T10:00:00`，时区 `Asia/Singapore`）。这和 Plan 2「沿用 `2026-10-15`、已连接 `Asia/Singapore`」同一天，但分钟钉死，避免进程当地钟和 `09:00–11:00` 窗口。过去的模型时间相对这个 `now` 来判，不相对跑分那天。

已连接用户要有一条 `CalendarConnection`，令牌按现有测试的方式放进假客户端能读的密文。未连接不插连接行，确认走本地接口，假 Google 的 insert/patch/delete 必须仍为空。

列表建议的 `complete_text` 返回 `advice` 或空。空时产品给出「暂时给不出建议」。本套不给这句打分，除非该题的 `copy_must` 写了别的错误文案。

## 6. 和 Plan 1、Plan 2 的关系

三层各管一件事。

| | Plan 1 发布脚本 | Plan 2 pytest | 本套 |
| --- | --- | --- | --- |
| 文档 | `docs/plans/calendar-release-scripts-100.md`，题单 CSV | `docs/plans/calendar-hard-gates.md` | 本文。题集落地后在 `backend/data/eval/calendar_behavior.jsonl` |
| 题量 | 100，分母是页面 | 五条门禁，点名到现有测试函数 | 150，分母是本套 |
| 怎么跑 | 人，或对着真实页面的 Playwright | `python -m pytest` 点名的日历测试；offline job 跑整个 `tests/` | `python scripts/eval.py run calendar` |
| Google | 真日历 | 假 | 假 |
| 模型 | 真模型，钟点对不上算 FAIL | 测试给定 JSON | 题内冻结 JSON |
| 开跑窗口 | 账户当地 `09:00–11:00` | 无 | 无。`now` 钉在题里 |
| 是不是合并门槛 | 不是日历代码 PR 的完成条件 | 是。改日历代码仍跑 Plan 2 第 4 节的命令 | 是评分回归。不替代那条 pytest 命令 |

Plan 1 保持可选抽查。抽查可以在任意钟点做，不必守发布脚本里的窗口。抽查结果不写进 `pass_rate`，也不改 150 这个分母。

Plan 2 已经锁住的测试（`test_create_phrase_returns_draft_and_writes_nothing`、`test_confirm_prefers_prefilled_external_id_over_exact_title`、`test_free_slot_note_does_not_call_a_busy_hour_free`、`test_create_notes_are_written_to_google_description` 等）保持原样。本套可以检查同一批可观察结果：确认前调用列表为空、卡片字段、409、安全说明「请以上面的列表为准，已有安排的时段不是空闲。」、瞬时文案与「重新连接」分开。检查写在 `calendar_behavior.py`，用产品返回值和假客户端记录，不调用测试函数，也不把这 150 题再包一层 pytest。

日历代码 PR 的完成条件仍是 Plan 2 第 4 节。本套变红时，先看是产品退步还是题写错了当前行为；不要为了套件去放宽 Plan 2 的断言。

## 7. 批准之后的落地顺序

每一阶段单独 PR，都对着 `release`。阶段 1 之前不要登记 `calendar`，否则契约对齐和 full 门禁测试会红。

1. **判分与夹具，先不登记。** 加上 `backend/app/eval/calendar_behavior.py` 和一小段 pytest：五行以内的内联题，五类硬检查各一题，外加一题过去墙钟。断言假 Google 没被打到网络、确认前无写入、block 字段、id、空档说明、description。这一步不改 `SUITES`、`CONTRACTS`、`GATES`。
2. **150 题和登记同一 PR。** 写入 `calendar_behavior.jsonl`（正好 150，冒烟 30），并同时改第 1 节表格里的登记、契约、门禁、`run_calendar_eval.py`、`docs/eval.md`、`backend/app/eval/README.md`、`eval.yml` 的 `eval-smoke` 一步、`test_hermetic_suites_isolate_app_db`。跑 `python scripts/lint_eval_datasets.py --card` 更新 `DATASETS.md`。本地跑通 smoke 和 full。硬检查和 `gated_pass_rate` 应当是 1.0，因为题只描述已经落地的行为。某题红了：题写超出现有行为就改题；产品退步就另开产品 PR，本 PR 不把地板降到 1.0 以下，也不把题从 150 里删掉而不补。
3. **记下第一轮完整档。** 报告在 `backend/reports/calendar/`。数字是行为分，引用时带上「冻结模型 JSON、假 Google」。不把这一轮当成模型能力。
4. **不增加活模型档，不增加真 Google 档。** 若以后要看抽槽质量，另立套件，不得把钟点准确率乘进本套的 `hard_pass_rate`。

阶段 2 的 PR 说明里写明两条本地命令已过：

```bash
cd backend
python scripts/lint_eval_datasets.py
python scripts/eval.py run calendar --profile smoke
python scripts/eval.py run calendar --profile full
```

## 8. 不在本文范围

- 撰写 150 条 jsonl，或任何产品代码、假客户端、登记补丁。那些属于第 7 节。
- 改 Plan 1 的 100 条原句、CSV，或把它接进 CI。
- 重写 Plan 2 已经落地的 pytest，或把 `scheduleCard.test.mjs` 再改一遍。
- 活模型、活 Google、OAuth 同意屏幕、Playwright。手测步骤仍在 `docs/plans/calendar-google-manual-acceptance.md`。
- 用正则纠正模型钟点，或要求套件在 `09:00–11:00` 跑。
- 把日历题并进 `agent` 的完成率门禁。那里的日历题继续只看路由。
