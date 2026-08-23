"""Data analysis helper: AnalysisIR -> template code -> sandbox execution."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.llm import complete_json
from app.core.messages import SANDBOX_TIMEOUT_MESSAGE
from app.models.models import Document
from app.sandbox.runner import ExecutionResult, docker_available, run_code
from app.services.analysis_ir import (
    PLANNER_SYSTEM,
    AnalysisIR,
    DeriveSpec,
    FilterClause,
    MetricSpec,
    UncomputableItem,
    _SUMMARY_JSON_MARK,
    _SUMMARY_MARK,
    apply_join_heuristic,
    build_heuristic_ir,
    infer_join_from_message,
    merge_llm_plan,
    parse_summary_payload,
    validate_ir_against_schema,
    validate_patch_code,
)
from app.services.tabular_inspect import TabularSchema, infer_tabular_schema, schema_to_hints
from app.storage import document_exists, document_local_path

_TABULAR_EXT = (".csv", ".xlsx")
_SANDBOX_HINTS = re.compile(
    r"(统计|汇总|分析|画图|图表|折线|柱状|饼图|可视化|pandas|dataframe|"
    r"销售额|均值|平均|求和|分组|透视|excel|csv|xlsx|表格|趋势|排名|top|分布|"
    r"中位数|中位|超过|小于|比率|占比)",
    re.I,
)
_DUCKDB_ROW_THRESHOLD = 50_000
_JOIN_INTENT = ("关联", "合并", "join", "连接")


def refuse_multitable_without_join(
    message: str,
    filenames: list[str],
    *,
    join_key: str = "",
    file_schemas: list[tuple[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Honest refuse when the question names 2+ tables but no valid equi-join key."""
    msg = message or ""
    msg_l = msg.lower()
    named = [
        fn
        for fn in filenames
        if Path(fn or "").stem.lower() and Path(fn or "").stem.lower() in msg_l
    ]
    if len(named) < 2 and len(filenames) >= 2 and any(k in msg for k in _JOIN_INTENT):
        named = list(filenames[:2])
    has_join_intent = any(k in msg for k in _JOIN_INTENT) or any(
        k in msg for k in ("关联", "合并", "连接")
    )
    if len(filenames) >= 2 and len(named) >= 2 and has_join_intent and not join_key:
        return {
            "metrics": [],
            "missing": [
                {
                    "reason": "多表分析需要显式关联键 join_key",
                    "missing_column": "",
                    "asked": "join",
                }
            ],
            "asked_ids": [],
        }
    if join_key and file_schemas and len(filenames) >= 2 and has_join_intent:
        schema_by = {fn: sch for fn, sch in file_schemas}
        tables = named if len(named) >= 2 else filenames[:2]
        missing_key = False
        for fn in tables[:2]:
            sch = schema_by.get(fn)
            if sch is not None and join_key not in getattr(sch, "columns", []):
                missing_key = True
                break
        if missing_key:
            return {
                "metrics": [],
                "missing": [
                    {
                        "reason": "关联键不存在",
                        "missing_column": join_key,
                        "asked": "join",
                    }
                ],
                "asked_ids": [],
            }
    return None


@dataclass
class AnalysisOutcome:
    ok: bool
    steps: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    code: str = ""
    artifact: dict[str, Any] | None = None
    evidence_text: str = ""
    error: str = ""
    filename: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    asked_ids: list[str] = field(default_factory=list)
    analysis_uncomputable: bool = False
    ir: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisSpec:
    """Backward-compatible plan object; prefer AnalysisIR via to_ir/from_ir."""

    task_family: Literal["aggregate", "compare", "visualize", "rank", "filter", "profile"] = "visualize"
    group_by: str = ""
    measure_columns: list[str] = field(default_factory=list)
    operation: str = "auto"
    chart_type: Literal["bar", "line", "table", "scatter", "auto"] = "auto"
    row_scope: Literal["all_rows", "aggregated_rows", "top_n"] = "aggregated_rows"
    multi_sheet_policy: Literal["concat_all", "single_sheet", "auto"] = "auto"
    output_contract: dict[str, Any] = field(default_factory=dict)
    filters: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    derive: list[dict[str, Any]] = field(default_factory=list)
    asked_ids: list[str] = field(default_factory=list)
    uncomputable: list[dict[str, Any]] = field(default_factory=list)
    time_column: str = ""
    time_grain: str = ""
    engine: str = "pandas"
    patch_code: str = ""
    join_left: str = ""
    join_right: str = ""
    join_key: str = ""
    source_hint: str = ""

    @classmethod
    def from_ir(cls, ir: AnalysisIR) -> "AnalysisSpec":
        return cls(
            task_family=ir.task_family,  # type: ignore[arg-type]
            group_by=ir.group_by,
            measure_columns=list(ir.measure_columns),
            operation=ir.operation,
            chart_type=ir.chart_type,  # type: ignore[arg-type]
            row_scope=ir.row_scope,  # type: ignore[arg-type]
            multi_sheet_policy=ir.multi_sheet_policy,  # type: ignore[arg-type]
            output_contract=dict(ir.output_contract or {}),
            filters=[asdict(f) for f in ir.filters],
            metrics=[asdict(m) for m in ir.metrics],
            derive=[asdict(d) for d in ir.derive],
            asked_ids=list(ir.asked_ids),
            uncomputable=[asdict(u) for u in ir.uncomputable],
            time_column=ir.time_column,
            time_grain=ir.time_grain,
            engine=ir.engine,
            patch_code=ir.patch_code,
            join_left=ir.join_left,
            join_right=ir.join_right,
            join_key=ir.join_key,
            source_hint=ir.source_hint,
        )

    def to_ir(self) -> AnalysisIR:
        filters = [
            FilterClause(
                column=str(f.get("column") or ""),
                op=str(f.get("op") or "eq"),  # type: ignore[arg-type]
                value=f.get("value"),
            )
            for f in (self.filters or [])
            if isinstance(f, dict) and f.get("column")
        ]
        metrics = [
            MetricSpec(
                id=str(m.get("id") or "m"),
                kind=str(m.get("kind") or "mean"),  # type: ignore[arg-type]
                column=str(m.get("column") or ""),
                quantile=m.get("quantile"),
                label=str(m.get("label") or ""),
            )
            for m in (self.metrics or [])
            if isinstance(m, dict)
        ]
        derive = [
            DeriveSpec(
                id=str(d.get("id") or "rate"),
                label=str(d.get("label") or ""),
                kind=str(d.get("kind") or "div"),  # type: ignore[arg-type]
                numerator=str(d.get("numerator") or ""),
                denominator=str(d.get("denominator") or ""),
            )
            for d in (self.derive or [])
            if isinstance(d, dict)
        ]
        uncomputable = [
            UncomputableItem(
                reason=str(u.get("reason") or ""),
                missing_column=str(u.get("missing_column") or ""),
                asked=str(u.get("asked") or ""),
            )
            for u in (self.uncomputable or [])
            if isinstance(u, dict)
        ]
        return AnalysisIR(
            task_family=self.task_family,  # type: ignore[arg-type]
            group_by=self.group_by,
            measure_columns=list(self.measure_columns),
            operation=self.operation,
            chart_type=self.chart_type,
            row_scope=self.row_scope,
            multi_sheet_policy=self.multi_sheet_policy,
            output_contract=dict(self.output_contract or {}),
            filters=filters,
            metrics=metrics,
            derive=derive,
            asked_ids=list(self.asked_ids),
            uncomputable=uncomputable,
            time_column=self.time_column,
            time_grain=self.time_grain or "",  # type: ignore[arg-type]
            source_hint=self.source_hint,
            join_left=self.join_left,
            join_right=self.join_right,
            join_key=self.join_key,
            engine="duckdb" if self.engine == "duckdb" else "pandas",
            patch_code=self.patch_code,
        )


def sandbox_hint(message: str) -> bool:
    return bool(_SANDBOX_HINTS.search(message or ""))


def list_tabular_documents(
    db: Session,
    *,
    user_id: int,
    document_ids: list[int] | None = None,
) -> list[Document]:
    q = db.query(Document).filter(Document.user_id == user_id, Document.status == "ready")
    if document_ids:
        q = q.filter(Document.id.in_(document_ids))
    rows = q.order_by(Document.id.desc()).all()
    out: list[Document] = []
    for doc in rows:
        name = (doc.filename or "").lower()
        if name.endswith(_TABULAR_EXT) and doc.stored_path and document_exists(doc.stored_path):
            out.append(doc)
    return out


def resolve_tabular_document(
    db: Session,
    *,
    user_id: int,
    document_ids: list[int] | None = None,
    message: str = "",
    source_hint: str = "",
) -> Document | None:
    rows = list_tabular_documents(db, user_id=user_id, document_ids=document_ids)
    if not rows:
        return None
    hint = (source_hint or "").strip().lower()
    msg = (message or "").lower()
    if hint:
        for doc in rows:
            fn = (doc.filename or "").lower()
            if hint in fn or fn in hint:
                return doc
    best: Document | None = None
    best_score = 0
    for doc in rows:
        fn = (doc.filename or "").lower()
        stem = Path(fn).stem
        score = 0
        if stem and stem in msg:
            score += 3
        for part in re.split(r"[\s_\-.]+", stem):
            if len(part) >= 2 and part in msg:
                score += 1
        if score > best_score:
            best_score = score
            best = doc
    if best is not None and best_score > 0:
        return best
    return rows[0]


def has_tabular_docs(
    db: Session,
    *,
    user_id: int,
    document_ids: list[int] | None = None,
) -> bool:
    return bool(list_tabular_documents(db, user_id=user_id, document_ids=document_ids))


def _heuristic_spec(
    message: str,
    schema: TabularSchema,
    *,
    file_schemas: list[tuple[str, TabularSchema]] | None = None,
    prior_ir: dict[str, Any] | None = None,
) -> AnalysisSpec:
    ir = build_heuristic_ir(message, schema)
    if file_schemas:
        ir = apply_join_heuristic(ir, message, file_schemas)
    if prior_ir and isinstance(prior_ir, dict):
        if not ir.filters and prior_ir.get("filters"):
            ir.filters = [
                FilterClause(
                    column=str(f.get("column") or ""),
                    op=str(f.get("op") or "eq"),  # type: ignore[arg-type]
                    value=f.get("value"),
                )
                for f in (prior_ir.get("filters") or [])
                if isinstance(f, dict) and f.get("column")
            ]
        if not ir.source_hint and prior_ir.get("source_hint"):
            ir.source_hint = str(prior_ir.get("source_hint"))
    return AnalysisSpec.from_ir(validate_ir_against_schema(ir, schema))


def _plan_analysis_spec(
    message: str,
    schema: TabularSchema,
    history: list[dict] | None = None,
    *,
    prior_ir: dict[str, Any] | None = None,
) -> AnalysisSpec:
    hints = schema_to_hints(schema)
    hist: list[Any] = list(history or [])[-4:]
    if prior_ir:
        hist = hist + [
            {
                "role": "system",
                "content": f"上一轮分析计划：{json.dumps(prior_ir, ensure_ascii=False)[:1500]}",
            }
        ]
    parsed = complete_json(
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {
                "role": "user",
                "content": f"最近对话：{hist!r}\n用户请求：{message}\n\n表格 schema：\n{hints}",
            },
        ]
    )
    base = build_heuristic_ir(message, schema)
    if prior_ir and isinstance(prior_ir, dict):
        try:
            prior = AnalysisSpec(
                filters=list(prior_ir.get("filters") or []),
                group_by=str(prior_ir.get("group_by") or ""),
                measure_columns=list(prior_ir.get("measure_columns") or []),
            ).to_ir()
            if not base.filters and prior.filters:
                base.filters = prior.filters
            if not base.group_by and prior.group_by:
                base.group_by = prior.group_by
        except Exception:
            pass
    if not isinstance(parsed, dict):
        return AnalysisSpec.from_ir(validate_ir_against_schema(base, schema))
    return AnalysisSpec.from_ir(merge_llm_plan(parsed, base, schema))


def _choose_engine(schema: TabularSchema, ir: AnalysisIR) -> str:
    if ir.engine == "duckdb":
        return "duckdb"
    total = sum(int(s.row_count or 0) for s in schema.sheets) if schema.sheets else 0
    if total >= _DUCKDB_ROW_THRESHOLD:
        return "duckdb"
    return "pandas"

_SANDBOX_HELPERS = r'''
def load_frames(path, policy):
    if path.endswith(".csv"):
        df0 = pd.read_csv(path)
        for c in list(df0.columns):
            if str(c).strip() == "":
                df0 = df0.drop(columns=[c])
                continue
            if df0[c].dtype == object:
                s = df0[c].astype(str).str.strip()
                cleaned = s.str.replace(",", "", regex=False).str.replace("%", "", regex=False)
                nums = pd.to_numeric(cleaned, errors="coerce")
                if nums.notna().sum() >= 1:
                    df0[c] = nums.where(nums.notna(), df0[c])
        return [df0]
    xls = pd.ExcelFile(path)
    names = xls.sheet_names if policy != "single_sheet" else xls.sheet_names[:1]
    frames = []
    for sn in names:
        df0 = pd.read_excel(path, sheet_name=sn)
        if df0 is None or df0.empty:
            continue
        df0.columns = [str(c).strip() for c in df0.columns]
        df0["__sheet__"] = sn
        frames.append(df0)
    return frames

def load_frames_duckdb(path, policy):
    try:
        if path.endswith(".csv"):
            return [duckdb.read_csv(path).df()]
    except Exception:
        pass
    return load_frames(path, policy)

def ensure_numeric(df, cols):
    for c in cols:
        if c in df.columns:
            if df[c].dtype == object:
                s = df[c].astype(str).str.strip().str.replace("%", "", regex=False).str.replace(",", "", regex=False)
                df[c] = pd.to_numeric(s, errors="coerce")
            else:
                df[c] = pd.to_numeric(df[c], errors="coerce")
    # Also coerce any object column that looks partially numeric (dirty sparse rows)
    for c in list(df.columns):
        if c in cols or df[c].dtype != object:
            continue
        s = df[c].astype(str).str.strip().str.replace("%", "", regex=False).str.replace(",", "", regex=False)
        nums = pd.to_numeric(s, errors="coerce")
        if nums.notna().sum() >= 1:
            df[c] = nums
    return df

def apply_filters(df, filters):
    out = df
    for f in filters or []:
        col = f.get("column") or ""
        op = f.get("op") or "eq"
        val = f.get("value")
        if not col or col not in out.columns:
            continue
        series = out[col]
        if op in ("gt", "ge", "lt", "le", "eq", "ne"):
            try:
                num = float(val)
                snum = pd.to_numeric(series, errors="coerce")
                if op == "gt":
                    out = out[snum > num]
                elif op == "ge":
                    out = out[snum >= num]
                elif op == "lt":
                    out = out[snum < num]
                elif op == "le":
                    out = out[snum <= num]
                elif op == "eq":
                    out = out[(snum == num) | (series.astype(str) == str(val))]
                elif op == "ne":
                    out = out[(snum != num) & (series.astype(str) != str(val))]
            except (TypeError, ValueError):
                if op == "eq":
                    out = out[series.astype(str) == str(val)]
                elif op == "ne":
                    out = out[series.astype(str) != str(val)]
        elif op == "contains" and val is not None:
            out = out[series.astype(str).str.contains(str(val), na=False)]
        elif op == "is_null":
            out = out[series.isna()]
        elif op == "in" and isinstance(val, list):
            out = out[series.isin(val)]
        elif op == "not_in" and isinstance(val, list):
            out = out[~series.isin(val)]
    return out

def dump_chart_sidecar(ax=None):
    import json as _json
    from pathlib import Path as _Path
    ax = ax or plt.gca()
    png = ARTIFACT_PATH
    stem = png.rsplit(".", 1)[0]
    try:
        plt.savefig(stem + ".svg", format="svg", bbox_inches="tight")
    except Exception:
        pass
    pts = []
    try:
        xticks = list(ax.get_xticklabels() or [])
        patches = [p for p in (getattr(ax, "patches", None) or []) if float(p.get_width() or 0) > 0]
        for i, p in enumerate(patches[:200]):
            lab = ""
            if i < len(xticks):
                lab = (xticks[i].get_text() or "").strip()
            if not lab:
                lab = str(round(float(p.get_x() + p.get_width() / 2), 4))
            pts.append({"label": lab, "series": "value", "value": round(float(p.get_height()), 4)})
    except Exception:
        pts = []
    try:
        _Path(stem + ".json").write_text(_json.dumps({"points": pts}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def emit_summary(payload, human_lines):
    print("===SUMMARY===")
    for line in human_lines:
        print(line)
    print("===SUMMARY_JSON===")
    print(json.dumps(payload, ensure_ascii=False))
'''

_SANDBOX_BODY = r'''
frames = load_frames(DATA_PATH, SPEC.get("multi_sheet_policy", "auto"))
if not frames:
    raise ValueError("未读到任何表格数据")
df = pd.concat(frames, ignore_index=True)
df.columns = [str(c).strip() for c in df.columns]
df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]

measure_cols = [c for c in SPEC.get("measure_columns", []) if c in df.columns]
group_col = SPEC.get("group_by") or ""
task_family = SPEC.get("task_family") or "visualize"
chart_type = SPEC.get("chart_type") or "bar"
row_scope = SPEC.get("row_scope") or "aggregated_rows"
operation = SPEC.get("operation") or "profile"
time_col = SPEC.get("time_column") or ""
time_grain = SPEC.get("time_grain") or ""
filters = SPEC.get("filters") or []
metric_specs = list(SPEC.get("metrics") or [])
derive_specs = list(SPEC.get("derive") or [])
asked_ids = list(SPEC.get("asked_ids") or [])
pre_missing = list(SPEC.get("uncomputable") or [])
patch = (SPEC.get("patch_code") or "").strip()

rows_in = len(df)
need_numeric = list(measure_cols)
for m in metric_specs:
    col = m.get("column") or ""
    if col:
        need_numeric.append(col)
for f in filters:
    if (f.get("column") or "") and f.get("op") in ("gt", "ge", "lt", "le"):
        need_numeric.append(f.get("column"))
for d in derive_specs:
    for k in ("numerator", "denominator"):
        if d.get(k):
            need_numeric.append(d.get(k))
df = ensure_numeric(df, list(dict.fromkeys([c for c in need_numeric if c])))

if time_col and time_col in df.columns:
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    if time_grain == "month":
        df["__time_grain__"] = df[time_col].dt.to_period("M").astype(str)
        group_col = group_col or "__time_grain__"
    elif time_grain == "week":
        df["__time_grain__"] = df[time_col].dt.to_period("W").astype(str)
        group_col = group_col or "__time_grain__"
    elif time_grain == "day":
        df["__time_grain__"] = df[time_col].dt.strftime("%Y-%m-%d")
        group_col = group_col or "__time_grain__"

df = apply_filters(df, filters)
if patch:
    ns = {"df": df, "pd": pd}
    exec(patch, {"__builtins__": {"len": len, "range": range, "min": min, "max": max, "sum": sum, "float": float, "int": int, "str": str, "list": list, "dict": dict}}, ns)
    df = ns.get("df", df)

rows_after = len(df)
metrics_out = []
missing_out = [dict(x) for x in pre_missing]

def add_metric(mid, label, value, unit=None):
    try:
        if value is None:
            return
        fv = float(value)
        if fv != fv:
            return
        metrics_out.append({"id": mid, "label": label or mid, "value": round(fv, 6), "unit": unit})
    except (TypeError, ValueError):
        return

if not metric_specs:
    if operation == "auto":
        operation = "profile"
    if operation == "count":
        metric_specs = [{"id": "row_count", "kind": "count", "column": "", "label": "行数"}]
    elif operation == "none":
        metric_specs = []
    elif operation == "profile" and measure_cols:
        c0 = measure_cols[0]
        metric_specs = [
            {"id": c0 + "_count", "kind": "count", "column": c0, "label": c0 + "_count"},
            {"id": c0 + "_mean", "kind": "mean", "column": c0, "label": c0 + "_mean"},
            {"id": c0 + "_median", "kind": "median", "column": c0, "label": c0 + "_median"},
            {"id": c0 + "_min", "kind": "min", "column": c0, "label": c0 + "_min"},
            {"id": c0 + "_max", "kind": "max", "column": c0, "label": c0 + "_max"},
        ]
    elif operation in ("sum", "avg", "mean", "median", "min", "max", "count_distinct") and measure_cols:
        kind = "mean" if operation in ("avg", "mean") else operation
        c0 = measure_cols[0]
        metric_specs = [{"id": kind + "_" + c0, "kind": kind, "column": c0, "label": c0}]
    elif task_family == "rank" and measure_cols:
        metric_specs = [{"id": "rank_" + measure_cols[0], "kind": "sum", "column": measure_cols[0], "label": measure_cols[0]}]

human = [
    "task_family=" + str(task_family),
    "row_scope=" + str(row_scope),
    "chart_type=" + str(chart_type),
    "used_columns=" + ",".join(measure_cols),
    "rows=" + str(rows_after),
    "rows_in=" + str(rows_in),
    "group_col=" + (group_col or "(none)"),
    "operation=" + str(operation),
]

plot_series = None
for m in metric_specs:
    mid = str(m.get("id") or "m")
    kind = str(m.get("kind") or "mean")
    col = str(m.get("column") or "")
    label = str(m.get("label") or mid)
    if kind == "count" and not col:
        add_metric(mid, label, rows_after)
        continue
    if kind != "count" and col and col not in df.columns:
        missing_out.append({"reason": "指标列不存在", "missing_column": col, "asked": mid})
        continue
    work = df
    if group_col and group_col in work.columns and kind in ("sum", "mean", "median", "min", "max", "count", "count_distinct"):
        g = work.groupby(group_col, dropna=False)
        if kind == "count":
            series = g.size()
        elif kind == "count_distinct":
            series = g[col].nunique()
        elif kind == "mean":
            series = g[col].mean()
        elif kind == "sum":
            series = g[col].sum()
        elif kind == "median":
            series = g[col].median()
        elif kind == "min":
            series = g[col].min()
        elif kind == "max":
            series = g[col].max()
        else:
            series = g[col].mean()
        series = series.round(6)
        plot_series = series
        for idx_val, val in series.items():
            add_metric(mid + "__" + str(idx_val), label + "[" + str(idx_val) + "]", val)
        if kind == "count":
            add_metric(mid, label, rows_after)
        elif kind == "count_distinct":
            add_metric(mid, label, work[col].nunique())
        else:
            add_metric(mid, label, getattr(work[col], kind if kind != "mean" else "mean")())
    else:
        if kind == "count":
            add_metric(mid, label, len(work) if not col else int(work[col].count()))
        elif kind == "count_distinct":
            add_metric(mid, label, work[col].nunique())
        elif kind == "quantile":
            q = m.get("quantile")
            q = 0.5 if q is None else float(q)
            add_metric(mid, label, work[col].quantile(q))
        else:
            fn = {"mean": "mean", "sum": "sum", "median": "median", "min": "min", "max": "max"}.get(kind, "mean")
            add_metric(mid, label, getattr(work[col], fn)())

id_to_val = {m["id"]: m["value"] for m in metrics_out}
for d in derive_specs:
    did = str(d.get("id") or "rate")
    num = str(d.get("numerator") or "")
    den = str(d.get("denominator") or "")
    label = str(d.get("label") or did)
    try:
        if num in id_to_val:
            nv = float(id_to_val[num])
        elif num in df.columns:
            nv = float(pd.to_numeric(df[num], errors="coerce").sum())
        else:
            missing_out.append({"reason": "派生分子缺失", "missing_column": num, "asked": did})
            continue
        if den in id_to_val:
            dv = float(id_to_val[den])
        elif den in df.columns:
            dv = float(pd.to_numeric(df[den], errors="coerce").sum())
        else:
            missing_out.append({"reason": "派生分母缺失", "missing_column": den, "asked": did})
            continue
        if abs(dv) < 1e-12:
            missing_out.append({"reason": "分母为零", "missing_column": den, "asked": did})
            continue
        kind = str(d.get("kind") or "div")
        if kind == "row_mean":
            col = num or den
            if col in df.columns:
                add_metric(did, label, pd.to_numeric(df[col], errors="coerce").mean())
            else:
                missing_out.append({"reason": "派生列缺失", "missing_column": col, "asked": did})
            continue
        val = (nv - dv) / dv if kind == "growth" else nv / dv
        add_metric(did, label, val)
    except Exception as exc:
        missing_out.append({"reason": str(exc)[:80], "missing_column": num or den, "asked": did})

for m in metrics_out[:40]:
    human.append(str(m["id"]) + "=" + str(m["value"]))

payload = {
    "file": "",
    "rows_in": rows_in,
    "rows_after_filter": rows_after,
    "metrics": metrics_out,
    "missing": missing_out,
    "asked_ids": asked_ids,
}

if chart_type != "table" and (plot_series is not None or metrics_out):
    try:
        if plot_series is not None:
            ax = plot_series.head(30).plot(kind="bar", figsize=(12, 7))
        else:
            ax = pd.Series({m["label"]: m["value"] for m in metrics_out[:20]}).plot(kind="bar", figsize=(12, 7))
        plt.xticks(rotation=20, ha="right")
        plt.tight_layout()
        plt.savefig(ARTIFACT_PATH, dpi=160, bbox_inches="tight")
        dump_chart_sidecar(ax)
    except Exception:
        pass
elif operation == "none" and measure_cols:
    plot_df = df[[c for c in ([group_col] if group_col in df.columns else []) + measure_cols]].head(200)
    human.append("operation=none")
    human.append(plot_df.head(20).to_string(index=False))
    if chart_type != "table":
        try:
            ax = plot_df[measure_cols].plot(kind="bar" if len(plot_df) <= 80 else "line", figsize=(14, 7), alpha=0.85)
            plt.tight_layout()
            plt.savefig(ARTIFACT_PATH, dpi=160, bbox_inches="tight")
            dump_chart_sidecar(ax)
        except Exception:
            pass

emit_summary(payload, human)
'''

def _render_analysis_code(spec: AnalysisSpec) -> str:
    ir = spec.to_ir()
    if ir.patch_code:
        ir.patch_code = validate_patch_code(ir.patch_code)
        spec = AnalysisSpec.from_ir(ir)
    spec_json = json.dumps(ir.to_dict(), ensure_ascii=False)
    use_duckdb = ir.engine == "duckdb"
    duck_import = "import duckdb\n" if use_duckdb else ""
    code = (
        "import json\n"
        "import pandas as pd\n"
        "import matplotlib.pyplot as plt\n"
        + duck_import
        + "SPEC = json.loads(r'''"
        + spec_json
        + "''')\n\n"
        + _SANDBOX_HELPERS
        + "\n"
        + _SANDBOX_BODY
    )
    if use_duckdb:
        code = code.replace(
            'frames = load_frames(DATA_PATH, SPEC.get("multi_sheet_policy", "auto"))',
            'frames = load_frames_duckdb(DATA_PATH, SPEC.get("multi_sheet_policy", "auto"))',
            1,
        )
    return code


def _repair_spec(spec: AnalysisSpec, schema: TabularSchema, *, stderr: str = "", png_bytes: int = 0) -> AnalysisSpec:
    ir = spec.to_ir()
    if not ir.measure_columns:
        ir.measure_columns = schema.measure_candidates[:4]
    ir.measure_columns = [c for c in ir.measure_columns if c in schema.columns]
    if not ir.measure_columns and schema.measure_candidates:
        ir.measure_columns = schema.measure_candidates[:4]
    if ir.group_by and ir.group_by not in schema.columns:
        ir.group_by = ""
    if "未找到可用数值列" in stderr and schema.measure_candidates:
        ir.measure_columns = schema.measure_candidates[:4]
    if png_bytes and png_bytes < 8000 and ir.chart_type == "bar" and ir.row_scope == "all_rows":
        ir.chart_type = "line"
    ir = validate_ir_against_schema(ir, schema)
    return AnalysisSpec.from_ir(ir)


def _summary_tail(stdout: str) -> str:
    text = stdout or ""
    if _SUMMARY_MARK in text:
        return text[text.index(_SUMMARY_MARK) :]
    return text[-2500:] if len(text) > 2500 else text


def _validate_output(spec: AnalysisSpec, result: ExecutionResult) -> str:
    stdout = result.stdout or ""
    if _SUMMARY_MARK not in stdout:
        return "缺少 SUMMARY"
    must_include = spec.output_contract.get("must_include") if isinstance(spec.output_contract, dict) else []
    for key in must_include or []:
        if key == "SUMMARY_JSON":
            continue
        if f"{key}=" not in stdout and key not in stdout:
            return f"缺少字段：{key}"
    if spec.row_scope == "all_rows" and spec.operation == "none" and "operation=none" not in stdout:
        return "明细图缺少 operation=none"
    if spec.output_contract.get("requires_chart") and not result.artifacts:
        payload = parse_summary_payload(stdout)
        if not payload.get("metrics") and not payload.get("missing"):
            return "缺少图表 Artifact"
    return ""


def _execute_analysis_spec(
    *,
    message: str,
    host_path: Path,
    schema: TabularSchema,
    spec: AnalysisSpec,
    steps: list[str],
    filename: str,
) -> AnalysisOutcome:
    """Shared sandbox execution path for DB-backed and local-table analysis."""
    ir = spec.to_ir()
    ir.engine = _choose_engine(schema, ir)  # type: ignore[assignment]
    spec = AnalysisSpec.from_ir(ir)
    steps.append("已生成分析规格，准备在沙箱中执行")
    if ir.engine == "duckdb":
        steps.append("大表启用 DuckDB 引擎")
    code = _render_analysis_code(spec)

    timeout = int(settings.sandbox_timeout_sec)
    total_rows = sum(int(s.row_count or 0) for s in schema.sheets) if schema.sheets else 0
    if total_rows >= _DUCKDB_ROW_THRESHOLD:
        timeout = max(timeout, 60)

    max_retries = max(0, int(settings.sandbox_max_retries))
    last: ExecutionResult | None = None
    validation_err = ""
    for attempt in range(1 + max_retries):
        steps.append(f"沙箱执行第 {attempt + 1} 次")
        last = run_code(code, data_path=str(host_path), timeout_sec=timeout)
        png_bytes = last.artifact_png_bytes or (
            (last.artifacts[0].get("png_bytes") or 0) if last.artifacts else 0
        )
        validation_err = "" if last.error or last.timed_out else _validate_output(spec, last)
        if not last.error and not last.timed_out and not validation_err:
            steps.append("沙箱执行成功")
            break
        if last.timed_out:
            steps.append(SANDBOX_TIMEOUT_MESSAGE)
            break
        reason = validation_err or (last.stderr or last.error)
        steps.append(f"执行失败：{reason[:200]}")
        if attempt >= max_retries:
            break
        steps.append("根据分析规格与错误日志修正执行计划")
        spec = _repair_spec(spec, schema, stderr=last.stderr or last.error, png_bytes=png_bytes)
        code = _render_analysis_code(spec)

    assert last is not None
    artifact: dict[str, Any] | None = None
    if last.artifacts:
        artifact = last.artifacts[0]
    elif code:
        artifact = {
            "kind": "code",
            "title": f"分析代码 · {filename}",
            "language": "python",
            "content": code,
        }

    summary = parse_summary_payload(last.stdout or "")
    summary.setdefault("file", filename)
    asked = list(spec.asked_ids or summary.get("asked_ids") or [])
    missing = list(summary.get("missing") or [])
    uncomputable = bool(missing) and not (summary.get("metrics") or [])
    if missing and asked:
        have = {str(m.get("id")) for m in (summary.get("metrics") or []) if isinstance(m, dict)}
        if any(a not in have for a in asked):
            uncomputable = True

    ok = not last.error and not last.timed_out and not validation_err
    evidence = _summary_tail(last.stdout or (last.stderr or last.error))
    if code:
        evidence = f"{evidence}\n\n【执行代码】\n{code[:1500]}"
    if last.timed_out:
        user_error = SANDBOX_TIMEOUT_MESSAGE
    elif not ok:
        user_error = validation_err or last.error
    else:
        user_error = ""
    return AnalysisOutcome(
        ok=ok,
        steps=steps,
        stdout=last.stdout,
        stderr=last.stderr,
        code=code,
        artifact=artifact,
        evidence_text=evidence,
        error=user_error,
        filename=filename,
        summary=summary,
        asked_ids=asked,
        analysis_uncomputable=uncomputable,
        ir=spec.to_ir().to_dict(),
    )


def analyze_local_tables(
    message: str,
    files: list[Path],
    *,
    history: list[dict] | None = None,
    prior_ir: dict[str, Any] | None = None,
    feedback: str = "",
    stage_copy: bool = False,
) -> AnalysisOutcome:
    """Run tabular analysis on local fixture paths (eval + tests, no DB)."""
    if not files:
        return AnalysisOutcome(ok=False, steps=["未提供表格文件"], error="no files")

    if not docker_available():
        return AnalysisOutcome(
            ok=False,
            steps=["沙箱不可用：请在宿主机执行 docker compose build sandbox"],
            error="docker unavailable",
        )

    named: list[tuple[str, Path]] = [(p.name, p) for p in files]
    file_schemas: list[tuple[str, TabularSchema]] = []
    for name, path in named:
        schema = infer_tabular_schema(str(path))
        if not schema.is_readable:
            return AnalysisOutcome(
                ok=False,
                steps=[f"表格探测失败：{schema.unreadable_reason}"],
                error=schema.unreadable_reason,
                filename=name,
            )
        file_schemas.append((name, schema))

    steps: list[str] = [f"本地表格：{', '.join(n for n, _ in named)}"]
    primary_name = str((prior_ir or {}).get("source_hint") or named[0][0])
    primary_path = next((p for n, p in named if n == primary_name), named[0][1])
    primary_schema = next(s for n, s in file_schemas if n == primary_name)

    plan_message = message
    if feedback:
        plan_message = f"{message}\n\n【补算反馈】{feedback}"

    join_left, join_right, join_key = infer_join_from_message(plan_message, file_schemas)
    host_path = primary_path
    schema = primary_schema

    if join_key and join_left and join_right:
        left_path = next((p for n, p in named if n == join_left), None)
        right_path = next((p for n, p in named if n == join_right), None)
        if left_path is None or right_path is None:
            payload = {
                "metrics": [],
                "missing": [
                    {
                        "reason": "多表关联需要两份 csv/xlsx",
                        "missing_column": join_key,
                        "asked": "join",
                    }
                ],
                "asked_ids": [],
            }
            return AnalysisOutcome(
                ok=True,
                steps=steps + ["需要两张表才能关联，但未找到全部文件"],
                evidence_text=f"{_SUMMARY_MARK}\n{_SUMMARY_JSON_MARK}\n{json.dumps(payload, ensure_ascii=False)}",
                filename=primary_name,
                summary=payload,
                analysis_uncomputable=True,
                ir={"join_key": join_key, "join_left": join_left, "join_right": join_right},
            )
        try:
            import pandas as pd

            left_df = pd.read_csv(left_path) if str(left_path).lower().endswith(".csv") else pd.read_excel(left_path)
            right_df = pd.read_csv(right_path) if str(right_path).lower().endswith(".csv") else pd.read_excel(right_path)
            if join_key not in left_df.columns or join_key not in right_df.columns:
                payload = {
                    "metrics": [],
                    "missing": [
                        {
                            "reason": "关联键不存在",
                            "missing_column": join_key,
                            "asked": "join",
                        }
                    ],
                    "asked_ids": [],
                }
                return AnalysisOutcome(
                    ok=True,
                    steps=steps + [f"关联键 {join_key} 不在两表中"],
                    evidence_text=f"{_SUMMARY_MARK}\n{_SUMMARY_JSON_MARK}\n{json.dumps(payload, ensure_ascii=False)}",
                    filename=primary_name,
                    summary=payload,
                    analysis_uncomputable=True,
                    ir={"join_key": join_key, "join_left": join_left, "join_right": join_right},
                )
            merged = left_df.merge(right_df, on=join_key, how="inner")
            tmp = primary_path.parent / f"_omni_join_{primary_path.stem}.csv"
            merged.to_csv(tmp, index=False)
            host_path = tmp
            schema = infer_tabular_schema(str(host_path))
            steps.append(f"已按 {join_key} 内连接 {join_left} 与 {join_right}")
        except Exception as exc:
            return AnalysisOutcome(
                ok=False,
                steps=steps + [f"多表关联失败：{exc}"],
                error=str(exc)[:200],
                filename=primary_name,
                ir={"join_key": join_key, "join_left": join_left, "join_right": join_right},
            )

    join_refuse = refuse_multitable_without_join(
        message or "",
        [n for n, _ in named],
        join_key=join_key,
        file_schemas=file_schemas,
    )
    if join_refuse is not None:
        return AnalysisOutcome(
            ok=True,
            steps=steps + ["检测到多表需求但未指定关联键，请说明用哪一列关联"],
            evidence_text=f"{_SUMMARY_MARK}\n{_SUMMARY_JSON_MARK}\n{json.dumps(join_refuse, ensure_ascii=False)}",
            filename=primary_name,
            summary=join_refuse,
            analysis_uncomputable=True,
            ir={"join_key": "", "join_left": "", "join_right": ""},
        )

    spec = _heuristic_spec(
        plan_message,
        schema,
        file_schemas=file_schemas,
        prior_ir=prior_ir,
    )
    ir = validate_ir_against_schema(spec.to_ir(), schema)
    ir.source_hint = primary_name
    ir.join_left = join_left
    ir.join_right = join_right
    ir.join_key = join_key
    spec = AnalysisSpec.from_ir(ir)

    if stage_copy:
        from app.core.tmpdir import ephemeral_dir

        root = ephemeral_dir()
        if root is not None:
            dest = root / f"office-eval-{host_path.name}"
            dest.write_bytes(host_path.read_bytes())
            host_path = dest

    spec.chart_type = "table"
    if isinstance(spec.output_contract, dict):
        spec.output_contract["requires_chart"] = False

    return _execute_analysis_spec(
        message=message,
        host_path=host_path,
        schema=schema,
        spec=spec,
        steps=steps,
        filename=primary_name,
    )


def run_analysis(
    db: Session,
    *,
    message: str,
    history: list[dict] | None,
    user_id: int,
    document_ids: list[int] | None = None,
    prior_ir: dict[str, Any] | None = None,
    feedback: str = "",
) -> AnalysisOutcome:
    steps: list[str] = []
    doc = resolve_tabular_document(
        db,
        user_id=user_id,
        document_ids=document_ids,
        message=message,
        source_hint=str((prior_ir or {}).get("source_hint") or ""),
    )
    if not doc:
        return AnalysisOutcome(
            ok=False,
            steps=["未找到可用的 csv/xlsx 文档，请先在知识库上传并选中表格文件"],
            error="no tabular document",
        )

    if not docker_available():
        return AnalysisOutcome(
            ok=False,
            steps=["沙箱不可用：请在宿主机执行 docker compose build sandbox"],
            error="docker unavailable",
            filename=doc.filename or "",
        )

    with document_local_path(doc.stored_path) as host_path:
        schema = infer_tabular_schema(str(host_path))
        steps.append(f"选用数据文件：{doc.filename}")
        steps.append("已探测表格结构（sheet/行数/列名）")
        if not schema.is_readable:
            return AnalysisOutcome(
                ok=False,
                steps=steps + [f"表格探测失败：{schema.unreadable_reason}"],
                error=schema.unreadable_reason,
                filename=doc.filename or "",
            )

        plan_message = message
        if feedback:
            plan_message = f"{message}\n\n【补算反馈】{feedback}"
            steps.append("根据指标缺口反馈重新规划")

        spec = _plan_analysis_spec(plan_message, schema, history=history, prior_ir=prior_ir)
        ir = spec.to_ir()
        ir.source_hint = doc.filename or ""
        ir.engine = _choose_engine(schema, ir)  # type: ignore[assignment]

        if ir.join_key and ir.join_left and ir.join_right:
            left = resolve_tabular_document(
                db, user_id=user_id, document_ids=document_ids, source_hint=ir.join_left
            )
            right = resolve_tabular_document(
                db, user_id=user_id, document_ids=document_ids, source_hint=ir.join_right
            )
            if left is None or right is None:
                payload = {
                    "metrics": [],
                    "missing": [
                        {
                            "reason": "多表关联需要两份 csv/xlsx",
                            "missing_column": ir.join_key,
                            "asked": "join",
                        }
                    ],
                    "asked_ids": list(ir.asked_ids),
                }
                return AnalysisOutcome(
                    ok=True,
                    steps=steps + ["需要两张表才能关联，但未找到全部文件"],
                    evidence_text=f"{_SUMMARY_MARK}\n{_SUMMARY_JSON_MARK}\n{json.dumps(payload, ensure_ascii=False)}",
                    filename=doc.filename or "",
                    summary=payload,
                    asked_ids=list(ir.asked_ids),
                    analysis_uncomputable=True,
                    ir=ir.to_dict(),
                )
            with document_local_path(left.stored_path) as lp, document_local_path(right.stored_path) as rp:
                try:
                    import pandas as pd

                    left_df = pd.read_csv(lp) if str(lp).lower().endswith(".csv") else pd.read_excel(lp)
                    right_df = pd.read_csv(rp) if str(rp).lower().endswith(".csv") else pd.read_excel(rp)
                    if ir.join_key not in left_df.columns or ir.join_key not in right_df.columns:
                        payload = {
                            "metrics": [],
                            "missing": [
                                {
                                    "reason": "关联键不存在",
                                    "missing_column": ir.join_key,
                                    "asked": "join",
                                }
                            ],
                            "asked_ids": list(ir.asked_ids),
                        }
                        return AnalysisOutcome(
                            ok=True,
                            steps=steps + [f"关联键 {ir.join_key} 不在两表中"],
                            evidence_text=f"{_SUMMARY_MARK}\n{_SUMMARY_JSON_MARK}\n{json.dumps(payload, ensure_ascii=False)}",
                            filename=doc.filename or "",
                            summary=payload,
                            asked_ids=list(ir.asked_ids),
                            analysis_uncomputable=True,
                            ir=ir.to_dict(),
                        )
                    merged = left_df.merge(right_df, on=ir.join_key, how="inner")
                    tmp = Path(str(host_path)).parent / "_omni_join.csv"
                    merged.to_csv(tmp, index=False)
                    host_path = tmp
                    schema = infer_tabular_schema(str(host_path))
                    steps.append(f"已按 {ir.join_key} 内连接 {left.filename} 与 {right.filename}")
                except Exception as exc:
                    return AnalysisOutcome(
                        ok=False,
                        steps=steps + [f"多表关联失败：{exc}"],
                        error=str(exc)[:200],
                        filename=doc.filename or "",
                        ir=ir.to_dict(),
                    )

        docs = list_tabular_documents(db, user_id=user_id, document_ids=document_ids)
        join_refuse = refuse_multitable_without_join(
            message or "",
            [d.filename or "" for d in docs],
            join_key=ir.join_key,
        )
        if join_refuse is not None:
            join_refuse["asked_ids"] = list(ir.asked_ids)
            return AnalysisOutcome(
                ok=True,
                steps=steps + ["检测到多表需求但未指定关联键，请说明用哪一列关联"],
                evidence_text=f"{_SUMMARY_MARK}\n{_SUMMARY_JSON_MARK}\n{json.dumps(join_refuse, ensure_ascii=False)}",
                filename=doc.filename or "",
                summary=join_refuse,
                asked_ids=list(ir.asked_ids),
                analysis_uncomputable=True,
                ir=ir.to_dict(),
            )

        spec = AnalysisSpec.from_ir(ir)
        return _execute_analysis_spec(
            message=message,
            host_path=Path(host_path),
            schema=schema,
            spec=spec,
            steps=steps,
            filename=doc.filename or "",
        )
