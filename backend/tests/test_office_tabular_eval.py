"""Unit tests for office tabular eval (no Docker required)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.eval.office_tabular import plan_matches  # noqa: E402
from app.services.analysis_ir import infer_join_from_message, numbers_equivalent  # noqa: E402
from app.services.data_analysis import _heuristic_spec, _render_analysis_code, refuse_multitable_without_join  # noqa: E402
from app.services.tabular_inspect import infer_tabular_schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "eval" / "fixtures"
DATASET = ROOT / "data" / "eval" / "office_tabular.jsonl"
GEN = ROOT / "scripts" / "gen_office_tabular_eval.py"


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_office_dataset_scale():
    lines = _load_cases()
    assert len(lines) >= 80
    assert sum(1 for c in lines if "join_ok" in c.get("tags", [])) >= 12
    assert sum(1 for c in lines if c.get("split") == "smoke") == 20


def test_generator_idempotent():
    before = DATASET.read_bytes()
    subprocess.run([sys.executable, str(GEN)], check=True, cwd=str(ROOT))
    after = DATASET.read_bytes()
    assert before == after


def test_office_compile_median_and_filter():
    schema = infer_tabular_schema(FIXTURES / "sales_regions.csv")
    med = _heuristic_spec("西南大区的利润中位数是多少", schema)
    assert med.operation == "median"
    code = _render_analysis_code(med)
    assert "===SUMMARY_JSON===" in code

    schema2 = infer_tabular_schema(FIXTURES / "attendance.csv")
    cnt = _heuristic_spec("迟到超过60分钟的人数", schema2)
    assert cnt.operation == "count"
    assert cnt.filters


def test_office_rate_derive_and_missing_bonus():
    schema = infer_tabular_schema(FIXTURES / "orders.csv")
    rate = _heuristic_spec("orders.csv 取消订单占比是多少", schema)
    assert rate.operation != "count"
    assert any(str(m.get("kind")) == "mean" for m in (rate.metrics or [])) or rate.derive

    sales = infer_tabular_schema(FIXTURES / "sales_regions.csv")
    miss = _heuristic_spec("年终奖中位数是多少", sales)
    assert miss.uncomputable
    assert any("年终奖" in str(u.get("missing_column") or "") for u in miss.uncomputable)


def test_median_filter_not_false_missing():
    schema = infer_tabular_schema(FIXTURES / "sales_regions.csv")
    spec = _heuristic_spec("西南大区利润中位数是多少", schema)
    assert spec.operation == "median"
    assert not spec.uncomputable


def test_office_join_refuse_uses_both_filenames():
    payload = refuse_multitable_without_join(
        "请关联 orders.csv 与 returns.csv 计算退货金额",
        ["orders.csv", "returns.csv"],
    )
    assert payload is not None
    assert "join" in json.dumps(payload, ensure_ascii=False)


def test_join_heuristic_infers_key():
    schemas = [
        ("orders.csv", infer_tabular_schema(FIXTURES / "orders.csv")),
        ("returns.csv", infer_tabular_schema(FIXTURES / "returns.csv")),
    ]
    left, right, key = infer_join_from_message(
        "关联 orders.csv returns.csv 按订单号，关联后订单金额总和",
        schemas,
    )
    assert left == "orders.csv"
    assert right == "returns.csv"
    assert key == "订单号"


def test_join_ok_gold_plan_present():
    join_cases = [c for c in _load_cases() if "join_ok" in c.get("tags", [])]
    assert join_cases
    for case in join_cases[:3]:
        gp = case.get("gold_plan") or {}
        assert gp.get("join_key")
        assert case.get("gold") is not None


def test_office_dry_compile_smoke_pass():
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from run_office_tabular_eval import _load_cases, eval_plan

    cases = _load_cases(split="smoke")
    results = eval_plan(cases)
    assert sum(1 for r in results if r.get("plan_ok")) == len(cases)


def test_dataset_has_concepts():
    lines = _load_cases()
    assert all(isinstance(c.get("concepts"), list) and c.get("concepts") for c in lines[:5])


def test_tabular_clean_sales_sum():
    import pandas as pd
    from app.services.tabular_clean import clean_sales_frame

    df = pd.read_csv(FIXTURES / "sales_regions.csv")
    cleaned = clean_sales_frame(df)
    assert float(cleaned["销售额"].sum(skipna=True)) > 2_900_000


def test_rate_equivalence_helper():
    assert numbers_equivalent(0.082, 8.2)
    assert numbers_equivalent(8.2, 0.082)


@pytest.mark.parametrize(
    "query,fixture",
    [
        ("这张表有年终奖列吗，合计是多少", "sales_regions.csv"),
        ("orders.csv 运费总和", "orders.csv"),
    ],
)
def test_missing_column_plan(query: str, fixture: str):
    schema = infer_tabular_schema(FIXTURES / fixture)
    spec = _heuristic_spec(query, schema)
    case = {"expect_uncomputable": True, "missing_tokens": ["年终奖" if "年终奖" in query else "运费"], "tags": ["missing"]}
    assert plan_matches(spec, case) or spec.uncomputable
