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
    "LieOpensNothing",
    "StopBeforeOpensNothing",
    "AtMostOneOpen",
    "OpenAfterArm",
    "RetryOpensNothing",
    "StopKeepsOpen",
)

FLAGS = ("breakLie", "breakStop", "breakEarly", "breakAgain", "breakKeep")


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
    surface = read("backend/app/agents/harness/surface.py")
    enum_body = between(surface, "class ServerSurface", "def surface_for_login")
    require("class ServerSurface(enum.Enum):" in surface, "ServerSurface is not a plain enum")
    require("(str, enum.Enum)" not in surface, "ServerSurface compares equal to a wire string")
    require('WEB = "web"' in enum_body, "the login surface is not web")
    require("DESKTOP" not in enum_body, "the enum gained a desktop member")
    login = between(surface, "def surface_for_login", "def coerce_surface")
    require("def surface_for_login() -> ServerSurface:" in login, "the mint takes an argument")
    require("return ServerSurface.WEB" in login, "the login mint can return desktop")
    require('presented in {"desktop", "web"}' in read("backend/app/agents/harness/desktop_channel.py"), "the word desktop can be a proof")

    chat = read("backend/app/api/chat.py")
    request = between(chat, "class ChatRequest(BaseModel):", "\n\n")
    require("desktop" not in request, "ChatRequest stores a desktop field")
    require("surface_for_login()" in chat, "chat does not mint the surface from the login")

    perform = between(read("desktop/executor.py"), "def perform(", "def open_with_xdg")
    require('license.get("kind") != "armed"' in perform, "perform runs without an armed license")
    confirm = read("desktop/confirm_local.py")
    perform_at = confirm.find("if not perform(")
    finish_at = confirm.find('"/api/desktop/finish"')
    require(perform_at >= 0 and finish_at > perform_at, "finish is not after perform")
    require('command == "propose"' in confirm, "propose is not its own command")
    require('command == "confirm"' in confirm, "confirm is not its own command")

    main = read("desktop/main.js")
    require("desktop-propose" in main and "desktop-confirm" in main, "the window has one click for propose and confirm")
    propose_at = main.find("desktop-propose")
    confirm_at = main.find("desktop-confirm")
    require(propose_at >= 0 and confirm_at > propose_at, "confirm is not a later click than propose")
    preload = read("desktop/preload.js")
    require("打开测试文件" in preload, "the package button is missing")
    require("desktop-propose" in preload and "desktop-confirm" in preload, "the preload spends the claim on the first click")

    commit_local = between(read("backend/app/services/local_action.py"), "def commit_local(", "def finish_local(")
    consume_at = commit_local.find("consume_schedule_claim(")
    arm_at = commit_local.find("arm_remote_claim(")
    audit_at = commit_local.find("proof.audit_word()")
    require(consume_at >= 0 and arm_at > consume_at, "the claim is not armed after consume")
    require(audit_at > arm_at, "the desktop word is not written after the arm")
    require("desktop/" not in read("backend/app/agents/harness/gateway.py"), "the gateway imports the package opener")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "DesktopOpen.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="desktop-open-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "DesktopOpen.tla", meta / "DesktopOpen.tla")
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
                "DesktopOpen.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the desktop open model.")
    parser.add_argument("--break-lie", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-early", action="store_true")
    parser.add_argument("--break-again", action="store_true")
    parser.add_argument("--break-keep", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakLie": args.break_lie,
        "breakStop": args.break_stop,
        "breakEarly": args.break_early,
        "breakAgain": args.break_again,
        "breakKeep": args.break_keep,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakLie": "LieOpensNothing",
        "breakStop": "StopBeforeOpensNothing",
        "breakEarly": "OpenAfterArm",
        "breakAgain": "AtMostOneOpen",
        "breakKeep": "StopKeepsOpen",
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
