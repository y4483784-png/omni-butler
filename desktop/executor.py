from __future__ import annotations

import subprocess


def perform(license: object, *, open_file, run_program) -> bool:
    if not isinstance(license, dict) or license.get("kind") != "armed":
        return False
    action = license.get("action")
    target = license.get("local_target")
    if not isinstance(target, str) or not target:
        return False
    if action == "desktop.open":
        open_file(target)
        return True
    if action == "desktop.run":
        args = license.get("local_args")
        if isinstance(args, str) or not isinstance(args, (list, tuple)):
            return False
        run_program(target, tuple(str(item) for item in args))
        return True
    return False


def open_with_xdg(path: str) -> None:
    subprocess.run(["xdg-open", path], check=False)


def run_argv(program: str, args: tuple[str, ...] | list[str]) -> None:
    subprocess.run([program, *args], shell=False, check=False)
