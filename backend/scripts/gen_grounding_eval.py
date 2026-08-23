#!/usr/bin/env python3
"""Generate ~48 grounding faithfulness eval cases (kb / web / sandbox).

Output: backend/data/eval/grounding_faithfulness.jsonl
Targets: ~16 distinct questions per source; include a few unanswerable cases.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "grounding_faithfulness.jsonl"


def _case(
    cid: str,
    *,
    source: str,
    query: str,
    gold_doc_keys: list[str] | None = None,
    contexts: list[str] | None = None,
    needs_sandbox: bool = False,
    expect_unanswerable: bool = False,
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": cid,
        "source": source,
        "query": query,
        "gold_doc_keys": gold_doc_keys or [],
        "contexts": contexts or [],
        "needs_sandbox": needs_sandbox,
        "expect_unanswerable": expect_unanswerable,
        "tags": tags or [source],
    }


# Frozen web snippets — fictional notices, not live search / not model-version trivia.
_WEB_POLICY = """【公告】华海智汇差旅补贴调整（2025-11-01 生效）
一、国内出差住宿标准：一线城市每晚上限 650 元，二线城市 480 元。
二、市内交通补贴按实际发票报销，单日上限 120 元。
三、出差餐补：早餐 30 元、午餐 50 元、晚餐 50 元，合计单日餐补上限 130 元。
四、提前结束出差须在返回当日 18:00 前于 OA 销差。
发布部门：行政部 | 发布日期：2025-10-15"""

_WEB_PRODUCT = """【产品说明】星河协作套件 v3.2 发布说明（2026-01-08）
新增：看板泳道自定义字段；会议纪要一键导出 Markdown。
性能：文档协同延迟中位数降至 180ms（内网基准）。
兼容：桌面客户端需 ≥ 2.9.0；移动端建议升级至 3.1。
已知限制：离线编辑暂不支持公式块。
客服热线：400-800-2210（工作日 9:00-18:00）"""

_WEB_SECURITY = """【安全通报】生产库访问策略更新（编号 SEC-2025-44）
自 2025-12-01 起，个人账号不得直连生产数据库；须通过堡垒机工单审批。
磁盘加密与 5 分钟自动锁屏为必选项。
安全事件上报时限：发现后 30 分钟内通知安全应急小组。
联系邮箱：sec-ops@example.internal"""

_SANDBOX_SALES = """选用数据文件：sales_q1.csv
沙箱执行成功
===SUMMARY===
rows=120
region_mean_profit=72.5
region_count=4
华东_sum=4597.7
华北_sum=4178.5
华南_sum=4127.0
西南_sum=3890.2
"""

_SANDBOX_ATTEND = """选用数据文件：attendance.csv
沙箱执行成功
===SUMMARY===
rows=86
late_count=14
avg_late_minutes=18.6
dept_ops_headcount=12
"""

_SANDBOX_ORDERS = """选用数据文件：orders.csv
沙箱执行成功
===SUMMARY===
rows=240
cancel_rate=0.082
top_sku=SKU-A19
avg_order_amount=356.4
"""


def kb_cases() -> list[dict]:
    items: list[dict] = []
    # Distinct policy / product questions grounded in YZ fixtures
    specs = [
        ("gf_kb_01", "月度迟到超过几次取消全勤奖？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_02", "正常工作时间是几点到几点？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_03", "午休时段怎么规定？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_04", "迟到 30 分钟以内怎么计？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_05", "对外发布文档需要谁双签？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_06", "例会原则上控制在多少分钟以内？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_07", "周五下午为什么不安排跨部门会议？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_08", "安全事件须在多久内上报？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_09", "办公终端锁屏多久自动触发？", ["office_policy_txt"], ["kb", "policy"]),
        ("gf_kb_10", "Omni-Butler 单次最多批量上传几个文件？", ["product_guide_md"], ["kb", "product"]),
        ("gf_kb_11", "知识库支持哪些文件格式？", ["product_guide_md"], ["kb", "product"]),
        ("gf_kb_12", "单文件上传上限是多少 MB？", ["product_guide_md"], ["kb", "product"]),
        ("gf_kb_13", "答非所问时应该先检查什么？", ["product_guide_md"], ["kb", "product"]),
        ("gf_kb_14", "生成图表时界面会怎样？", ["product_guide_md"], ["kb", "product"]),
        # Unanswerable from fixtures
        ("gf_kb_15", "公司年终奖计算公式是什么？", ["office_policy_txt"], ["kb", "unanswerable"]),
        ("gf_kb_16", "总部搬迁到哪一座城市？", ["product_guide_md"], ["kb", "unanswerable"]),
    ]
    for cid, query, keys, tags in specs:
        items.append(
            _case(
                cid,
                source="kb",
                query=query,
                gold_doc_keys=keys,
                expect_unanswerable=cid in ("gf_kb_15", "gf_kb_16"),
                tags=tags,
            )
        )
    return items


def web_cases() -> list[dict]:
    items: list[dict] = []
    # Answerable from frozen notices
    answerable = [
        ("gf_web_01", "一线城市出差住宿上限是多少？", _WEB_POLICY, ["web", "travel"]),
        ("gf_web_02", "二线城市住宿标准多少？", _WEB_POLICY, ["web", "travel"]),
        ("gf_web_03", "市内交通补贴单日上限？", _WEB_POLICY, ["web", "travel"]),
        ("gf_web_04", "差旅餐补单日上限合计多少？", _WEB_POLICY, ["web", "travel"]),
        ("gf_web_05", "差旅补贴哪天生效？", _WEB_POLICY, ["web", "travel"]),
        ("gf_web_06", "星河协作套件新版本号是多少？", _WEB_PRODUCT, ["web", "product"]),
        ("gf_web_07", "文档协同延迟中位数降到多少？", _WEB_PRODUCT, ["web", "product"]),
        ("gf_web_08", "桌面客户端最低版本要求？", _WEB_PRODUCT, ["web", "product"]),
        ("gf_web_09", "客服热线是多少？", _WEB_PRODUCT, ["web", "product"]),
        ("gf_web_10", "生产库个人账号还能直连吗？", _WEB_SECURITY, ["web", "security"]),
        ("gf_web_11", "安全通报编号是什么？", _WEB_SECURITY, ["web", "security"]),
        ("gf_web_12", "磁盘加密是否必选？", _WEB_SECURITY, ["web", "security"]),
        # Unanswerable: numbers/facts absent from frozen context
        ("gf_web_13", "海外出差住宿上限是多少？", _WEB_POLICY, ["web", "unanswerable"]),
        ("gf_web_14", "星河套件月活用户有多少？", _WEB_PRODUCT, ["web", "unanswerable"]),
        ("gf_web_15", "安全事件罚款金额是多少？", _WEB_SECURITY, ["web", "unanswerable"]),
        ("gf_web_16", "客服周末是否值班？", _WEB_PRODUCT, ["web", "unanswerable"]),
    ]
    for cid, query, ctx, tags in answerable:
        items.append(
            _case(
                cid,
                source="web",
                query=query,
                contexts=[ctx],
                expect_unanswerable="unanswerable" in tags,
                tags=tags,
            )
        )
    return items


def sandbox_cases() -> list[dict]:
    items: list[dict] = []
    answerable = [
        ("gf_sbx_01", "销售表一共有多少行？", _SANDBOX_SALES, ["sandbox", "sales"]),
        ("gf_sbx_02", "各大区平均利润是多少？", _SANDBOX_SALES, ["sandbox", "sales"]),
        ("gf_sbx_03", "覆盖了几个大区？", _SANDBOX_SALES, ["sandbox", "sales"]),
        ("gf_sbx_04", "华东销售额汇总是多少？", _SANDBOX_SALES, ["sandbox", "sales"]),
        ("gf_sbx_05", "华北销售额汇总是多少？", _SANDBOX_SALES, ["sandbox", "sales"]),
        ("gf_sbx_06", "考勤表有多少行记录？", _SANDBOX_ATTEND, ["sandbox", "attend"]),
        ("gf_sbx_07", "迟到人次是多少？", _SANDBOX_ATTEND, ["sandbox", "attend"]),
        ("gf_sbx_08", "平均迟到多少分钟？", _SANDBOX_ATTEND, ["sandbox", "attend"]),
        ("gf_sbx_09", "运营部人数是多少？", _SANDBOX_ATTEND, ["sandbox", "attend"]),
        ("gf_sbx_10", "订单表有多少行？", _SANDBOX_ORDERS, ["sandbox", "orders"]),
        ("gf_sbx_11", "取消率是多少？", _SANDBOX_ORDERS, ["sandbox", "orders"]),
        ("gf_sbx_12", "平均订单金额是多少？", _SANDBOX_ORDERS, ["sandbox", "orders"]),
        ("gf_sbx_13", "销量最高的 SKU 是哪个？", _SANDBOX_ORDERS, ["sandbox", "orders"]),
        # Unanswerable: metric not in SUMMARY
        ("gf_sbx_14", "西南大区的中位数利润是多少？", _SANDBOX_SALES, ["sandbox", "unanswerable"]),
        ("gf_sbx_15", "迟到超过 60 分钟的人数？", _SANDBOX_ATTEND, ["sandbox", "unanswerable"]),
        ("gf_sbx_16", "退货金额合计是多少？", _SANDBOX_ORDERS, ["sandbox", "unanswerable"]),
    ]
    for cid, query, ctx, tags in answerable:
        items.append(
            _case(
                cid,
                source="sandbox",
                query=query,
                contexts=[ctx],
                needs_sandbox=True,
                expect_unanswerable="unanswerable" in tags,
                tags=tags,
            )
        )
    return items


def main() -> None:
    cases = kb_cases() + web_cases() + sandbox_cases()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for row in cases:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(cases)} cases → {OUT}")
    by = {}
    for c in cases:
        by[c["source"]] = by.get(c["source"], 0) + 1
    print("by source:", by)


if __name__ == "__main__":
    main()
