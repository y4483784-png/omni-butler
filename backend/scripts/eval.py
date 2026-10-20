#!/usr/bin/env python3
"""Unified Omni-Butler eval runner.

Usage:
  python scripts/eval.py list
  python scripts/eval.py run rag --profile smoke
  python scripts/eval.py run agent --profile full --target services
  python scripts/eval.py last agent
  # yz 套件已下线，不要再 run yz。历史脚本 scripts/run_yz_eval.py 不在注册表里。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.eval.env import bootstrap, resolve_target, stub_blob_store_if_needed  # noqa: E402
from app.eval.gates import evaluate_gates  # noqa: E402
from app.eval.report import (  # noqa: E402
    RunContext,
    SuiteResult,
    latest_path,
    make_run_dir,
    read_baseline,
    read_latest,
    reports_root,
    settings_snapshot_safe,
    write_run,
)
from app.eval.suites import (  # noqa: E402
    SuiteSpec,
    get_suite,
    list_suites,
    load_suite_module,
    profile_argv,
)


def llm_stats_reset() -> None:
    try:
        from app.core.llm import llm_stats_reset as _reset

        _reset()
    except Exception:
        pass


def llm_stats_snapshot() -> dict[str, Any]:
    try:
        from app.core.llm import llm_stats_snapshot as _snap

        return {"llm": _snap()}
    except Exception:
        return {}


class _Tee:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def _tcp_ok(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    from urllib.request import urlopen

    try:
        with urlopen(url, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 300
    except OSError:
        return False


def _docker_ok() -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", "omni-sandbox:latest"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _llm_key_ok() -> bool:
    return bool(
        (os.environ.get("LLM_API_KEY") or "").strip()
    )


def _degradation_reason(report: dict[str, Any], profile: str) -> str:
    """Human-readable reason when a run fell back off the production path.

    Only the *full* profile promises the production path. The smoke profile is defined
    as a deterministic replay (keyword retrieval, cache replay, plan-only compile), so
    falling back there is the profile, not a degradation.
    """
    if not isinstance(report, dict) or profile != "full":
        return ""
    if report.get("degraded") is True:
        if "planner_error_rate" in report:
            return (
                "degraded: LLM planner failed on "
                f"{report.get('planner_error_count')} task(s)"
            )
        return "degraded: run did not use the production path"
    metrics = report.get("metrics")
    if isinstance(metrics, dict) and metrics.get("degraded") is True:
        mode = metrics.get("mode")
        return f"degraded: retrieval mode={mode} (production path not exercised)"
    return ""


def _sandbox_runner_health_url() -> str:
    base = (os.environ.get("SANDBOX_RUNNER_URL") or "http://sandbox-runner:8002").strip()
    return base.rstrip("/") + "/health"


def preflight(suite: SuiteSpec, target: str, profile: str, extra: list[str]) -> list[str]:
    """Return human-readable blockers; empty means OK."""
    errors: list[str] = []
    req = set(suite.requires)
    flags = list(profile_argv(suite, profile)) + list(extra)

    # Profile argv + CLI extras after `--` (not only the extras list).
    if suite.id == "tabular" and "--dry-compile" not in flags:
        req.add("sandbox")
    if suite.id == "tabular" and any(
        flags[i] == "--planner" and flags[i + 1] == "llm" for i in range(len(flags) - 1)
    ):
        req.add("llm")
    if suite.id == "rag" and "--zhipu-rerank" in flags:
        req.add("llm")
    if suite.id == "rag" and "--production" in flags:
        # Production ingest needs MinIO + Qdrant regardless of target; the venv
        # branch below then refuses early instead of degrading silently.
        req.update({"llm", "minio", "qdrant"})
    if suite.id == "context" and "--live" in flags:
        req.add("llm")

    if target == "venv" and suite.default_target == "services" and (
        "minio" in req or "qdrant" in req
    ):
        errors.append(
            f"套件 {suite.id} 默认需要 Compose 中间件。"
            "请改用：docker compose --profile eval run --rm eval "
            f"python scripts/eval.py run {suite.id} --profile {profile}"
        )

    # Calendar smoke replays a cache of real responses and does not call the model.
    # Routing smoke still asks for a key; do not copy that gap onto this suite.
    cache_replay = suite.id == "calendar" and "--require-cache" in flags
    if "llm" in req and not _llm_key_ok() and not cache_replay:
        # Settings may still load key from .env after bootstrap — soft check only
        # for early hint; suite will fail later if truly missing.
        env_file = BACKEND / ".env"
        has_file_key = False
        if env_file.is_file():
            text = env_file.read_text(encoding="utf-8", errors="ignore")
            has_file_key = "LLM_API_KEY=" in text
        if not has_file_key:
            errors.append("缺少 LLM_API_KEY（backend/.env 或环境变量）")

    if target == "services":
        if "minio" in req and not _tcp_ok("minio", 9000) and not _tcp_ok("127.0.0.1", 9000):
            errors.append(
                "MinIO 不可达。请在 Compose 栈已 up 时用 eval 服务运行："
                f"docker compose --profile eval run --rm eval python scripts/eval.py run {suite.id}"
            )
        if "qdrant" in req and not _tcp_ok("qdrant", 6333) and not _tcp_ok("127.0.0.1", 6333):
            errors.append("Qdrant 不可达（期望服务名 qdrant:6333）")
        if "sandbox" in req:
            health = _sandbox_runner_health_url()
            if not _http_ok(health):
                errors.append(
                    f"sandbox-runner 不可达（{health}）。"
                    "确认 sandbox-runner 为 healthy，不要在 eval 容器里查本机 docker 镜像。"
                )
    else:
        # venv: minio/qdrant on localhost usually unpublished — refuse early
        if "minio" in req or "qdrant" in req:
            if "minio" in req and not _tcp_ok("127.0.0.1", 9000):
                errors.append(
                    "venv 目标连不到本机 MinIO:9000。"
                    f"请用 Compose eval：docker compose --profile eval run --rm eval "
                    f"python scripts/eval.py run {suite.id} --profile {profile}"
                )
            if "qdrant" in req and not _tcp_ok("127.0.0.1", 6333):
                errors.append(
                    "venv 目标连不到本机 Qdrant:6333。"
                    f"请用 Compose eval：docker compose --profile eval run --rm eval "
                    f"python scripts/eval.py run {suite.id} --profile {profile}"
                )
        if "sandbox" in req and not _docker_ok():
            errors.append("缺少 omni-sandbox:latest；或改用 --profile smoke（dry-compile）")

    if target not in suite.targets:
        errors.append(
            f"套件 {suite.id} 不支持 target={target}；允许：{', '.join(suite.targets)}"
        )
    return errors


def _cmd_list(_: argparse.Namespace) -> int:
    print(f"{'ID':12} {'KIND':7} {'DEFAULT':8}  TITLE")
    print("-" * 72)
    for s in list_suites():
        print(f"{s.id:12} {s.kind:7} {s.default_target:8}  {s.title}")
        print(f"{'':12} requires={','.join(s.requires) or '-'}  targets={','.join(s.targets)}")
        print(f"{'':12} {s.description}")
        print()
    return 0


def _failure_ids(payload: dict[str, Any]) -> set[str]:
    fails = payload.get("failures") or (payload.get("raw") or {}).get("failures") or []
    out: set[str] = set()
    for row in fails:
        if isinstance(row, dict):
            out.add(str(row.get("id") or row.get("case_id") or ""))
        else:
            out.add(str(row))
    out.discard("")
    return out


def _profile_runs(suite: str, profile: str) -> list[Path]:
    base = reports_root() / suite / "runs"
    if not base.is_dir():
        return []
    runs = [
        p
        for p in base.iterdir()
        if p.is_dir() and (p / "report.json").is_file() and f"_{profile}" in p.name
    ]
    return sorted(runs, key=lambda p: p.name)


def _cmd_diff(args: argparse.Namespace) -> int:
    """Compare the two most recent same-profile runs: newly failed vs newly fixed."""
    suite = args.suite
    profile = args.profile
    runs = _profile_runs(suite, profile)
    if len(runs) < 2:
        print(f"need two {suite}/{profile} runs to diff; found {len(runs)}", file=sys.stderr)
        return 1
    prev, cur = runs[-2], runs[-1]
    prev_rep = json.loads((prev / "report.json").read_text(encoding="utf-8"))
    cur_rep = json.loads((cur / "report.json").read_text(encoding="utf-8"))
    old_ids = _failure_ids(prev_rep)
    new_ids = _failure_ids(cur_rep)
    newly_failed = sorted(new_ids - old_ids)
    newly_fixed = sorted(old_ids - new_ids)
    print(f"diff {suite} profile={profile}")
    print(f"  previous: {prev}")
    print(f"  current:  {cur}")
    print(f"  newly failed ({len(newly_failed)}):")
    for i in newly_failed[:40]:
        print(f"    + {i}")
    print(f"  newly fixed ({len(newly_fixed)}):")
    for i in newly_fixed[:40]:
        print(f"    - {i}")
    return 0


def _cmd_last(args: argparse.Namespace) -> int:
    suite = args.suite
    profile = getattr(args, "profile", None)
    data = read_latest(suite, profile)
    path = latest_path(suite, profile)
    if data is None:
        print(f"no latest report for {suite}: {path}", file=sys.stderr)
        return 1
    meta = data.get("meta") or {}
    print(f"suite={suite}  path={path}")
    print(
        f"profile={meta.get('profile')} target={meta.get('target')} "
        f"exit={meta.get('exit_code')} duration={meta.get('duration_s')}s "
        f"git={meta.get('git_sha')}"
    )
    print("metrics:")
    print(json.dumps(data.get("metrics"), ensure_ascii=False, indent=2)[:3000])
    return int(meta.get("exit_code") or 0)


def _run_pytest(suite: SuiteSpec, profile: str, run_dir: Path, extra: list[str]) -> SuiteResult:
    paths = profile_argv(suite, profile)  # type: ignore[arg-type]
    junit = run_dir / "junit.xml"
    raw_json = run_dir / "report.raw.json"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        *extra,
        "-q",
        "--tb=short",
        f"--junitxml={junit}",
    ]
    # pytest-json-report is optional
    try:
        import pytest_jsonreport  # noqa: F401

        cmd += ["--json-report", f"--json-report-file={raw_json}"]
    except ImportError:
        pass

    env = os.environ.copy()
    env.setdefault("DATABASE_URL", f"sqlite:///{(run_dir / 'pytest.db').as_posix()}")
    env.setdefault("REDIS_ENABLED", "false")
    env.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
    env.setdefault("SANDBOX_RUNNER_URL", "")
    env.setdefault("SECRET_KEY", "test-secret-key")
    env.setdefault("ADMIN_PASSWORD", "test-admin-pass")

    proc = subprocess.run(
        cmd,
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out, end="" if out.endswith("\n") or not out else "\n")
    (run_dir / "stdout.log").write_text(out, encoding="utf-8")

    passed = failed = skipped = errors = 0
    samples: list[dict[str, Any]] = []
    if junit.is_file():
        try:
            root = ET.parse(junit).getroot()
            # suites may nest
            suites = root.findall(".//testsuite") or ([root] if root.tag == "testsuite" else [])
            for ts in suites:
                passed += int(ts.attrib.get("tests", 0)) - int(ts.attrib.get("failures", 0)) - int(
                    ts.attrib.get("errors", 0)
                ) - int(ts.attrib.get("skipped", 0))
                failed += int(ts.attrib.get("failures", 0))
                errors += int(ts.attrib.get("errors", 0))
                skipped += int(ts.attrib.get("skipped", 0))
                for case in ts.findall("testcase"):
                    name = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
                    fail = case.find("failure")
                    err = case.find("error")
                    if fail is not None or err is not None:
                        node = fail if fail is not None else err
                        samples.append(
                            {
                                "id": name,
                                "status": "failed",
                                "message": (node.attrib.get("message") if node is not None else "")[:500],
                            }
                        )
        except ET.ParseError:
            pass

    metrics = {
        "passed": passed,
        "failed": failed + errors,
        "skipped": skipped,
        "exit_code": proc.returncode,
        "command": cmd,
    }
    return SuiteResult(
        report={"metrics": metrics, "failures": samples},
        samples=samples,
        exit_code=proc.returncode,
        summary_lines=[
            f"# pytest · {profile}",
            "",
            f"- passed: {passed}",
            f"- failed: {failed + errors}",
            f"- skipped: {skipped}",
            f"- exit: {proc.returncode}",
        ],
    )


def _run_eval_suite(
    suite: SuiteSpec,
    profile: str,
    run_dir: Path,
    extra: list[str],
    argv_record: list[str],
) -> SuiteResult:
    mod = load_suite_module(suite)
    parser = mod.build_parser()
    profile_args = profile_argv(suite, profile)  # type: ignore[arg-type]
    # Allow CLI extras to override profile defaults: profile first, then extra
    ns = parser.parse_args([*profile_args, *extra])
    # Redirect suite's own --report if present away from legacy paths
    if hasattr(ns, "report") and ns.report is not None:
        ns.report = run_dir / "legacy_compat.json"
    result: SuiteResult = mod.run(ns)
    # Capture stdout already teed; also dump suite text if any
    return result


def pick_target(suite: SuiteSpec, explicit: str | None) -> str:
    """CLI ``--target`` > ``EVAL_TARGET`` (if the suite allows it) > suite default."""
    if explicit:
        return resolve_target(explicit)
    env_target = resolve_target(None, default=suite.default_target)
    # Compose eval sets EVAL_TARGET=services; venv-only suites (pytest) stay venv.
    return env_target if env_target in suite.targets else suite.default_target


def _cmd_run(args: argparse.Namespace) -> int:
    suite = get_suite(args.suite)
    profile = args.profile
    target = pick_target(suite, args.target)
    extra: list[str] = list(args.extra or [])

    blockers = preflight(suite, target, profile, extra)
    if blockers:
        print("PREFLIGHT FAILED:", file=sys.stderr)
        for b in blockers:
            print(f"  - {b}", file=sys.stderr)
        return 2

    run_dir = make_run_dir(suite.id, profile)
    sqlite_path = run_dir / "eval.db"
    bootstrap(target, sqlite_path=sqlite_path, isolate_app_db=suite.isolate_app_db)
    if suite.needs_blob_stub and target == "venv":
        # Import after bootstrap so Settings see SQLite
        stub_blob_store_if_needed()

    started = datetime.now(timezone.utc)
    argv_record = ["scripts/eval.py", "run", suite.id, "--profile", profile, "--target", target, *extra]
    log_path = run_dir / "stdout.log"
    log_f = log_path.open("w", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(old_out, log_f)  # type: ignore[assignment]
    sys.stderr = _Tee(old_err, log_f)  # type: ignore[assignment]

    # Regression yardstick = last *passing* run, so a failing run cannot become the
    # baseline for the next one.
    baseline = read_baseline(suite.id, profile)
    # Snapshot before the suite mutates settings (rag hybrid/rerank toggles etc.).
    pre_settings = settings_snapshot_safe()
    llm_stats_reset()
    try:
        print(f"=== eval {suite.id} profile={profile} target={target} ===")
        print(f"run_dir={run_dir}")
        if suite.kind == "pytest":
            result = _run_pytest(suite, profile, run_dir, extra)
            dataset = None
        else:
            result = _run_eval_suite(suite, profile, run_dir, extra, argv_record)
            dataset = result.dataset_path
            if dataset is None:
                try:
                    mod = load_suite_module(suite)
                    ds = getattr(mod, "DEFAULT_DATASET", None) or getattr(mod, "DATASET", None)
                    if ds is not None:
                        dataset = Path(ds)
                except Exception:
                    pass

        # Unified gates: absolute floors + regression vs previous same-profile run.
        if suite.kind == "eval":
            degraded = _degradation_reason(result.report, profile)
            if degraded:
                print(f"\ngates: FAILED ({degraded})", file=sys.stderr)
                result.report["gates"] = {
                    "ok": False,
                    "failed": [degraded],
                    "checked": [],
                    "skipped": [],
                }
                result.exit_code = max(int(result.exit_code), 1)
            elif int(result.exit_code) == 2:
                # Hard preflight-style failure (e.g. router cache miss): nothing to
                # judge, but the report and the index row are still written.
                print("\ngates: skipped (suite reported a blocking error)")
            else:
                gate = evaluate_gates(suite.id, profile, result.report, baseline)
                result.report["gates"] = gate
                if gate["checked"] or gate["skipped"]:
                    print("\ngates:", "OK" if gate["ok"] else "FAILED")
                    for row in gate["checked"]:
                        print(f"  {row}")
                    for miss in gate["skipped"]:
                        print(f"  (skipped: {miss} not in report)")
                if not gate["ok"]:
                    for msg in gate["failed"]:
                        print(f"GATE FAIL: {msg}", file=sys.stderr)
                    result.exit_code = max(int(result.exit_code), 1)
        result.run_stats = {**llm_stats_snapshot(), **(result.run_stats or {})}

        ctx = RunContext(
            suite=suite.id,
            profile=profile,
            target=target,
            argv=argv_record,
            started_at=started,
            run_dir=run_dir,
            dataset_path=Path(dataset) if dataset else None,
            settings_snapshot=pre_settings,
            key_metrics=list(suite.key_metrics),
        )
        report_path = write_run(ctx, result)
        print(f"\nreport -> {report_path}")
        print(f"latest -> {latest_path(suite.id, profile)}")
        return int(result.exit_code)
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        log_f.close()


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Omni-Butler unified eval runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="List suites")
    sp.set_defaults(func=_cmd_list)

    rp = sub.add_parser("run", help="Run a suite")
    rp.add_argument("suite", choices=sorted(s.id for s in list_suites()))
    rp.add_argument("--profile", choices=("smoke", "full"), default="smoke")
    rp.add_argument("--target", choices=("venv", "services"), default=None)
    rp.set_defaults(func=_cmd_run)

    lp = sub.add_parser("last", help="Show latest report for a suite")
    lp.add_argument("suite", choices=sorted(s.id for s in list_suites()))
    lp.add_argument("--profile", choices=("smoke", "full"), default=None)
    lp.set_defaults(func=_cmd_last)

    dp = sub.add_parser("diff", help="Diff two latest same-profile runs")
    dp.add_argument("suite", choices=sorted(s.id for s in list_suites()))
    dp.add_argument("--profile", choices=("smoke", "full"), default="full")
    dp.set_defaults(func=_cmd_diff)

    return p


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Extras after `--` are forwarded to the suite / pytest (not consumed by this CLI).
    if "--" in raw:
        cut = raw.index("--")
        argv_main, extra = raw[:cut], raw[cut + 1 :]
    else:
        argv_main, extra = raw, []
    parser = build_cli()
    args = parser.parse_args(argv_main)
    args.extra = extra
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
