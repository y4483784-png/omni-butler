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
    "SaveBeforeExecute",
    "ResultBeforeNextModel",
    "WriteWaitsForClaim",
    "ForbidWebBlocksSearch",
    "StoppedJobDoesNotExecute",
    "ContinuationDueSurvivesCrash",
)

BREAKS = {
    "break_save": ("breakSave", "SaveBeforeExecute"),
    "break_result": ("breakResult", "ResultBeforeNextModel"),
    "break_write": ("breakWrite", "WriteWaitsForClaim"),
    "break_forbid": ("breakForbid", "ForbidWebBlocksSearch"),
    "break_stop": ("breakStop", "StoppedJobDoesNotExecute"),
    "break_continuation": ("breakContinuation", "ContinuationDueSurvivesCrash"),
}


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


def owner_predicates() -> None:
    import importlib.util

    path = ROOT / "backend" / "app" / "services" / "call_policy.py"
    spec = importlib.util.spec_from_file_location("call_policy_check", path)
    if spec is None or spec.loader is None:
        fail_model("call policy module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parked = "call-parked"
    require(module.acknowledgement_authorizes(False, None, parked) is False, "ordinary message authorizes")
    require(module.acknowledgement_authorizes(False, parked, parked) is False, "id without control authorizes")
    require(module.acknowledgement_authorizes(True, "call-other", parked) is False, "other id authorizes")
    require(module.acknowledgement_authorizes(True, parked, parked) is True, "explicit control does not authorize")
    require(module.recovery_decision(name="web", handed_off=True, exited=False, handed_count=1) == "park", "lease parks")
    require(module.recovery_decision(name="web", handed_off=False, exited=True, handed_count=0) == "replace", "no handoff replaces")
    require(module.recovery_decision(name="web", handed_off=True, exited=True, handed_count=1) == "unknown", "web exit is unknown")
    require(module.recovery_decision(name="kb", handed_off=True, exited=True, handed_count=1) == "replace", "kb may try once")
    require(module.recovery_decision(name="kb", handed_off=True, exited=True, handed_count=2) == "park", "kb stops after one")
    require("default" not in module.worker_queues(consume_loop=False), "default queue is consumed")
    require("default" not in module.worker_queues(consume_loop=True), "enabled loop still consumes default")
    require(module.worker_queues(consume_loop=True)[-1] == "loop", "loop queue missing")


def anchors() -> None:
    loop = read("backend/app/services/loop_round.py")
    cards = between(loop, "def open_cards(", "def observe_ready(")
    require("continuation_due" in cards, "open_cards does not assign continuation_due")
    for banned in ("release_hold", "issue_schedule_claim", "publish_schedule_claim"):
        require(banned not in cards, f"open_cards calls {banned}")

    llm = read("backend/app/core/llm.py")
    stream = between(llm, "async def stream_chat(", "def complete_text(")
    require("_refuse_outbound" in stream, "stream_chat does not reach _refuse_outbound")
    complete = between(llm, "def complete_text(", "def complete_json(")
    require("_refuse_outbound" in complete, "complete_text does not reach _refuse_outbound")


def write_cfg(directory: Path, flags: dict[str, bool]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name, (constant, _label) in BREAKS.items():
        lines.append(f"  {constant} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Loop.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="loop-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "Loop.tla", meta / "Loop.tla")
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
                "Loop.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the tool-round model.")
    parser.add_argument("--break-save", action="store_true")
    parser.add_argument("--break-result", action="store_true")
    parser.add_argument("--break-write", action="store_true")
    parser.add_argument("--break-forbid", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-continuation", action="store_true")
    args = parser.parse_args()
    flags = {name: bool(getattr(args, name)) for name in BREAKS}
    chosen = [name for name, enabled in flags.items() if enabled]
    if len(chosen) > 1:
        fail_model("pass only one break flag")
    owner_predicates()
    anchors()
    try:
        result = run_tlc(flags)
    except subprocess.TimeoutExpired:
        fail_model("TLC timed out")
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if chosen:
        label = BREAKS[chosen[0]][1]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
