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
