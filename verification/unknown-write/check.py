#!/usr/bin/env python3
"""Check that an unknown calendar write is not sent again, and a stopped tool is not run."""

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

HELD = ("TypeOK", "NoBlindPost", "AtMostOneRemote", "NoStoppedExecute")


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def py_block(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    indent = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"def {name}(") and (len(line) - len(stripped)) in {0, 4}:
            start = index
            indent = len(line) - len(stripped)
            break
    if start is None or indent is None:
        fail_model(f"missing def {name}")
    block: list[str] = []
    seen_header = False
    for line in lines[start:]:
        stripped = line.lstrip()
        current = len(line) - len(stripped) if stripped else indent + 1
        if seen_header and stripped and current <= indent and (
            stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("@")
        ):
            break
        block.append(line)
        if line.rstrip().endswith(":"):
            seen_header = True
    return "".join(block)


def anchors() -> None:
    confirm = read("backend/app/services/schedule_confirm.py")
    google = read("backend/app/services/calendar_google.py")
    gateway = read("backend/app/agents/harness/gateway.py")
    execution = read("backend/app/agents/harness/execution.py")
    effects = read("backend/app/agents/harness/effects.py")
    consume = py_block(confirm, "consume_schedule_claim")
    arm = py_block(confirm, "arm_remote_claim")
    insert = py_block(google, "_insert_google_event")
    invoke = py_block(gateway, "invoke")
    require('raise ScheduleConfirmClosed("unknown")' in consume, "consume no longer refuses an unknown claim")
    require("db.commit()" in arm, "arm_remote_claim no longer commits the request id")
    require('claim.remote_request_id = marker' in arm, "arm_remote_claim no longer stores the request id")
    armed_at = insert.find("_arm_user_write(request_id)")
    sent_at = insert.find('self._call("POST"')
    require(armed_at != -1 and sent_at != -1 and armed_at < sent_at, "the insert is sent before the request id is armed")
    require('return "reconcile"' in effects, "a non-replayable retry is no longer reconcile")
    require('turn.status == "stopped"' in execution, "execution no longer denies a stopped generation")
    require("task_run.load_turn(" in execution, "execution_denial no longer reloads the task row")
    deny_at = invoke.find("execution_denial(")
    run_at = invoke.find("spec.execute(")
    require(deny_at != -1 and run_at != -1 and deny_at < run_at, "invoke executes before the stop check")


def write_cfg(directory: Path, blind: bool, stopped_execute: bool) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  breakBlindRetry = {'TRUE' if blind else 'FALSE'}",
        f"  breakStoppedExecute = {'TRUE' if stopped_execute else 'FALSE'}",
    ]
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    path = directory / "UnknownWrite.cfg"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_tlc(blind: bool, stopped_execute: bool) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="unknown-write-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, blind, stopped_execute)
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
                "UnknownWrite.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the unknown-write model.")
    parser.add_argument("--break-blind", action="store_true")
    parser.add_argument("--break-stopped-execute", action="store_true")
    args = parser.parse_args()
    if args.break_blind and args.break_stopped_execute:
        fail_model("pass only one break flag")
    anchors()
    result = run_tlc(args.break_blind, args.break_stopped_execute)
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if args.break_blind:
        if "NoBlindPost" not in output or "No error has been found" in output:
            fail_model("the blind retry did not violate NoBlindPost")
        raise SystemExit(1)
    if args.break_stopped_execute:
        if "NoStoppedExecute" not in output or "No error has been found" in output:
            fail_model("the stopped execute did not violate NoStoppedExecute")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
