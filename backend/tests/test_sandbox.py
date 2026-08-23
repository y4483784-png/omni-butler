from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch
import asyncio

from app.sandbox.runner import ExecutionResult, run_code, run_code_async
from app.services.data_analysis import (
    AnalysisOutcome,
    AnalysisSpec,
    _heuristic_spec,
    _render_analysis_code,
    resolve_tabular_document,
    sandbox_hint,
)
from app.services.tabular_inspect import infer_tabular_schema


def test_sandbox_hint_detects_analysis():
    assert sandbox_hint("按月份汇总销售额并画折线图")
    assert not sandbox_hint("帮我写一首诗")


def test_resolve_tabular_document_prefers_csv():
    csv = MagicMock()
    csv.filename = "sales.csv"
    csv.stored_path = str(Path(__file__).resolve())
    pdf = MagicMock()
    pdf.filename = "a.pdf"
    pdf.stored_path = str(Path(__file__).resolve())

    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = [pdf, csv]

    with patch("app.services.data_analysis.Path.is_file", return_value=True):
        doc = resolve_tabular_document(db, user_id=1, document_ids=[1, 2])
    assert doc is csv


def test_infer_tabular_schema_csv_candidates():
    data = Path(__file__).resolve().parent / "_sample.csv"
    data.write_text("报考专业,政治,外语,业务课1\n计算机,80,81,120\n软件,75,79,118\n", encoding="utf-8")
    schema = infer_tabular_schema(data)
    assert "报考专业" in schema.dimension_candidates
    assert "政治" in schema.measure_candidates
    assert "外语" in schema.measure_candidates
    data.unlink(missing_ok=True)


def test_run_code_disabled():
    with patch("app.sandbox.runner.settings.sandbox_enabled", False):
        out = run_code("print(1)")
    assert out.error == "sandbox disabled"


def test_run_code_async_offloads_sync_runner():
    async def _go():
        with patch(
            "app.sandbox.runner.run_code",
            return_value=ExecutionResult(stdout="ok"),
        ) as mocked:
            out = await run_code_async("print(1)")
            assert out.stdout == "ok"
            mocked.assert_called_once()

    asyncio.run(_go())


def test_run_code_mocks_docker_success():
    data = Path(__file__).resolve().parent / "_sample.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        # Simulate chart written by container into the mounted out dir
        for i, part in enumerate(cmd):
            if part == "-v" and i + 1 < len(cmd) and ":/artifacts" in cmd[i + 1]:
                host = cmd[i + 1].split(":/artifacts")[0]
                Path(host).mkdir(parents=True, exist_ok=True)
                (Path(host) / "out.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
                (Path(host) / "out.svg").write_text(
                    "<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8"
                )
                (Path(host) / "out.json").write_text(
                    '{"points":[{"label":"A","series":"value","value":1}]}', encoding="utf-8"
                )
                break
        return FakeCompleted()

    with (
        patch("app.sandbox.runner.settings.sandbox_enabled", True),
        patch("app.sandbox.runner.shutil.which", return_value="docker"),
        patch("app.sandbox.runner.subprocess.run", side_effect=fake_run),
    ):
        out = run_code("print('ok')", data_path=data)
    assert not out.error
    assert out.stdout == "ok"
    assert out.artifacts and out.artifacts[0]["kind"] == "image"
    assert out.artifacts[0].get("svg")
    assert out.artifacts[0]["chart_points"][0]["label"] == "A"
    data.unlink(missing_ok=True)


def test_run_code_stages_incontainer_file_to_sandbox_tmp_dir(tmp_path):
    """docker.sock bind-mounts host paths; stage copies off /app-like locations."""
    from app.sandbox import runner as sbx

    src = tmp_path / "only-in-container.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    host_visible = tmp_path / "omni-tmp"
    host_visible.mkdir()
    seen: dict[str, str] = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        for i, part in enumerate(cmd):
            if part == "-v" and i + 1 < len(cmd) and ":/data/input" in cmd[i + 1]:
                seen["mount"] = cmd[i + 1].split(":/data/input")[0]
                break
        return FakeCompleted()

    with (
        patch("app.sandbox.runner.settings.sandbox_enabled", True),
        patch("app.sandbox.runner.settings.sandbox_tmp_dir", str(host_visible)),
        patch("app.core.tmpdir.settings.sandbox_tmp_dir", str(host_visible)),
        patch("app.sandbox.runner.shutil.which", return_value="docker"),
        patch("app.sandbox.runner._sandbox_image_ready", return_value=True),
        patch("app.sandbox.runner.subprocess.run", side_effect=fake_run),
    ):
        out = sbx.run_code_local("print(1)", data_path=src)
    assert not out.error
    assert "mount" in seen
    mount = Path(seen["mount"])
    assert mount.resolve().is_relative_to(host_visible.resolve())
    assert mount != src


def test_run_code_writes_artifacts_under_sandbox_tmp_dir(tmp_path):
    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    seen: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        for i, part in enumerate(cmd):
            if part == "-v" and i + 1 < len(cmd) and ":/artifacts" in cmd[i + 1]:
                host = cmd[i + 1].split(":/artifacts")[0]
                seen["host"] = host
                Path(host).mkdir(parents=True, exist_ok=True)
                (Path(host) / "out.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
                break
        return FakeCompleted()

    with (
        patch("app.sandbox.runner.settings.sandbox_enabled", True),
        patch("app.sandbox.runner.settings.sandbox_tmp_dir", str(tmp_path)),
        patch("app.core.tmpdir.settings.sandbox_tmp_dir", str(tmp_path)),
        patch("app.sandbox.runner.shutil.which", return_value="docker"),
        patch("app.sandbox.runner.subprocess.run", side_effect=fake_run),
    ):
        out = run_code("print('ok')")
    assert not out.error
    assert "host" in seen
    assert Path(seen["host"]).resolve().is_relative_to(tmp_path.resolve())


def test_run_code_via_sandbox_runner(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "stdout": "from-runner",
                "stderr": "",
                "timed_out": False,
                "artifacts": [],
                "error": "",
                "artifact_png_bytes": 0,
            }

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json):
            assert url.endswith("/run")
            assert json["code"] == "print(1)"
            return FakeResp()

    monkeypatch.setattr("app.sandbox.runner.settings.sandbox_enabled", True)
    monkeypatch.setattr(
        "app.sandbox.runner.settings.sandbox_runner_url", "http://sandbox-runner:8002"
    )
    monkeypatch.setattr("app.sandbox.runner.httpx.Client", FakeClient)
    out = run_code("print(1)")
    assert out.stdout == "from-runner"


def test_sandbox_server_run_endpoint():
    from fastapi.testclient import TestClient

    from app.sandbox.server import app as sandbox_app

    with patch(
        "app.sandbox.server.run_code_local",
        return_value=ExecutionResult(stdout="hi"),
    ):
        with TestClient(sandbox_app) as client:
            assert client.get("/health").json()["role"] == "sandbox-runner"
            r = client.post("/run", json={"code": "print(1)"})
            assert r.status_code == 200
            assert r.json()["stdout"] == "hi"


def test_run_analysis_retries_on_stderr():
    from app.services import data_analysis as da

    doc = MagicMock()
    doc.filename = "sales.csv"
    doc.stored_path = "/tmp/sales.csv"
    schema = MagicMock()
    schema.is_readable = True
    spec = AnalysisSpec(
        task_family="aggregate",
        group_by="地区",
        measure_columns=["销售额"],
        operation="sum",
        chart_type="bar",
        row_scope="aggregated_rows",
        multi_sheet_policy="single_sheet",
        output_contract={"summary_marker": "===SUMMARY===", "must_include": ["used_columns", "rows"]},
    )

    calls = {"n": 0}

    def fake_run_code(code, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return ExecutionResult(stderr="KeyError: month", error="KeyError: month")
        return ExecutionResult(
            stdout="===SUMMARY===\nused_columns=销售额\nrows=2\noperation=sum",
            artifacts=[{"kind": "image", "title": "t", "content": "data:image/png;base64,xx", "png_bytes": 12000}],
            artifact_png_bytes=12000,
        )

    with (
        patch.object(da, "resolve_tabular_document", return_value=doc),
        patch.object(da, "docker_available", return_value=True),
        patch.object(da, "document_local_path", return_value=nullcontext(Path(__file__))),
        patch.object(da, "infer_tabular_schema", return_value=schema),
        patch.object(da, "_plan_analysis_spec", return_value=spec),
        patch.object(da, "_render_analysis_code", side_effect=["print(1)", "print(2)"]),
        patch.object(da, "run_code", side_effect=fake_run_code),
        patch("app.services.data_analysis.settings.sandbox_max_retries", 3),
    ):
        out = da.run_analysis(MagicMock(), message="汇总", history=[], user_id=1)
    assert isinstance(out, AnalysisOutcome)
    assert out.ok
    assert calls["n"] == 2
    assert any("修正执行计划" in s for s in out.steps)


def test_heuristic_spec_all_rows_visualization():
    schema = MagicMock()
    schema.dimension_candidates = ["报考专业", "姓名"]
    schema.measure_candidates = ["政治", "外语", "业务课1", "业务课2"]
    schema.columns = ["报考专业", "姓名", "政治", "外语", "业务课1", "业务课2"]
    schema.date_candidates = []
    schema.sheets = []
    schema.aliases = {"英语": "外语"}
    spec = _heuristic_spec("画出所有考生政治，外语，业务课1，业务课2成绩的柱状图", schema)
    assert spec.row_scope == "all_rows"
    assert spec.operation == "none"
    assert spec.chart_type == "bar"
    assert spec.measure_columns[:2] == ["政治", "外语"]


def test_heuristic_median_and_filter_count():
    schema = MagicMock()
    schema.dimension_candidates = ["大区"]
    schema.measure_candidates = ["利润", "迟到分钟"]
    schema.columns = ["大区", "利润", "迟到分钟"]
    schema.date_candidates = []
    schema.sheets = []
    schema.aliases = {}
    med = _heuristic_spec("西南大区的利润中位数是多少", schema)
    assert med.operation == "median"
    assert any(m.get("kind") == "median" for m in med.metrics)
    assert any(f.get("value") == "西南" for f in med.filters) or any(
        "西南" in str(f.get("value")) for f in med.filters
    )
    cnt = _heuristic_spec("迟到超过60分钟的人数", schema)
    assert cnt.operation == "count"
    assert any(f.get("op") in ("gt", "ge") for f in cnt.filters)


def test_heuristic_never_silent_auto_avg():
    schema = MagicMock()
    schema.dimension_candidates = ["大区"]
    schema.measure_candidates = ["利润"]
    schema.columns = ["大区", "利润"]
    schema.date_candidates = []
    schema.sheets = []
    schema.aliases = {}
    spec = _heuristic_spec("看看利润情况", schema)
    assert spec.operation != "auto"
    assert spec.operation in ("profile", "sum", "avg", "median", "count", "none")


def test_render_emits_summary_json_and_median():
    spec = AnalysisSpec(
        task_family="aggregate",
        measure_columns=["利润"],
        operation="median",
        chart_type="table",
        row_scope="aggregated_rows",
        multi_sheet_policy="single_sheet",
        metrics=[{"id": "median_利润", "kind": "median", "column": "利润", "label": "利润中位数"}],
        asked_ids=["median_利润"],
        filters=[{"column": "大区", "op": "eq", "value": "西南"}],
        output_contract={"summary_marker": "===SUMMARY===", "must_include": ["used_columns", "rows"]},
    )
    code = _render_analysis_code(spec)
    assert "===SUMMARY_JSON===" in code
    assert "median" in code
    assert "apply_filters" in code


def test_missing_column_marked_uncomputable():
    from app.services.analysis_ir import build_heuristic_ir
    from app.services.tabular_inspect import SheetProfile, TabularSchema

    schema = TabularSchema(
        filename="t.csv",
        file_type=".csv",
        sheets=[SheetProfile(name="a", row_count=3, columns=["大区", "销售额"])],
        columns=["大区", "销售额"],
        measure_candidates=["销售额"],
        dimension_candidates=["大区"],
    )
    ir = build_heuristic_ir("年终奖中位数是多少", schema)
    assert ir.uncomputable
    assert any("年终奖" in (u.missing_column or "") for u in ir.uncomputable)
    assert not ir.metrics


def test_heuristic_rate_uses_derive_not_count():
    from app.services.tabular_inspect import SheetProfile, TabularSchema

    schema = TabularSchema(
        filename="orders.csv",
        file_type=".csv",
        sheets=[SheetProfile(name="a", row_count=1, columns=["取消数", "订单总数"])],
        columns=["取消数", "订单总数"],
        measure_candidates=["取消数", "订单总数"],
        dimension_candidates=[],
    )
    spec = _heuristic_spec("取消率是多少", schema)
    assert spec.derive
    assert spec.derive[0]["kind"] == "div"
    assert spec.operation != "count"


def test_refuse_multitable_without_join():
    from app.services.data_analysis import refuse_multitable_without_join

    payload = refuse_multitable_without_join(
        "请关联 orders.csv 与 returns.csv 计算退货金额",
        ["orders.csv", "returns.csv"],
    )
    assert payload and payload["missing"]
    assert refuse_multitable_without_join("销售额合计", ["orders.csv", "returns.csv"]) is None


def test_validate_patch_code_rejects_import_and_open():
    from app.services.analysis_ir import validate_patch_code

    assert validate_patch_code("df = df[df['a'] > 1]")
    assert validate_patch_code("import os") == ""
    assert validate_patch_code("open('/etc/passwd')") == ""


def test_render_analysis_code_contains_summary():
    spec = AnalysisSpec(
        task_family="aggregate",
        group_by="报考专业",
        measure_columns=["政治", "外语"],
        operation="avg",
        chart_type="bar",
        row_scope="aggregated_rows",
        multi_sheet_policy="concat_all",
        output_contract={"summary_marker": "===SUMMARY===", "must_include": ["used_columns", "rows"]},
    )
    code = _render_analysis_code(spec)
    assert "===SUMMARY===" in code
    assert "group_col" in code
    assert "measure_cols" in code
    assert "dump_chart_sidecar" in code
    assert ".svg" in code


if __name__ == "__main__":
    test_sandbox_hint_detects_analysis()
    test_resolve_tabular_document_prefers_csv()
    test_infer_tabular_schema_csv_candidates()
    test_run_code_disabled()
    test_run_code_mocks_docker_success()
    test_run_analysis_retries_on_stderr()
    test_heuristic_spec_all_rows_visualization()
    test_heuristic_median_and_filter_count()
    test_render_analysis_code_contains_summary()
    print("ok")
