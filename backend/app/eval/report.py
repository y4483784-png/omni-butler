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
    input_paths: list[Path] = field(default_factory=list)
    # latency / token / retry / infra accounting, merged into meta["run_stats"]
    run_stats: dict[str, Any] = field(default_factory=dict)


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
    base = reports_root() / suite / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = base / f"{ts}_{profile}"
    n = 1
    while True:
        try:
            run_dir.mkdir()
            return run_dir
        except FileExistsError:
            n += 1
            run_dir = base / f"{ts}_{profile}_{n}"


INDEX_MAX_LINES = 2000


def _append_index(index_path: Path, row: dict[str, Any]) -> None:
    """Append one index row; rotate when the file grows past INDEX_MAX_LINES."""
    if index_path.is_file():
        n = sum(1 for _ in index_path.open(encoding="utf-8"))
        if n >= INDEX_MAX_LINES:
            rotated = index_path.with_name("index.jsonl.1")
            if rotated.exists():
                rotated.unlink()
            os.replace(index_path, rotated)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def hash_inputs(paths: list[Path] | None) -> dict[str, str]:
    """sha256 for every input artifact (dataset, corpus dir files, fixtures)."""
    out: dict[str, str] = {}
    for p in paths or []:
        p = Path(p)
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            digest = file_sha256(f)
            if digest:
                key = f"{p.name}/{f.relative_to(p).as_posix()}" if p.is_dir() else f.name
                out[key] = digest
    return out


def settings_snapshot_safe() -> dict[str, Any]:
    """Public, non-secret knobs for report meta."""
    try:
        from app.core.config import settings

        from app.core.llm import ROLE_SPECS, resolve

        roles: dict[str, Any] = {}
        for role, spec in ROLE_SPECS.items():
            call = resolve(role)
            roles[spec.model_env.lower()] = call.model
            roles[spec.thinking_env.lower()] = "on" if call.thinking else "off"
        return {
            **roles,
            "embedding_model": settings.embedding_model,
            "llm_base_url": settings.llm_base_url,
            "rerank_model": getattr(settings, "rerank_model", None),
            "rag_top_k": settings.rag_top_k,
            "rag_candidate_k": getattr(settings, "rag_candidate_k", None),
            "rag_expand_window": getattr(settings, "rag_expand_window", None),
            "llm_timeout": settings.llm_timeout,
            "rag_hybrid_enabled": settings.rag_hybrid_enabled,
            "rag_rerank_provider": settings.rag_rerank_provider,
            "context_manager_enabled": settings.context_manager_enabled,
            "context_max_tokens": getattr(settings, "context_max_tokens", None),
            "grounding_enabled": settings.grounding_enabled,
            "redis_enabled": settings.redis_enabled,
            "database_url_scheme": (settings.database_url or "").split(":", 1)[0],
        }
    except Exception as exc:
        return {"error": str(exc)}


def default_input_paths(suite: str) -> list[Path]:
    """Inputs beyond the dataset file that a run's numbers depend on.

    Hashing only the dataset leaves the corpus and the table fixtures invisible: a
    fixture edit silently changes what a "regression" means.
    """
    data_root = BACKEND_ROOT / "data" / "eval"
    common = [data_root / "fixtures"]
    by_suite: dict[str, list[Path]] = {
        "rag": [data_root / "corpus"],
        "agent": [data_root / "seeds"],
        "grounding": [data_root / "seeds"],
    }
    return common + by_suite.get(suite, [])


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
        "inputs_sha256": hash_inputs(
            ([ctx.dataset_path] if ctx.dataset_path else [])
            + list(result.input_paths)
            + default_input_paths(ctx.suite)
        ),
        "run_stats": result.run_stats,
        "settings": ctx.settings_snapshot or settings_snapshot_safe(),
        "exit_code": int(result.exit_code),
        "run_dir": str(ctx.run_dir),
    }
    payload = {
        "meta": meta,
        "metrics": result.report.get("metrics", result.report),
        "failures": result.report.get("failures") or result.report.get("top_failures") or list(result.samples),
        "gate": result.report.get("gate"),
        "gates": result.report.get("gates"),
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
    passed = int(result.exit_code) == 0
    profile_latest = suite_dir / f"latest-{ctx.profile}.json"
    # "Promoted" = this run is what the profile-level latest file now shows: either it
    # passed, or there was nothing there to protect.
    promoted = passed or not profile_latest.is_file()
    # Copy, do not symlink (HGFS / Windows). A failing run is still archived under
    # runs/, but it must not become "the latest result": that file is what a reader
    # (and the next run's regression baseline) sees first.
    if promoted:
        _atomic_copy(report_path, profile_latest)
        _atomic_copy(summary_path, suite_dir / f"latest-{ctx.profile}.md")
    # Legacy ``latest.json`` = most recent *passing full* run; smoke never clobbers it.
    legacy = suite_dir / "latest.json"
    legacy_ok = passed and ctx.profile == "full"
    if not legacy_ok:
        prev_profile = prev_exit = None
        if legacy.is_file():
            try:
                prev_meta = json.loads(legacy.read_text(encoding="utf-8")).get("meta") or {}
                prev_profile = prev_meta.get("profile")
                prev_exit = prev_meta.get("exit_code")
            except (OSError, json.JSONDecodeError):
                prev_profile = prev_exit = None
        # First-ever run, or replacing a baseline that was itself not a passing full run.
        legacy_ok = prev_profile is None or prev_profile != "full" or int(prev_exit or 0) != 0
    if legacy_ok:
        _atomic_copy(report_path, legacy)
        _atomic_copy(summary_path, suite_dir / "latest.md")

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
        "latest": str(profile_latest),
        "promoted_to_latest": bool(promoted),
        "key_metrics": key_vals,
    }
    index_path = reports_root() / "index.jsonl"
    _append_index(index_path, index_row)

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


def latest_path(suite: str, profile: str | None = None) -> Path:
    name = f"latest-{profile}.json" if profile else "latest.json"
    return reports_root() / suite / name


def read_latest(suite: str, profile: str | None = None) -> dict[str, Any] | None:
    p = latest_path(suite, profile)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def profile_runs(suite: str, profile: str) -> list[Path]:
    """Every archived run of one suite/profile, oldest first."""
    base = reports_root() / suite / "runs"
    if not base.is_dir():
        return []
    runs = [
        p
        for p in base.iterdir()
        if p.is_dir() and (p / "report.json").is_file() and f"_{profile}" in p.name
    ]
    return sorted(runs, key=lambda p: p.name)


def read_baseline(suite: str, profile: str) -> dict[str, Any] | None:
    """Last *passing* run of this suite/profile, for regression comparison.

    Comparing against the newest run regardless of outcome lets a bad run become the
    yardstick for the next one, so a regression can hide behind an earlier regression.
    Falls back to the newest archived run when nothing passed yet.
    """
    runs = profile_runs(suite, profile)
    if not runs:
        return read_latest(suite, profile)
    for run in reversed(runs):
        try:
            payload = json.loads((run / "report.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if int((payload.get("meta") or {}).get("exit_code") or 0) == 0:
            return payload
    try:
        return json.loads((runs[-1] / "report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return read_latest(suite, profile)
