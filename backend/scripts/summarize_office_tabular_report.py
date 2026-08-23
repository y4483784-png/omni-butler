#!/usr/bin/env python3
"""Print a Markdown summary from office_tabular_eval_latest.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "reports" / "office_tabular_eval_latest.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, default=DEFAULT)
    args = ap.parse_args()
    r = json.loads(args.report.read_text(encoding="utf-8"))
    lines = [
        "# Office Tabular Eval Summary",
        "",
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
            lines.append(
                f"- `{f.get('id')}` [{f.get('failure_kind')}] "
                f"expected={f.get('expected')} actual={f.get('actual')}"
            )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
