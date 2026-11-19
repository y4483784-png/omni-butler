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
    "SuccessAfterEventId",
    "NoSecondInsert",
    "LookupNotInsert",
    "WrittenWithoutReceiptIsIncomplete",
)

FLAGS = (
    "breakSuccess",
    "breakSecondInsert",
    "breakLookupInsert",
    "breakWrittenComplete",
)

BREAKS = {
    "break_success": ("breakSuccess", "SuccessAfterEventId"),
    "break_second_insert": ("breakSecondInsert", "NoSecondInsert"),
    "break_lookup_insert": ("breakLookupInsert", "LookupNotInsert"),
    "break_written_complete": ("breakWrittenComplete", "WrittenWithoutReceiptIsIncomplete"),
}


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _top_level_body(text: str, name: str) -> str:
    marker = f"def {name}("
    begin = text.find(marker)
    if begin < 0:
        fail_model(f"missing {marker}")
    follow = text.find("\ndef ", begin + 1)
    if follow < 0:
        return text[begin:]
    return text[begin:follow]


def _call(body: str, opener: str) -> str:
    start = body.find(opener)
    if start < 0:
        fail_model(f"missing {opener}")
    depth = 0
    for index in range(start, len(body)):
        char = body[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return body[start:index + 1]
    fail_model(f"unclosed {opener}")


def require_create_receipt() -> None:
    calendar = read("backend/app/api/calendar.py")
    body = _top_level_body(calendar, "record_create_success")
    require("note_external_success(" in body, "record_create_success does not note external success")
    require("body=event_id" in body, "the receipt body is not the stored event id")
    call = _call(body, "note_external_success(")
    require("remote_request_id=armed" in call, "the seal does not pass the armed request id")
    require("external_id" not in call, "the seal passes external_id")
    require("submit_event(" not in body, "record_create_success calls submit_event")


def anchors() -> None:
    require_create_receipt()


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags.get(name) else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "CalendarCreate.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="calendarcreate-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "CalendarCreate.tla", meta / "CalendarCreate.tla")
        cfg = write_cfg(meta, flags, names)
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
                    "CalendarCreate.tla",
                ],
                cwd=meta,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail_model("CalendarCreate TLC timed out")


def judge(result: subprocess.CompletedProcess[str], label: str | None) -> None:
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if label:
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken CalendarCreate model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held CalendarCreate run failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the calendar create receipt model.")
    parser.add_argument("--break-success", action="store_true")
    parser.add_argument("--break-second-insert", action="store_true")
    parser.add_argument("--break-lookup-insert", action="store_true")
    parser.add_argument("--break-written-complete", action="store_true")
    args = parser.parse_args()
    chosen = [name for name in BREAKS if getattr(args, name)]
    if len(chosen) > 1:
        fail_model("pass only one break flag")
    anchors()
    if not chosen:
        judge(run_tlc({}, HELD), None)
        return
    constant, label = BREAKS[chosen[0]]
    judge(run_tlc({constant: True}, ("TypeOK", label)), label)


if __name__ == "__main__":
    main()
