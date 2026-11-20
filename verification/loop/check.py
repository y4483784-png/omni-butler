#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
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


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    fail_model(f"missing {name}")


def _calls(fn: ast.FunctionDef, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def _results_ready_waiting(test: ast.AST) -> bool:
    ready = False
    waiting = False
    for node in ast.walk(test):
        if isinstance(node, ast.Attribute) and node.attr == "RESULTS_READY":
            ready = True
        if isinstance(node, ast.Constant) and node.value == "waiting_person":
            waiting = True
    return ready and waiting


def _waiting_without_token(test: ast.AST) -> bool:
    waiting = False
    missing_token = False
    for node in ast.walk(test):
        if isinstance(node, ast.Constant) and node.value == "waiting_person":
            waiting = True
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Attribute)
            and node.operand.attr == "exec_token"
        ):
            missing_token = True
    return waiting and missing_token


def _effect_due(call: ast.Call) -> bool:
    if len(call.args) < 3:
        return False
    arg = call.args[2]
    if isinstance(arg, ast.Call) and _call_name(arg) == "bool" and arg.args:
        arg = arg.args[0]
    return (
        isinstance(arg, ast.Attribute)
        and arg.attr == "continuation_due"
        and isinstance(arg.value, ast.Name)
        and arg.value.id == "effect"
    )


def require_confirm_assignment(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        fail_model(f"loop_round.py did not parse: {exc}")
    confirm = _function(tree, "confirm_call")
    record = _function(tree, "_record_outcome")
    apply_due = _function(tree, "_apply_confirm_due")
    effect = _function(tree, "job_effect")
    setter = _function(tree, "_set_due")
    require(_calls(confirm, "_record_outcome"), "confirm_call does not record the outcome")
    require(_calls(record, "_apply_confirm_due"), "confirm does not apply continuation_due")
    waiting = [
        node
        for node in ast.walk(apply_due)
        if isinstance(node, ast.If) and _waiting_without_token(node.test)
    ]
    require(waiting, "confirm does not assign continuation_due while waiting")
    require(
        any(_effect_due(call) for node in waiting for call in _calls_in(node.body)),
        "confirm assignment does not store the waiting continuation_due",
    )
    ready = [
        node
        for node in ast.walk(effect)
        if isinstance(node, ast.If) and _results_ready_waiting(node.test)
    ]
    require(ready, "job_effect has no waiting confirm result")
    for node in ready:
        returned = False
        for stmt in node.body:
            if not isinstance(stmt, ast.Return) or not isinstance(stmt.value, ast.Call):
                continue
            if _call_name(stmt.value) != "JobEffect":
                continue
            returned = True
            due = next(
                (keyword.value for keyword in stmt.value.keywords if keyword.arg == "continuation_due"),
                None,
            )
            require(
                isinstance(due, ast.Constant) and due.value is True,
                "confirm assignment is not continuation_due True",
            )
        require(returned, "confirm assignment is not continuation_due True")
    stored = [
        keyword
        for keyword in ast.walk(setter)
        if isinstance(keyword, ast.keyword) and keyword.arg == "continuation_due"
    ]
    require(stored, "_set_due does not assign continuation_due")
    for keyword in stored:
        require(
            isinstance(keyword.value, ast.Name) and keyword.value.id == "value",
            "confirm assignment does not store continuation_due",
        )


def _calls_in(statements: list[ast.stmt]) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in statements:
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _call_name(child) == "_set_due":
                found.append(child)
    return found


def require_save_before_execute(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        fail_model(f"job.py did not parse: {exc}")
    fn = _function(tree, "_drive_held")
    saves = [call.lineno for call in _calls(fn, "save_turn")]
    executes = [call.lineno for call in _calls(fn, "_one_pass")]
    executes.extend(call.lineno for call in _calls(fn, "_invoke"))
    require(saves, "_drive_held does not call save_turn")
    require(executes, "_drive_held does not execute a saved call")
    require(min(saves) < min(executes), "save_turn is not before execute")


def anchors() -> None:
    loop = read("backend/app/services/loop_round.py")
    cards = between(loop, "def open_cards(", "def observe_ready(")
    for banned in ("release_hold", "issue_schedule_claim", "publish_schedule_claim"):
        require(banned not in cards, f"open_cards calls {banned}")
    require_confirm_assignment(loop)
    require_save_before_execute(read("backend/app/tasks/job.py"))

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
