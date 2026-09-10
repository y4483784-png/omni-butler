#!/usr/bin/env python3
"""Aggregate ContextManager chat_* log lines into an online report."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_REPORT = Path(__file__).resolve().parents[1] / "reports" / "context_online_latest.json"

_TTFT_RE = re.compile(
    r"chat_ttft\s+.*?\bttft_ms=(?P<ttft>\d+).*?\bctx_tokens=(?P<tokens>\d+)"
    r".*?\bctx_level=(?P<level>\S+).*?\bctx_dropped=(?P<dropped>\d+)"
    r"(?:.*?\bctx_compose_level=(?P<compose_level>\S+))?"
)
_CTX_RE = re.compile(
    r"chat_ctx\s+.*?\bctx_est_tokens=(?P<est>\d+)\s+ctx_actual_tokens=(?P<actual>\S+)"
    r"\s+ctx_err_pct=(?P<err>\S+).*?\bsummary_status=(?P<summary>\S+)"
)


def parse_lines(text: str) -> dict:
    levels: Counter[str] = Counter()
    compose_levels: Counter[str] = Counter()
    ttfts: list[float] = []
    dropped = 0
    compacted = 0
    n_ttft = 0
    est_err: list[float] = []
    summary_status: Counter[str] = Counter()
    n_ctx = 0

    for line in text.splitlines():
        m = _TTFT_RE.search(line)
        if m:
            n_ttft += 1
            ttfts.append(float(m.group("ttft")))
            level = m.group("level")
            levels[level] += 1
            if level and level != "safe":
                compacted += 1
            dropped += int(m.group("dropped"))
            cl = m.group("compose_level")
            if cl and cl != "-":
                compose_levels[cl] += 1
        m2 = _CTX_RE.search(line)
        if m2:
            n_ctx += 1
            err = m2.group("err")
            if err not in ("-", "None", ""):
                try:
                    est_err.append(float(err))
                except ValueError:
                    pass
            summary_status[m2.group("summary")] += 1

    def p95(vals: list[float]) -> float | None:
        if not vals:
            return None
        vals = sorted(vals)
        idx = max(0, int(round(0.95 * (len(vals) - 1))))
        return vals[idx]

    summary_fail = summary_status.get("failed", 0) + summary_status.get("失败_llm_error", 0)
    summary_total = sum(summary_status.values()) or 1

    report = {
        "n_ttft": n_ttft,
        "n_ctx": n_ctx,
        "ctx_level_dist": dict(levels),
        "compose_level_dist": dict(compose_levels),
        "compaction_rate": round(compacted / n_ttft, 4) if n_ttft else 0.0,
        "dropped_turns_sum": dropped,
        "ttft_ms_p50": round(statistics.median(ttfts), 2) if ttfts else None,
        "ttft_ms_p95": p95(ttfts),
        "est_error_pct_mean": round(sum(est_err) / len(est_err), 2) if est_err else None,
        "summary_status_dist": dict(summary_status),
        "summary_fail_rate": round(summary_fail / summary_total, 4),
        "alerts": [],
    }
    if report["summary_fail_rate"] > 0.05:
        report["alerts"].append("summary_fail_rate>5%")
    if report["est_error_pct_mean"] is not None and report["est_error_pct_mean"] > 20:
        report["alerts"].append("est_error_pct>20%")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse chat_ttft/chat_ctx logs")
    parser.add_argument("--log", type=Path, required=True, help="Log file path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    text = args.log.read_text(encoding="utf-8", errors="replace")
    report = parse_lines(text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"n_ttft={report['n_ttft']} compaction_rate={report['compaction_rate']} "
            f"summary_fail_rate={report['summary_fail_rate']} alerts={report['alerts']}"
        )
        print(f"report -> {args.report}")
    return 1 if report["alerts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
