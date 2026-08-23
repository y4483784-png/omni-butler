#!/usr/bin/env python3
"""Generate ~400 YZ full-chain eval cases from three fixture documents.

Output: backend/data/eval/yz_fullchain.jsonl
Targets: office_policy_txt ~120, product_guide_md ~100, attendance_csv ~100,
         chat_noise ~40, multi_doc ~40.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "yz_fullchain.jsonl"
CSV_FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "YZ测试文档" / "测试用例.csv"


def _case(
    cid: str,
    query: str,
    *,
    gold_doc_keys: list[str],
    gold_facts: list[str],
    ground_truth: str,
    expect_unanswerable: bool = False,
    gold: dict | None = None,
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": cid,
        "query": query,
        "gold_doc_keys": gold_doc_keys,
        "gold_facts": gold_facts,
        "ground_truth": ground_truth,
        "expect_unanswerable": expect_unanswerable,
        "gold": gold or {"intent": "rag", "tools": ["kb"]},
        "tags": tags or [],
    }


def _variants(templates: list[str], slots: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    keys = list(slots.keys())

    def rec(i: int, cur: dict[str, str]) -> None:
        if i == len(keys):
            for t in templates:
                try:
                    out.append(t.format(**cur))
                except KeyError:
                    out.append(t)
            return
        k = keys[i]
        for v in slots[k]:
            cur[k] = v
            rec(i + 1, cur)

    rec(0, {})
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def _load_csv_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with CSV_FIXTURE.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def _gen_txt_cases() -> list[dict]:
    rows: list[dict] = []
    n = 0

    groups: list[tuple[list[str], dict[str, list[str]], list[str], str, list[str]]] = [
        (
            ["{q}正常工作时间{tail}", "公司几点上班几点下班", "工作日作息怎么安排"],
            {"q": ["", "请问", "制度里", "帮我查"], "tail": ["", "？", "怎么规定", "是多少"]},
            ["09:00", "18:00", "09:00-18:00"],
            "正常工作时间为周一至周五 09:00-18:00",
            ["policy", "attendance", "hours"],
        ),
        (
            ["午休时间{tail}", "{q}中午休息多久", "午餐休息时段"],
            {"q": ["", "请问", "制度规定"], "tail": ["", "？", "是多少", "怎么安排"]},
            ["12:00", "13:30", "午休"],
            "午休 12:00-13:30",
            ["policy", "attendance", "break"],
        ),
        (
            [
                "迟到30分钟以内怎么算",
                "{q}迟到{tail}",
                "早退不到30分钟如何处理",
                "迟到半小时以内算什么事假",
            ],
            {"q": ["", "请问", "制度里"], "tail": ["怎么计", "规定", "？", "算什么事假"]},
            ["30分钟", "事假", "半小时"],
            "迟到或早退 30 分钟以内按事假半小时计",
            ["policy", "attendance", "late"],
        ),
        (
            [
                "迟到超过30分钟不足2小时怎么算",
                "{q}迟到超过半小时{tail}",
                "迟到1小时算半天事假吗",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "怎么计"]},
            ["30分钟", "2小时", "半天"],
            "超过 30 分钟不足 2 小时按半天事假计",
            ["policy", "attendance", "late"],
        ),
        (
            [
                "月度迟到超过几次取消全勤奖",
                "{q}全勤奖{tail}",
                "迟到几次没全勤奖",
                "累计迟到5次会怎样",
            ],
            {"q": ["", "请问", "制度"], "tail": ["", "？", "资格", "规定"]},
            ["5次", "全勤奖"],
            "月度累计迟到超过5次，取消当季度全勤奖资格",
            ["policy", "attendance", "exact"],
        ),
        (
            [
                "项目文档应该归档到哪里",
                "{q}文档管理{tail}",
                "能不能把文档放个人电脑",
                "知识库归档要求",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "规定", "要求"]},
            ["知识库", "归档"],
            "所有项目文档须统一归档至公司知识库，禁止散落在个人本地磁盘",
            ["policy", "document"],
        ),
        (
            [
                "对外发布文档需要谁签字",
                "{q}对外文档{tail}",
                "对外材料发布审批流程",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "审批", "双签"]},
            ["双签", "部门负责人", "法务"],
            "对外发布的文档须经过部门负责人与法务双签后方可发出",
            ["policy", "document", "approval"],
        ),
        (
            [
                "客户数据文件怎么传输",
                "{q}客户数据{tail}",
                "能明文邮件发客户资料吗",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "传输", "通道"]},
            ["加密通道", "明文"],
            "涉及客户数据的文件传输须通过加密通道，禁止明文邮件附件",
            ["policy", "security", "data"],
        ),
        (
            [
                "例会最长开多久",
                "{q}会议时长{tail}",
                "会议一般控制在多少分钟",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "限制"]},
            ["60分钟", "60"],
            "例会原则上控制在 60 分钟以内",
            ["policy", "meeting"],
        ),
        (
            [
                "会议议程要提前多久发",
                "{q}议程{tail}",
                "发会议议程要提前几个工作日",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "提前"]},
            ["1个工作日", "议程"],
            "需提前 1 个工作日发出议程",
            ["policy", "meeting", "agenda"],
        ),
        (
            [
                "会议纪要什么时候要完成",
                "{q}会议纪要{tail}",
                "会后多久要出纪要",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "时限"]},
            ["24小时", "纪要"],
            "会议决议须在会后 24 小时内形成纪要并同步至相关参会人",
            ["policy", "meeting", "minutes"],
        ),
        (
            [
                "周五下午能开跨部门会吗",
                "{q}周五下午{tail}",
                "跨部门会议周五下午安排吗",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "规定"]},
            ["周五", "跨部门"],
            "周五下午不安排跨部门会议，预留为个人专注工作时间",
            ["policy", "meeting", "friday"],
        ),
        (
            [
                "办公电脑屏幕自动锁屏多久",
                "{q}锁屏{tail}",
                "磁盘加密和锁屏要求",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "时间"]},
            ["5分钟", "锁屏", "磁盘加密"],
            "办公终端须开启磁盘加密与屏幕自动锁屏（5 分钟）",
            ["policy", "security", "device"],
        ),
        (
            [
                "能用个人账号访问生产环境吗",
                "{q}生产环境{tail}",
                "个人账号登录数据库允许吗",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "规定"]},
            ["个人账号", "生产环境"],
            "严禁使用个人账号访问生产环境与数据库",
            ["policy", "security", "access"],
        ),
        (
            [
                "发现安全事件多久要上报",
                "{q}安全事件{tail}",
                "安全应急上报时限",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "上报"]},
            ["30分钟", "安全事件"],
            "发现安全事件须在 30 分钟内上报安全应急小组",
            ["policy", "security", "incident"],
        ),
    ]

    for templates, slots, facts, truth, tag in groups:
        for q in _variants(templates, slots):
            n += 1
            rows.append(
                _case(
                    f"yz_txt_{n:03d}",
                    q,
                    gold_doc_keys=["office_policy_txt"],
                    gold_facts=facts,
                    ground_truth=truth,
                    tags=tag,
                )
            )

    unanswerable = [
        (
            "公司年假有多少天",
            "证据中未提及年假天数",
            ["policy", "unanswerable"],
        ),
        (
            "出差住宿标准是多少",
            "证据中未提及差旅住宿标准",
            ["policy", "unanswerable"],
        ),
        (
            "员工食堂午餐价格",
            "证据中未提及食堂或午餐价格",
            ["policy", "unanswerable"],
        ),
        (
            "年终奖发放比例",
            "证据中未提及年终奖",
            ["policy", "unanswerable"],
        ),
        (
            "VPN账号怎么申请",
            "证据中未提及VPN申请流程",
            ["policy", "unanswerable"],
        ),
    ]
    for q, truth, tag in unanswerable:
        n += 1
        rows.append(
            _case(
                f"yz_txt_{n:03d}",
                q,
                gold_doc_keys=["office_policy_txt"],
                gold_facts=[],
                ground_truth=truth,
                expect_unanswerable=True,
                tags=tag,
            )
        )

    return rows


def _gen_md_cases() -> list[dict]:
    rows: list[dict] = []
    n = 0

    groups: list[tuple[list[str], dict[str, list[str]], list[str], str, list[str]]] = [
        (
            ["{q}新建会话{tail}", "怎么开始对话", "左侧边栏如何开新聊天"],
            {"q": ["", "请问", "指南里"], "tail": ["", "？", "怎么操作"]},
            ["新建会话", "对话"],
            "在左侧边栏点击新建会话，输入自然语言即可开始对话",
            ["product", "getting_started"],
        ),
        (
            ["{q}知识库上传{tail}", "支持哪些文件格式", "能上传什么类型的文档"],
            {"q": ["", "请问", "Omni-Butler"], "tail": ["", "？", "格式"]},
            ["PDF", "DOCX", "CSV", "TXT", "MD"],
            "支持 PDF / DOCX / XLSX / CSV / TXT / MD",
            ["product", "formats"],
        ),
        (
            ["单文件大小上限多少", "{q}上传限制{tail}", "PDF最大能传多大"],
            {"q": ["", "请问"], "tail": ["", "？", "MB"]},
            ["20 MB", "20MB"],
            "单文件上限 20 MB",
            ["product", "limits"],
        ),
        (
            ["一次最多上传几个文件", "{q}批量上传{tail}", "单次上传文件数量限制"],
            {"q": ["", "请问"], "tail": ["", "？", "几个"]},
            ["5", "5个"],
            "单次最多批量上传 5 个文件",
            ["product", "limits"],
        ),
        (
            [
                "{q}知识库问答模式{tail}",
                "怎么让回答基于我的文档",
                "引用溯源怎么开",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "开启"]},
            ["知识库问答", "引用"],
            "开启知识库问答模式，提问将基于上传的文档并附带引用溯源",
            ["product", "kb_mode"],
        ),
        (
            [
                "知识库答非所问怎么办",
                "{q}解析完成{tail}",
                "文件状态显示完成才能问答吗",
            ],
            {"q": ["", "FAQ："], "tail": ["", "？", "状态"]},
            ["完成", "知识库问答"],
            "请确认文件已解析完成（状态为完成），并开启知识库问答模式",
            ["product", "faq"],
        ),
        (
            [
                "公式怎么显示",
                "{q}LaTeX{tail}",
                "行内公式E=mc2怎么写",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "支持"]},
            ["LaTeX", "E=mc^2"],
            "支持 LaTeX 行内与独立公式",
            ["product", "faq", "latex"],
        ),
        (
            [
                "日程冲突怎么办",
                "{q}会议冲突{tail}",
                "Agent检测到冲突会怎么做",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "处理"]},
            ["冲突", "修改"],
            "Agent 会检测冲突并主动建议其他时间，可在日程卡片上点击修改",
            ["product", "faq", "calendar"],
        ),
        (
            [
                "代码超过多少行会打开工作区",
                "{q}动态工作区{tail}",
                "生成图表会自动滑出工作区吗",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "触发"]},
            ["15", "动态工作区"],
            "输出超过 15 行代码或生成图表时，系统会自动滑出动态工作区",
            ["product", "sandbox_ui"],
        ),
        (
            [
                "上传销售表后怎么汇总",
                "{q}pandas{tail}",
                "数据分析示例用什么库",
            ],
            {"q": ["", "请问"], "tail": ["", "？", "代码"]},
            ["pandas", "groupby"],
            "可使用 pandas 读取表格并按大区等进行 groupby 汇总",
            ["product", "data_analysis"],
        ),
    ]

    for templates, slots, facts, truth, tag in groups:
        for q in _variants(templates, slots):
            n += 1
            rows.append(
                _case(
                    f"yz_md_{n:03d}",
                    q,
                    gold_doc_keys=["product_guide_md"],
                    gold_facts=facts,
                    ground_truth=truth,
                    tags=tag,
                )
            )

    unanswerable = [
        ("Omni-Butler 企业版定价", "证据中未提及定价", ["product", "unanswerable"]),
        ("如何绑定企业微信", "证据中未提及企业微信绑定", ["product", "unanswerable"]),
        ("移动端 App 下载地址", "证据中未提及移动端", ["product", "unanswerable"]),
    ]
    for q, truth, tag in unanswerable:
        n += 1
        rows.append(
            _case(
                f"yz_md_{n:03d}",
                q,
                gold_doc_keys=["product_guide_md"],
                gold_facts=[],
                ground_truth=truth,
                expect_unanswerable=True,
                tags=tag,
            )
        )

    return rows


def _gen_csv_cases() -> list[dict]:
    rows: list[dict] = []
    csv_rows = _load_csv_rows()
    n = 0

    for r in csv_rows:
        name = r["姓名"]
        dept = r["部门"]
        late = r["迟到次数"]
        ot = r["加班时长(小时)"]
        emp_id = r["工号"]
        actual = r["实际出勤天数"]
        leave = r["请假天数"]

        queries = [
            (
                f"{name}的迟到次数是多少",
                [late, "迟到"],
                f"{name}迟到{late}次",
                ["csv", "row", "late"],
            ),
            (
                f"工号{emp_id}在哪个部门",
                [dept, emp_id],
                f"工号{emp_id}{name}在{dept}",
                ["csv", "row", "dept"],
            ),
            (
                f"{name}加班多少小时",
                [ot, "加班"],
                f"{name}加班时长{ot}小时",
                ["csv", "row", "overtime"],
            ),
            (
                f"{name}实际出勤几天",
                [actual, "出勤"],
                f"{name}实际出勤{actual}天",
                ["csv", "row", "attendance"],
            ),
            (
                f"{name}请假多少天",
                [leave, "请假"],
                f"{name}请假{leave}天",
                ["csv", "row", "leave"],
            ),
        ]
        for q, facts, truth, tag in queries:
            n += 1
            rows.append(
                _case(
                    f"yz_csv_{n:03d}",
                    q,
                    gold_doc_keys=["attendance_csv"],
                    gold_facts=facts,
                    ground_truth=truth,
                    tags=tag,
                )
            )

    # Rank / aggregate — sandbox intent
    agg_cases = [
        (
            "考勤表里谁迟到次数最多",
            ["迟到", "最多"],
            "需对全表统计迟到次数并取最大值（如孙浩、邹杨等多人为4次）",
            {"intent": "data_analysis", "tools": ["sandbox"]},
            ["csv", "aggregate", "rank"],
        ),
        (
            "谁一次迟到都没有",
            ["0", "迟到"],
            "迟到次数为0的员工包括赵蕾、陶然、孔明、秦昊、戚薇等",
            {"intent": "data_analysis", "tools": ["sandbox"]},
            ["csv", "aggregate", "filter"],
        ),
        (
            "研发部一共有几个人",
            ["研发部"],
            "研发部共10人（按部门统计）",
            {"intent": "data_analysis", "tools": ["sandbox"]},
            ["csv", "aggregate", "dept"],
        ),
        (
            "产品部平均加班时长是多少",
            ["产品部", "加班"],
            "需对产品部员工加班时长求平均",
            {"intent": "data_analysis", "tools": ["sandbox"]},
            ["csv", "aggregate", "avg"],
        ),
        (
            "运营部谁的加班时长最长",
            ["运营部", "加班"],
            "需筛选运营部并比较加班时长",
            {"intent": "data_analysis", "tools": ["sandbox"]},
            ["csv", "aggregate", "rank"],
        ),
    ]
    for q, facts, truth, gold, tag in agg_cases:
        n += 1
        rows.append(
            _case(
                f"yz_csv_{n:03d}",
                q,
                gold_doc_keys=["attendance_csv"],
                gold_facts=facts,
                ground_truth=truth,
                gold=gold,
                tags=tag,
            )
        )

    return rows


def _gen_chat_cases() -> list[dict]:
    rows: list[dict] = []
    templates = [
        "你好，今天天气不错",
        "帮我写一首关于春天的诗",
        "什么是机器学习",
        "翻译一下 hello world",
        "讲个笑话",
        "1+1等于几",
        "推荐几本小说",
        "Python和Java哪个好",
        "周末有什么电影好看",
        "谢谢你的帮助",
        "你是谁",
        "讲个睡前故事",
        "如何学习编程",
        "写一段产品 Slogan",
        "解释一下相对论入门",
        "帮我润色这段邮件：您好",
        "成语接龙：一心一意",
        "给我三个旅行目的地建议",
        "什么是区块链",
        "写一句生日祝福",
    ]
    prefixes = ["", "顺便问下，", "对了，", "闲聊一下，"]
    for i, base in enumerate(templates):
        for j, pre in enumerate(prefixes[:2] if i % 3 else prefixes[:1]):
            cid = f"yz_chat_{len(rows)+1:03d}"
            rows.append(
                _case(
                    cid,
                    f"{pre}{base}",
                    gold_doc_keys=[],
                    gold_facts=[],
                    ground_truth="（闲聊/常识回答，无需引用知识库）",
                    gold={"intent": "chat", "tools": []},
                    tags=["chat", "noise"],
                )
            )
            if len(rows) >= 40:
                return rows
    return rows


def _gen_confusion_cases() -> list[dict]:
    rows: list[dict] = []
    cases = [
        (
            "yz_mix_001",
            "Omni-Butler 知识库里的办公制度规定几点上班",
            ["office_policy_txt"],
            ["09:00", "18:00"],
            "制度文件规定 09:00-18:00 上班（非产品指南）",
            ["confusion", "policy_over_product"],
        ),
        (
            "yz_mix_002",
            "上传的考勤 CSV 里张敏迟到几次，和公司制度里全勤奖规则有关吗",
            ["attendance_csv", "office_policy_txt"],
            ["张敏", "2", "5次", "全勤奖"],
            "张敏迟到2次；制度规定月度迟到超5次取消当季度全勤奖",
            ["confusion", "csv_and_policy"],
        ),
        (
            "yz_mix_003",
            "产品指南说单文件 20MB，制度文档里项目材料要归档到哪",
            ["product_guide_md", "office_policy_txt"],
            ["20 MB", "知识库", "归档"],
            "单文件上限20MB；制度要求项目文档统一归档公司知识库",
            ["confusion", "md_and_txt"],
        ),
        (
            "yz_mix_004",
            "用 Omni-Butler 上传华海智汇制度 txt 后，怎么查迟到规则",
            ["product_guide_md", "office_policy_txt"],
            ["上传", "迟到"],
            "上传后开启知识库问答；制度规定迟到30分钟内按事假半小时计等",
            ["confusion", "howto_and_policy"],
        ),
        (
            "yz_mix_005",
            "考勤表里李航迟到4次，会不会影响全勤奖",
            ["attendance_csv", "office_policy_txt"],
            ["李航", "4", "5次", "全勤奖"],
            "李航迟到4次；制度为月度超5次才取消全勤奖",
            ["confusion", "csv_policy_reasoning"],
        ),
    ]

    templates = [
        ("制度里周五下午能开会吗，Omni-Butler 日程冲突怎么处理", ["office_policy_txt"], ["周五", "跨部门"], "周五下午不安排跨部门会议", ["confusion"]),
        ("CSV 里 E1020 是谁，产品文档支持 csv 吗", ["attendance_csv", "product_guide_md"], ["E1020", "孔明", "CSV"], "E1020 孔明；产品支持 CSV", ["confusion"]),
        ("加密传输客户数据和上传 docx 上限", ["office_policy_txt", "product_guide_md"], ["加密", "20 MB"], "客户数据须加密通道；docx 上限20MB", ["confusion"]),
        ("知识库问答模式和文档双签流程", ["product_guide_md", "office_policy_txt"], ["知识库问答", "双签"], "开启 KB 模式；对外文档须双签", ["confusion"]),
        ("考勤表运营部人数和会议60分钟规则", ["attendance_csv", "office_policy_txt"], ["运营部", "60分钟"], "运营部多人；例会60分钟内", ["confusion"]),
    ]

    rows.extend(
        _case(cid, q, gold_doc_keys=keys, gold_facts=facts, ground_truth=truth, tags=tags)
        for cid, q, keys, facts, truth, tags in cases
    )

    n = len(rows)
    for q, keys, facts, truth, tags in templates:
        for variant in _variants([q], {"q": ["", "请问", "帮我查"], "tail": ["", "？"]}):
            n += 1
            rows.append(
                _case(
                    f"yz_mix_{n:03d}",
                    variant if variant != q else q,
                    gold_doc_keys=keys,
                    gold_facts=facts,
                    ground_truth=truth,
                    tags=tags,
                )
            )

    # Pad to ~40 with paraphrases
    extra = [
        ("制度汇编里安全事件上报时限", ["office_policy_txt"], ["30分钟"], "30分钟内上报", ["confusion"]),
        ("使用指南里 xlsx 用途是什么", ["product_guide_md"], ["xlsx", "表格"], "xlsx 用于结构化表格", ["confusion"]),
        ("考勤表范冰在哪个部门", ["attendance_csv"], ["范冰", "产品部"], "范冰在产品部", ["confusion"]),
        ("产品 FAQ 里公式 LaTeX 和制度磁盘加密", ["product_guide_md", "office_policy_txt"], ["LaTeX", "磁盘加密"], "支持 LaTeX；须磁盘加密", ["confusion"]),
    ]
    for q, keys, facts, truth, tags in extra:
        for pre in ["", "请问", "帮我确认"]:
            n += 1
            rows.append(
                _case(
                    f"yz_mix_{n:03d}",
                    f"{pre}{q}" if pre else q,
                    gold_doc_keys=keys,
                    gold_facts=facts,
                    ground_truth=truth,
                    tags=tags,
                )
            )
            if len(rows) >= 40:
                break
        if len(rows) >= 40:
            break

    return rows[:40]


def main() -> None:
    rows: list[dict] = []
    rows.extend(_gen_txt_cases())
    rows.extend(_gen_md_cases())
    rows.extend(_gen_csv_cases())
    rows.extend(_gen_chat_cases())
    rows.extend(_gen_confusion_cases())

    # Re-id sequentially for stable ordering in file
    for i, row in enumerate(rows, start=1):
        prefix = row["id"].split("_")[0] + "_" + row["id"].split("_")[1]
        row["id"] = f"{prefix}_{i:04d}" if len(rows) > 999 else row["id"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wrote {len(rows)} cases -> {OUT}")
    by_tag: dict[str, int] = {}
    for r in rows:
        for t in r.get("tags") or []:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("Sample tag counts:", dict(sorted(by_tag.items(), key=lambda x: -x[1])[:12]))


if __name__ == "__main__":
    main()
