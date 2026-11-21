#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from verification.tlc_jar import jar_path

HELD = (
    "TypeOK",
    "CreationAgeDoesNotStop",
    "NullSampleStaysQuiet",
    "OneNoticePerProgressStamp",
    "WaitingLeftAlone",
)

BREAKS = {
    "break_stop": ("breakStop", "CreationAgeDoesNotStop"),
    "break_double": ("breakDouble", "OneNoticePerProgressStamp"),
    "break_wait": ("breakWait", "WaitingLeftAlone"),
    "break_age": ("breakAge", "CreationAgeDoesNotStop"),
}

COVERAGE = {
    "old-null": ("old", "WitnessOldNull"),
    "young-null": ("young", "WitnessYoungNull"),
    "old-sample": ("old", "WitnessOldSample"),
    "young-sample": ("young", "WitnessYoungSample"),
}


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    at = text.find(start)
    require(at != -1, f"missing {start}")
    stop = text.find(end, at + len(start))
    require(stop != -1, f"missing {end}")
    return text[at:stop]


def probe_kind(name: str) -> str:
    if name in HELD:
        return "product"
    if name in {label for _, label in COVERAGE.values()}:
        return "coverage"
    return "unknown"


def anchors() -> None:
    watchdog = read("backend/app/tasks/watchdog.py")
    require('"running": "leave"' in watchdog, "running is not left alone")
    require("created_at" not in watchdog, "the sweep reads created_at")
    require("last_progress_at" in watchdog, "the sweep does not read last_progress_at")
    require("row.last_progress_at =" not in watchdog, "the sweep assigns last_progress_at")
    require("stamp is not None" in watchdog, "a null sample can become a notice")
    require("maintenance_session" in watchdog, "the sweep does not use the maintenance session")
    require("reconcile_task_images" in watchdog, "image cleanup is not on the maintenance beat")
    require("set_rls_context" in watchdog, "the notice write skips the tenant context")
    require("with_for_update" in watchdog, "the notice does not lock the row")
    for banned in (
        "ORDINARY_STALL",
        "SweepLedger",
        "task_run.stop",
        "_suspended_credit",
        "CHECK_INTERVAL",
        "report",
        "publish_schedule_claim",
    ):
        require(banned not in watchdog, f"watchdog.py still contains {banned}")

    beat = read("backend/app/tasks/celery_app.py")
    entry = between(beat, '"watch-task-runs"', "import app.tasks.ingestion")
    require("app.tasks.watchdog.sweep_task" in entry, "the beat entry does not name sweep_task")
    require('"schedule": 60' in entry, "the watch beat is not 60 seconds")

    task_run = read("backend/app/services/task_run.py")
    renew = between(task_run, "def renew_hold(", "def release_hold(")
    require("last_progress_at" not in renew, "renew_hold writes last_progress_at")
    claim = between(task_run, "def claim_web_execution(", "def load_turn(")
    require("touch_progress" in claim, "claim_web_execution does not stamp progress")
    require("last_progress_at=_clock()" not in claim, "claim_web_execution assigns the clock itself")
    require(
        "OUTPUT_COMMIT_GAP = timedelta(seconds=5)" in task_run,
        "the output commit gap is not five seconds",
    )

    spec = read("verification/watch/Watch.tla")
    type_ok = between(spec, "TypeOK ==", "CreationAgeDoesNotStop ==")
    require("createdOld = TRUE" not in type_ok, "TypeOK still forces an old job")
    require("createdOld \\in BOOLEAN" in type_ok, "TypeOK does not admit both ages")
    age = between(spec, "CreationAgeDoesNotStop ==", "NullSampleStaysQuiet ==")
    require("createdOld =>" not in age, "the age invariant is vacuous for a young job")
    require('status \\in {"running"' in age, "the age invariant does not constrain status")


def flag(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def write_cfg(
    directory: Path,
    *,
    break_stop: bool,
    break_double: bool,
    break_wait: bool,
    break_age: bool,
    created_old: bool,
    invariants: tuple[str, ...],
) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  breakStop = {flag(break_stop)}",
        f"  breakDouble = {flag(break_double)}",
        f"  breakWait = {flag(break_wait)}",
        f"  breakAge = {flag(break_age)}",
        f"  createdOld = {flag(created_old)}",
    ]
    for name in invariants:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Watch.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(
    *,
    break_stop: bool,
    break_double: bool,
    break_wait: bool,
    break_age: bool,
    created_old: bool,
    invariants: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="watch-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "Watch.tla", meta / "Watch.tla")
        cfg = write_cfg(
            meta,
            break_stop=break_stop,
            break_double=break_double,
            break_wait=break_wait,
            break_age=break_age,
            created_old=created_old,
            invariants=invariants,
        )
        try:
            return subprocess.run(
                [
                    "java",
                    "-XX:+UseParallelGC",
                    "-cp",
                    str(jar_path()),
                    "tlc2.TLC",
                    "-workers",
                    "1",
                    "-nowarning",
                    "-config",
                    str(cfg),
                    "-metadir",
                    str(meta),
                    "Watch.tla",
                ],
                cwd=meta,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail_model("TLC timed out")


def judge(result: subprocess.CompletedProcess[str], label: str | None) -> None:
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if label is not None:
        violation = f"Invariant {label} is violated."
        if (
            result.returncode != 12
            or violation not in output
            or "No error has been found" in output
            or "TLC threw an unexpected exception" in output
        ):
            fail_model(
                f"expected TLC exit 12 violating {label}, TLC exited {result.returncode}"
            )
        print(f"TLC exit 12 violates {label}.")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model(f"the held run failed with TLC exit {result.returncode}")
    print("TLC exit 0, no error.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the watch model.")
    parser.add_argument("--age", choices=("old", "young"))
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-double", action="store_true")
    parser.add_argument("--break-wait", action="store_true")
    parser.add_argument("--break-age", action="store_true")
    parser.add_argument("--witness", choices=tuple(COVERAGE))
    args = parser.parse_args()
    chosen = [name for name in BREAKS if getattr(args, name)]
    if len(chosen) > 1 or (chosen and args.witness):
        fail_model("pass only one break flag")
    anchors()
    if args.witness:
        witness_age, label = COVERAGE[args.witness]
        if args.age is not None and args.age != witness_age:
            fail_model("the witness age does not match --age")
        if probe_kind(label) != "coverage":
            fail_model(f"{label} is not a coverage witness")
        result = run_tlc(
            break_stop=False,
            break_double=False,
            break_wait=False,
            break_age=False,
            created_old=witness_age == "old",
            invariants=HELD + (label,),
        )
        judge(result, label)
        return
    created_old = (args.age or "old") == "old"
    break_stop = args.break_stop
    break_double = args.break_double
    break_wait = args.break_wait
    break_age = args.break_age
    label = BREAKS[chosen[0]][1] if chosen else None
    if break_age and not created_old:
        label = None
    result = run_tlc(
        break_stop=break_stop,
        break_double=break_double,
        break_wait=break_wait,
        break_age=break_age,
        created_old=created_old,
        invariants=HELD,
    )
    judge(result, label)


if __name__ == "__main__":
    main()
