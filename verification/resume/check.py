#!/usr/bin/env python3

from __future__ import annotations

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
    "DisconnectDoesNotStopJob",
    "ReservationThenAdopt",
    "PublishCrashRecovers",
    "LiveHolderNotReclaimed",
    "CallsSavedBeforeModel",
    "ArmedWriteNotSent",
    "StaleEpochDropped",
    "CommitOrderedCursor",
)


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


def anchors() -> None:
    resume = read("backend/app/services/resume.py")
    require("AND exec_token IS NULL" in resume, "reserve no longer requires a null exec token")
    require("SET exec_token = :worker" in resume, "adopt no longer sets exec_token")
    require("continuation_due = false" in resume, "adopt no longer clears continuation_due")
    require("dispatch_token = :token" in resume, "reserve no longer stores a dispatch token")
    sessions = read("backend/app/api/sessions.py")
    events = between(sessions, "def get_events(", "def rename(")
    require("events_after" in events, "events route does not read by seq")
    require("task_run.stop" not in events, "events route stops a job")
    jobs = between(sessions, "def get_jobs(", "def get_events(")
    require("read_snapshot" in jobs, "jobs route does not take the cursor lock")
    snapshot = between(resume, "def read_snapshot(", "def events_after(")
    require('"cursor": published_seq' in snapshot, "jobs cursor is not published_seq")
    require("max(" not in snapshot, "jobs cursor uses a max id")


FLAGS = {
    "--stop-on-disconnect": ("allowDisconnectStop", "DisconnectDoesNotStopJob"),
    "--hold-without-reserve": ("allowHoldEarly", "ReservationThenAdopt"),
    "--publish-during-crash": ("allowPublishCrash", "PublishCrashRecovers"),
    "--reclaim-live": ("allowReclaim", "LiveHolderNotReclaimed"),
    "--model-before-calls": ("allowEarlyModel", "CallsSavedBeforeModel"),
    "--allow-resend": ("allowResend", "ArmedWriteNotSent"),
    "--allow-stale": ("allowStale", "StaleEpochDropped"),
    "--allow-dirty": ("allowDirty", "CommitOrderedCursor"),
    "--drop-recover": ("allowRecover", "LiveRecovery"),
}

CONSTANTS = (
    "allowDisconnectStop",
    "allowHoldEarly",
    "allowPublishCrash",
    "allowReclaim",
    "allowEarlyModel",
    "allowResend",
    "allowStale",
    "allowDirty",
    "allowRecover",
)


def write_cfg(directory: Path, broken: str | None) -> Path:
    values = {name: "FALSE" for name in CONSTANTS}
    values["allowRecover"] = "TRUE"
    if broken is not None:
        name, _expected = FLAGS[broken]
        values[name] = "FALSE" if name == "allowRecover" else "TRUE"
    lines = ["CONSTANTS"]
    for name in CONSTANTS:
        lines.append(f"{name} = {values[name]}")
    lines.append("SPECIFICATION Spec")
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("PROPERTY LiveRecovery")
    lines.append("")
    path = directory / "Resume.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(broken: str | None) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="resume-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "Resume.tla", meta / "Resume.tla")
        cfg = write_cfg(meta, broken)
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
                "Resume.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )


def violated_names(output: str) -> list[str]:
    import re

    found = re.findall(r"Invariant (\w+) is violated", output)
    if re.search(r"Temporal property LiveRecovery was violated", output):
        found.append("LiveRecovery")
    return found


def main() -> None:
    chosen = [arg for arg in sys.argv[1:] if arg in FLAGS]
    if len(chosen) > 1:
        fail_model("pass one break flag")
    broken = chosen[0] if chosen else None
    anchors()
    try:
        result = run_tlc(broken)
    except subprocess.TimeoutExpired:
        fail_model("TLC timed out")
    output = f"{result.stdout}\n{result.stderr}"
    if "Deadlock reached" in output:
        print(output)
        fail_model("TLC deadlocked.")
    names = violated_names(output)
    if broken is None:
        if result.returncode != 0 and not names and "No error has been found" not in output:
            print(output)
            fail_model(f"TLC exited {result.returncode} without a property violation.")
        if names:
            print(output)
            print("violated: " + ", ".join(names))
            raise SystemExit(1)
        print("Resume holds " + ", ".join(HELD) + " and LiveRecovery.")
        raise SystemExit(0)
    expected = FLAGS[broken][1]
    if expected not in names:
        print(output)
        fail_model(f"{broken} did not violate {expected}.")
    print(f"{broken} violates {expected}.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
