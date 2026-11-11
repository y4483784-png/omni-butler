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
    "OneReceipt",
    "BareWritesNothing",
    "SuccessAfterAccept",
    "StopKeepsAccepted",
    "LinkIsNotSend",
)

FLAGS = (
    "breakDedupe",
    "breakBare",
    "breakSuccess",
    "breakStop",
    "breakLink",
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
    seal = mail[mail.find("def _seal(") :]
    require("note_external_success(" in seal, "seal does not note external success")
    require("gmail" not in mail, "the mail entry reads gmail")
    require("search_meeting_mail" not in mail, "the mail entry calls meeting search")

    gmail = read("backend/app/services/gmail.py")
    require("def search_meeting_mail(" in gmail, "meeting search was removed")
    require("event_inbox" not in gmail, "meeting search submits events")

    calendar = read("backend/app/api/calendar.py")
    require("note_external_success" not in calendar, "calendar confirm notes mail success")

    card = read("frontend/src/components/FollowupCard.tsx")
    require("gmail.send" not in card, "FollowupCard mentions gmail.send")
    require("用邮件应用打开" in card, "the mailto label is gone")
    for line in card.splitlines():
        if "mailtoHref" in line and "onClick" in line:
            fail_model("the mailto anchor has a click handler")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "MailEntry.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="mail-entry-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "MailEntry.tla", meta / "MailEntry.tla")
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
                "MailEntry.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the mail entry model.")
    parser.add_argument("--break-dedupe", action="store_true")
    parser.add_argument("--break-bare", action="store_true")
    parser.add_argument("--break-success", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-link", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakDedupe": args.break_dedupe,
        "breakBare": args.break_bare,
        "breakSuccess": args.break_success,
        "breakStop": args.break_stop,
        "breakLink": args.break_link,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakDedupe": "OneReceipt",
        "breakBare": "BareWritesNothing",
        "breakSuccess": "SuccessAfterAccept",
        "breakStop": "StopKeepsAccepted",
        "breakLink": "LinkIsNotSend",
    }
    active = next((name for name, enabled in flags.items() if enabled), None)
    names = ("TypeOK", expected[active]) if active else HELD
    result = run_tlc(flags, names)
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
