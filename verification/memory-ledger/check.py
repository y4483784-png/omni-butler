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
    "NotCompletedWritesNothing",
    "LowConfidenceNoCard",
    "OneClaim",
    "PersonNotUndone",
)

FLAGS = ("breakStop", "breakQuiet", "breakClaim", "breakErase")


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
    chat = read("backend/app/api/chat.py")
    gate_at = chat.find("if persisted and not is_stopped():")
    remember_at = chat.rfind("_remember_turn_isolated")
    require(gate_at != -1 and remember_at != -1 and gate_at < remember_at, "memory runs before the stop gate")
    gate = chat[gate_at:remember_at]
    completed_at = gate.find('if finished.status == "completed":')
    require(completed_at != -1, "a generation that is not completed can still be remembered")

    memory = read("backend/app/services/memory.py")
    decide = between(memory, "def decide_apply(", "def extract_for_ledger(")
    require("if low_confidence:" in decide, "low confidence can become the live profile")
    require('incoming == "turn" and latest_applied_actor == "person"' in decide, "a turn can replace a person's value")
    remember = between(memory, "def remember_from_turn(", "def serialize_memory(")
    guard_at = remember.find('if generation is not None and status != "completed":')
    extract_at = remember.find("extract_for_ledger(")
    require(guard_at != -1 and extract_at != -1 and guard_at < extract_at, "a stopped turn still calls the model")
    require("publish_schedule_claim" not in memory, "a memory write publishes a confirm card")

    pulse = read("backend/app/tasks/pulse.py")
    push = between(pulse, "def push_pulse(", "def run_pulse(")
    require("publish_schedule_claim(" in push, "the pulse does not publish a claim")
    require(
        "if any(_token(row) == open_claim.token for row in messages):" in push,
        "a second pulse does not stop when the token is already on the message",
    )
    require('return Suppressed(fingerprint)' in push, "the pulse has no suppressed result")
    require("CalendarEvent" not in pulse, "the pulse inserts a calendar event")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "MemoryLedger.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="memory-ledger-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "MemoryLedger.tla", meta / "MemoryLedger.tla")
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
                "MemoryLedger.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the memory ledger model.")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-quiet", action="store_true")
    parser.add_argument("--break-claim", action="store_true")
    parser.add_argument("--break-erase", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakStop": args.break_stop,
        "breakQuiet": args.break_quiet,
        "breakClaim": args.break_claim,
        "breakErase": args.break_erase,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakStop": "NotCompletedWritesNothing",
        "breakQuiet": "LowConfidenceNoCard",
        "breakClaim": "OneClaim",
        "breakErase": "PersonNotUndone",
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
