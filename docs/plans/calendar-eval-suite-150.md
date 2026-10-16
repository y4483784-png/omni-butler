# 日历评分套件（150 题）

本文只定计划。不改产品代码，不写 150 条题目，不改页面脚本。对应 PR 只含本文。

读者是把日历接进现有评分体系的工程师。主路径是正式评测套件：登记、题集契约、统一入口、分数、门禁。页面手测不是分母。

已经定过的，本文照做：

- 套件风格与现有 `kind="eval"` 套件相同，出分数，不把手工走查当主路径。
- 题量正好 **150**。套件 id 是 `calendar`。
- 计分走真实模型：产品里的 `complete_json` / `complete_text`。题里不放模型 JSON，跑分也不拿作者写的 JSON 去替模型。
- 计分套件里用假 Google，不连真实 Google Calendar。
- [`calendar-release-scripts-100.md`](calendar-release-scripts-100.md) 和 [`fixtures/calendar-release-scripts-100.csv`](fixtures/calendar-release-scripts-100.csv) 留作可选抽查，不进分母。
- [`calendar-hard-gates.md`](calendar-hard-gates.md) 的 pytest 硬门禁已经落地。本套是评分层，不把那五条重写成新的 pytest。
- `now` 钉在题里（缺省 `2026-10-15T10:00:00`，`Asia/Singapore`）。不要求在账户当地 `09:00–11:00` 开跑。过去的墙钟可以出现在原句里；卡片必须等于该句的金标准，不能因为早于 `now` 就改写或算通过。
- 原句把时间说全时，卡片起止必须与金标准完全相同。错一个钟点就是这题失败。没有「模型漂移仍算合理」的软分。

`agent` 套件里 `task_type=calendar` 只看有没有走到日历工具（`backend/app/eval/judges.py`），完成率门禁还把日历题排除在 `core_completion_rate` 之外。路由套件只看 intent / 工具集合。两套都不判卡片、确认和假 Google。本套不往 `agent_tasks.jsonl` 或 `office_tool_routing.jsonl` 加题。

## 1. 接进现有评测系统

登记册是唯一入口。`python scripts/eval.py list` 和 `run` 都读 `backend/app/eval/suites.py` 的 `SUITES`。套件模块必须提供 `build_parser()` 和 `run(args) -> SuiteResult`（`backend/scripts/eval.py` 的 `_run_eval_suite`）。报告写到 `backend/reports/<套件>/`，总索引是 `backend/reports/index.jsonl`。门禁在 `backend/app/eval/gates.py` 的 `GATES`，由 `eval.py` 在 `run()` 返回之后统一判定。

落地时同一 PR 改这些文件。只登记、不交题集，会让契约对齐和 full 门禁测试同时变红，所以不要拆开提交。

| 接哪里 | 文件 | 落地时做什么 |
| --- | --- | --- |
| 登记 | `backend/app/eval/suites.py` | 增加 id `calendar`，见第 1.1 节 |
| 题集契约 | `backend/app/eval/contracts.py` | `CalendarCase`、`CONTRACTS["calendar"]`、`TAG_VOCAB["calendar"]`、配额检查 |
| 对齐测试 | `backend/tests/test_eval_datasets_contract.py` | `test_contracts_cover_registered_eval_datasets` 要求每个 `kind="eval"` 套件都有契约。不用改测试逻辑 |
| 契约入口 | `backend/scripts/lint_eval_datasets.py` | 已对 `CONTRACTS` 全量校验。不用新入口 |
| 每次 push 的 CI | `.github/workflows/eval.yml` 的 `offline` job | 已经跑 `python scripts/lint_eval_datasets.py` 和 `python -m pytest tests -q`。题集一进 `CONTRACTS`，形状和配额就在每次 push / PR 上被卡住 |
| 门禁表 | `backend/app/eval/gates.py` | `("calendar", "full")` 必须有门禁。`backend/tests/test_eval_gates.py` 的 `test_eval_suites_declare_gates_for_full` 会查 |
| 冒烟档也写门禁 | 同上 | `("calendar", "smoke")` 用同一组地板 |
| 跑分模块 | `backend/scripts/run_calendar_eval.py` | `build_parser` / `run`。`DATASET` 指向下面的 jsonl |
| 判分 | `backend/app/eval/calendar_behavior.py` | 单题检查与汇总。对标 `backend/app/eval/office_tabular.py` 的通过/失败，模型调用对标 `backend/app/eval/tool_routing.py` |
| 题集 | `backend/data/eval/calendar_behavior.jsonl` | 正好 150 行。本计划不写这文件 |
| 模型缓存 | `backend/data/eval/calendar_model_cache.json` | 只存真实 `complete_json` / `complete_text` 的返回。不是金标准，作者不手写条目。第一轮完整档之后才提交 |
| 预检 | `backend/scripts/eval.py` 的 `preflight` | `requires` 含 `llm`。`--require-cache` 时不要求 `LLM_API_KEY`（缓存重放不打模型）。今天路由冒烟仍会因缺钥匙在预检失败；本套不要照抄这个缺口 |
| 离线冒烟 workflow | `.github/workflows/eval.yml` 的 `eval-smoke` job | 缓存文件进仓库之后，再加 `python scripts/eval.py run calendar --profile smoke`。该 job 把钥匙设成空，所以这一行不能先于缓存 |
| 路径差异说明 | `docs/eval.md`、`backend/app/eval/README.md` 第 0b 节 | 加一行：真实抽槽 + 假 Google。数字是「模型抽出的卡片 / 确认结果对不对」，不是线上 Google |
| 题卡 | `backend/data/eval/DATASETS.md` | 用 `python scripts/lint_eval_datasets.py --card` 生成，不要手改 |
| 隔离库断言 | `backend/tests/test_eval_runner.py` 的 `test_hermetic_suites_isolate_app_db` | 断言 `get_suite("calendar").isolate_app_db is True` |

不放进每次 push 的 `python -m pytest tests -q`。那条已经跑 Plan 2 的日历测试。150 题的分数跑在 `eval.py`。

不新增 `gen_calendar_eval.py`。jsonl 就是源。配额由契约卡住。

### 1.1 登记项

```python
"calendar": SuiteSpec(
    id="calendar",
    title="日历行为评分",
    kind="eval",
    description=(
        "150 题。真实 complete_json / complete_text，假 Google。"
        "时间说全时卡片起止必须与金标准相同。"
        "另查确认前不写、会议/时间块、按 id 改删、空档诚实、备注进说明。"
    ),
    requires=("llm",),
    targets=("venv", "services"),
    key_metrics=(
        "raw.pass_rate",
        "raw.hard_pass_rate",
        "raw.clock_exact_rate",
        "raw.slices.confirm_before_write.pass_rate",
        "raw.slices.meeting_block.pass_rate",
        "raw.slices.id_mutate.pass_rate",
        "raw.slices.free_busy.pass_rate",
        "raw.slices.notes_description.pass_rate",
        "raw.slices.wall_clock.pass_rate",
        "raw.slices.error_paths.pass_rate",
        "raw.infra_error_rate",
    ),
    profiles={
        "smoke": ["--split", "smoke", "--require-cache"],
        "full": ["--split", "full", "--refresh-cache"],
    },
    module="scripts.run_calendar_eval",
    default_target="venv",
    needs_blob_stub=True,
    isolate_app_db=True,
)
```

`isolate_app_db=True` 与 rag、tabular、context 相同，scratch SQLite，不碰线上库。`needs_blob_stub=True` 只为 venv 引导和那三套对齐；本套不读对象存储。

完整档打真实模型并重写缓存，对标路由的 `--refresh-cache`。冒烟档只重放这份缓存，对标路由的 `--require-cache`：指纹对不上就 `exit_code=2`（`blocked`），不当成模型得了 0 分，也不改用作者写的 JSON。`eval.py` 在退出码 2 时跳过门禁，仍写报告。

报告记录 `model`（`complete_json` 实际使用的 `resolved_planner_model()`）和 `mode`（`live` 或 `cache`）。完整档若没打到模型（`mode` 不是 `live`），`degraded=true`。`eval.py` 的 `_degradation_reason` 会因此判失败。冒烟档的缓存重放是这个档的定义，不是降级。

`run()` 自己的 `exit_code` 在正常出分时保持 0。过不过由 `GATES` 决定。缓存缺失是 2。

命令：

```bash
cd backend
python scripts/eval.py run calendar --profile full    # 要 LLM_API_KEY，重写缓存
python scripts/eval.py run calendar --profile smoke   # 只读缓存
python scripts/eval.py last calendar --profile full
```

不需要 `docker compose --profile eval`。venv 目标即可。不需要真实 Google。

## 2. 题的形状

契约用 `contracts.py` 里的 `StrictModel`（`extra="forbid"`）。缺字段、多字段、枚举写错，`lint_eval_datasets.py` 失败。`text_field` 用 `None`：同一句配不同种子是合法的。唯一性看 `id`，再加自定义检查：`(utterance 串, connection, fault, 种子 key 列表)` 不得重复。

`id` 为 `CAL-001` … `CAL-150`，三位数字，稳定不重排。不要用发布脚本的 `CRS-*`。

`split` 只有 `smoke` 和 `full`。冒烟档只跑 `split=smoke`。完整档跑全部 150，包含冒烟行。这和表格套件「full 含 smoke」一致。

题里没有模型返回值字段。契约拒绝名为 `model`、`extract_create`、`extract_update`、`free_slots` 的键。

### 2.1 字段

| 字段 | 取值 |
| --- | --- |
| `id` | `CAL-001` … `CAL-150` |
| `split` | `smoke` 或 `full` |
| `category` | 第 3 节 12 个枚举之一。一题一个，配额按这个字段算 |
| `tags` | 只许 `TAG_VOCAB` 里的词。类别名各写一次，另可加 `past_clock`、`disconnected`、`multi_turn`、`all_day` |
| `time_complete` | 布尔。原句是否把要核对的起止说全。见第 2.4 节 |
| `now` | 无偏移墙钟，缺省整套用 `2026-10-15T10:00:00`。跑分把 `calendar_now` 钉在这一刻，不读进程时钟 |
| `zone` | 缺省 `Asia/Singapore`。已连接时写进账户时区；未连接不发明时区 |
| `connection` | `connected` 或 `disconnected` |
| `fault` | `none`、`blackhole`、`revoke`、`insert_500` |
| `seeds` | 数组，可空。每条含 `key`、`title`、`start_at`、`end_at`、`description`、`location`、`all_day`、`participants`、`external_id`、`local` |
| `turns` | 1–3 句原话，按顺序发给 `execute_calendar`。只有 `utterance`，没有模型 JSON |
| `expect` | 第 2.3 节。金标准由出题人按原句和 `now` 写死 |

种子的 `start_at` / `end_at` 是无偏移墙钟。`local=true` 时另插本地行；已连接且 `local=false` 时只出现在假 Google 的 `events.list`。未连接的种子必须 `local=true`，因为没有 Google 可读。

`fault` 只驱动假客户端，对应 `backend/tests/test_calendar_google.py` 里 `FakeGoogle` 已经分得开的旋钮：`blackhole` 是时区 GET 或列表的传输错误 / 5xx（`CalendarTransientError`）；`revoke` 是 401/403 或刷新 400（`CalendarTokenError`）；`insert_500` 是 `events.insert` 返回 500，确认接口仍是现有的写入失败（502，详情含「未保存」），不要说成前两句。

### 2.2 模型怎么打

跑分不替换 `complete_json` / `complete_text` 的返回值。`execute_calendar` 照旧走到 `extract_event`、`extract_list_day`、`extract_free_slots`、`_planning_advice`，由它们调用 `backend/app/core/llm.py`。

`complete_json` 在调用方不传 `model` 时用 `resolved_planner_model()`。本套不另选一个模型，也不另做判分模型。报告里的 `model` 就是这个名字。

传输失败和抽错不是一回事，对齐 `complete_json` 自己的注释：评测调用方打开 `raise_on_error=True`，超时、限流、连接失败变成 `LLMInfraError`。

- 包装层只做这一件事：同样的 messages 发给真实实现，`raise_on_error=True`，捕获 `LLMInfraError` 后把该题标成 `infra_error`。包装层不读题里的期望，不填 JSON。
- `infra_error` 不进 `pass_rate` / `hard_pass_rate` / `clock_exact_rate` 的分母，单独报 `infra_error_rate`。
- 非法 JSON、空抽取、钟点不对、字段不对，都留在分母里，该题失败。
- 没有钥匙且不是 `--require-cache`：预检失败，退出码 2，不出一个「全失败」的分数。

缓存对标 `backend/data/eval/router_cache.json`。键是模型名、抽槽提示词、原句、`now`、时区的指纹。值是该次真实返回。完整档 `--refresh-cache` 忽略旧值并写回。冒烟档 `--require-cache` 缺一条或指纹变了就整轮 blocked。提示词一改，冒烟必须先重跑完整档。不许把金标准抄进缓存来让题变绿。

列表建议的措辞不判，除非该题的 `copy_must` 写了错误文案。建议模型失败时产品说「暂时给不出建议」，这句本身不扣分。空档题要判的是产品有没有把已占用时段说成有空，不是建议好不好。

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

`time_complete=true` 时，`card.fields` 必须带 `start_at` 和 `end_at`。两者都是无偏移的 `YYYY-MM-DDTHH:MM:00`。契约拒绝缺这俩、或字符串里有 `+` / `Z` 的行。

一题 **通过**，当且仅当 `expect` 里每一条都成立，并且每一轮在点确认之前 insert / patch / delete 都是空的、本地行的标题、起止、`status` 没变。没有半分。失败样例写入 `samples.jsonl`，带上 `id`、`category`、`failed_checks`。钟点不符的检查名是 `clock_exact`。

观察点只有三处，都在进程里：

1. `execute_calendar` 的 `schedule_card` 和 `direct_answer`。
2. 假 Google 的 insert / patch / delete 调用：有没有、打到哪个 id、JSON 里有没有 `attendees`、`description` 是什么。
3. 确认接口的状态码和本地行。已连接的创建是 `POST /api/calendar`，已连接的改期和撤销是 `POST /api/calendar/google/confirm`，未连接是 `PATCH` / `DELETE /api/calendar/{id}`。

不观察页面控件、按钮文案、气泡顺序。那些留在 Plan 1 和已经进门禁的 `frontend/tests/scheduleCard.test.mjs`。

### 2.4 时间说全时，卡片必须相等

`now` 的公历日是 `D0`。出题人用下面的读法把原句写成绝对墙钟，写进 `expect.card.fields`。跑分只比较卡片和这份金标准，不比「模型自己的 JSON 和卡片一不一致」。

| 原句 | 金标准 |
| --- | --- |
| 今天 | `D0` |
| 明天 | `D0+1` |
| 后天 | `D0+2` |
| 大后天 | `D0+3` |
| 今晚 | `D0` 的 20:00 |
| 早上九点、上午九点 | 09:00 |
| 上午十点、十一点 | 10:00、11:00 |
| 中午十二点 | 12:00 |
| 下午三点、四点 | 15:00、16:00 |
| 晚上七点、八点 | 19:00、20:00 |
| 半小时、一小时、两小时、三小时 | 30、60、120、180 分钟 |

创建句说了开始，没说结束也没说时长：结束 = 开始 + 60 分钟。这是现有草稿补全。金标准就是这个结束。模型给了别的长度，题失败。不许为了让题变绿，在产品里加正则去改卡片。

改期句只搬开始、没有要求改结束或备注：结束 = 新开始 + 种子时长，备注 = 种子备注全文。

原句把日期和开始钟点都说全，或改期句把新的开始说全，则 `time_complete=true`。卡片的 `start_at` / `end_at` 必须与金标准逐字相同。差一分钟就是失败。不设容差，不设「接近也算」，不把模型的返回值当成金标准。

原句点名的时间早于该题 `now` 时，金标准就是那个过去的墙钟。卡片必须等于它。早于 `now` 不是丢掉、改写、拒绝确认的理由，也不是放宽相等的理由。这类题加 tag `past_clock`。

`time_complete=false` 的题（只读列表、没有点名钟点的空档、纯错误路径）不写 `start_at` / `end_at`，不进 `clock_exact_rate`。空档题仍然硬判：重叠段必须丢掉，说明不得把丢掉的忙时说成有空。原句如果点名了某一段（「明天三点到四点有空吗」），这一段就是说全的时间，`time_complete=true`，建议或回复不得把这段改成别的钟点，也不得在它与已有日程重叠时仍说它有空。

`wall_clock` 这 4 题全部 `time_complete=true`，其中至少 1 题带 `past_clock`，金标准早于该题 `now`。它们进 `hard_pass_rate`，并有自己的切片门禁。不单列一个不挡门禁的软分。

开跑时刻不在 `09:00–11:00` 不影响金标准，因为 `now` 在题里，不读跑分那天的钟。

## 3. 配额

正好 **150**。下表之和是 150。冒烟列之和是 **30**。

`hard_pass_rate` 的分母是 100：原先五类 96 题，加上 `wall_clock` 4 题。`clock_exact_rate` 的分母是全部 `time_complete` 的题，跨类别，不只有这 4 题。`pass_rate` 的分母是本档里非 `infra_error` 的题，150 题都在里面。

| category | 全量 | 冒烟 | 锁什么 | 进哪一个率 |
| --- | ---: | ---: | --- | --- |
| `confirm_before_write` | 20 | 4 | 聊天创建、改期、撤销、列表、空档都不写；下一句作废草稿；未连接也不写 Google | `hard_pass_rate` |
| `meeting_block` | 18 | 3 | block 无参与人、无地点；空 `kind` 按 meeting；撤销卡不按 kind 换成时间块 | `hard_pass_rate` |
| `id_mutate` | 22 | 4 | 唯一命中预填 `external_id`；确认打到这个 id；同标题无 id 为 409；未知 id 为 409 | `hard_pass_rate` |
| `free_busy` | 20 | 4 | 先列出当天；重叠段丢掉；端点相接留下；午夜段留下；全天不算有空；读失败不编空闲 | `hard_pass_rate` |
| `notes_description` | 16 | 3 | 备注写入 Google `description` 和本地行；空备注可确认；没要求改备注就不截短；撤销确认不改 description | `hard_pass_rate` |
| `wall_clock` | 4 | 2 | 说全的起止与卡片逐字相同，含至少 1 题过去的墙钟；无偏移 | `hard_pass_rate`，并进入 `clock_exact_rate` |
| `create_meeting` | 10 | 2 | 说全的墙钟与卡片、假 Google 一致；来宾为空；参与人只在本地 | `pass_rate`，说全的题同时进 `clock_exact_rate` |
| `create_block` | 10 | 2 | 说全的时长与卡片一致；无参与人无地点；来宾为空 | 同上 |
| `list_schedule` | 8 | 2 | 只读列表卡，`phase=info`，不写 | `pass_rate` |
| `conflict` | 8 | 1 | 重叠留在卡上，不插入；建议空隙不与已占用相交；端点相接不算冲突 | `pass_rate`；点名了时段的题进 `clock_exact_rate` |
| `attendees` | 6 | 1 | 会议卡留参与人；时间块清空；Google 体没有 `attendees` | `pass_rate`；说全了时间的题进 `clock_exact_rate` |
| `error_paths` | 8 | 2 | 瞬时错误与授权失效文案分开；确认 502 / 401 都不写；insert 500 仍是「未保存」 | 切片门禁，也进 `pass_rate` |
| 合计 | **150** | **30** |  |  |

契约检查这些整数，容差是 0。冒烟行必须盖住每一个进门禁的类别。`wall_clock` 的 4 行全部 `time_complete=true`，至少 1 行 tag 为 `past_clock`。`create_meeting` 和 `create_block` 全部 `time_complete=true`，因为这两类就是在锁说全的创建。

硬检查的题，契约还要拒绝「没写对应断言」的行：

| category | `expect` 里必须能看出 |
| --- | --- |
| `confirm_before_write` | `before_confirm_google=none`，并且 `confirm=do_not_confirm` 或确认前检查覆盖每一轮 |
| `meeting_block` | `card.type` 是会议或时间块；block 的 `participants` 与 `location` 被检查 |
| `id_mutate` | `google.external_id` 或 409 的 `http_status` |
| `free_busy` | `card.must_list_titles` 或读失败文案，加上 `suggested_slots` / `must_not_overlap` / `copy_must` 里至少一项 |
| `notes_description` | `card.fields.description` 或 `google.match.description` |
| `wall_clock` | `time_complete=true`，且 `start_at` / `end_at` 都在 |

出题时可以借用发布脚本里的场景（同标题两条、全天、空备注），但期望按本节写，不按页面控件写。不要把 CRS 原句整表复制进来充数。金标准按第 2.4 节从原句算，不从某次模型输出倒推。

## 4. 分数和门禁

单题二分。传输失败的题不进下面各率的分母。汇总进套件报告的顶层（与 tabular、context 相同，门禁的点路径直接读这些键）：

| 键 | 含义 |
| --- | --- |
| `n` | 本档题数。冒烟 30，完整 150 |
| `n_scored` | `n` 减去 `infra_error` |
| `passed` | 通过题数 |
| `pass_rate` | `passed / n_scored`。全部分母，含钟点题 |
| `hard_n` / `hard_pass_rate` | 六类硬检查（五类行为 + `wall_clock`）。完整档分母最多 100，再减去其中的 infra |
| `n_clock` / `clock_exact_rate` | 全部 `time_complete` 且非 infra 的题。卡片起止与金标准相同的比例 |
| `slices.<category>.n` / `passed` / `pass_rate` | 每一类都要写出。冒烟档不会为 0 |
| `infra_error_rate` | 传输失败题 / `n` |
| `model` | `resolved_planner_model()` |
| `mode` | `live` 或 `cache` |
| `degraded` | 完整档不是 `live` 时为 `true` |
| `split` | `smoke` 或 `full` |

`key_metrics` 用 `raw.` 前缀，因为 `backend/app/eval/report.py` 把套件报告放在 `raw` 下。`GATES` 的路径不加 `raw.`。

地板是 **1.0**，`max_drop=None`。错的钟点留在分母里，所以不能靠「基线减 0.05」给模型漂移留预算。检索和路由的 0.05 是它们的指标定义；本套的钟点指标是相等或不相等。第一轮不满 1.0 时，改提示词、改产品或改正写错的金标准。不把 `clock_exact_rate`、`slices.wall_clock.pass_rate` 或 `pass_rate` 降到 1.0 以下。不把失败题移出 `time_complete`。

`infra_error_rate` 的天花板是 0.05，`max_drop=None`，与 `agent` 套件相同。全是传输失败时通过率不得记成 1。

`("calendar", "smoke")` 和 `("calendar", "full")` 都写这些门禁：

| metric | 界限 |
| --- | --- |
| `pass_rate` | min 1.0 |
| `hard_pass_rate` | min 1.0 |
| `clock_exact_rate` | min 1.0 |
| `slices.confirm_before_write.pass_rate` | min 1.0 |
| `slices.meeting_block.pass_rate` | min 1.0 |
| `slices.id_mutate.pass_rate` | min 1.0 |
| `slices.free_busy.pass_rate` | min 1.0 |
| `slices.notes_description.pass_rate` | min 1.0 |
| `slices.wall_clock.pass_rate` | min 1.0 |
| `slices.error_paths.pass_rate` | min 1.0 |
| `infra_error_rate` | max 0.05 |

总地板已经是 1.0 时，切片门禁看起来重复。仍然要写上。只看总均值时，4 道钟点题全错也只把 150 题的通过率拉低约 0.027。切片把门关在 `wall_clock` 和 `clock_exact_rate` 上，避免以后有人放松 `pass_rate` 时钟点跟着漏掉。放松 `pass_rate` 之前，必须先给 `create_meeting`、`create_block`、`list_schedule`、`conflict`、`attendees` 各自补上 1.0 的切片门禁。钟点这两条门禁不在可放松之列。

失败样例进 `SuiteResult.samples`，报告的 failures 列表能点到 `CAL-*` 和 `failed_checks`。修的是产品、提示词或写错的金标准。题量必须保持 150；删题就要补题。

## 5. 什么是假的，什么是真的

| | 本套 |
| --- | --- |
| 假 | Google HTTP。跑分替换 `app.services.calendar_secrets._http_request`。行为对齐测试里的 `FakeGoogle`（列表、insert、patch、delete、时区、刷新、500/401）。评测代码不要 import `backend/tests/test_calendar_google.py` |
| 真 | 模型。`complete_json` 和 `complete_text` 打到 `resolved_planner_model()`。缓存只重放真实返回 |
| 真 | `execute_calendar`（`backend/app/agents/tools/calendar_tool.py`） |
| 真 | 抽槽之后的补全、会议/时间块卡片、`_without_busy_overlap`、`_replace_unreliable_free_note` |
| 真 | 确认接口：`POST /api/calendar`、`POST /api/calendar/google/confirm`、未连接的 `PATCH` / `DELETE /api/calendar/{id}` |
| 真 | scratch SQLite 里的本地行和连接行。用户由评测自己建，用完删 |
| 不跑 | 真实 OAuth、真实 Google、Playwright、`run_agent_workflow`、路由 `plan_tools`、作者手写的模型 JSON |

时钟：每题开始把 `calendar_now` 换成该题的 `now`（缺省 `2026-10-15T10:00:00`，时区 `Asia/Singapore`）。这和 Plan 2「沿用 `2026-10-15`、已连接 `Asia/Singapore`」同一天，但分钟钉死，避免进程当地钟和 `09:00–11:00` 窗口。金标准相对这个 `now`，不相对跑分那天。

已连接用户要有一条 `CalendarConnection`，令牌按现有测试的方式放进假客户端能读的密文。未连接不插连接行，确认走本地接口，假 Google 的 insert/patch/delete 必须仍为空。

## 6. 和 Plan 1、Plan 2 的关系

三层各管一件事。

| | Plan 1 发布脚本 | Plan 2 pytest | 本套 |
| --- | --- | --- | --- |
| 文档 | `docs/plans/calendar-release-scripts-100.md`，题单 CSV | `docs/plans/calendar-hard-gates.md` | 本文。题集落地后在 `backend/data/eval/calendar_behavior.jsonl` |
| 题量 | 100，分母是页面 | 五条门禁，点名到现有测试函数 | 150，分母是本套 |
| 怎么跑 | 人，或对着真实页面的 Playwright | `python -m pytest` 点名的日历测试；offline job 跑整个 `tests/` | `python scripts/eval.py run calendar` |
| Google | 真日历 | 假 | 假 |
| 模型 | 真模型，钟点对不上算 FAIL | 测试给定 JSON | 真模型。原句时间说全时，卡片必须与金标准相同 |
| 开跑窗口 | 账户当地 `09:00–11:00` | 无 | 无。`now` 钉在题里 |
| 是不是合并门槛 | 不是日历代码 PR 的完成条件 | 是。改日历代码仍跑 Plan 2 第 4 节的命令 | 是评分回归。不替代那条 pytest 命令 |

Plan 1 保持可选抽查。抽查可以在任意钟点做，不必守发布脚本里的窗口。抽查结果不写进 `pass_rate`，也不改 150 这个分母。

Plan 2 已经锁住的测试保持原样。那些测试给定模型 JSON，用来锁产品在抽取已经正确时的行为。本套不把那层 JSON 夹具当成评分输入。本套仍检查同一批可观察结果：确认前调用列表为空、卡片字段、409、安全说明「请以上面的列表为准，已有安排的时段不是空闲。」、瞬时文案与「重新连接」分开。检查写在 `calendar_behavior.py`，用产品返回值和假客户端记录，不调用测试函数，也不把这 150 题再包一层 pytest。

日历代码 PR 的完成条件仍是 Plan 2 第 4 节。本套变红时，先看是产品退步、提示词退步，还是金标准把原句读错了。不要为了套件去放宽 Plan 2 的断言，也不要把钟点门禁改成软分。

## 7. 批准之后的落地顺序

每一阶段单独 PR，都对着 `release`。阶段 2 之前不要登记 `calendar`，否则契约对齐和 full 门禁测试会红。

1. **判分函数，先不登记，也不打模型。** 加上 `backend/app/eval/calendar_behavior.py` 里的纯比较：给一张卡片和一份 `expect`，断言确认前无写入、block 字段、id、空档说明、description，以及 `time_complete` 时起止字符串相等。过去的墙钟与金标准不同则失败。这一步不改 `SUITES`、`CONTRACTS`、`GATES`，不包装 `complete_json`。
2. **150 题、登记、真实模型同一 PR。** 写入 `calendar_behavior.jsonl`（正好 150，冒烟 30），并同时改第 1 节表格里的登记、契约、门禁、`run_calendar_eval.py`、预检对 `--require-cache` 的例外、`docs/eval.md`、`backend/app/eval/README.md`、`test_hermetic_suites_isolate_app_db`。跑 `python scripts/lint_eval_datasets.py --card` 更新 `DATASETS.md`。用钥匙跑完整档。`clock_exact_rate` 和 `pass_rate` 不满 1.0 就停在这里：改提示词或产品，或改正读错的金标准。不降低第 4 节的地板，不往题里加模型 JSON。绿色之后把 `calendar_model_cache.json` 和这一轮一起提交。
3. **冒烟进 workflow。** 缓存已经在仓库里之后，把 `python scripts/eval.py run calendar --profile smoke` 加进 `eval.yml` 的 `eval-smoke`。没有缓存就不要加，否则钥匙为空的那次会在预检或缓存缺失处停住。
4. **不增加第二套「只看抽槽」的套件，也不加真 Google 档。** 钟点已经在本套的 `clock_exact_rate` 里。

阶段 2 的 PR 说明里写明本地命令已过：

```bash
cd backend
python scripts/lint_eval_datasets.py
python scripts/eval.py run calendar --profile full
python scripts/eval.py run calendar --profile smoke
```

## 8. 不在本文范围

- 撰写 150 条 jsonl，或任何产品代码、假客户端、登记补丁。那些属于第 7 节。
- 改 Plan 1 的 100 条原句、CSV，或把它接进 CI。
- 重写 Plan 2 已经落地的 pytest，或把 `scheduleCard.test.mjs` 再改一遍。
- 用作者写的模型 JSON 作为计分输入，或把缓存手改成金标准。
- 真实 OAuth、真实 Google、Playwright。手测步骤仍在 `docs/plans/calendar-google-manual-acceptance.md`。
- 用正则纠正模型钟点，把钟点门禁降到 1.0 以下，或要求套件在 `09:00–11:00` 跑。
- 把日历题并进 `agent` 的完成率门禁。那里的日历题继续只看路由。
