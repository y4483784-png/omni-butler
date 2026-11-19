#!/usr/bin/env python3
"""Check that a new turn, a disconnect, and a cancel leave the right job running."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = Path(__file__).resolve().parent
WRITER = ROOT / "backend" / "app" / "services" / "task_run.py"
CHAT = ROOT / "backend" / "app" / "api" / "chat.py"
sys.path.insert(0, str(ROOT))
from verification.tlc_jar import jar_path

HELD = (
    "JobSurvivesNewTurn",
    "JobSurvivesDisconnect",
    "CancelStopsNamedJob",
    "AmbiguousCancelAsks",
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
    require('or row.kind == "job"' in writer, "begin_turn no longer skips a job")
    require("def continue_job(" in writer, "task_run.py no longer has continue_job")
    require("def apply_intent(" in writer, "task_run.py no longer has apply_intent")
    require("generation=turn.generation" in chat, "chat.py no longer stops the interaction generation")
    require(
        "generation=relation.target_generation" in chat,
        "chat.py no longer stops the named job",
    )


def write_cfg(directory: Path) -> Path:
    lines = ["SPECIFICATION Spec"]
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Talk.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc() -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH. Install a JDK 21 and rerun.")
    with tempfile.TemporaryDirectory(prefix="talk-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta)
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
                "Talk.tla",
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
    anchors()
    try:
        result = run_tlc()
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
    if broken:
        print(output)
        print("violated: " + ", ".join(broken))
        raise SystemExit(1)
    print("Talk holds " + ", ".join(HELD) + ".")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
