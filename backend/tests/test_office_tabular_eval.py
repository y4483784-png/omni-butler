"""Unit tests for office tabular eval (no Docker required)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.eval.office_tabular import aggregate_report, plan_matches  # noqa: E402
from app.services.analysis_ir import infer_join_from_message, numbers_equivalent  # noqa: E402
from app.services.data_analysis import _heuristic_spec, _render_analysis_code, refuse_multitable_without_join  # noqa: E402
from app.services.tabular_inspect import infer_tabular_schema, merge_schemas  # noqa: E402

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
    assert lines
    assert sum(1 for c in lines if "join_ok" in c.get("tags", [])) >= 12


def test_generator_idempotent():
    """Same contract as the shared idempotency test: CRLF checkout is not drift."""
    from app.eval.jsonl_io import normalize_newlines

    before = DATASET.read_bytes()
    try:
        subprocess.run([sys.executable, str(GEN)], check=True, cwd=str(ROOT))
        after = DATASET.read_bytes()
        assert normalize_newlines(before) == normalize_newlines(after)
        assert b"\r" not in after
    finally:
        if DATASET.read_bytes() != before:
            DATASET.write_bytes(before)


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


def test_percent_cell_is_fraction():
    from app.services.tabular_clean import parse_numeric_cell

    assert parse_numeric_cell("12.5%") == pytest.approx(0.125)
    assert parse_numeric_cell('"68,000"') == 68000.0
    assert parse_numeric_cell("510,000") == 510000.0


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


def test_planner_error_is_not_a_heuristic_pass(monkeypatch):
    """A planner that produced nothing must fail the plan, not inherit the ablation arm."""
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import run_office_tabular_eval as runner
    from app.services.data_analysis import AnalysisOutcome

    case = next(c for c in _load_cases() if c["split"] == "smoke" and not c["expect_uncomputable"])
    monkeypatch.setattr(runner, "_PLANNER", "llm")
    monkeypatch.setattr(
        "app.sandbox.runner.docker_available", lambda *a, **k: True
    )
    monkeypatch.setattr(
        runner,
        "analyze_local_tables",
        lambda *a, **k: AnalysisOutcome(ok=True, steps=[], summary={"metrics": []}, ir=None),
    )
    rows = runner.eval_exec([case])
    row = rows[0]
    assert row["planner_used"] is True
    assert row["planner_ok"] is False
    assert row["plan_ok"] is False
    assert row["failure_kind"] == "planner_error"
    # The ablation arm still records what the heuristic planner would have said.
    assert "heuristic_plan_ok" in row

    report = aggregate_report(rows, mode="live")
    assert report["planner_error_count"] == 1
    assert report["planner_error_rate"] == pytest.approx(1.0)
    assert row["spec_source"] == "heuristic_fallback"


def test_nonempty_heuristic_ir_is_not_an_llm_plan(monkeypatch):
    """complete_json returning None still yields a heuristic ir. That is not llm."""
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import run_office_tabular_eval as runner
    from app.services.data_analysis import AnalysisOutcome

    case = next(c for c in _load_cases() if c["split"] == "smoke" and not c["expect_uncomputable"])
    monkeypatch.setattr(runner, "_PLANNER", "llm")
    monkeypatch.setattr("app.sandbox.runner.docker_available", lambda *a, **k: True)
    monkeypatch.setattr(
        runner,
        "analyze_local_tables",
        lambda *a, **k: AnalysisOutcome(
            ok=True,
            steps=[],
            summary={"metrics": []},
            ir={"operation": "sum", "measure_columns": ["金额"], "filters": []},
            plan_source="heuristic_fallback",
        ),
    )
    row = runner.eval_exec([case])[0]
    assert row["planner_ok"] is False
    assert row["spec_source"] == "heuristic_fallback"
    assert row["plan_ok"] is False
    assert row["failure_kind"] == "planner_error"


def test_plan_source_is_heuristic_fallback_when_llm_returns_none(monkeypatch):
    from app.services.data_analysis import _plan_analysis_spec
    from app.services.tabular_inspect import infer_tabular_schema

    monkeypatch.setattr("app.services.data_analysis.complete_json", lambda *a, **k: None)
    schema = infer_tabular_schema(FIXTURES / "orders.csv")
    spec, source = _plan_analysis_spec("orders.csv 金额总和", schema)
    assert source == "heuristic_fallback"
    assert spec.to_ir().to_dict()  # heuristic ir is non-empty
    monkeypatch.setattr(
        "app.services.data_analysis.complete_json",
        lambda *a, **k: {"operation": "sum", "measure_columns": ["金额"]},
    )
    _spec, source = _plan_analysis_spec("orders.csv 金额总和", schema)
    assert source == "llm"


def test_planner_error_rate_zero_when_planner_unused():
    rows = [
        {
            "id": "x",
            "split": "smoke",
            "difficulty": "easy",
            "tags": ["sum"],
            "expect_uncomputable": False,
            "plan_ok": True,
            "exec_ok": True,
            "planner_used": False,
            "planner_ok": None,
        }
    ]
    report = aggregate_report(rows, mode="live")
    assert report["planner_error_count"] == 0
    assert report["planner_error_rate"] == 0.0
    assert report["planner_used_count"] == 0


def test_merge_schemas_is_a_pure_column_union():
    """The joined shape is the union of the headers — no data rows are read."""
    orders = infer_tabular_schema(FIXTURES / "orders.csv")
    returns = infer_tabular_schema(FIXTURES / "returns.csv")
    before = (list(orders.columns), list(orders.measure_candidates), list(orders.dimension_candidates))

    merged = merge_schemas(orders, returns)

    assert merged.columns == list(dict.fromkeys([*orders.columns, *returns.columns]))
    # The shared join key must not be duplicated.
    assert merged.columns.count("订单号") == 1
    # Candidates come from both sides and stay inside the column set.
    assert {"金额", "退货金额"} <= set(merged.measure_candidates)
    assert set(merged.measure_candidates) <= set(merged.columns)
    assert set(merged.dimension_candidates) <= set(merged.columns)
    # Pure function: sources untouched.
    assert (
        list(orders.columns),
        list(orders.measure_candidates),
        list(orders.dimension_candidates),
    ) == before


def test_operation_none_is_not_a_match_for_rate_max_min_avg():
    from app.services.data_analysis import AnalysisSpec

    bare = AnalysisSpec(operation="none")
    for op in ("rate", "max", "min", "avg"):
        case = {"gold_plan": {"operation": op}, "tags": [], "expect_uncomputable": False}
        assert plan_matches(bare, case) is False
    # A rate encoded as mean-of-flag still matches; the operation alias is what was loose.
    rated = AnalysisSpec(operation="none", metrics=[{"kind": "mean", "column": "取消标记"}])
    assert plan_matches(
        rated,
        {"gold_plan": {"operation": "rate"}, "tags": ["rate"], "expect_uncomputable": False},
    )


def test_empty_measure_columns_do_not_satisfy_gold_measure():
    from app.services.data_analysis import AnalysisSpec

    case = {
        "gold_plan": {"operation": "sum", "measure": "预算"},
        "tags": ["sum"],
        "expect_uncomputable": False,
    }
    assert plan_matches(AnalysisSpec(operation="sum", measure_columns=[]), case) is False
    assert plan_matches(AnalysisSpec(operation="sum", measure_columns=["预算"]), case) is True
    assert plan_matches(AnalysisSpec(operation="sum", measure_columns=["金额"]), case) is False


def test_join_case_plan_matches_gold_filters():
    """ot_050 used to resolve its filter column from a row-count artefact of the real
    merge; the header union resolves it from the header, which is what the gold says."""
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import run_office_tabular_eval as runner

    case = next(c for c in _load_cases() if c["id"] == "ot_050")
    spec = runner._plan_spec(case)
    assert spec.operation == "sum"
    assert spec.join_key == case["gold_plan"]["join_key"]
    assert spec.filters == case["gold_plan"]["filters"]
    assert plan_matches(spec, case)


def test_join_case_specs_are_deterministic():
    """Same input, same plan — the union must not depend on row profiling."""
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import run_office_tabular_eval as runner

    joins = [c for c in _load_cases() if "join_ok" in c["tags"]]
    assert joins
    for case in joins:
        first = runner._plan_spec(case)
        second = runner._plan_spec(case)
        assert (first.operation, first.join_key, first.group_by) == (
            second.operation,
            second.join_key,
            second.group_by,
        )
