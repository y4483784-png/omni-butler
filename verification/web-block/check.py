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
    "SurfaceStaysWeb",
    "NoLocalRun",
    "ShellStaysWhitelist",
    "TokenStillDenied",
)

FLAGS = ("breakSpoof", "breakLocal", "breakShell")


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
    surface = read("backend/app/agents/harness/surface.py")
    require("class ServerSurface(enum.Enum):" in surface, "ServerSurface is not a plain enum")
    require("(str, enum.Enum)" not in surface, "ServerSurface compares equal to a wire string")
    require('WEB = "web"' in surface, "the only server surface is not web")
    login = between(surface, "def surface_for_login", "def coerce_surface")
    require("def surface_for_login() -> ServerSurface:" in login, "the mint takes an argument it does not read")
    require("return ServerSurface.WEB" in login, "the login mint can return a desktop surface")
    require("ChatRequest" not in surface, "the mint reads the chat body")
    coerce = between(surface, "def coerce_surface", "def local_tool_denial")
    require("return ServerSurface.WEB" in coerce, "a non-member surface is not collapsed to web")
    denial = surface[surface.find("def local_tool_denial") :]
    require(denial.startswith("def local_tool_denial"), "local_tool_denial is missing")
    require("if not local:" in denial, "a server tool is treated as local")
    require("return None" in denial, "a server tool has no allow return")
    require("coerce_surface(surface).value" in surface, "the denial sentence uses the raw client string")
    require("拒绝本地工具" in surface, "the denial sentence dropped the local refusal")

    gateway = read("backend/app/agents/harness/gateway.py")
    invoke = gateway[gateway.find("def invoke") :]
    require(invoke.startswith("def invoke"), "invoke is missing")
    deny_at = invoke.find("local_tool_denial(")
    auto_at = invoke.find("_autoharness_precheck")
    white_at = invoke.find("_local_permission_ok")
    exec_at = invoke.find("spec.execute")
    require(
        0 <= deny_at < auto_at < white_at < exec_at,
        "the local denial is not before the whitelist and execute",
    )
    require('event["server_surface"]' in invoke, "the audit line has no server surface")

    chat = read("backend/app/api/chat.py")
    request = between(chat, "class ChatRequest(BaseModel):", "\n\n")
    require("desktop" not in request, "ChatRequest stores a desktop field")
    require("confirm_token" not in request, "ChatRequest stores a confirm token")
    require("surface_for_login()" in chat, "chat does not mint the surface from the login")

    workflow = read("backend/app/agents/workflow.py")
    require("coerce_surface(surface)" in workflow, "the workflow does not coerce the carried surface")
    require('surface=coerce_surface(state.get("surface"))' in workflow, "ToolContext is built without the coerced surface")

    registry = read("backend/app/agents/tools/registry.py")
    require("local: bool = False" in registry, "ToolSpec has no local flag")
    allowed = between(read("backend/app/agents/harness/constitution.yaml"), "allowed_tools:", "tool_risk:")
    require("local_probe" not in allowed, "the test tool is on the production allow list")
    require("- kb\n" in allowed and "- sandbox\n" in allowed, "the web allow list lost a builtin")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "WebBlock.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="web-block-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "WebBlock.tla", meta / "WebBlock.tla")
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
                "WebBlock.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the web execution surface model.")
    parser.add_argument("--break-spoof", action="store_true")
    parser.add_argument("--break-local", action="store_true")
    parser.add_argument("--break-shell", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakSpoof": args.break_spoof,
        "breakLocal": args.break_local,
        "breakShell": args.break_shell,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakSpoof": "SurfaceStaysWeb",
        "breakLocal": "NoLocalRun",
        "breakShell": "ShellStaysWhitelist",
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
