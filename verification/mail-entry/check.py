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
from verification.calendar.check import require_create_receipt
from verification.tlc_jar import jar_path

ENTRY_HELD = (
    "TypeOK",
    "OneReceipt",
    "BareWritesNothing",
    "SuccessAfterAccept",
    "StopKeepsAccepted",
    "LinkIsNotSend",
)

SEND_HELD = (
    "TypeOK",
    "NoAcceptWithoutProvider",
    "OneSendPerClaim",
    "UnknownDoesNotResend",
)

ENTRY_FLAGS = (
    "breakDedupe",
    "breakBare",
    "breakSuccess",
    "breakStop",
    "breakLink",
)

SEND_FLAGS = (
    "breakNoProvider",
    "breakTwoSends",
    "breakResend",
)

ENTRY_BREAKS = {
    "break_dedupe": ("breakDedupe", "OneReceipt"),
    "break_bare": ("breakBare", "BareWritesNothing"),
    "break_success": ("breakSuccess", "SuccessAfterAccept"),
    "break_stop": ("breakStop", "StopKeepsAccepted"),
    "break_link": ("breakLink", "LinkIsNotSend"),
}

SEND_BREAKS = {
    "break_no_provider": ("breakNoProvider", "NoAcceptWithoutProvider"),
    "break_two_sends": ("breakTwoSends", "OneSendPerClaim"),
    "break_resend": ("breakResend", "UnknownDoesNotResend"),
}


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


def _contains(node: ast.AST, target: ast.AST) -> bool:
    return any(child is target for child in ast.walk(node))


def _writes(nodes: list[ast.stmt]) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _call_name(child) == "_write_acceptance":
                found.append(child)
    return found


def _exits(statements: list[ast.stmt]) -> bool:
    for stmt in statements:
        if isinstance(stmt, (ast.Raise, ast.Return)):
            return True
        if isinstance(stmt, ast.If):
            return _exits(stmt.body) and _exits(stmt.orelse)
    return False


def _is_not_provider(test: ast.AST) -> bool:
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Name)
        and test.operand.id == "provider"
    )


def _is_provider(test: ast.AST) -> bool:
    return isinstance(test, ast.Name) and test.id == "provider"


def _stored_provider(value: ast.AST) -> bool:
    if not isinstance(value, ast.IfExp):
        return False
    if not isinstance(value.body, ast.Name) or value.body.id != "remote":
        return False
    if not isinstance(value.orelse, ast.Constant) or value.orelse.value != "":
        return False
    test = value.test
    if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.And):
        return False
    has_remote = False
    has_attempt = False
    for item in test.values:
        if isinstance(item, ast.Name) and item.id == "remote":
            has_remote = True
        if (
            isinstance(item, ast.UnaryOp)
            and isinstance(item.op, ast.Not)
            and isinstance(item.operand, ast.Call)
            and isinstance(item.operand.func, ast.Attribute)
            and item.operand.func.attr == "startswith"
            and item.operand.args
            and isinstance(item.operand.args[0], ast.Name)
            and item.operand.args[0].id == "_ATTEMPT_PREFIX"
        ):
            has_attempt = True
    return has_remote and has_attempt


def _provider_assignments(fn: ast.FunctionDef) -> list[ast.Assign]:
    found: list[ast.Assign] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "provider" for target in node.targets):
            found.append(node)
    return found


def _provider_id(call: ast.Call) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == "provider_id":
            return keyword.value
    return None


def _empty_provider_reaches(statements: list[ast.stmt], call: ast.Call, blocked: bool = False) -> bool:
    for stmt in statements:
        if blocked:
            return False
        if _contains(stmt, call):
            if isinstance(stmt, ast.If) and _is_provider(stmt.test):
                in_body = any(_contains(child, call) for child in stmt.body)
                in_else = any(_contains(child, call) for child in stmt.orelse)
                if in_body and not in_else:
                    return False
            if isinstance(stmt, ast.If) and _is_not_provider(stmt.test):
                in_body = any(_contains(child, call) for child in stmt.body)
                if in_body:
                    return True
            if isinstance(stmt, ast.If):
                if any(_contains(child, call) for child in stmt.body):
                    return _empty_provider_reaches(stmt.body, call, blocked)
                return _empty_provider_reaches(stmt.orelse, call, blocked)
            return True
        if isinstance(stmt, ast.If) and _is_not_provider(stmt.test) and _exits(stmt.body):
            blocked = True
    return False


def _is_transport_none(test: ast.AST) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "transport"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _lookup_accepted(test: ast.AST, op: type[ast.cmpop]) -> bool:
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], op)
        and isinstance(test.left, ast.Call)
        and _call_name(test.left) == "_lookup_result"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "accepted"
    )


def _unsafe_bound_writes(statements: list[ast.stmt]) -> list[ast.Call]:
    unsafe: list[ast.Call] = []
    guarded = False
    for stmt in statements:
        if guarded:
            return unsafe
        if isinstance(stmt, ast.If) and _lookup_accepted(stmt.test, ast.NotEq):
            unsafe.extend(_writes(stmt.body))
            if _exits(stmt.body) and not _writes(stmt.body):
                guarded = True
            else:
                unsafe.extend(_unsafe_bound_writes(stmt.orelse))
            continue
        if isinstance(stmt, ast.If) and _lookup_accepted(stmt.test, ast.Eq):
            unsafe.extend(_unsafe_bound_writes(stmt.orelse))
            continue
        if isinstance(stmt, ast.If):
            unsafe.extend(_unsafe_bound_writes(stmt.body))
            unsafe.extend(_unsafe_bound_writes(stmt.orelse))
            continue
        unsafe.extend(_writes([stmt]))
    return unsafe


def _transport_none(fn: ast.FunctionDef) -> ast.If:
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and _is_transport_none(node.test):
            return node
    fail_model("reconcile has no no-transport branch")


def _tail_after(fn: ast.FunctionDef, target: ast.If) -> list[ast.stmt]:
    def search(statements: list[ast.stmt]) -> list[ast.stmt] | None:
        for index, stmt in enumerate(statements):
            if stmt is target:
                return statements[index + 1 :]
            for attr in ("body", "orelse"):
                inner = getattr(stmt, attr, None)
                if isinstance(inner, list):
                    found = search(inner)
                    if found is not None:
                        return found
        return None

    found = search(fn.body)
    if found is None:
        fail_model("reconcile has no no-transport branch")
    return found


def require_reconcile(mail: str) -> None:
    try:
        tree = ast.parse(mail)
    except SyntaxError as exc:
        fail_model(f"mail_entry.py did not parse: {exc}")
    fn = _function(tree, "_reconcile_send")
    none = _transport_none(fn)
    tail = _tail_after(fn, none)
    writes = _writes(none.body)
    if not _exits(none.body):
        writes.extend(_writes(tail))
    for call in writes:
        provider = _provider_id(call)
        stored = _provider_assignments(fn)
        require(
            isinstance(provider, ast.Name)
            and provider.id == "provider"
            and bool(stored)
            and all(_stored_provider(item.value) for item in stored)
            and not _empty_provider_reaches(fn.body, call),
            "no-transport branch repairs without a stored provider id",
        )
    unsafe = _unsafe_bound_writes(tail)
    unsafe.extend(_unsafe_bound_writes(none.orelse))
    require(
        not unsafe,
        "bound-transport branch writes an acceptance when lookup is not accepted",
    )


def anchors() -> None:
    mail = read("backend/app/services/mail_entry.py")
    commit = between(mail, "def commit_send(", "def _active_mail_claim(")
    empty_at = commit.find('if token is None or str(token).strip() == ""')
    accept_at = commit.find("_accept(")
    seal_at = commit.find("_seal(")
    require(empty_at >= 0, "commit without a token is not refused")
    require(
        empty_at < accept_at < seal_at,
        "success is marked before the token check or the accept row",
    )
    selected_at = commit.find("_selected_transport(")
    consume_at = commit.find("consume_schedule_claim(")
    require(
        empty_at < selected_at < consume_at,
        "the transport check is not before consume_schedule_claim",
    )
    seal = mail[mail.find("def _seal(") :]
    require("note_external_success(" in seal, "seal does not note external success")
    require("gmail" not in mail, "the mail entry reads gmail")
    require("search_meeting_mail" not in mail, "the mail entry calls meeting search")
    require('CONFIRM_DETAIL["unknown"]' not in mail, "mail uses the calendar unknown sentence")
    require("结果未知，未再次发送。" in mail, "the mail unknown sentence is missing")
    require_reconcile(mail)

    schedule = read("backend/app/services/schedule_confirm.py")
    require(
        "结果未知，未再次写入。请先核对日历。" in schedule,
        "the calendar unknown sentence changed",
    )

    gmail = read("backend/app/services/gmail.py")
    require("def search_meeting_mail(" in gmail, "meeting search was removed")
    require("event_inbox" not in gmail, "meeting search submits events")

    require_create_receipt()

    card = read("frontend/src/components/FollowupCard.tsx")
    require("gmail.send" not in card, "FollowupCard mentions gmail.send")
    require("用邮件应用打开" in card, "the mailto label is gone")
    for line in card.splitlines():
        if "mailtoHref" in line and "onClick" in line:
            fail_model("the mailto anchor has a click handler")


def write_cfg(
    directory: Path,
    spec: str,
    constants: tuple[str, ...],
    flags: dict[str, bool],
    names: tuple[str, ...],
) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in constants:
        lines.append(f"  {name} = {'TRUE' if flags.get(name) else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / f"{spec}.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(
    spec: str,
    constants: tuple[str, ...],
    flags: dict[str, bool],
    names: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix=f"{spec.lower()}-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / f"{spec}.tla", meta / f"{spec}.tla")
        cfg = write_cfg(meta, spec, constants, flags, names)
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
                    f"{spec}.tla",
                ],
                cwd=meta,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail_model(f"{spec} TLC timed out")


def judge(spec: str, result: subprocess.CompletedProcess[str], label: str | None) -> None:
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if label:
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken {spec} model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model(f"the held {spec} run failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the mail entry and mail send models.")
    parser.add_argument("--break-dedupe", action="store_true")
    parser.add_argument("--break-bare", action="store_true")
    parser.add_argument("--break-success", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-link", action="store_true")
    parser.add_argument("--break-no-provider", action="store_true")
    parser.add_argument("--break-two-sends", action="store_true")
    parser.add_argument("--break-resend", action="store_true")
    args = parser.parse_args()
    chosen = [name for name in (*ENTRY_BREAKS, *SEND_BREAKS) if getattr(args, name)]
    if len(chosen) > 1:
        fail_model("pass only one break flag")
    anchors()
    if not chosen:
        entry = run_tlc("MailEntry", ENTRY_FLAGS, {}, ENTRY_HELD)
        judge("MailEntry", entry, None)
        send = run_tlc("MailSend", SEND_FLAGS, {}, SEND_HELD)
        judge("MailSend", send, None)
        return
    name = chosen[0]
    if name in ENTRY_BREAKS:
        constant, label = ENTRY_BREAKS[name]
        result = run_tlc("MailEntry", ENTRY_FLAGS, {constant: True}, ("TypeOK", label))
        judge("MailEntry", result, label)
        return
    constant, label = SEND_BREAKS[name]
    result = run_tlc("MailSend", SEND_FLAGS, {constant: True}, ("TypeOK", label))
    judge("MailSend", result, label)


if __name__ == "__main__":
    main()
