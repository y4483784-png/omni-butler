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
    "AtMostOnePlace",
    "BareWritesNothing",
    "UnpaidWritesNothing",
    "SuccessAfterRecord",
    "UnnamedIsNotOrder",
    "WallDoesNotClick",
    "StopKeepsRecord",
)

FLAGS = (
    "breakBare",
    "breakUnpaid",
    "breakSuccess",
    "breakAgain",
    "breakSeen",
    "breakPage",
    "breakWall",
    "breakStop",
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
    errands = read("backend/app/services/errands.py")
    commit = between(errands, "def commit_errand(", "def drop_sealed(")
    empty_at = commit.find('if token is None or str(token).strip() == ""')
    secret_at = commit.find('if pay is None:')
    arm_at = commit.find("arm_remote_claim(")
    place_at = commit.find("_place(")
    require(empty_at >= 0, "commit without a token is not refused")
    require(secret_at >= 0, "a missing pay secret is not refused")
    require(arm_at >= 0 and place_at >= 0, "the place call is missing")
    require(
        empty_at < secret_at < arm_at < place_at,
        "the external call is not after the token check, the pay secret, and the arm",
    )
    require("db.rollback()" in commit[secret_at:arm_at], "a missing pay secret does not roll back before arm")
    require("decide_replay(" in errands, "the unknown path does not call decide_replay")
    require("note_external_success" not in errands, "the errand path notes external success")
    require("settle_unknown" not in errands, "the errand path calls settle_unknown")
    require("lookup_inserted_event" not in errands, "the errand path looks up a calendar event")
    require("def public_start_url(" in errands, "the browser start url is missing")

    decide = read("services/errand-compose/decide.py")
    require("playwright" not in decide.lower(), "decide imports a browser")
    wall_at = decide.find("请使用App")
    ordered_at = decide.find("已下单")
    require(wall_at >= 0 and ordered_at > wall_at, "the app wall is not decided before 已下单")
    require("def allowed_start(" in decide, "the start url is not checked")

    driver = read("services/errand-compose/driver.py")
    early_at = driver.find('early["outcome"]')
    click_at = driver.find("_click_buy(")
    require(early_at >= 0 and click_at > early_at, "the pay click is not after the early page decision")
    require("allowed_start(" in driver, "the driver does not check the start url")
    require("The plan did not choose this library." in driver, "the library note is missing")

    for path in (ROOT / "backend" / "app").rglob("*.py"):
        require("playwright" not in path.read_text(encoding="utf-8").lower(), f"{path} imports playwright")

    card = read("frontend/src/components/ErrandCard.tsx")
    require("确认下单" in card, "the confirm button label is gone")
    require("password" not in card.lower(), "the errand card mentions a password")

    calendar = read("backend/app/api/calendar.py")
    require("note_external_success" not in calendar, "calendar confirm notes external success")

    detail = read("backend/app/services/schedule_confirm.py")
    require("请先核对日历" in detail, "the unknown sentence was rewritten")
    require('"errand.place"' in detail, "errand.place is not a claim action")


def write_cfg(directory: Path, flags: dict[str, bool], names: tuple[str, ...]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name in FLAGS:
        lines.append(f"  {name} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in names:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "ErrandPlace.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool], names: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="errand-place-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "ErrandPlace.tla", meta / "ErrandPlace.tla")
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
                "ErrandPlace.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the errand place model.")
    parser.add_argument("--break-bare", action="store_true")
    parser.add_argument("--break-unpaid", action="store_true")
    parser.add_argument("--break-success", action="store_true")
    parser.add_argument("--break-again", action="store_true")
    parser.add_argument("--break-seen", action="store_true")
    parser.add_argument("--break-page", action="store_true")
    parser.add_argument("--break-wall", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    args = parser.parse_args()
    flags = {
        "breakBare": args.break_bare,
        "breakUnpaid": args.break_unpaid,
        "breakSuccess": args.break_success,
        "breakAgain": args.break_again,
        "breakSeen": args.break_seen,
        "breakPage": args.break_page,
        "breakWall": args.break_wall,
        "breakStop": args.break_stop,
    }
    if sum(1 for value in flags.values() if value) > 1:
        fail_model("pass only one break flag")
    anchors()
    expected = {
        "breakBare": "BareWritesNothing",
        "breakUnpaid": "UnpaidWritesNothing",
        "breakSuccess": "SuccessAfterRecord",
        "breakAgain": "AtMostOnePlace",
        "breakSeen": "AtMostOnePlace",
        "breakPage": "UnnamedIsNotOrder",
        "breakWall": "WallDoesNotClick",
        "breakStop": "StopKeepsRecord",
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
