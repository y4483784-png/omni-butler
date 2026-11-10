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
    "StopBlocksInvoke",
    "NoSecondInsert",
    "CalendarStaysUnwritten",
    "LookupFollowsChoice",
    "DispatchStaysQuiet",
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
    stop = text.find(end, at)
    require(stop != -1, f"missing {end}")
    return text[at:stop]


def anchors() -> None:
    workflow = read("backend/app/agents/workflow.py")
    require("create_event" not in workflow, "the work loop names create_event")
    require("save_found_event" not in workflow, "the work loop stores a found calendar row")
    require("call_effect" not in workflow, "the work loop calls call_effect")
    require("plan_tools" not in workflow, "the work graph names plan_tools")

    retrieve = between(workflow, "def retrieve_node(", "def reflect_node(")
    stopped = retrieve.find("if is_stopped():")
    admit = retrieve.find("admit(")
    invoke = retrieve.find("gateway.invoke(")
    require(stopped != -1 and admit != -1 and invoke != -1, "retrieve lost stop, admit, or invoke")
    require(stopped < invoke, "retrieve calls the gateway before it reads stop")
    require(admit < invoke, "retrieve calls the gateway before admit")

    admit_body = between(workflow, "def admit(", "def classify_tool_result(")
    stop_at = admit_body.find("is_stopped()")
    allow_at = admit_body.find("execution_denial(")
    require(stop_at != -1 and allow_at != -1 and stop_at < allow_at, "admit decides before it reads stop and the allow list")

    settle = between(workflow, "def settle_unknown(", "def project_reply(")
    replay = settle.find("decide_replay(")
    early = settle.find('if choice != "reconcile":')
    lookup = settle.find("lookup_inserted_event")
    require(replay != -1 and early != -1 and lookup != -1, "settle_unknown lost the plug")
    require(replay < early < lookup, "lookup runs before decide_replay refuses")

    reflect = between(workflow, "def reflect_node(", "def answer_node(")
    unknown = reflect.find("UnsettledUnknown")
    settle_call = reflect.find("settle_unknown(")
    require(unknown != -1 and settle_call != -1 and unknown < settle_call, "reflect does not settle an unknown result")
    require("gateway.invoke" not in reflect, "reflect calls the gateway")

    dispatch = read("backend/app/agents/dispatch.py")
    assign_at = dispatch.find("def assign_turn(")
    require(assign_at != -1, "assign_turn is missing")
    assign = dispatch[assign_at:]
    require("gateway.invoke" not in assign, "assign_turn calls gateway.invoke")
    require("publish_schedule_claim" not in assign, "assign_turn publishes a schedule claim")
    require("stream_chat" not in assign, "assign_turn writes the reply")
    require("create_event" not in assign, "assign_turn writes a calendar row")

    chat = read("backend/app/api/chat.py")
    branch = chat.find("elif llm_messages:")
    stream = chat.find("stream_chat(", branch)
    require(branch != -1 and stream != -1 and branch < stream, "chat streams when the work result has no messages")


def write_cfg(directory: Path, flags: dict[str, bool]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name, value in flags.items():
        lines.append(f"  {name} = {'TRUE' if value else 'FALSE'}")
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "WorkLoop.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="work-loop-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, flags)
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
                "WorkLoop.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the work-loop model.")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-unknown", action="store_true")
    parser.add_argument("--break-calendar", action="store_true")
    parser.add_argument("--break-dispatch", action="store_true")
    parser.add_argument("--break-lookup", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakStop": args.break_stop,
        "breakUnknown": args.break_unknown,
        "breakCalendar": args.break_calendar,
        "breakDispatch": args.break_dispatch,
        "breakLookup": args.break_lookup,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    result = run_tlc(flags)
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    expected = {
        "breakStop": "StopBlocksInvoke",
        "breakUnknown": "NoSecondInsert",
        "breakCalendar": "CalendarStaysUnwritten",
        "breakDispatch": "DispatchStaysQuiet",
        "breakLookup": "LookupFollowsChoice",
    }
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
