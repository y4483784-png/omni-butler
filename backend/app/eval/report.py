"""Unified eval report writer: runs/<ts>/ + latest.json + index.jsonl."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def reports_root() -> Path:
    override = (os.environ.get("EVAL_REPORTS_DIR") or "").strip()
    root = Path(override) if override else BACKEND_ROOT / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _git_sha() -> str:
    env_sha = (os.environ.get("GIT_SHA") or "").strip()
    if env_sha:
        return env_sha
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BACKEND_ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip() or "unknown"
    except Exception:
        return "unknown"


def file_sha256(path: Path | None) -> str | None:
    if path is None or not Path(path).is_file():
        return None
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def dig(obj: Any, path: str, default: Any = None) -> Any:
    """Resolve dotted / bracket-free path like ``metrics.recall_at_k``."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(part, default if part == path.split(".")[-1] else None)
        else:
            cur = getattr(cur, part, default)
    return cur if cur is not None else default


@dataclass
class SuiteResult:
    """Normalized suite output for the report writer."""

    report: dict[str, Any]
    samples: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = 0
    summary_lines: list[str] = field(default_factory=list)
    dataset_path: Path | None = None


@dataclass
class RunContext:
    suite: str
    profile: str
    target: str
    argv: list[str]
    started_at: datetime
    run_dir: Path
    dataset_path: Path | None = None
    settings_snapshot: dict[str, Any] = field(default_factory=dict)
    key_metrics: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.started_at).total_seconds())


def make_run_dir(suite: str, profile: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = reports_root() / suite / "runs" / f"{ts}_{profile}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def settings_snapshot_safe() -> dict[str, Any]:
    """Public, non-secret knobs for report meta."""
    try:
        from app.core.config import settings

        return {
            "llm_model": settings.llm_model,
            "router_model": settings.router_model or settings.planner_model or settings.llm_model,
            "embedding_model": settings.embedding_model,
            "rag_top_k": settings.rag_top_k,
            "rag_hybrid_enabled": settings.rag_hybrid_enabled,
            "rag_rerank_provider": settings.rag_rerank_provider,
            "context_manager_enabled": settings.context_manager_enabled,
            "grounding_enabled": settings.grounding_enabled,
            "redis_enabled": settings.redis_enabled,
            "database_url_scheme": (settings.database_url or "").split(":", 1)[0],
        }
    except Exception as exc:
        return {"error": str(exc)}


def write_run(ctx: RunContext, result: SuiteResult) -> Path:
    """Write report.json / samples.jsonl / summary.md / latest* / index.jsonl."""
    duration = ctx.duration_s
    meta = {
        "suite": ctx.suite,
        "profile": ctx.profile,
        "target": ctx.target,
        "argv": ctx.argv,
        "started_at": ctx.started_at.isoformat(),
        "duration_s": round(duration, 3),
        "git_sha": _git_sha(),
        "dataset": str(ctx.dataset_path) if ctx.dataset_path else None,
        "dataset_sha256": file_sha256(ctx.dataset_path),
        "settings": ctx.settings_snapshot or settings_snapshot_safe(),
        "exit_code": int(result.exit_code),
        "run_dir": str(ctx.run_dir),
    }
    payload = {
        "meta": meta,
        "metrics": result.report.get("metrics", result.report),
        "failures": result.report.get("failures", []),
        "gate": result.report.get("gate"),
        "raw": result.report,
    }

    report_path = ctx.run_dir / "report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    samples_path = ctx.run_dir / "samples.jsonl"
    with samples_path.open("w", encoding="utf-8") as f:
        for row in result.samples:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = result.summary_lines or _default_summary(ctx, payload)
    summary_path = ctx.run_dir / "summary.md"
    summary_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    suite_dir = reports_root() / ctx.suite
    suite_dir.mkdir(parents=True, exist_ok=True)
    # Copy, do not symlink (HGFS / Windows).
    shutil.copyfile(report_path, suite_dir / "latest.json")
    shutil.copyfile(summary_path, suite_dir / "latest.md")

    key_vals: dict[str, Any] = {}
    for path in ctx.key_metrics:
        key_vals[path] = dig(payload, path)
        if key_vals[path] is None:
            key_vals[path] = dig(result.report, path)

    index_row = {
        "suite": ctx.suite,
        "profile": ctx.profile,
        "target": ctx.target,
        "started_at": meta["started_at"],
        "duration_s": meta["duration_s"],
        "exit_code": meta["exit_code"],
        "git_sha": meta["git_sha"],
        "run_dir": str(ctx.run_dir),
        "latest": str(suite_dir / "latest.json"),
        "key_metrics": key_vals,
    }
    index_path = reports_root() / "index.jsonl"
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(index_row, ensure_ascii=False) + "\n")

    return report_path


def _default_summary(ctx: RunContext, payload: dict[str, Any]) -> list[str]:
    lines = [
        f"# {ctx.suite} · {ctx.profile}",
        "",
        f"- target: `{ctx.target}`",
        f"- exit: `{payload['meta']['exit_code']}`",
        f"- duration: `{payload['meta']['duration_s']}s`",
        f"- git: `{payload['meta']['git_sha']}`",
        f"- run_dir: `{ctx.run_dir}`",
        "",
        "## Metrics",
        "",
        "```json",
        json.dumps(payload.get("metrics"), ensure_ascii=False, indent=2)[:4000],
        "```",
    ]
    fails = payload.get("failures") or []
    if fails:
        lines.extend(["", f"## Failures ({len(fails)})", ""])
        for row in fails[:20]:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('id') or row.get('case_id') or '?'}`: {json.dumps(row, ensure_ascii=False)[:200]}")
            else:
                lines.append(f"- {row}")
    return lines


def latest_path(suite: str) -> Path:
    return reports_root() / suite / "latest.json"


def read_latest(suite: str) -> dict[str, Any] | None:
    p = latest_path(suite)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
