#!/usr/bin/env python3
"""Build ``agent_tasks.jsonl``: end-to-end tasks for the real agent loop.

Replaces the template-farmed YZ set. Principles:
* one independent intent per row (at most two phrasings per fact: standalone + follow-up),
* a third of the tasks are multi-turn (anaphora needs the history),
* ``task_type`` + structured ``answer_spec`` instead of substring ``gold_facts``,
* refusals carry no ``gold_doc_keys``; attendance aggregates are ``sandbox`` tasks,
  consistent with the routing suite's labelling convention,
* numeric gold is recomputed from the CSV fixture when it is present, never typed
  by hand. A clean checkout has no CSV (gitignored); the generator then reads the
  frozen ``seeds/yz_attendance.jsonl``, same default as ``gen_yz_eval.py``.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.contracts import AgentCase, normalize_text
from app.eval.jsonl_io import write_jsonl

CSV_FIXTURE = ROOT / "tests" / "YZ测试文档" / "测试用例.csv"
YZ_SEED = ROOT / "data" / "eval" / "seeds" / "yz_queries.jsonl"
ATT_SEED = ROOT / "data" / "eval" / "seeds" / "yz_attendance.jsonl"
OUT = ROOT / "data" / "eval" / "agent_tasks.jsonl"

POLICY, PRODUCT, ATT = "office_policy_txt", "product_guide_md", "attendance_csv"


def num(v: float, tol: float = 0.0) -> dict:
    return {"type": "number", "value": v, "tol": tol}


def enum(*opts: str) -> dict:
    return {"type": "enum", "any_of": list(opts)}


def tr(start: str, end: str) -> dict:
    return {"type": "time_range", "start": start, "end": end}


def allof(*items: dict) -> dict:
    return {"type": "all_of", "items": list(items)}


def turns(q: str, a: str) -> list[dict]:
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


# (standalone question, spec, doc, history question, history answer, follow-up question, tags)
FACTS: list[tuple] = [
    ("公司的正常工作时间是几点到几点？", tr("09:00", "18:00"), POLICY,
     "公司工作日是怎么安排的？", "周一至周五上班，具体时段见考勤制度。", "具体是几点上班、几点下班？", ["hours"]),
    ("中午的午休安排在什么时间段？", tr("12:00", "13:30"), POLICY,
     "我想了解一下办公时间的规定。", "正常工作时间为周一至周五 09:00-18:00。", "那中午休息是什么时候？", ["break"]),
    ("我今天迟到了二十分钟，考勤会怎么记？", enum("事假半小时", "半小时事假", "半小时"), POLICY,
     "考勤制度里对迟到有规定吗？", "有，按迟到时长分档处理。", "迟到二十分钟属于哪一档，怎么记？", ["late"]),
    ("如果我迟到了一个半小时，会被怎么处理？", enum("半天事假", "半天"), POLICY,
     "迟到三十分钟以内按事假半小时计。", "是的，这是第一档。", "那迟到一个半小时呢？", ["late"]),
    ("一个月迟到超过几次，季度全勤奖就没了？", num(5), POLICY,
     "公司有全勤奖吗？", "有，按季度评定。", "迟到多少次会被取消资格？", ["late", "bonus"]),
    ("项目文档应该统一放在哪里保存？", enum("公司知识库", "知识库"), POLICY,
     "我的项目文档可以放在自己电脑里吗？", "制度对文档存放有要求。", "那应该统一归档到哪里？", ["document"]),
    ("对外发布文档需要哪些人签字确认？", allof(enum("部门负责人"), enum("法务")), POLICY,
     "我们要把一份方案发给外部合作方。", "对外发布的文档有审批要求。", "需要谁来签字才能发出？", ["document", "approval"]),
    ("给客户发数据文件，可以直接用普通邮件附件吗？", enum("禁止", "不能", "不可以", "不允许"), POLICY,
     "我要给客户发一份带客户信息的文件。", "涉及客户数据的传输有安全规定。", "能直接用邮件附件发吗？", ["security", "document"]),
    ("例会原则上不要超过多长时间？", num(60), POLICY,
     "部门例会有什么要求？", "例会需要控制时长并提前发议程。", "时长上限是多少分钟？", ["meeting"]),
    ("开会前，会议议程至少要提前多久发出？", enum("1 个工作日", "1个工作日", "一个工作日", "提前 1"), POLICY,
     "开例会之前要做哪些准备？", "需要提前准备并发出议程。", "议程要提前多久发？", ["meeting"]),
    ("会议开完以后，纪要要在多长时间内整理好？", num(24), POLICY,
     "会议决议需要怎么留存？", "需要形成会议纪要并同步给参会人。", "最晚多少小时内要完成？", ["meeting"]),
    ("周五下午能不能安排跨部门的会议？", enum("不安排", "不能", "不可以", "不得", "不应"), POLICY,
     "周五下午一般用来做什么？", "预留为个人专注工作时间。", "那能约跨部门会议吗？", ["meeting"]),
    ("办公电脑的屏幕自动锁屏要设置成几分钟？", num(5), POLICY,
     "办公终端需要开启哪些安全功能？", "磁盘加密和屏幕自动锁屏。", "自动锁屏是几分钟？", ["security", "device"]),
    ("可以用个人账号登录生产环境的数据库吗？", enum("严禁", "禁止", "不能", "不可以", "不允许"), POLICY,
     "我想直接连一下生产数据库查个数据。", "访问生产环境需要遵守安全规定。", "能用我自己的个人账号吗？", ["security", "access"]),
    ("发现信息安全事件后，最迟要在多久内上报？", num(30), POLICY,
     "万一发现有安全事件应该怎么办？", "需要及时上报给相关团队。", "最晚多少分钟内要报？", ["security", "incident"]),
    ("信息安全事件应该上报给哪个团队？", enum("安全应急小组"), POLICY,
     "公司对安全事件有响应流程吗？", "有，发现后须在时限内上报。", "具体报给谁？", ["security", "incident"]),
    ("Omni-Butler 一次最多可以批量上传多少个文件？", num(5), PRODUCT,
     "我想把一批文档导入知识库。", "可以在知识库面板拖拽上传。", "一次最多能传几个？", ["upload", "limits"]),
    ("知识库里每个文件最大支持多少 MB？", num(20), PRODUCT,
     "知识库支持上传哪些类型的文件？", "支持 PDF、DOCX、XLSX、CSV、TXT、MD。", "那单个文件大小上限是多少 MB？", ["upload", "limits"]),
    ("知识库支持上传哪些格式的文档？", allof(enum("PDF", "pdf"), enum("DOCX", "docx"), enum("XLSX", "xlsx")), PRODUCT,
     "怎么把文件放进知识库？", "打开知识库面板后拖拽上传即可。", "支持哪几种文件格式？", ["upload", "formats"]),
    ("想让回答依据我刚上传的文档，需要打开什么模式？", enum("知识库问答"), PRODUCT,
     "我上传了一份制度文档，可它没被用上。", "需要确认文档是否解析完成。", "还需要打开哪个模式才会基于文档回答？", ["kb_mode"]),
    ("代码超过多少行时，系统会自动弹出动态工作区？", num(15), PRODUCT,
     "生成数据分析代码时界面会有什么变化？", "系统会根据代码长度切换展示方式。", "超过多少行会滑出工作区？", ["sandbox_ui"]),
    ("日程出现冲突时，我可以在日程卡片上点哪个按钮调整？", enum("修改"), PRODUCT,
     "Agent 能处理日程冲突吗？", "能，会检测冲突并建议其他时间。", "我自己想改的话点哪里？", ["calendar"]),
    ("知识库回答答非所问时，应该先检查文件的什么状态？", enum("完成", "解析"), PRODUCT,
     "知识库回答不准怎么办？", "先排查文档本身的状态。", "具体先看文件处于什么状态？", ["troubleshoot"]),
    ("这个产品支持用什么语法显示数学公式？", enum("LaTeX", "latex"), PRODUCT,
     "聊天里能显示数学公式吗？", "支持行内和独立公式。", "用的是什么语法？", ["render"]),
    ("在哪里点击可以新建一个会话？", enum("新建会话"), PRODUCT,
     "刚打开产品，怎么开始提问？", "在左侧边栏操作即可。", "具体点哪个按钮？", ["getting_started"]),
    ("上传销售表之后，数据分析可以用什么库做汇总？", enum("pandas"), PRODUCT,
     "上传销售表后能做数据分析吗？", "可以让 Agent 生成代码进行汇总。", "生成的代码用的是什么库？", ["data_analysis"]),
]

# Natural / noisy alternative phrasing for a handful of facts (second phrasing per intent).
NOISY: list[tuple[str, dict, str, list[str]]] = [
    ("麻烦帮我看下，咱们公司每天几点开始上班，几点能走？", tr("09:00", "18:00"), POLICY, ["hours", "noisy"]),
    ("同事问我迟到超过半小时算不算半天假，帮我确认一下规则", enum("半天事假", "半天"), POLICY, ["late", "noisy"]),
    ("有个客户要资料，我能不能顺手用邮件附件发明文文件？", enum("禁止", "不能", "不可以", "不允许"), POLICY, ["security", "noisy"]),
    ("下午开会前想确认一下，开会纪要是不是得当天整理出来？时限多少小时？", num(24), POLICY, ["meeting", "noisy"]),
    ("我一次想传七八个文件进知识库，上限是几个来着？", num(5), PRODUCT, ["upload", "noisy"]),
    ("大文件传不上去，是不是超过多少 MB 了？", num(20), PRODUCT, ["upload", "noisy"]),
]

CHAT: list[tuple[str, list[dict]]] = [
    ("你好，今天天气不错", []),
    ("帮我写一首关于春天的诗", []),
    ("什么是机器学习？", []),
    ("把 hello world 翻译成中文", []),
    ("讲个笑话吧", []),
    ("1+1 等于几", []),
    ("推荐几本适合通勤路上看的小说", []),
    ("Python 和 Java 哪个更适合入门？", []),
    ("周末有什么好看的电影吗", []),
    ("谢谢你的帮助", []),
    ("换个话题，给我讲个睡前故事吧", turns("迟到半小时以内怎么记？", "按事假半小时计。")),
    ("好的我明白了，你能再用一句话概括一下吗", turns("例会有什么要求？", "原则上 60 分钟以内，并提前 1 个工作日发议程。")),
]

CALENDAR: list[tuple[str, list[dict]]] = [
    ("帮我约明天下午三点和产品组开个评审会", []),
    ("下周二上午十点提醒我提交周报", []),
    ("周五晚上七点安排一个团队聚餐的日程", []),
    ("我明天有哪些安排？", []),
    ("每周一早上九点提醒我写周计划", []),
    ("下个月五号上午安排一次季度复盘会", []),
    ("把它改到下午四点", turns("帮我安排明天上午十点和研发组开会。", "已创建明天 10:00 的研发组会议。")),
    ("再给我加一个提前十分钟的提醒", turns("周四下午两点和客户有个电话会议。", "已记录周四 14:00 的客户电话会议。")),
]

REFUSE_KB_EXTRA: list[tuple[str, str]] = [
    ("公司年假一共有多少天？", POLICY),
    ("出差住宿的报销标准是多少？", POLICY),
    ("员工食堂午餐一顿多少钱？", POLICY),
    ("年终奖是怎么计算发放的？", POLICY),
    ("VPN 账号要怎么申请？", POLICY),
    ("Omni-Butler 企业版的价格是多少？", PRODUCT),
    ("怎么把产品绑定到企业微信？", PRODUCT),
    ("移动端 App 在哪里下载？", PRODUCT),
    ("产品服务器部署需要多大内存？", PRODUCT),
    ("系统最多支持多少用户同时在线？", PRODUCT),
    ("制度里有没有写员工股票期权的归属年限？", POLICY),
    ("使用指南有没有提供客服电话号码？", PRODUCT),
]
REFUSE_TABLE: list[str] = [
    "考勤表里有没有记录每位员工的籍贯？",
    "请告诉我每位员工的月薪是多少。",
    "表里销售额最高的员工是谁？",
]


_EMPLOYEE_RE = re.compile(r"工号([A-Za-z0-9]+)的(.+?)，")
_HEADCOUNT_SUFFIX = "共有多少名员工出现在考勤表中？"
_LATE_SUFFIX = "所有员工迟到次数合计是多少？"
_GAP_QUERY = "实际出勤少于应出勤的员工有多少？"


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def load_csv_if_present() -> list[dict] | None:
    """Local attendance fixture. Absent on a clean checkout (gitignored)."""
    if not CSV_FIXTURE.is_file():
        return None
    with CSV_FIXTURE.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _employees_from_seed(seed_rows: list[dict]) -> list[dict]:
    """Rebuild the columns the lookup / threshold tasks need from frozen summaries."""
    employees: list[dict] = []
    for row in seed_rows:
        if "employee_summary" not in (row.get("tags") or []):
            continue
        match = _EMPLOYEE_RE.match(str(row.get("query") or ""))
        facts = list(row.get("gold_facts") or [])
        if match is None or len(facts) < 5:
            raise ValueError(f"attendance seed employee_summary is not usable: {row.get('query')}")
        employees.append(
            {
                "工号": match.group(1),
                "姓名": str(facts[0]),
                "实际出勤天数": str(facts[1]),
                "迟到次数": str(facts[2]),
                "请假天数": str(facts[3]),
                "加班时长(小时)": str(facts[4]),
            }
        )
    if len(employees) < 6:
        raise ValueError(f"attendance seed has {len(employees)} employee summaries, need >= 6")
    return employees


def _department_pairs(csv_rows: list[dict] | None, seed_rows: list[dict]) -> list[tuple[str, str, int, int, int, int]]:
    """Sorted-department rotation: (prev, next, head_prev, head_next, late_prev, late_next)."""
    if csv_rows is not None:
        names = sorted({row["部门"] for row in csv_rows})

        def group(name: str) -> list[dict]:
            return [row for row in csv_rows if row["部门"] == name]

        return [
            (
                prev,
                nxt,
                len(group(prev)),
                len(group(nxt)),
                sum(int(row["迟到次数"]) for row in group(prev)),
                sum(int(row["迟到次数"]) for row in group(nxt)),
            )
            for prev, nxt in zip(names, names[1:] + names[:1])
        ]

    stats: dict[str, dict[str, int]] = {}
    for row in seed_rows:
        if "department_aggregate" not in (row.get("tags") or []):
            continue
        query = str(row.get("query") or "")
        facts = row.get("gold_facts") or []
        if not facts:
            continue
        value = int(float(facts[0]))
        if query.endswith(_HEADCOUNT_SUFFIX):
            stats.setdefault(query[: -len(_HEADCOUNT_SUFFIX)], {})["n"] = value
        elif query.endswith(_LATE_SUFFIX):
            stats.setdefault(query[: -len(_LATE_SUFFIX)], {})["late"] = value
    names = sorted(stats)
    if not names or any("n" not in stats[name] or "late" not in stats[name] for name in names):
        raise ValueError("attendance seed is missing department headcount or late totals")
    return [
        (prev, nxt, stats[prev]["n"], stats[nxt]["n"], stats[prev]["late"], stats[nxt]["late"])
        for prev, nxt in zip(names, names[1:] + names[:1])
    ]


def _multi_tool_counts(csv_rows: list[dict] | None, seed_rows: list[dict]) -> tuple[int, int, int, int]:
    """late>5, late>3, actual<expected, leave>2. The gap count is a seed aggregate."""
    if csv_rows is not None:
        late = [int(row["迟到次数"]) for row in csv_rows]
        leave = [float(row["请假天数"]) for row in csv_rows]
        actual = [int(row["实际出勤天数"]) for row in csv_rows]
        expected = [int(row["应出勤天数"]) for row in csv_rows]
        return (
            sum(value > 5 for value in late),
            sum(value > 3 for value in late),
            sum(got < exp for got, exp in zip(actual, expected)),
            sum(value > 2 for value in leave),
        )
    employees = _employees_from_seed(seed_rows)
    late = [int(row["迟到次数"]) for row in employees]
    leave = [float(row["请假天数"]) for row in employees]
    gap = next(
        (int(float(row["gold_facts"][0])) for row in seed_rows if row.get("query") == _GAP_QUERY),
        None,
    )
    if gap is None:
        raise ValueError(f"attendance seed missing {_GAP_QUERY}")
    return (
        sum(value > 5 for value in late),
        sum(value > 3 for value in late),
        gap,
        sum(value > 2 for value in leave),
    )


def fmt(v: float) -> float:
    return round(float(v), 2)


def build_cases() -> list[dict]:
    rows: list[dict] = []

    def add(**kw):
        kw.setdefault("history", [])
        kw.setdefault("gold_doc_keys", [])
        kw.setdefault("answer_spec", None)
        kw.setdefault("route_gold", None)
        # Availability switches used to be hard-coded in the runner; every task
        # now declares them explicitly so the clamps are actually exercised.
        kw.setdefault(
            "context",
            {
                "has_kb_docs": True,
                "has_tabular_docs": True,
                "document_keys": [],
                "pending_calendar": None,
            },
        )
        rows.append(kw)

    kb = {"intent": "rag", "tools": ["kb"]}
    for q, spec, doc, hq, ha, fq, tags in FACTS:
        add(query=q, task_type="fact_qa", route_gold=kb, gold_doc_keys=[doc], answer_spec=spec,
            tags=["fact", *tags])
        add(query=fq, history=turns(hq, ha), task_type="fact_qa", route_gold=kb, gold_doc_keys=[doc],
            answer_spec=spec, tags=["fact", "multiturn", *tags])
    for q, spec, doc, tags in NOISY:
        add(query=q, task_type="fact_qa", route_gold=kb, gold_doc_keys=[doc], answer_spec=spec,
            tags=["fact", *tags])

    # --- attendance: aggregates are sandbox compute tasks (routing-suite convention) ---
    # CSV is the local refresh source. A clean checkout uses the frozen seed so
    # regenerating agent_tasks.jsonl stays byte-idempotent without the gitignored file.
    csv_rows = load_csv_if_present()
    seed_rows = _read_jsonl(ATT_SEED)
    if csv_rows is None:
        print(
            f"attendance CSV missing ({CSV_FIXTURE.name}); using frozen seed {ATT_SEED.name}",
            file=sys.stderr,
        )
    sandbox = {"intent": "data_analysis", "tools": ["sandbox"]}
    agg = seed_rows
    for r in agg:
        tags = set(r["tags"])
        if not tags & {"department_aggregate", "global_aggregate"}:
            continue
        try:
            value = float(r["gold_facts"][0])
        except (ValueError, IndexError):
            continue
        add(query=r["query"], task_type="compute", route_gold=sandbox, gold_doc_keys=[ATT],
            answer_spec=num(value, 0.01), tags=["attendance", "aggregate"])

    # multi-turn compute follow-ups ("那X部门呢")
    for a, b, _na, nb, la, lb in _department_pairs(csv_rows, seed_rows):
        add(query=f"那{b}呢？",
            history=turns(f"{a}一共有多少名员工出现在考勤表里？", f"{a}共有{_na}名员工。"),
            task_type="compute", route_gold=sandbox, gold_doc_keys=[ATT],
            answer_spec=num(nb), tags=["attendance", "multiturn", "headcount"])
        add(query=f"{b}的合计是多少？",
            history=turns(f"{a}所有员工迟到次数加起来是多少？", f"{a}迟到次数合计{la}次。"),
            task_type="compute", route_gold=sandbox, gold_doc_keys=[ATT],
            answer_spec=num(lb), tags=["attendance", "multiturn", "late_total"])

    # employee lookups stay on the KB path (cell lookup, not statistics)
    lookup_rows = csv_rows[:6] if csv_rows is not None else _employees_from_seed(seed_rows)[:6]
    for item in lookup_rows:
        spec = allof(
            num(int(item["实际出勤天数"])), num(int(item["迟到次数"])),
            num(float(item["请假天数"]), 0.01), num(float(item["加班时长(小时)"]), 0.01),
        )
        add(query=(f"帮我查一下工号{item['工号']}的{item['姓名']}这个月的出勤情况，"
                   "包括实际出勤、迟到、请假和加班。"),
            task_type="fact_qa", route_gold=kb, gold_doc_keys=[ATT], answer_spec=spec,
            tags=["attendance", "lookup"])

    # multi-tool: policy threshold + table statistics
    late_gt_5, late_gt_3, attendance_gap, leave_gt_2 = _multi_tool_counts(csv_rows, seed_rows)
    multi = {"intent": "data_analysis", "tools": ["kb", "sandbox"]}
    for q, v in [
        ("按公司制度里全勤奖的迟到标准，统计考勤表里迟到次数超过 5 次的员工有几个？", late_gt_5),
        ("制度规定迟到超过 3 次要重点关注，考勤表里迟到次数超过 3 次的有多少人？", late_gt_3),
        ("结合考勤制度，看看表里实际出勤天数少于应出勤天数的员工有多少名？", attendance_gap),
        ("参考制度里的事假规定，考勤表中请假超过 2 天的员工有几个？", leave_gt_2),
    ]:
        add(query=q, task_type="multi_tool", route_gold=multi, gold_doc_keys=[ATT, POLICY],
            answer_spec=num(v), tags=["multi_tool", "attendance"])

    # --- refusals: no gold_doc_keys (nothing to cite) ---
    for q, doc in REFUSE_KB_EXTRA:
        add(query=q, task_type="refuse", route_gold=kb, tags=["refuse", "kb"])
    for q in REFUSE_TABLE:
        add(query=q, task_type="refuse", route_gold=None, tags=["refuse", "table"])
    for q, hq, ha in [
        ("那年假呢，一年有多少天？", "公司的考勤时间是怎么规定的？", "周一至周五 09:00-18:00。"),
        ("那它的收费标准是多少？", "Omni-Butler 支持哪些文件格式？", "支持 PDF、DOCX、XLSX、CSV、TXT、MD。"),
        ("再说说出差补贴有多少？", "迟到怎么计算？", "30 分钟以内按事假半小时计。"),
        ("那报销流程是怎样的？", "对外发布文档要谁签字？", "部门负责人与法务双签。"),
    ]:
        add(query=q, history=turns(hq, ha), task_type="refuse", route_gold=kb,
            tags=["refuse", "kb", "multiturn"])

    for q, hist in CHAT:
        add(query=q, history=hist, task_type="chat", route_gold={"intent": "chat", "tools": []},
            tags=["chat"] + (["multiturn"] if hist else []))
    for q, hist in CALENDAR:
        add(query=q, history=hist, task_type="calendar",
            route_gold={"intent": "calendar", "tools": ["calendar"]},
            tags=["calendar"] + (["multiturn"] if hist else []))

    # --- availability clamps: the ask implies a tool the workspace cannot offer ---
    no_table = {
        "has_kb_docs": True,
        "has_tabular_docs": False,
        "document_keys": [],
        "pending_calendar": None,
    }
    no_kb = {
        "has_kb_docs": False,
        "has_tabular_docs": True,
        "document_keys": [],
        "pending_calendar": None,
    }
    # Expecting an empty tool set is how production would behave and is exactly
    # what the availability clamps are supposed to guarantee.
    no_tool_gold = {"intent": "chat", "tools": []}
    for q, spec in [
        ("帮我把考勤表里所有人的迟到分钟数加起来。", enum("无法", "不能", "缺少", "未提供")),
        ("这张考勤表里迟到超过三次的员工一共有几个？", enum("无法", "不能", "缺少", "未提供")),
        ("麻烦统计一下报销表里各部门的支出合计。", enum("无法", "不能", "缺少", "未提供")),
        ("算一下库存表里每种物料的平均周转天数。", enum("无法", "不能", "缺少", "未提供")),
    ]:
        add(query=q, task_type="no_tool", route_gold=no_tool_gold, answer_spec=spec,
            context=dict(no_table), tags=["attendance", "availability_gate"])

    # No knowledge base attached: an explicit "the library says ..." question can
    # only be declined (no retrieval possible), and nothing may be executed.
    for q in [
        "知识库里写的出差住宿报销上限是多少？",
        "麻烦查一下知识库里请假的完整审批流程。",
        "帮我确认知识库里 IT 设备的更换周期规定。",
        "知识库里的文件上传规范一共支持哪些格式？",
    ]:
        add(query=q, task_type="refuse", route_gold=no_tool_gold,
            context=dict(no_kb), tags=["refuse", "kb", "availability_gate"])

    # --- pending calendar slot fill: production short-circuits the router ---
    for slot_q, title in [
        ("时间改成明天下午两点。", "需求评审"),
        ("参会人再加上法务部。", "合同评审"),
        ("会议室换成海棠厅。", "部门周会"),
        ("标题改成季度经营复盘。", "复盘会"),
        ("时长从一小时改成四十五分钟。", "站会"),
        ("提醒时间设为提前一小时。", "培训"),
    ]:
        add(query=slot_q, history=turns(f"帮我安排{title}。", "还需要补充或确认会议信息。"),
            task_type="calendar", route_gold={"intent": "calendar", "tools": ["calendar"]},
            context={
                "has_kb_docs": True,
                "has_tabular_docs": True,
                "document_keys": [],
                "pending_calendar": {"title": title, "action": "create"},
            },
            tags=["calendar", "multiturn", "slot_fill"])

    # --- document scoping: production forces the kb tool and filters by id ---
    for q, keys, spec in [
        ("只看我指定的这份制度文档，迟到半小时以内怎么计？", [POLICY],
         enum("半天事假", "半日事假", "半小时")),
        ("仅根据我选中的这份产品指南，单次最多能传几个文件？", [PRODUCT], num(5)),
        ("只查我圈定的政策文档，会议纪要要几个小时内同步？", [POLICY], num(24)),
        ("请只依据我指定的文件回答：对外发布文档需要谁签字？", [POLICY],
         enum("部门负责人与法务双签", "部门负责人和法务双签", "双签")),
        ("只在我选中的产品与制度两份文档里找，支持哪些文件格式？", [PRODUCT, POLICY],
         enum("PDF", "DOCX", "XLSX", "CSV", "TXT", "MD")),
    ]:
        add(query=q, task_type="fact_qa", route_gold=kb, gold_doc_keys=list(keys),
            answer_spec=spec,
            context={
                "has_kb_docs": True,
                "has_tabular_docs": True,
                "document_keys": list(keys),
                "pending_calendar": None,
            },
            tags=["kb", "doc_scope"])

    out: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        key = normalize_text(row["query"]) + "|" + normalize_text(json.dumps(row["history"], ensure_ascii=False))
        if key in seen:
            raise ValueError(f"duplicate task: {row['query']}")
        seen.add(key)
        model = AgentCase.model_validate({"id": f"agent_{i:04d}", **row})
        out.append(model.model_dump(mode="python"))
    return out


def main() -> int:
    rows = build_cases()
    write_jsonl(OUT, rows)
    multi = sum(1 for r in rows if r["history"])
    print(f"wrote {len(rows)} agent tasks -> {OUT} (multi-turn {multi}, {multi/len(rows):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
