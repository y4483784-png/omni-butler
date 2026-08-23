"""Shared tabular cell cleaning for eval gold standards and sandbox execution."""

from __future__ import annotations

import re

_DIRTY_NUM_RE = re.compile(r"^[+-]?[\d,.\s]+%?$")


def clean_cell_text(value: object) -> str:
    """Normalize a single cell for numeric parsing (commas, trailing %)."""
    s = str(value or "").strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return s.replace(",", "").replace("%", "").strip()


def coerce_series_numeric(series):
    """Return a numeric pandas Series with per-cell comma/% stripping."""
    import pandas as pd

    if series is None:
        return pd.Series(dtype=float)
    if getattr(series, "dtype", None) != object:
        return pd.to_numeric(series, errors="coerce")
    cleaned = series.astype(str).str.strip().str.replace(",", "", regex=False).str.replace("%", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def clean_dataframe_measures(df, columns: list[str] | None = None):
    """Coerce measure-like columns to numeric in-place; returns df."""
    import pandas as pd

    out = df.copy()
    cols = list(columns) if columns else list(out.columns)
    for col in cols:
        if col not in out.columns:
            continue
        if out[col].dtype == object or _looks_numeric_column(out[col]):
            out[col] = coerce_series_numeric(out[col])
    return out


def _looks_numeric_column(series) -> bool:
    sample = series.dropna().astype(str).head(20)
    if sample.empty:
        return False
    hits = sum(1 for v in sample if _DIRTY_NUM_RE.match(str(v).strip()))
    return hits >= max(1, len(sample) // 2)


def clean_sales_frame(df):
    """Generator-compatible sales_regions cleaning."""
    return clean_dataframe_measures(df, [c for c in df.columns if c in ("利润", "销售额")])
