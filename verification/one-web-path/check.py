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

PASSING = (
    "TypeOK",
    "NoZhipu",
    "NoBoth",
    "DefaultOffers",
    "ExplicitOmits",
    "StopBlocksLaterSend",
    "CalendarStaysUnwritten",
    "CitationNeedsSource",
)

FLAG_ORDER = (
    "breakBoth",
    "breakOmit",
    "breakZhipu",
    "breakStop",
    "breakCalendar",
    "breakCite",
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
    require("web_search" not in build, "_build_request names web_search")
    require("enable_search" not in build, "_build_request names enable_search")
    require('"thinking"' in build, "_build_request lost the thinking switch")
    create = between(llm, "def _create_responses(", "def _yield_for(")
    stopped = create.find("is_stopped()")
    send = create.find("responses.create")
    require(stopped != -1 and send != -1 and stopped < send, "responses create runs before the stop check")

    responses = read("backend/app/core/responses_api.py")
    body = between(responses, "def build_responses_body(", "def replay_input(")
    require('{"type": "web_search"}' in body, "the responses body does not attach web_search")
    require("NATIVE_WEB_MODEL" in body, "an offered search does not select the pro model")
    require('"tool_choice"' not in body, "the responses body sets tool_choice")
    require("enable_search" not in body, "the responses body invents enable_search")
    require('"web_search": True' not in body and '"web_search": true' not in body, "web_search is a guessed boolean")

    web = read("backend/app/agents/tools/web.py")
    execute = between(web, "def execute_web(", "\n\n")
    require("search_planned(" not in execute, "execute_web calls search_planned")
    require("create_event" not in web, "the web tool writes the calendar")

    router = read("backend/app/agents/router.py")
    plan = between(router, "def _decision_to_plan(", "def _apply_availability(")
    require('pending.append("web")' not in plan, "the router still queues the client web plug")

    workflow = read("backend/app/agents/workflow.py")
    require("create_event" not in workflow, "the work loop names create_event")

    verify = read("backend/app/agents/harness/verify.py")
    require('next_tool="web"' not in verify, "verify queues the client web plug again")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAG_ORDER:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "OneWebPath.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def clear_traces() -> None:
    for path in SPEC_DIR.glob("OneWebPath_TTrace_*"):
        path.unlink(missing_ok=True)


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
    parser.add_argument("--break-omit", action="store_true")
    parser.add_argument("--break-zhipu", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-calendar", action="store_true")
    parser.add_argument("--break-cite", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakBoth": args.break_both,
        "breakOmit": args.break_omit,
        "breakZhipu": args.break_zhipu,
        "breakStop": args.break_stop,
        "breakCalendar": args.break_calendar,
        "breakCite": args.break_cite,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    clear_traces()
    anchors()
    expected = {
        "breakBoth": "NoBoth",
        "breakOmit": "DefaultOffers",
        "breakZhipu": "NoZhipu",
        "breakStop": "StopBlocksLaterSend",
        "breakCalendar": "CalendarStaysUnwritten",
        "breakCite": "CitationNeedsSource",
    }
    active = next((name for name, enabled in flags.items() if enabled), None)
    names = ("TypeOK", expected[active]) if active else PASSING
    result = run_tlc(flags, names)
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    clear_traces()
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the passing run failed")


if __name__ == "__main__":
    main()
