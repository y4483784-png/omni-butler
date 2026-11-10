#!/usr/bin/env python3
"""Check the TaskRun move table against the five-state rules."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = Path(__file__).resolve().parent
WRITER = ROOT / "backend" / "app" / "services" / "task_run.py"
CHAT = ROOT / "backend" / "app" / "api" / "chat.py"

TLA_VERSION = "1.8.0"
JAR_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar"
# The v1.8.0 release URL served this asset on 2026-11-10.
# Implementation-Version 2.0, revision d8fb231, built 2026-11-06.
JAR_SHA256 = "e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f"
JAR_CACHE = SPEC_DIR / ".tools" / "tla2tools.jar"

HELD = (
    "TypeOK",
    "CompletedStays",
    "StoppedStays",
    "NotRunningAgain",
    "WaitingNeverCompletes",
)

ANCHORS = (
    '"finish": ("write", "completed")',
    '"stop": ("write", "stopped")',
    '"finish": ("keep", None)',
    '"stop": ("keep", None)',
    '"finish": ("reject", None)',
)


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def anchors() -> None:
    writer = WRITER.read_text(encoding="utf-8")
    chat = CHAT.read_text(encoding="utf-8")
    for needle in ANCHORS:
        require(needle in writer, f"task_run.py no longer contains {needle}")
    require("task_run.begin_turn" in chat, "chat.py no longer starts a turn")
    require("task_run.wait_for_person" in chat, "chat.py no longer waits on a confirm card")
    require("task_run.finish" in chat, "chat.py no longer finishes a turn")
    require("task_run.stop" not in chat, "chat.py calls stop. This knife leaves that to tests.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def jar_path() -> Path:
    if JAR_CACHE.is_file() and sha256(JAR_CACHE) == JAR_SHA256:
        return JAR_CACHE
    JAR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JAR_CACHE.with_suffix(".jar.partial")
    urllib.request.urlretrieve(JAR_URL, tmp)
    if sha256(tmp) != JAR_SHA256:
        tmp.unlink(missing_ok=True)
        fail_model(f"tla2tools.jar sha256 does not match the {TLA_VERSION} pin")
    tmp.replace(JAR_CACHE)
    return JAR_CACHE


def write_cfg(directory: Path, break_completed_stop: bool) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  breakCompletedStop = {'TRUE' if break_completed_stop else 'FALSE'}",
    ]
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "TaskRun.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(break_completed_stop: bool) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH. Install a JDK 21 and rerun.")
    with tempfile.TemporaryDirectory(prefix="task-run-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, break_completed_stop)
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
                "TaskRun.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def violated(output: str) -> list[str]:
    return re.findall(r"Invariant (\w+) is violated", output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--break-completed-stop",
        action="store_true",
        help="stop rewrites a completed turn, which must violate CompletedStays",
    )
    args = parser.parse_args()
    anchors()
    try:
        result = run_tlc(args.break_completed_stop)
    except subprocess.TimeoutExpired:
        fail_model("TLC timed out")
    output = f"{result.stdout}\n{result.stderr}"
    if "Deadlock reached" in output:
        print(output)
        fail_model("TLC deadlocked.")
    broken = violated(output)
    if result.returncode != 0 and not broken and "No error has been found" not in output:
        print(output)
        fail_model(f"TLC exited {result.returncode} without a property violation.")
    if args.break_completed_stop:
        if broken != ["CompletedStays"] and "CompletedStays" not in broken:
            print(output)
            fail_model(f"the break did not violate CompletedStays. Saw {broken}.")
        print("CompletedStays is violated when stop rewrites a finished turn.")
        raise SystemExit(1)
    if broken:
        print(output)
        print("violated: " + ", ".join(broken))
        raise SystemExit(1)
    print("TaskRun holds " + ", ".join(HELD) + ".")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
