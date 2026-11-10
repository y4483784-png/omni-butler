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
    "NoBoth",
    "NoGuessedField",
    "StopBlocksLaterSend",
    "CalendarStaysUnwritten",
)

CUTOVER = (
    "TypeOK",
    "FieldWhenAllowed",
    "AbsentSkipsZhipu",
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
    at = text.find(start)
    require(at != -1, f"missing {start}")
    stop = text.find(end, at + len(start))
    require(stop != -1, f"missing {end}")
    return text[at:stop]


def anchors() -> None:
    llm = read("backend/app/core/llm.py")
    build = between(llm, "def _build_request(", "def answer_pair(")
    require('"thinking"' in build, "_build_request lost the thinking switch")
    require("web_search" not in build, "_build_request names web_search")
    require("上网" not in build, "_build_request names the allow-list token")
    create = between(llm, "def _create_sync(", "def _create_async(")
    stopped = create.find("is_stopped()")
    send = create.find("chat.completions.create")
    require(stopped != -1 and send != -1 and stopped < send, "create runs before the stop check")

    web = read("backend/app/agents/tools/web.py")
    execute = between(web, "def execute_web(", "\n\n")
    require("search_planned(" in execute, "execute_web does not call search_planned")
    require("create_event" not in web, "the web tool writes the calendar")

    router = read("backend/app/agents/router.py")
    plan = between(router, "def _decision_to_plan(", "def _apply_availability(")
    require('pending.append("web")' in plan, "the router no longer queues web")
    require("上网" not in router, "the router reads the allow-list token")

    search = read("backend/app/services/web_search.py")
    require("上网" not in search, "web search reads the allow-list token")
    url = between(search, "def _web_search_url(", "def ")
    require("web_search" in url, "_web_search_url lost its path")

    workflow = read("backend/app/agents/workflow.py")
    require("create_event" not in workflow, "the work loop names create_event")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name, value in flags.items():
        lines.append(f"  {name} = {'TRUE' if value else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "OneWebPath.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="one-web-path-tlc-") as tmp:
        meta = Path(tmp)
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
                "OneWebPath.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the one-web-path model.")
    parser.add_argument("--break-both", action="store_true")
    parser.add_argument("--break-field", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-calendar", action="store_true")
    parser.add_argument("--cutover", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakBoth": args.break_both,
        "breakField": args.break_field,
        "breakStop": args.break_stop,
        "breakCalendar": args.break_calendar,
    }
    if sum(1 for value in flags.values() if value) > 1 or (args.cutover and any(flags.values())):
        fail_model("pass only one break flag")
    anchors()
    names = CUTOVER if args.cutover else HELD
    if args.break_both:
        names = ("TypeOK", "NoBoth", "StopBlocksLaterSend", "CalendarStaysUnwritten")
    result = run_tlc(flags, names)
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    expected = {
        "breakBoth": "NoBoth",
        "breakField": "NoGuessedField",
        "breakStop": "StopBlocksLaterSend",
        "breakCalendar": "CalendarStaysUnwritten",
    }
    if args.cutover:
        if "FieldWhenAllowed" not in output and "AbsentSkipsZhipu" not in output:
            fail_model("the cutover invariants did not fail")
        if "No error has been found" in output:
            fail_model("the cutover invariants held")
        raise SystemExit(1)
    active = next((name for name, enabled in flags.items() if enabled), None)
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
