#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
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
    "HashOnly",
    "RevokeOne",
    "SpoofDenied",
    "TokenIsNotAConfirm",
)

FLAGS = ("breakHash", "breakRevoke", "breakSpoof", "breakConfirm")


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


def clip(text: str, start: str, markers: tuple[str, ...]) -> str:
    begin = text.find(start)
    if begin < 0:
        fail_model(f"missing {start}")
    finish = len(text)
    for marker in markers:
        found = text.find(marker, begin + len(start))
        if found >= 0:
            finish = min(finish, found)
    return text[begin:finish]


def anchors() -> None:
    channel = read("backend/app/agents/harness/desktop_channel.py")
    body = clip(channel, "def recognize_package(", ("\ndef ",))
    word = 'presented in {"desktop", "web"}'
    word_at = body.find(word)
    hash_at = body.find("hashlib.sha256")
    require(word in channel, "the word desktop can be a proof")
    require(word_at >= 0 and hash_at > word_at, "the word check is not before the hash")
    require("hmac.compare_digest" in body, "recognize_package does not compare the hash")
    require("desktop_package_token" not in body, "recognize_package still reads the shared token")

    device = clip(read("backend/app/models/models.py"), "class DesktopDevice", ("\nclass ",))
    require("token_hash" in device, "DesktopDevice has no token_hash")
    require("revoked_at" in device, "DesktopDevice has no revoked_at")
    require(
        re.search(r"(?m)^\s*token\s*=\s*Column\(", device) is None,
        "DesktopDevice stores the raw token",
    )

    commit_local = between(
        read("backend/app/services/local_action.py"),
        "def commit_local(",
        "def finish_local(",
    )
    blank_at = commit_local.find('str(token).strip() == ""')
    consume_at = commit_local.find("consume_schedule_claim(")
    require(blank_at >= 0 and consume_at > blank_at, "a blank token is not rejected before consume")

    listed = clip(read("backend/app/api/desktop.py"), "class DeviceListItem", ("\nclass ",))
    require("token_hash" not in listed, "the list response includes token_hash")
    serializer = clip(read("backend/app/services/desktop_devices.py"), "def list_devices(", ("\ndef ",))
    require("token_hash" not in serializer, "the list serializer includes token_hash")

    stop = between(read("backend/app/services/task_run.py"), "def stop(", "def continue_job(")
    require("revoked_at" not in stop, "task_run.stop assigns revoked_at")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "DesktopDevices.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="devices-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "DesktopDevices.tla", meta / "DesktopDevices.tla")
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
                "DesktopDevices.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the desktop devices model.")
    parser.add_argument("--break-hash", action="store_true")
    parser.add_argument("--break-revoke", action="store_true")
    parser.add_argument("--break-spoof", action="store_true")
    parser.add_argument("--break-confirm", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakHash": args.break_hash,
        "breakRevoke": args.break_revoke,
        "breakSpoof": args.break_spoof,
        "breakConfirm": args.break_confirm,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakHash": "HashOnly",
        "breakRevoke": "RevokeOne",
        "breakSpoof": "SpoofDenied",
        "breakConfirm": "TokenIsNotAConfirm",
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
