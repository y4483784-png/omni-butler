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
    "OneSearchProvider",
    "ChatModelUnchanged",
)

BREAKS = {
    "break_provider": ("breakProvider", "OneSearchProvider"),
    "break_model": ("breakModel", "ChatModelUnchanged"),
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


def anchors() -> None:
    web = read("backend/app/agents/tools/web.py")
    execute = between(web, "def execute_web(", "def execute_web_fetch(")
    require("deepseek_search(" in execute, "execute_web does not call deepseek_search")
    require("_web_search_url" not in execute, "execute_web contains _web_search_url")
    job = read("backend/app/tasks/job.py")
    require("_web_search_url" not in job, "job.py contains _web_search_url")
    module = read("backend/app/services/deepseek_search.py")
    require("localization" not in module, "deepseek search sends localization")


def write_cfg(directory: Path, flags: dict[str, bool]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name, (constant, _label) in BREAKS.items():
        lines.append(f"  {constant} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Search.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="search-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "Search.tla", meta / "Search.tla")
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
                "Search.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the search model.")
    parser.add_argument("--break-provider", action="store_true")
    parser.add_argument("--break-model", action="store_true")
    args = parser.parse_args()
    flags = {name: bool(getattr(args, name)) for name in BREAKS}
    chosen = [name for name, enabled in flags.items() if enabled]
    if len(chosen) > 1:
        fail_model("pass only one break flag")
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
