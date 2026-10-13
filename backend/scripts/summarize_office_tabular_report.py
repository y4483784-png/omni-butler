#!/usr/bin/env python3
"""Print a Markdown summary from tabular suite latest.json (or legacy path)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "reports" / "tabular" / "latest.json"
LEGACY = ROOT / "reports" / "office_tabular_eval_latest.json"


def _load(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "raw" in raw and isinstance(raw["raw"], dict) and "accuracy" in raw["raw"]:
        return raw["raw"]
    if "metrics" in raw and isinstance(raw["metrics"], dict) and "accuracy" in raw["metrics"]:
        return raw["metrics"]
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()
    path = args.report or (DEFAULT if DEFAULT.is_file() else LEGACY)
    r = _load(path)
    lines = [
        "# Office Tabular Eval Summary",
        "",
        f"- source: `{path}`",
        f"- mode: **{r.get('mode')}** | split: **{r.get('split', 'all')}** | total: **{r.get('total')}**",
        f"- Exec accuracy: **{r.get('accuracy', 0):.1%}** | Plan: **{r.get('plan_accuracy', 0):.1%}**",
        f"- Smoke exec: **{r.get('smoke_accuracy', 0):.1%}** | Join ok: **{r.get('join_success_accuracy', 0):.1%}**",
        "",
        "## By tag",
    ]
    for tag, stats in sorted((r.get("by_tag") or {}).items()):
        lines.append(
            f"- `{tag}`: exec {stats.get('exec_ok', 0)}/{stats.get('total', 0)} "
            f"({stats.get('accuracy', 0):.0%})"
        )
    gap = r.get("plan_exec_gap_by_tag") or {}
    if gap:
        lines.extend(["", "## Plan−Exec gap (top)"])
        for tag, g in sorted(gap.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"- `{tag}`: {g:.0%}")
    fails = r.get("top_failures") or []
    if fails:
        lines.extend(["", "## Top failures"])
        for f in fails[:10]:
            lines.append(f"- {f}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
