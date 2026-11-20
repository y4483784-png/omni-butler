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
    "SameActionOnce",
    "ContinueSameJob",
    "WebStillDenied",
)

FLAGS = (
    "breakAgain",
    "breakPath",
    "breakUnknown",
    "breakOther",
    "breakProbe",
    "breakStopped",
    "breakWeb",
)


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        fail_model(f"missing {start}")
    finish = text.find(end, begin + len(start))
    if finish < 0:
        fail_model(f"missing {end}")
    return text[begin:finish]


def anchors() -> None:
    parts = between(
        read("backend/app/services/schedule_confirm.py"),
        'if action in {"desktop.open", "desktop.run"}:',
        'if action == "errand.place":',
    )
    require("device_id" in parts, "write_parts omits device_id for a desktop action")

    perform = between(read("desktop/executor.py"), "def perform(", "def open_with_xdg")
    kind_at = perform.find('license.get("kind") != "armed"')
    device_at = perform.find('license.get("device_id") != enrolled_device_id')
    require(kind_at >= 0 and device_at > kind_at, "perform does not guard the armed kind and the device")

    finish = between(
        read("backend/app/services/local_action.py"),
        "def finish_local(",
        "def _args(",
    )
    require("continue_job(" in finish, "finish_local does not continue the job")

    require(
        "local_tool_denial" in read("backend/app/agents/harness/gateway.py"),
        "the gateway dropped the local tool denial",
    )
    require(
        "perform(" not in read("frontend/src/components/ChatWindow.tsx"),
        "the web page calls perform",
    )


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Desktop.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="desktop-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "Desktop.tla", meta / "Desktop.tla")
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
                "Desktop.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the desktop confirm model.")
    parser.add_argument("--break-again", action="store_true")
    parser.add_argument("--break-path", action="store_true")
    parser.add_argument("--break-unknown", action="store_true")
    parser.add_argument("--break-other", action="store_true")
    parser.add_argument("--break-probe", action="store_true")
    parser.add_argument("--break-stopped", action="store_true")
    parser.add_argument("--break-web", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakAgain": args.break_again,
        "breakPath": args.break_path,
        "breakUnknown": args.break_unknown,
        "breakOther": args.break_other,
        "breakProbe": args.break_probe,
        "breakStopped": args.break_stopped,
        "breakWeb": args.break_web,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakAgain": "SameActionOnce",
        "breakPath": "SameActionOnce",
        "breakUnknown": "SameActionOnce",
        "breakOther": "ContinueSameJob",
        "breakProbe": "ContinueSameJob",
        "breakStopped": "ContinueSameJob",
        "breakWeb": "WebStillDenied",
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
