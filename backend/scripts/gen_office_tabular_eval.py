#!/usr/bin/env python3
"""Generate office tabular eval fixtures + jsonl with pandas-frozen gold standards."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "eval" / "fixtures"
OUT_JSONL = ROOT / "data" / "eval" / "office_tabular.jsonl"
SEED = 42

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.tabular_clean import clean_sales_frame  # noqa: E402

_TAG_CONCEPT = {
    "sum": "aggregation",
    "count": "aggregation",
    "median": "aggregation",
    "max": "aggregation",
    "min": "aggregation",
    "mean": "aggregation",
    "count_distinct": "aggregation",
    "group": "aggregation",
    "filter": "filter",
    "date": "filter",
    "rate": "derive",
    "dirty": "dirty_parse",
    "join_ok": "join",
    "join_refuse": "refusal",
    "missing": "refusal",
    "followup": "followup",
}


def _tags_to_concepts(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        c = _TAG_CONCEPT.get(tag)
        if c and c not in out:
            out.append(c)
    return out or ["aggregation"]


def _write_fixtures() -> dict[str, pd.DataFrame]:
    rng = random.Random(SEED)
    FIXTURES.mkdir(parents=True, exist_ok=True)

    depts = ["研发", "产品", "销售", "运营"]
    employees = []
    for i in range(1, 21):
        employees.append(
            {
                "工号": f"E{i:03d}",
                "姓名": f"员工{i}",
                "部门": depts[i % len(depts)],
            }
        )
    df_emp = pd.DataFrame(employees)

    attendance_rows = []
    base = date(2025, 1, 6)
    for i in range(1, 21):
        for w in range(4):
            d = base + timedelta(days=w * 7 + (i % 5))
            late = rng.randint(0, 120)
            attendance_rows.append({"工号": f"E{i:03d}", "迟到分钟": late, "日期": d.isoformat()})
    df_att = pd.DataFrame(attendance_rows)

    regions = ["华东", "华北", "华南", "西南", "西北"]
    orders_rows = []
    for i in range(1, 81):
        orders_rows.append(
            {
                "订单号": f"O{i:04d}",
                "客户": f"客户{rng.randint(1, 30)}",
                "大区": regions[i % len(regions)],
                "金额": round(rng.uniform(100, 5000), 2),
                "取消标记": 1 if rng.random() < 0.12 else 0,
                "下单日": (date(2025, 1, 1) + timedelta(days=rng.randint(0, 90))).isoformat(),
            }
        )
    df_ord = pd.DataFrame(orders_rows)

    returns_rows = []
    for idx in rng.sample(range(1, 81), 28):
        returns_rows.append(
            {
                "订单号": f"O{idx:04d}",
                "退货金额": round(rng.uniform(50, 800), 2),
                "退货日": (date(2025, 2, 1) + timedelta(days=rng.randint(0, 60))).isoformat(),
            }
        )
    df_ret = pd.DataFrame(returns_rows)

    sales_rows = [
        {"大区": "华东", "利润": 120000, "销售额": 500000},
        {"大区": "华北", "利润": 95000, "销售额": 420000},
        {"大区": "华南", "利润": 88000, "销售额": 390000},
        {"大区": "西南", "利润": 72000, "销售额": 310000},
        {"大区": "西北", "利润": 54000, "销售额": 250000},
        {"大区": "东北", "利润": 61000, "销售额": 270000},
        {"大区": "西南", "利润": "68,000", "销售额": "305,000"},
        {"大区": "华东", "利润": "12.5%", "销售额": "510,000"},
    ]
    df_sales = pd.DataFrame(sales_rows)

    products = [{"SKU": f"SKU{i:03d}", "单价": round(rng.uniform(10, 500), 2)} for i in range(1, 16)]
    df_prod = pd.DataFrame(products)

    frames = {
        "employees.csv": df_emp,
        "attendance.csv": df_att,
        "orders.csv": df_ord,
        "returns.csv": df_ret,
        "sales_regions.csv": df_sales,
        "products.csv": df_prod,
    }
    for name, df in frames.items():
        df.to_csv(FIXTURES / name, index=False, encoding="utf-8-sig")
    return frames


def _clean_sales(df: pd.DataFrame) -> pd.DataFrame:
    return clean_sales_frame(df)


def _case(
    cid: str,
    query: str,
    *,
    split: str,
    difficulty: str,
    tags: list[str],
    fixtures: list[str],
    gold: float | None = None,
    gold_plan: dict | None = None,
    tolerance: float = 0.01,
    expect_uncomputable: bool = False,
    missing_tokens: list[str] | None = None,
    prior_ir: dict | None = None,
    accept_percent: bool = False,
    require_derive: bool = False,
) -> dict:
    primary = fixtures[0]
    row: dict = {
        "id": cid,
        "query": query,
        "split": split,
        "difficulty": difficulty,
        "tags": tags,
        "concepts": _tags_to_concepts(tags),
        "fixture": primary,
        "fixtures": fixtures,
        "tolerance": tolerance,
        "expect_uncomputable": expect_uncomputable,
        "missing_tokens": missing_tokens or [],
        "accept_percent": accept_percent,
        "require_derive": require_derive,
    }
    if gold is not None:
        row["gold"] = gold
    if gold_plan:
        row["gold_plan"] = gold_plan
    if prior_ir:
        row["prior_ir"] = prior_ir
    return row


def _build_cases(frames: dict[str, pd.DataFrame]) -> list[dict]:
    sales = _clean_sales(frames["sales_regions.csv"])
    att = frames["attendance.csv"]
    emp = frames["employees.csv"]
    ord_df = frames["orders.csv"]
    ret = frames["returns.csv"]
    regions = ["华东", "华北", "华南", "西南", "西北"]

    cases: list[dict] = []
    n = 0

    def add(**kwargs):
        nonlocal n
        n += 1
        cid = kwargs.pop("id", None) or f"ot_{n:03d}"
        split = kwargs.pop("split", "full")
        if n <= 20:
            split = "smoke"
        cases.append(_case(cid, split=split, **kwargs))

    # --- legacy smoke semantics (6 original themes) ---
    add(
        id="ot_001",
        query="sales_regions.csv 销售额合计是多少",
        difficulty="easy",
        tags=["sum"],
        fixtures=["sales_regions.csv"],
        gold=float(sales["销售额"].sum(skipna=True)),
        gold_plan={"operation": "sum", "filters": [], "derive": False},
    )
    add(
        id="ot_002",
        query="西南大区利润中位数是多少",
        difficulty="hard",
        tags=["median", "filter"],
        fixtures=["sales_regions.csv"],
        gold=float(sales.loc[sales["大区"] == "西南", "利润"].median()),
        gold_plan={
            "operation": "median",
            "filters": [{"column": "大区", "op": "eq", "value": "西南"}],
            "derive": False,
        },
    )
    add(
        id="ot_003",
        query="orders.csv 取消订单占比是多少",
        difficulty="hard",
        tags=["rate", "dirty"],
        fixtures=["orders.csv"],
        gold=float(ord_df["取消标记"].mean()),
        gold_plan={"operation": "rate", "filters": [], "derive": True},
        require_derive=True,
        accept_percent=True,
    )
    add(
        id="ot_004",
        query="这张表有年终奖列吗，合计是多少",
        difficulty="easy",
        tags=["missing"],
        fixtures=["sales_regions.csv"],
        expect_uncomputable=True,
        missing_tokens=["年终奖"],
        gold_plan={"uncomputable": True},
    )
    add(
        id="ot_005",
        query="attendance.csv 迟到超过 60 分钟的记录有多少条",
        difficulty="easy",
        tags=["filter", "count"],
        fixtures=["attendance.csv"],
        gold=float((att["迟到分钟"] > 60).sum()),
        gold_plan={
            "operation": "count",
            "filters": [{"column": "迟到分钟", "op": "gt", "value": 60}],
            "derive": False,
        },
    )
    add(
        id="ot_006",
        query="请关联 orders.csv 和 returns.csv 算退货总额",
        difficulty="hard",
        tags=["join_refuse"],
        fixtures=["orders.csv", "returns.csv"],
        expect_uncomputable=True,
        missing_tokens=["join"],
        gold_plan={"uncomputable": True},
    )

    # --- single-table easy (~25 total easy singles) ---
    easy_specs = [
        ("orders.csv 订单金额总和", ["orders.csv"], ["sum"], float(ord_df["金额"].sum()), {"operation": "sum"}),
        ("orders.csv 有多少条订单", ["orders.csv"], ["count"], float(len(ord_df)), {"operation": "count"}),
        ("华东大区的订单金额合计", ["orders.csv"], ["sum", "filter"], float(ord_df.loc[ord_df["大区"] == "华东", "金额"].sum()), {
            "operation": "sum",
            "filters": [{"column": "大区", "op": "eq", "value": "华东"}],
        }),
        ("attendance.csv 迟到分钟总和", ["attendance.csv"], ["sum"], float(att["迟到分钟"].sum()), {"operation": "sum"}),
        ("employees.csv 有多少名员工", ["employees.csv"], ["count"], float(len(emp)), {"operation": "count"}),
        ("研发部门有多少人", ["employees.csv"], ["filter", "count"], float((emp["部门"] == "研发").sum()), {
            "operation": "count",
            "filters": [{"column": "部门", "op": "eq", "value": "研发"}],
        }),
        ("sales_regions.csv 利润总和", ["sales_regions.csv"], ["sum"], float(sales["利润"].sum(skipna=True)), {"operation": "sum"}),
        ("华北销售额是多少", ["sales_regions.csv"], ["sum", "filter"], float(sales.loc[sales["大区"] == "华北", "销售额"].sum()), {
            "operation": "sum",
            "filters": [{"column": "大区", "op": "eq", "value": "华北"}],
        }),
        ("orders.csv 取消订单有多少单", ["orders.csv"], ["filter", "count"], float(ord_df["取消标记"].sum()), {
            "operation": "count",
            "filters": [{"column": "取消标记", "op": "eq", "value": 1}],
        }),
        ("products.csv 单价最高是多少", ["products.csv"], ["max"], float(frames["products.csv"]["单价"].max()), {"operation": "max"}),
        ("products.csv 单价最低是多少", ["products.csv"], ["min"], float(frames["products.csv"]["单价"].min()), {"operation": "min"}),
        ("attendance.csv 平均迟到分钟", ["attendance.csv"], ["mean"], float(att["迟到分钟"].mean()), {"operation": "avg"}),
        ("华南大区订单数", ["orders.csv"], ["filter", "count"], float((ord_df["大区"] == "华南").sum()), {
            "operation": "count",
            "filters": [{"column": "大区", "op": "eq", "value": "华南"}],
        }),
        ("西北大区利润合计", ["sales_regions.csv"], ["sum", "filter"], float(sales.loc[sales["大区"] == "西北", "利润"].sum()), {
            "operation": "sum",
            "filters": [{"column": "大区", "op": "eq", "value": "西北"}],
        }),
        ("returns.csv 退货金额总和", ["returns.csv"], ["sum"], float(ret["退货金额"].sum()), {"operation": "sum"}),
        ("returns.csv 有多少笔退货", ["returns.csv"], ["count"], float(len(ret)), {"operation": "count"}),
        ("销售部门员工人数", ["employees.csv"], ["filter", "count"], float((emp["部门"] == "销售").sum()), {
            "operation": "count",
            "filters": [{"column": "部门", "op": "eq", "value": "销售"}],
        }),
        ("orders.csv 金额平均值", ["orders.csv"], ["mean"], float(ord_df["金额"].mean()), {"operation": "avg"}),
        ("attendance.csv 记录条数", ["attendance.csv"], ["count"], float(len(att)), {"operation": "count"}),
    ]
    for q, fix, tags, gold, gp in easy_specs:
        add(query=q, difficulty="easy", tags=tags, fixtures=fix, gold=gold, gold_plan={**gp, "derive": False})

    # --- single-table hard ---
    merged_att = att.merge(emp, on="工号", how="inner")
    rd_late = merged_att[(merged_att["部门"] == "研发") & (merged_att["迟到分钟"] > 60)]
    hard_specs = [
        (
            "华东大区利润中位数",
            ["sales_regions.csv"],
            ["median", "filter"],
            float(sales.loc[sales["大区"] == "华东", "利润"].median()),
            {"operation": "median", "filters": [{"column": "大区", "op": "eq", "value": "华东"}]},
        ),
        (
            "orders.csv 按大区分组统计订单金额总和",
            ["orders.csv"],
            ["sum", "group"],
            float(ord_df["金额"].sum()),
            {"operation": "sum", "group_by": "大区"},
        ),
        (
            "attendance.csv 迟到分钟中位数",
            ["attendance.csv"],
            ["median"],
            float(att["迟到分钟"].median()),
            {"operation": "median"},
        ),
        (
            "orders.csv 未取消订单金额合计",
            ["orders.csv"],
            ["sum", "filter"],
            float(ord_df.loc[ord_df["取消标记"] == 0, "金额"].sum()),
            {"operation": "sum", "filters": [{"column": "取消标记", "op": "eq", "value": 0}]},
        ),
        (
            "sales_regions.csv 销售额最大值",
            ["sales_regions.csv"],
            ["max", "dirty"],
            float(sales["销售额"].max()),
            {"operation": "max"},
        ),
        (
            "sales_regions.csv 利润最小值",
            ["sales_regions.csv"],
            ["min", "dirty"],
            float(sales["利润"].min()),
            {"operation": "min"},
        ),
        (
            "orders.csv 2025年1月下单的订单有多少",
            ["orders.csv"],
            ["filter", "count", "date"],
            float(ord_df["下单日"].str.startswith("2025-01").sum()),
            {"operation": "count"},
        ),
        (
            "attendance.csv 迟到超过90分钟的有多少条",
            ["attendance.csv"],
            ["filter", "count"],
            float((att["迟到分钟"] > 90).sum()),
            {"operation": "count", "filters": [{"column": "迟到分钟", "op": "gt", "value": 90}]},
        ),
        (
            "orders.csv 西南大区取消率",
            ["orders.csv"],
            ["rate", "filter"],
            float(ord_df.loc[ord_df["大区"] == "西南", "取消标记"].mean()),
            {"operation": "rate", "filters": [{"column": "大区", "op": "eq", "value": "西南"}], "derive": True},
            True,
        ),
        (
            "orders.csv 金额大于3000的订单有多少",
            ["orders.csv"],
            ["filter", "count"],
            float((ord_df["金额"] > 3000).sum()),
            {"operation": "count", "filters": [{"column": "金额", "op": "gt", "value": 3000}]},
        ),
        (
            "returns.csv 退货金额中位数",
            ["returns.csv"],
            ["median"],
            float(ret["退货金额"].median()),
            {"operation": "median"},
        ),
        (
            "employees.csv 产品部门人数",
            ["employees.csv"],
            ["filter", "count"],
            float((emp["部门"] == "产品").sum()),
            {"operation": "count", "filters": [{"column": "部门", "op": "eq", "value": "产品"}]},
        ),
        (
            "sales_regions.csv 按大区统计利润总和",
            ["sales_regions.csv"],
            ["sum", "group", "dirty"],
            float(sales["利润"].sum(skipna=True)),
            {"operation": "sum", "group_by": "大区"},
        ),
        (
            "orders.csv 客户数量（去重）",
            ["orders.csv"],
            ["count_distinct"],
            float(ord_df["客户"].nunique()),
            {"operation": "count_distinct"},
        ),
        (
            "attendance.csv 迟到超过120分钟占比",
            ["attendance.csv"],
            ["rate"],
            float((att["迟到分钟"] > 120).mean()),
            {"operation": "rate", "derive": True},
            True,
        ),
    ]
    for item in hard_specs:
        q, fix, tags, gold, gp = item[:5]
        accept = item[5] if len(item) > 5 else False
        add(
            query=q,
            difficulty="hard",
            tags=tags,
            fixtures=fix,
            gold=gold,
            gold_plan={**gp, "derive": gp.get("derive", False)},
            require_derive=gp.get("derive", False),
            accept_percent=accept,
        )

    # --- join_ok (~15) ---
    inner = ord_df.merge(ret, on="订单号", how="inner")
    join_specs = [
        (
            "按订单号关联 orders.csv 与 returns.csv，退货金额合计是多少",
            ["orders.csv", "returns.csv"],
            float(inner["退货金额"].sum()),
            {"operation": "sum", "join_key": "订单号", "join_left": "orders.csv", "join_right": "returns.csv"},
        ),
        (
            "用订单号连接 orders.csv 和 returns.csv，有多少笔退货订单",
            ["orders.csv", "returns.csv"],
            float(len(inner)),
            {"operation": "count", "join_key": "订单号", "join_left": "orders.csv", "join_right": "returns.csv"},
        ),
        (
            "orders.csv 与 returns.csv 按订单号关联后，退货金额平均值",
            ["orders.csv", "returns.csv"],
            float(inner["退货金额"].mean()),
            {"operation": "avg", "join_key": "订单号", "join_left": "orders.csv", "join_right": "returns.csv"},
        ),
        (
            "关联 orders.csv returns.csv 按订单号，关联后订单金额总和",
            ["orders.csv", "returns.csv"],
            float(inner["金额"].sum()),
            {"operation": "sum", "join_key": "订单号", "join_left": "orders.csv", "join_right": "returns.csv"},
        ),
        (
            "按订单号关联 orders.csv 与 returns.csv，退货金额中位数",
            ["orders.csv", "returns.csv"],
            float(inner["退货金额"].median()),
            {"operation": "median", "join_key": "订单号", "join_left": "orders.csv", "join_right": "returns.csv"},
        ),
        (
            "按工号关联 attendance.csv 与 employees.csv，研发部门迟到超过60分钟的人数",
            ["attendance.csv", "employees.csv"],
            float(len(rd_late)),
            {
                "operation": "count",
                "join_key": "工号",
                "join_left": "attendance.csv",
                "join_right": "employees.csv",
                "filters": [{"column": "部门", "op": "eq", "value": "研发"}, {"column": "迟到分钟", "op": "gt", "value": 60}],
            },
        ),
        (
            "用工号连接 attendance.csv 和 employees.csv，销售部门迟到分钟总和",
            ["attendance.csv", "employees.csv"],
            float(
                merged_att.loc[merged_att["部门"] == "销售", "迟到分钟"].sum()
            ),
            {
                "operation": "sum",
                "join_key": "工号",
                "join_left": "attendance.csv",
                "join_right": "employees.csv",
                "filters": [{"column": "部门", "op": "eq", "value": "销售"}],
            },
        ),
        (
            "按工号关联 attendance.csv 与 employees.csv，产品部门平均迟到分钟",
            ["attendance.csv", "employees.csv"],
            float(merged_att.loc[merged_att["部门"] == "产品", "迟到分钟"].mean()),
            {
                "operation": "avg",
                "join_key": "工号",
                "join_left": "attendance.csv",
                "join_right": "employees.csv",
                "filters": [{"column": "部门", "op": "eq", "value": "产品"}],
            },
        ),
        (
            "attendance.csv 与 employees.csv 按工号关联，运营部门人数",
            ["attendance.csv", "employees.csv"],
            float(merged_att.loc[merged_att["部门"] == "运营", "工号"].nunique()),
            {
                "operation": "count_distinct",
                "join_key": "工号",
                "join_left": "attendance.csv",
                "join_right": "employees.csv",
                "filters": [{"column": "部门", "op": "eq", "value": "运营"}],
            },
        ),
        (
            "按订单号关联 orders.csv 与 returns.csv，华东大区退货金额合计",
            ["orders.csv", "returns.csv"],
            float(inner.loc[inner["大区"] == "华东", "退货金额"].sum()),
            {
                "operation": "sum",
                "join_key": "订单号",
                "join_left": "orders.csv",
                "join_right": "returns.csv",
                "filters": [{"column": "大区", "op": "eq", "value": "华东"}],
            },
        ),
        (
            "orders.csv returns.csv 按订单号连接，退货金额最大值",
            ["orders.csv", "returns.csv"],
            float(inner["退货金额"].max()),
            {"operation": "max", "join_key": "订单号", "join_left": "orders.csv", "join_right": "returns.csv"},
        ),
        (
            "按工号关联 attendance.csv 与 employees.csv，迟到超过100分钟记录数",
            ["attendance.csv", "employees.csv"],
            float((merged_att["迟到分钟"] > 100).sum()),
            {
                "operation": "count",
                "join_key": "工号",
                "join_left": "attendance.csv",
                "join_right": "employees.csv",
                "filters": [{"column": "迟到分钟", "op": "gt", "value": 100}],
            },
        ),
        (
            "按订单号关联 orders.csv 与 returns.csv，退货金额大于500的有多少",
            ["orders.csv", "returns.csv"],
            float((inner["退货金额"] > 500).sum()),
            {
                "operation": "count",
                "join_key": "订单号",
                "join_left": "orders.csv",
                "join_right": "returns.csv",
                "filters": [{"column": "退货金额", "op": "gt", "value": 500}],
            },
        ),
    ]
    for q, fix, gold, gp in join_specs:
        add(
            query=q,
            difficulty="hard",
            tags=["join_ok"],
            fixtures=fix,
            gold=gold,
            gold_plan={**gp, "derive": False},
        )

    # --- join / missing refuse ---
    refuse_specs = [
        ("请把 orders.csv 和 returns.csv 关联算总额", ["orders.csv", "returns.csv"], ["join_refuse"], ["join"]),
        ("关联 attendance.csv 与 employees.csv 算平均迟到", ["attendance.csv", "employees.csv"], ["join_refuse"], ["join"]),
        ("orders.csv 和 products.csv 按 SKU 关联算金额", ["orders.csv", "products.csv"], ["join_refuse"], ["join", "SKU"]),
        ("只用 orders.csv 但要和 returns.csv 关联算退货", ["orders.csv"], ["join_refuse", "missing"], ["join"]),
        ("sales_regions.csv 和 orders.csv 关联算利润", ["sales_regions.csv", "orders.csv"], ["join_refuse"], ["join"]),
        ("按客户号关联 orders.csv 与 returns.csv", ["orders.csv", "returns.csv"], ["join_refuse"], ["join", "客户号"]),
        ("这张表年终奖合计", ["employees.csv"], ["missing"], ["年终奖"]),
        ("employees.csv 工龄合计", ["employees.csv"], ["missing"], ["工龄"]),
        ("orders.csv 运费总和", ["orders.csv"], ["missing"], ["运费"]),
        ("returns.csv 客户满意度平均", ["returns.csv"], ["missing"], ["满意度"]),
        ("公司年假制度是多少天", ["employees.csv"], ["missing"], ["年假"]),
        ("products.csv 库存数量合计", ["products.csv"], ["missing"], ["库存"]),
        ("attendance.csv 与 products.csv 关联", ["attendance.csv", "products.csv"], ["join_refuse"], ["join"]),
    ]
    for q, fix, tags, tokens in refuse_specs:
        add(
            query=q,
            difficulty="hard" if "join_refuse" in tags else "easy",
            tags=tags,
            fixtures=fix,
            expect_uncomputable=True,
            missing_tokens=tokens,
            gold_plan={"uncomputable": True},
        )

    # --- follow-up (~8) ---
    filt_ir = {
        "task_family": "filter",
        "operation": "count",
        "filters": [{"column": "大区", "op": "eq", "value": "西南"}],
        "source_hint": "orders.csv",
    }
    sw = ord_df.loc[ord_df["大区"] == "西南", "金额"]
    add(
        query="西南大区有多少订单",
        difficulty="easy",
        tags=["filter", "count", "followup"],
        fixtures=["orders.csv"],
        gold=float((ord_df["大区"] == "西南").sum()),
        gold_plan={"operation": "count", "filters": [{"column": "大区", "op": "eq", "value": "西南"}]},
    )
    add(
        query="这些订单金额中位数是多少",
        difficulty="hard",
        tags=["median", "followup"],
        fixtures=["orders.csv"],
        gold=float(sw.median()),
        gold_plan={"operation": "median", "filters": [{"column": "大区", "op": "eq", "value": "西南"}]},
        prior_ir=filt_ir,
    )
    add(
        query="attendance.csv 迟到超过60分钟有多少",
        difficulty="easy",
        tags=["filter", "count", "followup"],
        fixtures=["attendance.csv"],
        gold=float((att["迟到分钟"] > 60).sum()),
        gold_plan={"operation": "count", "filters": [{"column": "迟到分钟", "op": "gt", "value": 60}]},
    )
    add(
        query="那这些记录的平均迟到分钟呢",
        difficulty="hard",
        tags=["mean", "followup"],
        fixtures=["attendance.csv"],
        gold=float(att.loc[att["迟到分钟"] > 60, "迟到分钟"].mean()),
        gold_plan={"operation": "avg", "filters": [{"column": "迟到分钟", "op": "gt", "value": 60}]},
        prior_ir={
            "task_family": "filter",
            "operation": "count",
            "filters": [{"column": "迟到分钟", "op": "gt", "value": 60}],
            "source_hint": "attendance.csv",
        },
    )
    add(
        query="研发部门员工有哪些",
        difficulty="easy",
        tags=["filter", "followup"],
        fixtures=["employees.csv"],
        gold=float((emp["部门"] == "研发").sum()),
        gold_plan={"operation": "count", "filters": [{"column": "部门", "op": "eq", "value": "研发"}]},
    )
    add(
        query="他们平均迟到多少分钟",
        difficulty="hard",
        tags=["join_ok", "followup"],
        fixtures=["attendance.csv", "employees.csv"],
        gold=float(merged_att.loc[merged_att["部门"] == "研发", "迟到分钟"].mean()),
        gold_plan={
            "operation": "avg",
            "join_key": "工号",
            "join_left": "attendance.csv",
            "join_right": "employees.csv",
            "filters": [{"column": "部门", "op": "eq", "value": "研发"}],
        },
    )
    add(
        query="orders.csv 华东大区订单金额合计",
        difficulty="easy",
        tags=["sum", "filter", "followup"],
        fixtures=["orders.csv"],
        gold=float(ord_df.loc[ord_df["大区"] == "华东", "金额"].sum()),
        gold_plan={"operation": "sum", "filters": [{"column": "大区", "op": "eq", "value": "华东"}]},
    )
    add(
        query="其中取消订单占比",
        difficulty="hard",
        tags=["rate", "followup"],
        fixtures=["orders.csv"],
        gold=float(ord_df.loc[ord_df["大区"] == "华东", "取消标记"].mean()),
        gold_plan={
            "operation": "rate",
            "filters": [{"column": "大区", "op": "eq", "value": "华东"}],
            "derive": True,
        },
        require_derive=True,
        accept_percent=True,
    )

    # pad to ~90 if short
    while len(cases) < 88:
        i = len(cases)
        region = regions[i % len(regions)]
        g = float(ord_df.loc[ord_df["大区"] == region, "金额"].sum())
        add(
            query=f"orders.csv {region}大区订单金额合计",
            difficulty="easy" if i % 2 == 0 else "hard",
            tags=["sum", "filter"],
            fixtures=["orders.csv"],
            gold=g,
            gold_plan={"operation": "sum", "filters": [{"column": "大区", "op": "eq", "value": region}]},
        )

    return cases


def _enrich_case(row: dict) -> dict:
    if "rate" in row.get("tags", []):
        gp = dict(row.get("gold_plan") or {})
        gp.setdefault("rate_kind", "mean")
        row["gold_plan"] = gp
    if "dirty" in row.get("tags", []):
        row.setdefault("gold_method", "pandas_clean_sum")
    return row


def main() -> None:
    frames = _write_fixtures()
    cases = [_enrich_case(c) for c in _build_cases(frames)]
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for row in cases:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    smoke = sum(1 for c in cases if c.get("split") == "smoke")
    join_ok = sum(1 for c in cases if "join_ok" in c.get("tags", []))
    print(f"Wrote {len(cases)} cases ({smoke} smoke, {join_ok} join_ok) -> {OUT_JSONL}")


if __name__ == "__main__":
    main()
