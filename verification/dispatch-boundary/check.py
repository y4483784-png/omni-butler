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
    "DispatchDoesNotInvoke",
    "DispatchDoesNotClaim",
    "DispatchDoesNotWrite",
    "StopBlocksGateway",
)


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def anchors() -> None:
    dispatch = read("backend/app/agents/dispatch.py")
    assign = dispatch[dispatch.find("def assign_turn(") :]
    require(assign.startswith("def assign_turn("), "assign_turn is missing")
    require("plan_tools(" in assign, "assign_turn no longer calls plan_tools")
    require("gateway.invoke" not in assign, "assign_turn calls gateway.invoke")
    require("publish_schedule_claim" not in assign, "assign_turn publishes a schedule claim")
    require("create_event" not in assign, "assign_turn writes a calendar row")

    workflow = read("backend/app/agents/workflow.py")
    require("plan_tools" not in workflow, "the work graph still names plan_tools")
    require("def plan_node(" not in workflow, "plan_node is back inside the work graph")
    start = workflow.find("def run_agent_workflow(")
    entry = workflow[start : workflow.find("def retrieve_node(", start)]
    invoke_at = workflow.find("g.compile().invoke", start)
    stop_at = entry.find("if is_stopped():")
    require(start != -1 and stop_at != -1 and invoke_at != -1, "the work entry lost its stop check")
    require(stop_at < (invoke_at - start), "the graph is compiled before the stop check")
    retrieve = workflow[workflow.find("def retrieve_node(") : workflow.find("def reflect_node(")]
    stopped_retrieve = retrieve.find("if is_stopped():")
    gateway_at = retrieve.find("gateway.invoke(")
    require(
        stopped_retrieve != -1 and gateway_at != -1 and stopped_retrieve < gateway_at,
        "retrieve calls the gateway before it reads stop",
    )

    chat = read("backend/app/api/chat.py")
    sync = chat[chat.find("def _run_workflow_sync(") : chat.find("async def _run_workflow_with_heartbeat(")]
    assign_at = sync.find("assign_turn(")
    run_at = sync.find("run_agent_workflow(")
    require(assign_at != -1 and run_at != -1 and assign_at < run_at, "the worker runs the graph before assign_turn")
    returned = chat.find("wf = await wf_task")
    claim_at = chat.find("publish_schedule_claim(")
    require(
        returned != -1 and claim_at != -1 and returned < claim_at,
        "a schedule claim is published before the work graph returns",
    )


def write_cfg(
    directory: Path,
    *,
    invoke: bool,
    claim: bool,
    calendar: bool,
    stop: bool,
) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  breakInvoke = {'TRUE' if invoke else 'FALSE'}",
        f"  breakClaim = {'TRUE' if claim else 'FALSE'}",
        f"  breakCalendar = {'TRUE' if calendar else 'FALSE'}",
        f"  breakStop = {'TRUE' if stop else 'FALSE'}",
    ]
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "DispatchBoundary.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(*, invoke: bool, claim: bool, calendar: bool, stop: bool) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="dispatch-boundary-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, invoke=invoke, claim=claim, calendar=calendar, stop=stop)
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
                "DispatchBoundary.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the dispatch-boundary model.")
    parser.add_argument("--break-invoke", action="store_true")
    parser.add_argument("--break-claim", action="store_true")
    parser.add_argument("--break-calendar", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    args = parser.parse_args()
    chosen = sum(
        1
        for flag in (args.break_invoke, args.break_claim, args.break_calendar, args.break_stop)
        if flag
    )
    if chosen > 1:
        fail_model("pass only one break flag")
    anchors()
    result = run_tlc(
        invoke=args.break_invoke,
        claim=args.break_claim,
        calendar=args.break_calendar,
        stop=args.break_stop,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    expected = {
        "break_invoke": "DispatchDoesNotInvoke",
        "break_claim": "DispatchDoesNotClaim",
        "break_calendar": "DispatchDoesNotWrite",
        "break_stop": "StopBlocksGateway",
    }
    active = next((name for name, _label in expected.items() if getattr(args, name)), None)
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
