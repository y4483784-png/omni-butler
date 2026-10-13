"""Shared tabular cell cleaning for eval gold standards and sandbox execution.

``%`` values become fractions (``12.5%`` → ``0.125``). Quoted / comma-grouped
numbers are parsed, not dropped. The sandbox helper snippet below is the same
logic inlined into the isolated runner (it cannot import this module).
"""

from __future__ import annotations

import re

_DIRTY_NUM_RE = re.compile(r"""^[+-]?["']?[\d,.\s]+%?["']?$""")


def clean_cell_text(value: object) -> str:
    """Strip wrapping quotes/whitespace; empty/NA sentinels become ``""``."""
    s = str(value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return s


def parse_numeric_cell(value: object) -> float | None:
    """Parse one cell: commas stripped; a trailing ``%`` divides by 100."""
    s = clean_cell_text(value)
    if not s:
        return None
    pct = s.endswith("%")
    s = s.replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return n / 100.0 if pct else n


def coerce_series_numeric(series):
    """Return a numeric pandas Series with per-cell comma/%/quote stripping."""
    import pandas as pd

    if series is None:
        return pd.Series(dtype=float)
    parsed = series.map(parse_numeric_cell)
    return pd.to_numeric(parsed, errors="coerce")


def _looks_numeric_column(series) -> bool:
    sample = series.dropna().head(20)
    if sample.empty:
        return False
    hits = sum(1 for v in sample if parse_numeric_cell(v) is not None)
    return hits >= max(1, len(sample) // 2)


def clean_dataframe_measures(df, columns: list[str] | None = None):
    """Coerce measure-like columns to numeric; returns a copy."""
    out = df.copy()
    cols = list(columns) if columns else list(out.columns)
    for col in cols:
        if col not in out.columns:
            continue
        if out[col].dtype == object or _looks_numeric_column(out[col]):
            out[col] = coerce_series_numeric(out[col])
    return out


def clean_loaded_frame(df):
    """Read-as-string frame → coerce columns that are mostly numeric."""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    drop = [c for c in out.columns if str(c).strip() == ""]
    if drop:
        out = out.drop(columns=drop)
    for col in list(out.columns):
        if _looks_numeric_column(out[col]):
            out[col] = coerce_series_numeric(out[col])
    return out


def clean_sales_frame(df):
    """Generator-compatible sales_regions cleaning."""
    return clean_dataframe_measures(df, [c for c in df.columns if c in ("利润", "销售额")])


# Inlined into the sandbox runner (no app imports there). Keep in lockstep with
# parse_numeric_cell / clean_loaded_frame above.
SANDBOX_CLEAN_SNIPPET = r'''
def parse_numeric_cell(value):
    s = str(value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    pct = s.endswith("%")
    s = s.replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        n = float(s)
    except (TypeError, ValueError):
        return None
    return n / 100.0 if pct else n

def clean_loaded_frame(df):
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    drop = [c for c in list(out.columns) if str(c).strip() == ""]
    if drop:
        out = out.drop(columns=drop)
    for col in list(out.columns):
        parsed = out[col].map(parse_numeric_cell)
        if parsed.notna().sum() >= max(1, int(len(parsed) * 0.5)):
            out[col] = parsed
    return out
'''
