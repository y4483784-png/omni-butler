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
    "ImageStaysOnChatModel",
    "MissingUsageStaysNull",
    "PendingDeleteBeforeBlob",
    "TerminalStays",
    "StrangerHidden",
    "StagedFence",
)


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def model_row(spec: str) -> None:
    rows = [line for line in spec.splitlines() if "imageRow \\in" in line]
    require(len(rows) == 1, "the picture model lost the image row type")
    require('"staged"' in rows[0], "the picture model omits staged")
    require('"tombstone"' in rows[0], "the picture model omits tombstone")
    require("StagedFence ==" in spec, "the picture model omits the staged fence")


def anchors() -> None:
    chat = read("backend/app/api/chat.py")
    llm = read("backend/app/core/llm.py")
    task_run = read("backend/app/services/task_run.py")
    images = read("backend/app/services/task_images.py")
    usage = read("backend/app/services/llm_usage.py")
    require("ocr_image_bytes" not in chat, "chat.py calls ocr_image_bytes")
    require("ocr_image_bytes" not in images, "task image load calls ocr_image_bytes")
    apply_end = task_run.find("\n_CARD_ACTION")
    apply = task_run[task_run.find("def _apply_event"):apply_end]
    require("mark_pending_delete" in apply, "terminal transition does not mark pending_delete")
    require("purge_pending" not in apply, "blob purge runs inside the status transition")
    require(".delete(" not in apply, "blob delete runs inside the status transition")
    record_end = llm.find("\ndef _mock_text")
    record = llm[llm.find("def _record_call"):record_end]
    require("prompt_tokens or 0" in record, "in-memory counter no longer uses or 0")
    require("or 0" not in usage, "durable usage coerces a missing count to zero")
    require("usage_count" in usage, "durable usage reader is gone")
    require('startswith("task-image/")' in images, "storage key prefix is gone")
    require('"pending_delete"' in images, "pending_delete is gone")
    require('"staged"' in images, "staged bind rows are gone")
    require('"tombstone"' in images, "an unacknowledged image key is not retained")
    require(
        'else:\n            _fence_staged(image_id, task_run_id, mode="terminal")' in images,
        "a missing blob does not use the terminal fence",
    )
    model_row(read("verification/picture/Picture.tla"))
    settle = images[images.find("def _running_task") : images.find("\ndef purge_pending")]
    require('purge_state == "staged"' in settle, "reconcile writes image state without a staged fence")
    require("row.purge_state" not in settle, "reconcile assigns a loaded image row")
    watchdog = read("backend/app/tasks/watchdog.py")
    require("reconcile_task_images" in watchdog, "image cleanup is not on the maintenance beat")
    complete = llm[llm.find("def complete_text") : llm.find("\ndef complete_json")]
    stop_at = complete.find("if finish is FinishClass.STOP")
    require(stop_at > 0, "complete_text lost the stop finish")
    require(
        "record_response_usage" in complete[:stop_at],
        "usage is recorded only after a stop finish",
    )
    stream = llm[llm.find("def stream_chat") : llm.find("\ndef complete_text")]
    handler = stream.find("except (LLMInfraError, LLMStructuredError)")
    require("_store_usage()" in stream[handler : handler + 160], "a stream error drops usage")


def write_cfg(
    directory: Path,
    *,
    ocr: bool,
    zero: bool,
    early: bool,
    revert: bool,
    leak: bool,
    fence: bool,
) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  breakOcr = {'TRUE' if ocr else 'FALSE'}",
        f"  breakZero = {'TRUE' if zero else 'FALSE'}",
        f"  breakEarlyBlob = {'TRUE' if early else 'FALSE'}",
        f"  breakRevert = {'TRUE' if revert else 'FALSE'}",
        f"  breakLeak = {'TRUE' if leak else 'FALSE'}",
        f"  breakFence = {'TRUE' if fence else 'FALSE'}",
    ]
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Picture.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(
    *,
    ocr: bool,
    zero: bool,
    early: bool,
    revert: bool,
    leak: bool,
    fence: bool,
) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="picture-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(
            meta,
            ocr=ocr,
            zero=zero,
            early=early,
            revert=revert,
            leak=leak,
            fence=fence,
        )
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
                "Picture.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the picture model.")
    parser.add_argument("--break-ocr", action="store_true")
    parser.add_argument("--break-zero", action="store_true")
    parser.add_argument("--break-early-blob", action="store_true")
    parser.add_argument("--break-revert", action="store_true")
    parser.add_argument("--break-leak", action="store_true")
    parser.add_argument("--break-fence", action="store_true")
    args = parser.parse_args()
    chosen = sum(
        1
        for flag in (
            args.break_ocr,
            args.break_zero,
            args.break_early_blob,
            args.break_revert,
            args.break_leak,
            args.break_fence,
        )
        if flag
    )
    if chosen > 1:
        fail_model("pass only one break flag")
    anchors()
    result = run_tlc(
        ocr=args.break_ocr,
        zero=args.break_zero,
        early=args.break_early_blob,
        revert=args.break_revert,
        leak=args.break_leak,
        fence=args.break_fence,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    expected = {
        "break_ocr": "ImageStaysOnChatModel",
        "break_zero": "MissingUsageStaysNull",
        "break_early_blob": "PendingDeleteBeforeBlob",
        "break_revert": "TerminalStays",
        "break_leak": "StrangerHidden",
        "break_fence": "StagedFence",
    }
    active = next((name for name, _label in expected.items() if getattr(args, name)), None)
    if active:
        label = expected[active]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
