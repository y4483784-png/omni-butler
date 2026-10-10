#!/usr/bin/env python3
"""Build the 400-case YZ full-chain dataset from reviewed seeds + CSV facts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.contracts import YZCase, normalize_text

SEED = ROOT / "data" / "eval" / "seeds" / "yz_queries.jsonl"
ATTENDANCE_SEED = ROOT / "data" / "eval" / "seeds" / "yz_attendance.jsonl"
CSV_FIXTURE = ROOT / "tests" / "YZ测试文档" / "测试用例.csv"
OUT = ROOT / "data" / "eval" / "yz_fullchain.jsonl"


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _attendance_case(
    query: str,
    facts: list[str],
    truth: str,
    tags: list[str],
) -> dict:
    return {
        "query": query,
        "gold_doc_keys": ["attendance_csv"],
        "gold_facts": facts,
        "ground_truth": truth,
        "expect_unanswerable": False,
        "gold": {"intent": "rag", "tools": ["kb"]},
        "tags": ["attendance", *tags],
    }


def build_attendance_cases_from_csv() -> list[dict]:
    with CSV_FIXTURE.open(encoding="utf-8-sig", newline="") as handle:
        source = list(csv.DictReader(handle))
    if len(source) != 40:
        raise ValueError(f"expected 40 attendance fixture rows, got {len(source)}")

    rows: list[dict] = []
    # One comprehensive, natural query per employee (40).
    for item in source:
        facts = [
            item["姓名"],
            item["实际出勤天数"],
            item["迟到次数"],
            item["请假天数"],
            item["加班时长(小时)"],
        ]
        rows.append(
            _attendance_case(
                f"工号{item['工号']}的{item['姓名']}，实际出勤、迟到、请假和加班分别是多少？",
                facts,
                (
                    f"{item['姓名']}实际出勤{item['实际出勤天数']}天，"
                    f"迟到{item['迟到次数']}次，请假{item['请假天数']}天，"
                    f"加班{item['加班时长(小时)']}小时。"
                ),
                ["employee_summary"],
            )
        )

    # Focused employee questions use a different fact combination (20).
    for index, item in enumerate(source[:20]):
        if index % 4 == 0:
            query = f"{item['姓名']}的应出勤天数与实际出勤天数相差多少？"
            delta = int(item["应出勤天数"]) - int(item["实际出勤天数"])
            facts = [str(delta)]
            truth = f"相差{delta}天。"
            tag = "attendance_gap"
        elif index % 4 == 1:
            query = f"{item['姓名']}当月迟到了几次，同时请假多少天？"
            facts = [item["迟到次数"], item["请假天数"]]
            truth = f"迟到{item['迟到次数']}次，请假{item['请假天数']}天。"
            tag = "late_and_leave"
        elif index % 4 == 2:
            query = f"{item['姓名']}的加班时长是否超过十小时，具体是多少？"
            facts = [item["加班时长(小时)"]]
            verdict = "超过" if float(item["加班时长(小时)"]) > 10 else "未超过"
            truth = f"{verdict}十小时，实际为{item['加班时长(小时)']}小时。"
            tag = "overtime"
        else:
            query = f"请核对{item['工号']}所属部门和迟到次数。"
            facts = [item["部门"], item["迟到次数"]]
            truth = f"{item['工号']}属于{item['部门']}，迟到{item['迟到次数']}次。"
            tag = "department"
        rows.append(_attendance_case(query, facts, truth, [tag]))

    # Five aggregate judgments for each department (20).
    departments = sorted({item["部门"] for item in source})
    for department in departments:
        group = [item for item in source if item["部门"] == department]
        metrics = [
            (
                f"{department}共有多少名员工出现在考勤表中？",
                [str(len(group))],
                f"{department}共有{len(group)}名员工。",
                "headcount",
            ),
            (
                f"{department}所有员工迟到次数合计是多少？",
                [str(sum(int(item["迟到次数"]) for item in group))],
                f"迟到次数合计{sum(int(item['迟到次数']) for item in group)}次。",
                "late_total",
            ),
            (
                f"{department}累计加班时长是多少小时？",
                [_number(sum(float(item["加班时长(小时)"]) for item in group))],
                f"累计加班{_number(sum(float(item['加班时长(小时)']) for item in group))}小时。",
                "overtime_total",
            ),
            (
                f"{department}员工平均实际出勤多少天？",
                [_number(mean(int(item["实际出勤天数"]) for item in group))],
                f"平均实际出勤{_number(mean(int(item['实际出勤天数']) for item in group))}天。",
                "attendance_average",
            ),
            (
                f"{department}员工请假天数平均值是多少？",
                [_number(mean(float(item["请假天数"]) for item in group))],
                f"平均请假{_number(mean(float(item['请假天数']) for item in group))}天。",
                "leave_average",
            ),
        ]
        for query, facts, truth, tag in metrics:
            rows.append(_attendance_case(query, facts, truth, [tag, "department_aggregate"]))

    actual = [int(item["实际出勤天数"]) for item in source]
    expected = [int(item["应出勤天数"]) for item in source]
    late = [int(item["迟到次数"]) for item in source]
    leave = [float(item["请假天数"]) for item in source]
    overtime = [float(item["加班时长(小时)"]) for item in source]
    global_specs = [
        ("这份考勤表总共有多少名员工？", len(source), "名", "headcount"),
        ("全体员工实际出勤天数合计多少？", sum(actual), "天", "attendance_total"),
        ("全体员工应出勤天数合计多少？", sum(expected), "天", "expected_total"),
        ("表中迟到次数总计多少？", sum(late), "次", "late_total"),
        ("所有员工请假天数合计多少？", sum(leave), "天", "leave_total"),
        ("全部加班时长累计多少？", sum(overtime), "小时", "overtime_total"),
        ("平均每名员工实际出勤多少天？", mean(actual), "天", "attendance_average"),
        ("平均每名员工迟到多少次？", mean(late), "次", "late_average"),
        ("人均请假天数是多少？", mean(leave), "天", "leave_average"),
        ("人均加班时长是多少？", mean(overtime), "小时", "overtime_average"),
        ("实际出勤达到应出勤天数的员工有多少？", sum(a >= e for a, e in zip(actual, expected)), "名", "full_attendance"),
        ("实际出勤少于应出勤的员工有多少？", sum(a < e for a, e in zip(actual, expected)), "名", "attendance_gap"),
        ("迟到次数为零的员工共有多少？", sum(value == 0 for value in late), "名", "no_late"),
        ("迟到次数超过三次的员工有多少？", sum(value > 3 for value in late), "名", "late_threshold"),
        ("请假超过两天的员工有多少？", sum(value > 2 for value in leave), "名", "leave_threshold"),
        ("加班超过十小时的员工有多少？", sum(value > 10 for value in overtime), "名", "overtime_threshold"),
        ("考勤表涉及多少个不同部门？", len(departments), "个", "department_count"),
        ("最高迟到次数是多少？", max(late), "次", "late_max"),
        ("最高加班时长是多少？", max(overtime), "小时", "overtime_max"),
        ("全表实际出勤率是多少？", sum(actual) / sum(expected) * 100, "%", "attendance_rate"),
    ]
    for query, value, unit, tag in global_specs:
        rendered = _number(float(value))
        rows.append(
            _attendance_case(
                query,
                [rendered],
                f"结果为{rendered}{unit}。",
                [tag, "global_aggregate"],
            )
        )
    if len(rows) != 100:
        raise ValueError(f"attendance rows={len(rows)}, expected 100")
    return rows


def load_attendance_cases() -> list[dict]:
    if not ATTENDANCE_SEED.is_file():
        raise FileNotFoundError(
            f"missing frozen attendance seed: {ATTENDANCE_SEED}; "
            "run with --refresh-attendance-seed on a machine that has the CSV fixture"
        )
    rows = [
        json.loads(line)
        for line in ATTENDANCE_SEED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(rows) != 100:
        raise ValueError(f"expected 100 frozen attendance seeds, got {len(rows)}")
    return rows


def build_cases() -> list[dict]:
    seeds = [
        json.loads(line)
        for line in SEED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(seeds) != 300:
        raise ValueError(f"expected 300 reviewed non-attendance seeds, got {len(seeds)}")
    rows = [*seeds, *load_attendance_cases()]
    seen: set[str] = set()
    categories: Counter[str] = Counter()
    output: list[dict] = []
    category_names = {"policy", "product", "attendance", "multi_doc", "unanswerable", "chat"}
    for index, seed in enumerate(rows, start=1):
        key = normalize_text(str(seed.get("query") or ""))
        if not key or key in seen:
            raise ValueError(f"duplicate/empty normalized query: {seed.get('query')}")
        seen.add(key)
        category = category_names.intersection(seed.get("tags") or [])
        if len(category) != 1:
            raise ValueError(f"row {index} needs exactly one category tag: {sorted(category)}")
        categories.update(category)
        row = {"id": f"yz_{index:04d}", **seed}
        output.append(YZCase.model_validate(row).model_dump(mode="python"))
    expected = {
        "policy": 130,
        "product": 100,
        "attendance": 100,
        "multi_doc": 30,
        "unanswerable": 20,
        "chat": 20,
    }
    if categories != expected:
        raise ValueError(f"unexpected YZ categories: {dict(categories)}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-attendance-seed",
        action="store_true",
        help="recalculate the frozen 100-case attendance slice from the local CSV fixture",
    )
    args = parser.parse_args(argv)
    if args.refresh_attendance_seed:
        attendance = build_attendance_cases_from_csv()
        ATTENDANCE_SEED.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False, allow_nan=False) for row in attendance
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"refreshed {len(attendance)} attendance seeds -> {ATTENDANCE_SEED}")
    rows = build_cases()
    OUT.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} YZ cases -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
