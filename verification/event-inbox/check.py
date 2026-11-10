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
    "ReceiveIsNotSuccess",
    "StoppedWritesNothing",
    "CompletedNotReused",
    "OneCard",
    "QueueStaysOne",
    "PassthroughStays",
)

FLAGS = (
    "breakDedupe",
    "breakStopped",
    "breakReuse",
    "breakSecond",
    "breakSuccess",
    "breakPass",
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
    inbox = read("backend/app/services/event_inbox.py")
    submit = between(inbox, "def submit_event(", "def note_external_success(")
    require("if existing is not None:" in submit, "submit lost the delivery dedupe")
    require("note_external_success(" not in submit, "submit marks external success")
    require('_noise(text, automated, kind)' in submit, "submit skips the program filter")
    require('decision == "ignore"' in submit, "submit skips the model decision")

    route = between(inbox, "def _route_act(", "def _while_waiting(")
    stopped = between(route, 'if target.status == "stopped":', 'if target.status == "completed":')
    completed = between(route, 'if target.status == "completed":', 'if target.status == "waiting_person":')
    require("_open_task(" not in stopped, "stopped opens a task")
    require("_reissue(" not in stopped, "stopped reissues a card")
    require("external_success" not in stopped, "stopped marks external success")
    require('route="stopped"' in stopped, "stopped has no stopped route")
    require("_open_task(" in completed, "completed does not open a new task")
    require("target.generation" not in completed, "completed reuses that generation")
    require('if target.status == "waiting_person":' in route, "waiting is merged into another status")
    require('if target.status in _ENTER:' in route, "passthrough branch is missing")
    require('route="passthrough"' in route, "passthrough route is missing")
    require("_ENTER = frozenset({\"running\", \"waiting_resource\"})" in inbox, "enter set drifted")

    waiting = between(inbox, "def _while_waiting(", "def _reissue(")
    require('route="kept"' in waiting, "same fingerprint does not keep the claim")
    require("already is not None" in waiting, "the one-reissue guard is missing")
    require('route="queued"' in waiting, "a later event does not queue")
    require('route="reissued"' in waiting, "a different fingerprint does not reissue")

    inserted = between(inbox, "def _insert(", "def _existing(")
    require('if state == "external_success":' in inserted, "insert can store external success")
    require("收到事件还不是外部成功" in inserted, "insert no longer refuses success")

    claim = between(inbox, "def _written_claim(", "def _view(")
    require('claim.status != "written"' in claim, "success does not require a written claim")
    require("remote_request_id" in claim, "success does not match the remote id")

    require("search_meeting_mail" not in inbox, "the inbox calls meeting search")
    require("create_event" not in inbox, "the inbox writes the calendar")
    require('_SOURCES = frozenset({"internal", "mail"})' in inbox, "source allow list drifted")

    main = read("backend/app/main.py")
    require("events.router" in main, "the inbox route is not registered")
    gmail = read("backend/app/services/gmail.py")
    require("def search_meeting_mail(" in gmail, "meeting search was removed")
    require("event_inbox" not in gmail, "meeting search submits events")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "EventInbox.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="event-inbox-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "EventInbox.tla", meta / "EventInbox.tla")
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
                "EventInbox.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the event inbox model.")
    parser.add_argument("--break-dedupe", action="store_true")
    parser.add_argument("--break-stopped", action="store_true")
    parser.add_argument("--break-reuse", action="store_true")
    parser.add_argument("--break-second", action="store_true")
    parser.add_argument("--break-success", action="store_true")
    parser.add_argument("--break-pass", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakDedupe": args.break_dedupe,
        "breakStopped": args.break_stopped,
        "breakReuse": args.break_reuse,
        "breakSecond": args.break_second,
        "breakSuccess": args.break_success,
        "breakPass": args.break_pass,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakDedupe": "OneReceipt",
        "breakStopped": "StoppedWritesNothing",
        "breakReuse": "CompletedNotReused",
        "breakSecond": "OneCard",
        "breakSuccess": "ReceiveIsNotSuccess",
        "breakPass": "PassthroughStays",
    }
    active = next((name for name, enabled in flags.items() if enabled), None)
    names = ( "TypeOK", expected[active] ) if active else HELD
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
