#!/usr/bin/env python3
"""Grep each 引用锚点 row in docs/current-product-flows.md."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "current-product-flows.md"
SECTION = "## 引用锚点"


def anchors(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == SECTION:
            start = index + 1
            break
    if start is None:
        return []
    rows: list[tuple[str, str]] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        path, symbol = cells[0], cells[1]
        if path in {"文件", ""} or set(path) <= set("-: "):
            continue
        rows.append((path, symbol))
    return rows


def main() -> int:
    if not DOC.is_file():
        print(f"missing doc {DOC}", file=sys.stderr)
        return 1
    rows = anchors(DOC.read_text(encoding="utf-8"))
    if not rows:
        print("no anchors parsed", file=sys.stderr)
        return 1
    failed = 0
    for path, symbol in rows:
        target = ROOT / path
        if not target.is_file():
            print(f"MISSING FILE\t{path}\t{symbol}")
            failed += 1
            continue
        found = subprocess.run(
            ["grep", "-F", "-q", "-e", symbol, "--", str(target)],
            check=False,
        )
        if found.returncode != 0:
            print(f"MISSING ANCHOR\t{path}\t{symbol}")
            failed += 1
    print(f"checked {len(rows)} anchors, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
