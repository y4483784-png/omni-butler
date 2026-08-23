"""AnalysisIR: structured plan for tabular sandbox (filters, metrics, derive).

LLM fills JSON; a deterministic compiler emits pandas/DuckDB code.
Structured ===SUMMARY_JSON=== drives verify / critique gates.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.services.tabular_inspect import TabularSchema

FilterOp = Literal["eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "contains", "is_null"]
MetricKind = Literal[
    "sum", "mean", "median", "min", "max", "count", "count_distinct", "quantile"
]
TaskFamily = Literal["aggregate", "compare", "visualize", "rank", "filter", "profile"]

_SUMMARY_JSON_MARK = "===SUMMARY_JSON==="
_SUMMARY_MARK = "===SUMMARY==="

_MEDIAN_RE = re.compile(r"(中位数|中位|median)", re.I)
_MIN_RE = re.compile(r"(最小|最低|min\b)", re.I)
_MAX_RE = re.compile(r"(最大|最高|max\b)", re.I)
_MEAN_RE = re.compile(r"(平均|均值|mean|avg)", re.I)
_SUM_RE = re.compile(r"(求和|总和|合计|汇总|sum)", re.I)
_COUNT_RE = re.compile(r"(数量|人数|多少|几人|条数|行数|订单数|笔数|单数|记录数|count)", re.I)
_DISTINCT_RE = re.compile(r"(去重|不重复|distinct|unique)", re.I)
_RATE_RE = re.compile(r"(比率|比例|占比|取消率|转化率|rate)", re.I)
_FILTER_RE = re.compile(
    r"(超过|大于|小于|不少于|不多于|等于|不等于|包含|>|<|>=|<=)",
    re.I,
)
_NUM_COMPARE_RE = re.compile(
    r"(超过|大于|>=|>)\s*(\d+(?:\.\d+)?)|(小于|少于|<=|<)\s*(\d+(?:\.\d+)?)|"
    r"(\d+(?:\.\d+)?)\s*(分钟|元|次|人|%)?",
    re.I,
)
_RANK_RE = re.compile(r"(排名|排行|top|前\d+)", re.I)
_DIST_RE = re.compile(r"(分布|直方|hist|histogram)", re.I)
_VIS_RE = re.compile(r"(图|图表|画出|绘制|可视化|plot|chart|bar|line|scatter)", re.I)
_ALL_ROWS_RE = re.compile(r"(所有|全部|每个|逐个|原始|明细)", re.I)
_BAR_RE = re.compile(r"(柱状|条形|bar)", re.I)
_LINE_RE = re.compile(r"(折线|趋势|line)", re.I)
_SCATTER_RE = re.compile(r"(散点|scatter)", re.I)
_TABLE_RE = re.compile(r"(表格|列表|表)", re.I)
_MONTH_RE = re.compile(r"(按月|每月|月度|month)", re.I)
_WEEK_RE = re.compile(r"(按周|每周|week)", re.I)
_DAY_RE = re.compile(r"(按日|每天|日度|day)", re.I)
_ASKED_MEASURE_RE = re.compile(
    r"(?:^|的|表)([\u4e00-\u9fffA-Za-z0-9_]{2,12})(?:中位数|合计|总和|均值|平均值|平均)"
)
_GENERIC_ASKED = frozenset({"合计", "平均", "中位数", "多少", "人数", "数量"})

_ALLOWED_DERIVE = frozenset({"div", "growth", "row_mean"})

# AST whitelist for optional patch snippets (P2)
_ALLOWED_AST_NODES = (
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AugAssign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Subscript,
    ast.Slice,
    ast.Attribute,
    ast.Call,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.BoolOp,
    ast.IfExp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.keyword,
)
_FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "open",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "type",
        "os",
        "sys",
        "subprocess",
        "pathlib",
        "shutil",
        "socket",
        "requests",
        "urllib",
        "pickle",
        "ctypes",
    }
)
_ALLOWED_ATTR_ROOTS = frozenset({"df", "pd", "np", "result", "work", "plot_df", "frames"})


@dataclass
class FilterClause:
    column: str
    op: FilterOp = "eq"
    value: Any = None


@dataclass
class MetricSpec:
    id: str
    kind: MetricKind = "mean"
    column: str = ""
    quantile: float | None = None
    label: str = ""


@dataclass
class DeriveSpec:
    id: str
    label: str = ""
    kind: Literal["div", "growth", "row_mean"] = "div"
    numerator: str = ""
    denominator: str = ""


@dataclass
class UncomputableItem:
    reason: str
    missing_column: str = ""
    asked: str = ""


@dataclass
class AnalysisIR:
    task_family: TaskFamily = "aggregate"
    group_by: str = ""
    measure_columns: list[str] = field(default_factory=list)
    operation: str = "auto"
    chart_type: str = "auto"
    row_scope: str = "aggregated_rows"
    multi_sheet_policy: str = "auto"
    output_contract: dict[str, Any] = field(default_factory=dict)
    filters: list[FilterClause] = field(default_factory=list)
    metrics: list[MetricSpec] = field(default_factory=list)
    derive: list[DeriveSpec] = field(default_factory=list)
    asked_ids: list[str] = field(default_factory=list)
    uncomputable: list[UncomputableItem] = field(default_factory=list)
    time_column: str = ""
    time_grain: Literal["", "day", "week", "month"] = ""
    source_hint: str = ""
    join_left: str = ""
    join_right: str = ""
    join_key: str = ""
    engine: Literal["pandas", "duckdb"] = "pandas"
    patch_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_col(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "").strip())


def _pick_group_candidate(message: str, schema: TabularSchema) -> str:
    for col in schema.dimension_candidates:
        if col and col in (message or ""):
            return col
    return schema.dimension_candidates[0] if schema.dimension_candidates else ""


def _pick_measure_candidates(message: str, schema: TabularSchema) -> list[str]:
    matched: list[str] = []
    norm_cols = {_normalize_col(c): c for c in schema.measure_candidates}
    for col in schema.measure_candidates:
        if col in (message or ""):
            matched.append(col)
    msg_norm = _normalize_col(message or "")
    for norm, col in norm_cols.items():
        if norm and norm in msg_norm and col not in matched:
            matched.append(col)
    if matched:
        dedup: list[str] = []
        for item in matched:
            if item not in dedup:
                dedup.append(item)
        return dedup
    if _ALL_ROWS_RE.search(message or ""):
        return schema.measure_candidates[: min(4, len(schema.measure_candidates))]
    return schema.measure_candidates[: min(2, len(schema.measure_candidates))]


def _pick_filter_column(message: str, schema: TabularSchema, measures: list[str]) -> str:
    text = message or ""
    for col in list(measures) + list(schema.measure_candidates) + list(schema.columns):
        if col and col in text:
            return col
    # 迟到分钟 / 迟到 common patterns
    for col in schema.columns:
        if any(k in col for k in ("分钟", "时长", "金额", "次数", "利润", "销售")):
            return col
    return measures[0] if measures else (schema.measure_candidates[0] if schema.measure_candidates else "")


def _extract_compare_filter(message: str, schema: TabularSchema, measures: list[str]) -> FilterClause | None:
    text = message or ""
    if not _FILTER_RE.search(text):
        return None
    col = _pick_filter_column(text, schema, measures)
    if not col:
        return None
    m = re.search(r"(超过|大于|不少于|>=|>)\s*(\d+(?:\.\d+)?)", text)
    if m:
        return FilterClause(column=col, op="gt" if m.group(1) in ("超过", "大于", ">") else "ge", value=float(m.group(2)))
    m = re.search(r"(小于|少于|不多于|<=|<)\s*(\d+(?:\.\d+)?)", text)
    if m:
        return FilterClause(column=col, op="lt" if m.group(1) in ("小于", "少于", "<") else "le", value=float(m.group(2)))
    m = re.search(r"(等于|=)\s*(\d+(?:\.\d+)?)", text)
    if m:
        return FilterClause(column=col, op="eq", value=float(m.group(2)))
    # dimension equality: 华东 / 西南 in message matching a dim value sample is hard;
    # if a dimension column name + region-like token appears, filter by contains on dim.
    for dim in schema.dimension_candidates:
        if dim and dim in text:
            # value after 的 / 在
            for tok in re.findall(r"([\u4e00-\u9fff]{2,6})", text):
                if tok in (dim, "大区", "地区", "部门", "中位数", "利润", "销售", "合计", "平均"):
                    continue
                if any(k in tok for k in ("华东", "华北", "华南", "西南", "西北", "东北", "运营", "研发", "产品", "测试")):
                    return FilterClause(column=dim, op="eq", value=tok)
    return None


def _asked_measure_token(message: str) -> str:
    m = _ASKED_MEASURE_RE.search(message or "")
    if not m:
        return ""
    token = (m.group(1) or "").strip()
    if token in _GENERIC_ASKED:
        return ""
    return token


_REGION_PREFIXES = ("华东", "华北", "华南", "西南", "西北", "东北")
_MEASURE_SUFFIXES = ("利润", "销售额", "金额", "退货金额", "单价", "迟到分钟")


def _normalize_asked_column(token: str, schema: TabularSchema) -> str:
    """Split composite tokens like 西南大区利润 into real column 利润."""
    if not token:
        return ""
    if token in schema.columns:
        return token
    for col in schema.columns:
        if col and token.endswith(col):
            return col
    for col in schema.measure_candidates:
        if col and col in token:
            return col
    for region in _REGION_PREFIXES:
        if token.startswith(region):
            rest = token[len(region) :].replace("大区", "").strip()
            for suf in _MEASURE_SUFFIXES:
                if rest == suf or suf in rest:
                    for col in schema.columns:
                        if suf in col or col in suf:
                            return col
    return token


def _should_skip_missing_column(token: str, schema: TabularSchema, filters: list[FilterClause]) -> bool:
    if not token or token in schema.columns:
        return True
    normalized = _normalize_asked_column(token, schema)
    if normalized in schema.columns:
        return True
    if filters and any(
        any(s in token for s in _MEASURE_SUFFIXES) and any(r in token for r in _REGION_PREFIXES)
        for _ in [0]
    ):
        return True
    return False


def _resolve_operation(message: str, *, row_scope: str) -> str:
    text = message or ""
    if _MEDIAN_RE.search(text):
        return "median"
    if _RATE_RE.search(text):
        # 「取消率是多少」含「多少」，不能落到 count
        return "rate"
    if _DISTINCT_RE.search(text) and _COUNT_RE.search(text):
        return "count_distinct"
    if _MEAN_RE.search(text):
        return "avg"
    if _SUM_RE.search(text):
        return "sum"
    if any(k in text for k in ("销售额", "金额", "利润", "退货金额")) and re.search(
        r"(多少|是多少|合计|总和)", text
    ):
        return "sum"
    if _MIN_RE.search(text) and not _RANK_RE.search(text):
        return "min"
    if _MAX_RE.search(text) and not _RANK_RE.search(text):
        return "max"
    if _COUNT_RE.search(text):
        return "count"
    if row_scope == "all_rows":
        return "none"
    # Never silently default to avg — profile-style when unclear
    return "profile"


def _op_to_metric_kind(operation: str) -> MetricKind:
    return {
        "avg": "mean",
        "mean": "mean",
        "sum": "sum",
        "median": "median",
        "min": "min",
        "max": "max",
        "count": "count",
        "count_distinct": "count_distinct",
        "profile": "mean",
        "auto": "mean",
        "none": "count",
        "rate": "mean",
    }.get(operation, "mean")  # type: ignore[return-value]


def build_heuristic_ir(message: str, schema: TabularSchema) -> AnalysisIR:
    text = message or ""
    if _RANK_RE.search(text) and not _MEDIAN_RE.search(text):
        task_family: TaskFamily = "rank"
        row_scope = "top_n"
    elif _DIST_RE.search(text):
        task_family = "profile"
        row_scope = "all_rows"
    elif _VIS_RE.search(text):
        task_family = "visualize"
        row_scope = (
            "all_rows"
            if _ALL_ROWS_RE.search(text) and not _MEAN_RE.search(text)
            else "aggregated_rows"
        )
    elif _FILTER_RE.search(text) and _COUNT_RE.search(text):
        task_family = "filter"
        row_scope = "aggregated_rows"
    else:
        task_family = "aggregate"
        row_scope = "aggregated_rows"

    operation = _resolve_operation(text, row_scope=row_scope)
    if task_family == "filter" and operation in ("auto", "profile", "none"):
        operation = "count"

    if _BAR_RE.search(text):
        chart_type = "bar"
    elif _LINE_RE.search(text):
        chart_type = "line"
    elif _SCATTER_RE.search(text):
        chart_type = "scatter"
    elif _TABLE_RE.search(text) and not _VIS_RE.search(text):
        chart_type = "table"
    else:
        chart_type = "bar" if task_family in ("aggregate", "visualize", "rank", "filter") else "table"

    group_by = (
        _pick_group_candidate(text, schema)
        if ("按" in text or "分组" in text or "分类" in text)
        else ""
    )
    measures = _pick_measure_candidates(text, schema)
    if task_family in ("profile", "rank") and not measures:
        measures = schema.measure_candidates[:1]

    filters: list[FilterClause] = []
    filt = _extract_compare_filter(text, schema, measures)
    if filt is not None:
        filters.append(filt)
        if not group_by and filt.op in ("eq", "contains") and filt.column in schema.dimension_candidates:
            pass  # filter only
        task_family = "filter" if operation == "count" and filters else task_family

    # Region in question without explicit "按": still filter
    if not filters:
        m_dept = re.search(r"([\u4e00-\u9fff]{1,4})部门", text)
        if m_dept:
            dept_val = m_dept.group(1)
            for dim in schema.dimension_candidates:
                if "部门" in dim:
                    filters.append(FilterClause(column=dim, op="eq", value=dept_val))
                    break
    if not filters and "取消" in text and not _RATE_RE.search(text) and any(
        "取消" in c for c in schema.columns
    ):
        for c in schema.columns:
            if "取消" in c:
                filters.append(FilterClause(column=c, op="eq", value=1))
                break
    if not filters and "未取消" in text and any("取消" in c for c in schema.columns):
        for c in schema.columns:
            if "取消" in c:
                filters.append(FilterClause(column=c, op="eq", value=0))
                break
    if not filters:
        for dim in schema.dimension_candidates:
            for tok in ("华东", "华北", "华南", "西南", "西北", "东北"):
                if tok in text and dim:
                    # Prefer dim that looks like region
                    if any(k in dim for k in ("区", "地区", "大区", "区域")) or dim in text:
                        filters.append(FilterClause(column=dim, op="eq", value=tok))
                        break
            if filters:
                break

    time_column = ""
    time_grain: Literal["", "day", "week", "month"] = ""
    if schema.date_candidates:
        if _MONTH_RE.search(text):
            time_column = schema.date_candidates[0]
            time_grain = "month"
        elif _WEEK_RE.search(text):
            time_column = schema.date_candidates[0]
            time_grain = "week"
        elif _DAY_RE.search(text):
            time_column = schema.date_candidates[0]
            time_grain = "day"

    metrics: list[MetricSpec] = []
    asked_ids: list[str] = []
    uncomputable: list[UncomputableItem] = []

    # 「有年终奖列吗」类问法
    m_col = re.search(r"有\s*([^\s，,？?列吗]+)\s*列", text)
    if m_col:
        col_tok = (m_col.group(1) or "").strip()
        if col_tok and col_tok not in schema.columns:
            uncomputable.append(
                UncomputableItem(
                    reason=f"表中无列：{col_tok}",
                    missing_column=col_tok,
                    asked=col_tok,
                )
            )
            return AnalysisIR(
                task_family=task_family,
                group_by=group_by,
                measure_columns=measures,
                operation=operation if operation != "auto" else "profile",
                chart_type=chart_type,
                row_scope=row_scope,
                multi_sheet_policy="concat_all" if len(schema.sheets) > 1 else "single_sheet",
                output_contract={
                    "summary_marker": _SUMMARY_MARK,
                    "must_include": ["used_columns", "row_scope", "task_family", "rows", "SUMMARY_JSON"],
                    "requires_chart": False,
                },
                filters=filters,
                metrics=[],
                derive=[],
                asked_ids=[],
                uncomputable=uncomputable,
                time_column=time_column,
                time_grain=time_grain,
            )

    asked_col = _asked_measure_token(text)
    if not asked_col:
        for m in re.finditer(r"([\u4e00-\u9fff]{2,8})(?:合计|总和|平均|中位数)", text):
            tok = (m.group(1) or "").strip()
            if tok and tok not in _GENERIC_ASKED and tok not in schema.columns:
                asked_col = tok
                break
    asked_col = _normalize_asked_column(asked_col, schema)
    if asked_col and asked_col not in schema.columns and not _should_skip_missing_column(
        asked_col, schema, filters
    ):
        uncomputable.append(
            UncomputableItem(
                reason=f"表中无列：{asked_col}",
                missing_column=asked_col,
                asked=asked_col,
            )
        )
        return AnalysisIR(
            task_family=task_family,
            group_by=group_by,
            measure_columns=measures,
            operation=operation if operation != "auto" else "profile",
            chart_type=chart_type,
            row_scope=row_scope,
            multi_sheet_policy="concat_all" if len(schema.sheets) > 1 else "single_sheet",
            output_contract={
                "summary_marker": _SUMMARY_MARK,
                "must_include": ["used_columns", "row_scope", "task_family", "rows", "SUMMARY_JSON"],
                "requires_chart": False,
            },
            filters=filters,
            metrics=[],
            derive=[],
            asked_ids=[],
            uncomputable=uncomputable,
            time_column=time_column,
            time_grain=time_grain,
        )

    kind = _op_to_metric_kind(operation)
    if operation == "rate":
        operation = "none"
    if operation == "count":
        mid = "row_count"
        label = "行数" if not filters else "筛选后行数"
        metrics.append(MetricSpec(id=mid, kind="count", column="", label=label))
        asked_ids.append(mid)
    elif operation == "none":
        pass
    elif operation == "profile":
        col = measures[0] if measures else ""
        if col:
            for k, lab in (
                ("count", f"{col}_count"),
                ("mean", f"{col}_mean"),
                ("median", f"{col}_median"),
                ("min", f"{col}_min"),
                ("max", f"{col}_max"),
            ):
                metrics.append(MetricSpec(id=lab, kind=k, column=col, label=lab))  # type: ignore[arg-type]
            asked_ids.append(f"{col}_mean")
        else:
            uncomputable.append(
                UncomputableItem(reason="无可用数值列", missing_column="", asked=text[:80])
            )
    else:
        col = measures[0] if measures else ""
        if kind in ("sum", "mean", "median", "min", "max", "count_distinct") and not col:
            # Try to find column mentioned for the metric
            hint = ""
            for c in schema.columns:
                if c and c in text:
                    hint = c
                    break
            uncomputable.append(
                UncomputableItem(
                    reason=f"缺少计算 {kind} 所需的列",
                    missing_column=hint or "measure",
                    asked=text[:80],
                )
            )
        else:
            mid = f"{kind}_{_normalize_col(col) or 'rows'}"
            label = f"{col}{ {'median':'中位数','mean':'平均值','sum':'合计','min':'最小','max':'最大','count_distinct':'去重数'}.get(kind, kind) }"
            metrics.append(MetricSpec(id=mid, kind=kind, column=col, label=label))
            asked_ids.append(mid)

    derive: list[DeriveSpec] = []
    if _RATE_RE.search(text):
        flag_col = ""
        for c in schema.columns:
            if c in schema.measure_candidates and any(k in c for k in ("取消", "退货", "失败", "标记")):
                flag_col = c
                break
        if flag_col:
            mid = f"rate_{_normalize_col(flag_col)}"
            metrics.append(MetricSpec(id=mid, kind="mean", column=flag_col, label="比率"))
            asked_ids.append("rate")
        elif len(schema.measure_candidates) >= 1:
            num_col = ""
            den_col = ""
            for c in schema.columns:
                if any(k in c for k in ("取消", "退货", "失败", "迟到")):
                    num_col = c
                if any(k in c for k in ("总量", "总数", "全部")) and c in schema.measure_candidates:
                    den_col = c
            if num_col and den_col and num_col != den_col:
                derive.append(
                    DeriveSpec(
                        id="rate",
                        label="比率",
                        kind="div",
                        numerator=num_col,
                        denominator=den_col,
                    )
                )
                asked_ids.append("rate")
            elif num_col:
                derive.append(
                    DeriveSpec(
                        id="rate",
                        label="比率",
                        kind="row_mean",
                        numerator=num_col,
                        denominator=num_col,
                    )
                )
                asked_ids.append("rate")

    # Mark uncomputable when user asks median but we somehow lack column
    if _MEDIAN_RE.search(text) and not any(m.kind == "median" for m in metrics):
        need = measures[0] if measures else ""
        if need not in schema.columns:
            uncomputable.append(
                UncomputableItem(
                    reason="证据/表中无中位数所需列",
                    missing_column=need or "利润",
                    asked="中位数",
                )
            )

    return AnalysisIR(
        task_family=task_family,
        group_by=group_by,
        measure_columns=measures,
        operation=operation if operation != "profile" else "profile",
        chart_type=chart_type,
        row_scope=row_scope,
        multi_sheet_policy="concat_all" if len(schema.sheets) > 1 else "single_sheet",
        output_contract={
            "summary_marker": _SUMMARY_MARK,
            "must_include": ["used_columns", "row_scope", "task_family", "rows", "SUMMARY_JSON"],
            "requires_chart": chart_type != "table",
        },
        filters=filters,
        metrics=metrics,
        derive=derive,
        asked_ids=asked_ids,
        uncomputable=uncomputable,
        time_column=time_column,
        time_grain=time_grain,
    )


_JOIN_KEY_RE = re.compile(
    r"按\s*([^\s，,。.与和及]+)\s*[，,]?\s*(?:关联|连接|join|合并|关联后|连接后)",
    re.I,
)
_JOIN_KEY_ALT_RE = re.compile(r"(?:用|以)\s*([^\s，,。.]+)\s*(?:关联|连接|join|合并)", re.I)
_JOIN_KEY_TRAILING_RE = re.compile(
    r"(?:关联|连接|join|合并).{0,48}?按\s*([^\s，,。.与和及]+)",
    re.I,
)


def _files_named_in_message(message: str, filenames: list[str]) -> list[str]:
    msg_l = (message or "").lower()
    out: list[str] = []
    for fn in filenames:
        stem = Path(fn or "").stem.lower()
        if stem and (stem in msg_l or str(fn).lower() in msg_l):
            out.append(fn)
    return out


def infer_join_from_message(
    message: str,
    file_schemas: list[tuple[str, TabularSchema]],
) -> tuple[str, str, str]:
    """Infer equi-join left/right filenames and key column from the question."""
    if len(file_schemas) < 2:
        return "", "", ""
    filenames = [fn for fn, _ in file_schemas]
    mentioned = _files_named_in_message(message, filenames)
    if len(mentioned) < 2:
        mentioned = filenames[:2]
    if len(mentioned) < 2:
        return "", "", ""

    key = ""
    for pat in (_JOIN_KEY_RE, _JOIN_KEY_ALT_RE, _JOIN_KEY_TRAILING_RE):
        m = pat.search(message or "")
        if m:
            key = m.group(1).strip()
            break
    if not key:
        return "", "", ""

    left_name, right_name = mentioned[0], mentioned[1]
    schema_by_name = {fn: sch for fn, sch in file_schemas}
    left_schema = schema_by_name.get(left_name)
    right_schema = schema_by_name.get(right_name)
    if not left_schema or not right_schema:
        return "", "", ""

    actual_key = key
    left_cols = set(left_schema.columns)
    right_cols = set(right_schema.columns)
    if actual_key not in left_cols or actual_key not in right_cols:
        for c in left_cols:
            if c in key or key in c:
                if c in right_cols:
                    actual_key = c
                    break
        else:
            return "", "", ""
    if actual_key not in left_cols or actual_key not in right_cols:
        return "", "", ""
    return left_name, right_name, actual_key


def apply_join_heuristic(
    ir: AnalysisIR,
    message: str,
    file_schemas: list[tuple[str, TabularSchema]],
) -> AnalysisIR:
    if ir.join_key:
        return ir
    left, right, key = infer_join_from_message(message, file_schemas)
    if key:
        ir.join_left = left
        ir.join_right = right
        ir.join_key = key
    return ir


def validate_ir_against_schema(ir: AnalysisIR, schema: TabularSchema) -> AnalysisIR:
    """Drop invented columns; move impossible asks to uncomputable."""
    cols = set(schema.columns)
    measures_ok = [c for c in ir.measure_columns if c in cols]
    ir.measure_columns = measures_ok
    if ir.group_by and ir.group_by not in cols:
        ir.group_by = ""
    if ir.time_column and ir.time_column not in cols:
        ir.time_column = ""
        ir.time_grain = ""

    valid_filters: list[FilterClause] = []
    for f in ir.filters:
        if f.column in cols:
            valid_filters.append(f)
        else:
            ir.uncomputable.append(
                UncomputableItem(
                    reason="过滤列不存在",
                    missing_column=f.column,
                    asked=f"{f.op}:{f.value}",
                )
            )
    ir.filters = valid_filters

    valid_metrics: list[MetricSpec] = []
    for m in ir.metrics:
        if m.kind == "count" or (m.column and m.column in cols) or (not m.column and m.kind == "count"):
            if m.kind != "count" and m.column and m.column not in cols:
                ir.uncomputable.append(
                    UncomputableItem(
                        reason="指标列不存在",
                        missing_column=m.column,
                        asked=m.id,
                    )
                )
                ir.asked_ids = [a for a in ir.asked_ids if a != m.id]
            else:
                valid_metrics.append(m)
        else:
            ir.uncomputable.append(
                UncomputableItem(
                    reason="指标列不存在",
                    missing_column=m.column or "",
                    asked=m.id,
                )
            )
            ir.asked_ids = [a for a in ir.asked_ids if a != m.id]
    ir.metrics = valid_metrics

    valid_derive: list[DeriveSpec] = []
    for d in ir.derive:
        if d.kind not in _ALLOWED_DERIVE:
            continue
        ok_num = d.numerator in cols or any(m.id == d.numerator for m in ir.metrics)
        ok_den = d.denominator in cols or any(m.id == d.denominator for m in ir.metrics)
        if ok_num and ok_den:
            valid_derive.append(d)
        else:
            ir.uncomputable.append(
                UncomputableItem(
                    reason="派生指标缺少列",
                    missing_column=d.numerator if not ok_num else d.denominator,
                    asked=d.id,
                )
            )
            ir.asked_ids = [a for a in ir.asked_ids if a != d.id]
    ir.derive = valid_derive

    if ir.join_key and (ir.join_left or ir.join_right):
        # join files are names; key must exist — checked at runtime
        pass
    return ir


def merge_llm_plan(parsed: dict[str, Any], base: AnalysisIR, schema: TabularSchema) -> AnalysisIR:
    if not isinstance(parsed, dict):
        return base
    try:
        measures = [
            c
            for c in (parsed.get("measure_columns") or [])
            if c in schema.measure_candidates or c in schema.columns
        ]
        group_by = str(parsed.get("group_by") or "")
        if group_by and group_by not in schema.columns:
            group_by = ""
        operation = str(parsed.get("operation") or base.operation)
        if operation == "auto":
            operation = base.operation if base.operation != "auto" else "profile"

        filters: list[FilterClause] = list(base.filters)
        raw_filters = parsed.get("filters") or []
        if isinstance(raw_filters, list) and raw_filters:
            filters = []
            for item in raw_filters:
                if not isinstance(item, dict):
                    continue
                col = str(item.get("column") or "")
                op = str(item.get("op") or "eq")
                if col and op in (
                    "eq",
                    "ne",
                    "gt",
                    "ge",
                    "lt",
                    "le",
                    "in",
                    "not_in",
                    "contains",
                    "is_null",
                ):
                    filters.append(FilterClause(column=col, op=op, value=item.get("value")))  # type: ignore[arg-type]

        metrics: list[MetricSpec] = list(base.metrics)
        raw_metrics = parsed.get("metrics") or []
        if isinstance(raw_metrics, list) and raw_metrics:
            metrics = []
            for item in raw_metrics:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id") or "").strip() or "m"
                kind = str(item.get("kind") or "mean")
                if kind not in (
                    "sum",
                    "mean",
                    "median",
                    "min",
                    "max",
                    "count",
                    "count_distinct",
                    "quantile",
                ):
                    continue
                metrics.append(
                    MetricSpec(
                        id=mid,
                        kind=kind,  # type: ignore[arg-type]
                        column=str(item.get("column") or ""),
                        quantile=float(item["quantile"]) if item.get("quantile") is not None else None,
                        label=str(item.get("label") or mid),
                    )
                )

        asked = list(parsed.get("asked_ids") or base.asked_ids)
        if metrics and not asked:
            asked = [m.id for m in metrics]

        derive: list[DeriveSpec] = list(base.derive)
        raw_derive = parsed.get("derive") or []
        if isinstance(raw_derive, list) and raw_derive:
            derive = []
            for item in raw_derive:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "div")
                if kind not in _ALLOWED_DERIVE:
                    continue
                derive.append(
                    DeriveSpec(
                        id=str(item.get("id") or "rate"),
                        label=str(item.get("label") or ""),
                        kind=kind,  # type: ignore[arg-type]
                        numerator=str(item.get("numerator") or ""),
                        denominator=str(item.get("denominator") or ""),
                    )
                )

        ir = AnalysisIR(
            task_family=str(parsed.get("task_family") or base.task_family),  # type: ignore[arg-type]
            group_by=group_by or base.group_by,
            measure_columns=measures or base.measure_columns,
            operation=operation,
            chart_type=str(parsed.get("chart_type") or base.chart_type),
            row_scope=str(parsed.get("row_scope") or base.row_scope),
            multi_sheet_policy=str(parsed.get("multi_sheet_policy") or base.multi_sheet_policy),
            output_contract=parsed.get("output_contract") or base.output_contract,
            filters=filters,
            metrics=metrics,
            derive=derive,
            asked_ids=asked,
            uncomputable=list(base.uncomputable),
            time_column=str(parsed.get("time_column") or base.time_column),
            time_grain=str(parsed.get("time_grain") or base.time_grain) or "",  # type: ignore[arg-type]
            source_hint=str(parsed.get("source_hint") or base.source_hint),
            join_left=str(parsed.get("join_left") or ""),
            join_right=str(parsed.get("join_right") or ""),
            join_key=str(parsed.get("join_key") or ""),
            engine=str(parsed.get("engine") or base.engine),  # type: ignore[arg-type]
            patch_code=str(parsed.get("patch_code") or ""),
        )
        if not isinstance(ir.output_contract, dict):
            ir.output_contract = base.output_contract
        return validate_ir_against_schema(ir, schema)
    except Exception:
        return base


def validate_patch_code(code: str) -> str:
    """Return sanitized patch or empty string if unsafe."""
    src = (code or "").strip()
    if not src:
        return ""
    if len(src) > 4000:
        return ""
    try:
        tree = ast.parse(src, mode="exec")
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST_NODES):
            return ""
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return ""
        if isinstance(node, ast.Attribute):
            # walk root
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id not in _ALLOWED_ATTR_ROOTS and cur.id not in (
                "True",
                "False",
                "None",
            ):
                # allow chained from df/pd/np only
                if cur.id not in _ALLOWED_ATTR_ROOTS:
                    return ""
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_NAMES:
                return ""
            if isinstance(func, ast.Attribute) and func.attr in (
                "read_csv",
                "read_excel",
                "to_csv",
                "to_excel",
                "to_pickle",
                "system",
                "popen",
                "eval",
                "query",  # string channel
            ):
                return ""
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return ""
    return src


def parse_summary_payload(stdout: str) -> dict[str, Any]:
    """Extract structured SUMMARY_JSON from sandbox stdout."""
    text = stdout or ""
    if _SUMMARY_JSON_MARK in text:
        tail = text.split(_SUMMARY_JSON_MARK, 1)[1].strip()
        # first JSON object
        start = tail.find("{")
        if start >= 0:
            depth = 0
            for i, ch in enumerate(tail[start:], start=start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(tail[start : i + 1])
                        except json.JSONDecodeError:
                            break
    # fallback: scrape key=value lines after SUMMARY
    metrics: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    rows_in = 0
    if _SUMMARY_MARK in text:
        body = text.split(_SUMMARY_MARK, 1)[1]
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("rows="):
                try:
                    rows_in = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif "=" in line and not line.startswith("{"):
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k in ("task_family", "row_scope", "chart_type", "used_columns", "group_col", "operation"):
                    continue
                try:
                    num = float(v.replace(",", ""))
                    metrics.append({"id": k, "label": k, "value": num, "unit": None})
                except ValueError:
                    pass
    return {
        "file": "",
        "rows_in": rows_in,
        "rows_after_filter": rows_in,
        "metrics": metrics,
        "missing": missing,
        "asked_ids": [m["id"] for m in metrics],
    }


def metric_values_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for m in payload.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        try:
            out[mid] = float(m.get("value"))
        except (TypeError, ValueError):
            continue
    return out


def asked_ids_covered(payload: dict[str, Any], asked_ids: list[str]) -> tuple[bool, list[str]]:
    if not asked_ids:
        # no specific ask — any metrics or explicit missing ok
        if payload.get("metrics") or payload.get("missing") is not None:
            return True, []
        return False, ["(any)"]
    have = {str(m.get("id")) for m in (payload.get("metrics") or []) if isinstance(m, dict)}
    missing_asked = [a for a in asked_ids if a not in have]
    # If marked uncomputable for those ids, treat as covered (honest gap)
    miss_ids = {
        str(x.get("asked") or x.get("id") or "")
        for x in (payload.get("missing") or [])
        if isinstance(x, dict)
    }
    still = [a for a in missing_asked if a not in miss_ids]
    return len(still) == 0, still


def numbers_equivalent(a: float, b: float, *, tol: float = 1e-6) -> bool:
    if abs(a - b) <= tol:
        return True
    # rate equivalence: 0.082 ↔ 8.2 (%)
    if abs(a * 100 - b) <= max(tol * 100, 1e-4):
        return True
    if abs(b * 100 - a) <= max(tol * 100, 1e-4):
        return True
    return False


def expand_allowed_number_tokens(values: list[float]) -> set[str]:
    """String forms accepted in answers for metric values (incl. percent)."""
    tokens: set[str] = set()
    for v in values:
        for fmt in (f"{v}", f"{v:.10g}", f"{v:.2f}", f"{v:.1f}", f"{v:.0f}"):
            tokens.add(fmt.replace(",", "").rstrip("0").rstrip(".") if "." in fmt else fmt)
        # percent forms
        pct = v * 100
        for fmt in (f"{pct}", f"{pct:.2f}", f"{pct:.1f}", f"{pct:.0f}"):
            cleaned = fmt.replace(",", "").rstrip("0").rstrip(".") if "." in fmt else fmt
            tokens.add(cleaned)
            tokens.add(cleaned + "%")
        if abs(v) >= 1:
            pct2 = v / 100.0
            for fmt in (f"{pct2}", f"{pct2:.4f}", f"{pct2:.3f}", f"{pct2:.2f}"):
                tokens.add(fmt.replace(",", "").rstrip("0").rstrip(".") if "." in fmt else fmt)
    # normalize
    return {t for t in tokens if t and t not in (".", "-")}


PLANNER_SYSTEM = (
    "你是表格分析规划器。只输出 JSON。"
    '结构：{"task_family":"aggregate|compare|visualize|rank|filter|profile",'
    ' "group_by":"", "measure_columns":["..."],'
    ' "operation":"sum|avg|count|median|min|max|count_distinct|none|profile",'
    ' "chart_type":"bar|line|table|scatter|auto",'
    ' "row_scope":"all_rows|aggregated_rows|top_n",'
    ' "multi_sheet_policy":"concat_all|single_sheet|auto",'
    ' "filters":[{"column":"","op":"eq|ne|gt|ge|lt|le|in|contains|is_null","value":null}],'
    ' "metrics":[{"id":"","kind":"sum|mean|median|min|max|count|count_distinct","column":"","label":""}],'
    ' "derive":[{"id":"rate","kind":"div","numerator":"","denominator":"","label":""}],'
    ' "asked_ids":["..."],'
    ' "time_column":"", "time_grain":"day|week|month|",'
    ' "source_hint":"", "join_left":"", "join_right":"", "join_key":"",'
    ' "output_contract":{"summary_marker":"===SUMMARY===","must_include":["used_columns","rows","SUMMARY_JSON"]}}'
    " 规则：只可从给定 schema 选择列名；不要编造列名。"
    " 禁止把 operation 设为 auto；不确定时用 profile 或在 metrics 里列出。"
    " 问中位数用 median；问超过/小于某值的人数用 filters+count；"
    " 列不存在时不要用其他列顶替，把需求放进 asked_ids，由执行器标记 missing。"
)
