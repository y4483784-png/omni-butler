#!/usr/bin/env python3
"""Check stop-before-send, late-chunk voiding, and no memory after stop."""

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
    "NoSendAfterStop",
    "NoLateChunk",
    "NoMemoryWhenStopped",
    "CalendarStays",
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
    llm = read("backend/app/core/llm.py")
    chat = read("backend/app/api/chat.py")
    workflow = read("backend/app/agents/workflow.py")
    execution = read("backend/app/agents/harness/execution.py")
    create = llm[llm.find("def _create_sync(") : llm.find("def _create_async(")]
    stopped_at = create.find("if is_stopped():")
    send_at = create.find("client.chat.completions.create(")
    require(stopped_at != -1 and send_at != -1 and stopped_at < send_at, "_create_sync sends before the stop read")
    require("raise GenerationStopped()" in create, "_create_sync no longer raises GenerationStopped")
    chunk = llm[llm.find("if content:") : llm.find("if content:") + 240]
    require("if is_stopped():" in chunk and "yield AnswerDelta(content)" in chunk, "a late answer chunk is still yielded")
    require(chunk.find("if is_stopped():") < chunk.find("yield AnswerDelta(content)"), "the late chunk is yielded before the stop read")
    require("task_run.stop(" in chat, "chat.py no longer records stop when the client leaves")
    require("if persisted and not is_stopped():" in chat, "a stopped generation can still enter memory")
    remember_at = chat.rfind("_remember_turn_isolated")
    gate_at = chat.find("if persisted and not is_stopped():")
    require(gate_at != -1 and remember_at != -1 and gate_at < remember_at, "memory runs before the stop gate")
    answer = workflow[workflow.find("def answer_node(") : workflow.find("def after_plan(")]
    draft_at = answer.find("draft = complete_text(")
    cutoff_at = answer.find("if is_stopped():", draft_at)
    ground_at = answer.find("ground_and_repair_answer(")
    require(
        draft_at != -1 and cutoff_at != -1 and ground_at != -1 and draft_at < cutoff_at < ground_at,
        "grounding still runs after a stopped answer",
    )
    require("task_run.load_turn(" in execution, "execution_denial no longer loads a fresh TaskRun")
    require('row.status == "stopped"' not in execution or "turn.status == \"stopped\"" in execution, "the stop denial left execution_denial")
    require("delete_event" not in chat, "chat.py deletes a calendar event")


def write_cfg(
    directory: Path,
    *,
    send: bool,
    late: bool,
    memory: bool,
    calendar: bool,
) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  breakSend = {'TRUE' if send else 'FALSE'}",
        f"  breakLate = {'TRUE' if late else 'FALSE'}",
        f"  breakMemory = {'TRUE' if memory else 'FALSE'}",
        f"  breakCalendar = {'TRUE' if calendar else 'FALSE'}",
    ]
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "StopBeforeSend.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(*, send: bool, late: bool, memory: bool, calendar: bool) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="stop-before-send-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, send=send, late=late, memory=memory, calendar=calendar)
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
                "StopBeforeSend.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the stop-before-send model.")
    parser.add_argument("--break-send", action="store_true")
    parser.add_argument("--break-late", action="store_true")
    parser.add_argument("--break-memory", action="store_true")
    parser.add_argument("--break-calendar", action="store_true")
    args = parser.parse_args()
    chosen = sum(
        1
        for flag in (args.break_send, args.break_late, args.break_memory, args.break_calendar)
        if flag
    )
    if chosen > 1:
        fail_model("pass only one break flag")
    anchors()
    result = run_tlc(
        send=args.break_send,
        late=args.break_late,
        memory=args.break_memory,
        calendar=args.break_calendar,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    expected = {
        "break_send": "NoSendAfterStop",
        "break_late": "NoLateChunk",
        "break_memory": "NoMemoryWhenStopped",
        "break_calendar": "CalendarStays",
    }
    active = next((name for name, label in expected.items() if getattr(args, name)), None)
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
