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

PLACE_BREAKS = {
    "break_bare": ("breakBare", "BareWritesNothing"),
    "break_unpaid": ("breakUnpaid", "UnpaidWritesNothing"),
    "break_success": ("breakSuccess", "SuccessAfterRecord"),
    "break_again": ("breakAgain", "AtMostOnePlace"),
    "break_seen": ("breakSeen", "AtMostOnePlace"),
    "break_page": ("breakPage", "UnnamedIsNotOrder"),
    "break_wall": ("breakWall", "WallDoesNotClick"),
    "break_stop": ("breakStop", "StopKeepsRecord"),
}

ECHO_HELD = (
    "TypeOK",
    "EchoRequiredForPlaced",
    "LookupDoesNotPlace",
    "SecretsDropped",
)

ECHO_FLAGS = (
    "breakEmptyEcho",
    "breakLookupPlace",
    "breakSecrets",
)

ECHO_BREAKS = {
    "break_empty_echo": ("breakEmptyEcho", "EchoRequiredForPlaced"),
    "break_lookup_place": ("breakLookupPlace", "LookupDoesNotPlace"),
    "break_secrets": ("breakSecrets", "SecretsDropped"),
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

    require_create_receipt()

    detail = read("backend/app/services/schedule_confirm.py")
    require("请先核对日历" in detail, "the unknown sentence was rewritten")
    require('"errand.place"' in detail, "errand.place is not a claim action")
    product_rules()


def _load(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail_model(f"missing {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def product_rules() -> None:
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        from app.services.errands import interpret
    except Exception as exc:
        fail_model(f"interpret could not be loaded: {exc}")
    decide = _load(ROOT / "services" / "errand-compose" / "decide.py", "errand_decide_check")
    server = _load(ROOT / "services" / "errand-compose" / "server.py", "errand_server_check")
    armed = "armed-1"
    for blank in (" ", "\t", " \t ", ""):
        placed = interpret(
            {"outcome": "placed", "order_id": blank, "idempotency_key": armed},
            armed,
        )
        require(placed.kind != "placed", f"interpret places order id {blank!r}")
        require(placed.kind == "unknown", f"interpret does not return unknown for order id {blank!r}")
    for blank in ("", " ", "\t", " \t "):
        paid = decide.decide("checkout", armed=armed, pay_clicked=True, order_id=blank)
        require(paid["outcome"] != "placed", f"decide places order id {blank!r}")
        require(paid["outcome"] == "unknown", f"decide does not return unknown for order id {blank!r}")
        named = decide.decide(f"已下单 {armed}", armed=armed, order_id=blank)
        require(named["outcome"] == "unknown", f"decide treats order id {blank!r} as already")
    already = interpret(
        {
            "outcome": "already",
            "order_id": " \t ",
            "idempotency_key": armed,
            "told": f"已下单 {armed}",
        },
        armed,
    )
    require(already.kind == "unknown", "interpret treats a whitespace order id as already")
    public = server._public(
        {
            "outcome": "placed",
            "order_id": " ",
            "pay": "secret-pay",
            "login": "secret-login",
        },
        {"idempotency_key": armed, "pay": "task-pay", "login": "task-login"},
    )
    require("pay" not in public, "_public leaks pay")
    require("login" not in public, "_public leaks login")
    dumped = str(public)
    require("secret-pay" not in dumped and "secret-login" not in dumped, "_public leaks a secret value")
    require("task-pay" not in dumped and "task-login" not in dumped, "_public leaks a task secret")
    chained = interpret(public, armed)
    require(chained.kind != "placed", "a public whitespace order id is placed")
    padded = interpret(
        {"outcome": "placed", "order_id": "  m-1 \t", "idempotency_key": armed},
        armed,
    )
    require(
        padded.kind == "placed" and padded.order_id == "m-1",
        "a padded order id is not kept stripped",
    )
    kept = decide.decide("checkout", armed=armed, pay_clicked=True, order_id="  m-1 \t")
    require(
        kept["outcome"] == "placed" and kept["order_id"] == "m-1",
        "decide does not keep a padded order id",
    )


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
    parser = argparse.ArgumentParser(description="Check the errand place and errand echo models.")
    parser.add_argument("--break-bare", action="store_true")
    parser.add_argument("--break-unpaid", action="store_true")
    parser.add_argument("--break-success", action="store_true")
    parser.add_argument("--break-again", action="store_true")
    parser.add_argument("--break-seen", action="store_true")
    parser.add_argument("--break-page", action="store_true")
    parser.add_argument("--break-wall", action="store_true")
    parser.add_argument("--break-stop", action="store_true")
    parser.add_argument("--break-empty-echo", action="store_true")
    parser.add_argument("--break-lookup-place", action="store_true")
    parser.add_argument("--break-secrets", action="store_true")
    args = parser.parse_args()
    chosen = [name for name in (*PLACE_BREAKS, *ECHO_BREAKS) if getattr(args, name)]
    if len(chosen) > 1:
        fail_model("pass only one break flag")
    anchors()
    if not chosen:
        place = run_tlc("ErrandPlace", FLAGS, {}, HELD)
        judge("ErrandPlace", place, None)
        echo = run_tlc("ErrandEcho", ECHO_FLAGS, {}, ECHO_HELD)
        judge("ErrandEcho", echo, None)
        return
    name = chosen[0]
    if name in PLACE_BREAKS:
        constant, label = PLACE_BREAKS[name]
        result = run_tlc("ErrandPlace", FLAGS, {constant: True}, ("TypeOK", label))
        judge("ErrandPlace", result, label)
        return
    constant, label = ECHO_BREAKS[name]
    result = run_tlc("ErrandEcho", ECHO_FLAGS, {constant: True}, ("TypeOK", label))
    judge("ErrandEcho", result, label)


if __name__ == "__main__":
    main()
