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
    "WaitStays",
    "CompletedStays",
    "SleepNotSilence",
    "NewerStays",
    "OtherStays",
    "NoCard",
)

FLAGS = ("breakWait", "breakDone", "breakSleep", "breakNewer", "breakOther", "breakCard")


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    at = text.find(start)
    require(at != -1, f"missing {start}")
    stop = text.find(end, at + len(start))
    require(stop != -1, f"missing {end}")
    return text[at:stop]


def anchors() -> None:
    watchdog = read("backend/app/tasks/watchdog.py")
    effect = between(watchdog, "_EFFECT: dict[str, str] = {", "def _validate_effect")
    require('"running": "stop"' in effect, "running is not the only timed status")
    require('"waiting_person": "leave"' in effect, "waiting on a person is a stall")
    require('"waiting_resource": "leave"' in effect, "waiting on a resource is a stall")
    require('"completed": "leave"' in effect, "completed is a stall")
    require('"stopped": "leave"' in effect, "a stopped generation is restarted")
    require("ORDINARY_STALL = timedelta(minutes=20)" in watchdog, "the ordinary stall is not 20 minutes")
    require("CHECK_INTERVAL = timedelta(seconds=60)" in watchdog, "the check interval is not 60 seconds")

    gap = between(watchdog, "def _sleep_gap", "def _silence")
    require("if gap > 3 * CHECK_INTERVAL:" in gap, "a short tick gap is treated as sleep")
    require("return gap" in gap, "sleep credit is not the whole gap")

    decide = between(watchdog, "def decide", "def load_runs")
    require(
        "ledger.credit.get(run.key, timedelta(0)) + added" in decide,
        "sleep credit does not stay on the ledger",
    )

    sweep = between(watchdog, "def run_sweep", "_ledger = SweepLedger")
    fresh = sweep.find('_EFFECT[fresh.status] != "stop"')
    stop = sweep.find("task_run.stop(user_id=key.user_id, generation=key.generation)")
    require(fresh != -1 and stop != -1 and fresh < stop, "stop runs before the fresh status check")
    require("maintenance_session()" in between(watchdog, "def load_runs", "def run_sweep"), "the sweep reads through a tenant session")
    require("publish_schedule_claim" not in watchdog, "the watchdog publishes a confirm card")
    require("CalendarEvent" not in watchdog, "the watchdog inserts a calendar row")

    beat = between(read("backend/app/tasks/celery_app.py"), "beat_schedule={", "import app.tasks.ingestion")
    require('"recover-stale-document-ingests"' in beat, "ingest recovery left the beat schedule")
    require('"memory-schedule-pulse"' in beat, "the memory pulse left the beat schedule")
    require('"watch-task-runs"' in beat, "the watchdog beat entry is missing")
    require('"app.tasks.watchdog.sweep_task"' in beat, "the beat entry does not name sweep_task")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "TaskWatch.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="task-watch-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "TaskWatch.tla", meta / "TaskWatch.tla")
        cfg = write_cfg(meta, flags, names)
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
                "TaskWatch.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the task watchdog model.")
    parser.add_argument("--break-wait", action="store_true")
    parser.add_argument("--break-done", action="store_true")
    parser.add_argument("--break-sleep", action="store_true")
    parser.add_argument("--break-newer", action="store_true")
    parser.add_argument("--break-other", action="store_true")
    parser.add_argument("--break-card", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakWait": args.break_wait,
        "breakDone": args.break_done,
        "breakSleep": args.break_sleep,
        "breakNewer": args.break_newer,
        "breakOther": args.break_other,
        "breakCard": args.break_card,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakWait": "WaitStays",
        "breakDone": "CompletedStays",
        "breakSleep": "SleepNotSilence",
        "breakNewer": "NewerStays",
        "breakOther": "OtherStays",
        "breakCard": "NoCard",
    }
    active = next((name for name, enabled in flags.items() if enabled), None)
    names = ("TypeOK", expected[active]) if active else HELD
    result = run_tlc(flags, names)
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
