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
from verification.tlc_jar import RELEASE_TAG as TLA_VERSION
from verification.tlc_jar import jar_path

HELD = (
    "TypeOK",
    "WritesFollowConfirm",
    "NoWriteBeforeConfirm",
    "NoReentryWhileBusy",
    "SecondWriteImpliesRestore",
    "AtMostOneSuccessfulWrite",
    "NoWriteAfterRetire",
    "NoWriteWithoutToken",
)
LOCK_BROKEN = tuple(name for name in HELD if name != "AtMostOneSuccessfulWrite")
HEALTH = ("TypeOK", "WritesFollowConfirm")
CLAIM_HELD = (
    "TypeOK",
    "AtMostOneSuccessfulWrite",
    "NoWriteWithoutToken",
    "WriteMatchedFingerprint",
    "WriteSawLiveGeneration",
    "OpenClaimHasLiveGeneration",
    "AtMostOneOpenRow",
    "OwnerWrites",
)
CLAIM_BREAKS = {
    "token": "NoWriteWithoutToken",
    "match": "WriteMatchedFingerprint",
    "retire": "OpenClaimHasLiveGeneration",
    "once": "AtMostOneSuccessfulWrite",
    "dedup": "AtMostOneOpenRow",
    "owner": "OwnerWrites",
    "borrow": "NoBorrow",
}
CONTINUE_HELD = (
    "TypeOK",
    "NoWriteWithoutToken",
    "AtMostOneSuccessfulWrite",
    "AtMostOneOpenRow",
    "NoBorrow",
    "UnrelatedStopKeepsClick",
)

SUBMIT_DISABLED = 'disabled={locked || busy || mode === "choose"}'
CONFIRMED_PHASE = 'phase: "confirmed"'
WRITERS = (
    "createCalendarEvent",
    "updateCalendarEvent",
    "cancelCalendarEvent",
    "confirmGoogleCalendarChange",
)

CARD = "frontend/src/components/ScheduleCard.tsx"
EXPIRY = "frontend/src/utils/scheduleExpiry.ts"
CHAT = "frontend/src/services/chatStream.ts"
API = "backend/app/api/calendar.py"
CALENDAR = "backend/app/services/calendar.py"
LOCAL = "backend/app/services/calendar_local.py"
TOOL = "backend/app/agents/tools/calendar_tool.py"


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.is_file():
        fail_model(f"missing {rel}")
    return path.read_text(encoding="utf-8")


def py_block(source: str, signature: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(signature):
            start = index
            break
    if start is None:
        fail_model(f"missing {signature}")
    header = start
    paren = 0
    started = False
    while header < len(lines):
        for char in lines[header]:
            if char == "(":
                paren += 1
                started = True
            elif char == ")":
                paren -= 1
        if started and paren <= 0 and ":" in lines[header]:
            break
        header += 1
    else:
        fail_model(f"missing body for {signature}")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = header + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() == "":
            end += 1
            continue
        current = len(line) - len(line.lstrip())
        if current <= indent:
            break
        end += 1
    return "".join(lines[start:end])


def brace_slice(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        fail_model(f"missing {signature}")
    paren = 0
    seen_paren = False
    body_at = None
    for index in range(start, len(source)):
        char = source[index]
        if char == "(":
            paren += 1
            seen_paren = True
        elif char == ")":
            paren -= 1
        elif char == "{" and paren == 0 and seen_paren:
            body_at = index
            break
    if body_at is None:
        fail_model(f"missing body for {signature}")
    depth = 0
    quote = ""
    index = body_at
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
        index += 1
    fail_model(f"unbalanced body for {signature}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def require_before(text: str, earlier: str, later: str, message: str) -> None:
    if earlier not in text or later not in text or text.index(earlier) > text.index(later):
        fail_model(message)


def returns_early_on_cancelled_status(body: str) -> bool:
    return re.search(
        r"if\b[^\n]*\bcancelled\b[^\n]*:\s*return\b",
        body,
    ) is not None


def gate_enabled(card_fn: str) -> bool:
    return SUBMIT_DISABLED in card_fn


def lock_enabled(confirm_fn: str) -> bool:
    return CONFIRMED_PHASE in confirm_fn


def require_definitions() -> None:
    spec = read("verification/schedule-confirm/ScheduleConfirm.tla")
    for name in HELD:
        if not re.search(rf"^{re.escape(name)} ==", spec, re.M):
            fail_model(f"ScheduleConfirm.tla has no {name} == definition.")


def check_anchors() -> tuple[bool, bool]:
    card_src = read(CARD)
    expiry = read(EXPIRY)
    chat = read(CHAT)
    api = read(API)
    calendar = read(CALENDAR)
    local = read(LOCAL)
    tool = read(TOOL)

    card_fn = brace_slice(card_src, "export function ScheduleCard")
    confirm = brace_slice(card_src, "async function confirm")
    require_before(
        confirm,
        "setBusy(true)",
        "await",
        "confirm no longer sets busy before the first await.",
    )
    finally_at = confirm.find("finally")
    require(
        finally_at >= 0 and "setBusy(false)" in confirm[finally_at:],
        "confirm no longer clears busy in finally.",
    )
    require("if (busy)" not in confirm, "confirm now tests busy.")
    require("inflight" not in confirm, "confirm now mentions inflight.")
    form_at = card_fn.find("onSubmit=")
    call_at = card_fn.find("void confirm()")
    require(
        form_at >= 0 and call_at > form_at,
        "the form onSubmit no longer calls confirm.",
    )
    for signature in (
        "function chooseCandidate",
        "function applySlot",
        "function useSlot",
        "async function reply",
    ):
        body = brace_slice(card_src, signature)
        for name in WRITERS:
            require(name not in body, f"{signature} now calls {name}.")
    require(
        'card.phase !== "draft"' in brace_slice(expiry, "export function isExpired"),
        "isExpired no longer ignores a card that is not draft.",
    )
    require(
        'phase: "cancelled"' in brace_slice(chat, "function voidOpenDrafts"),
        "voidOpenDrafts no longer cancels a draft card.",
    )
    loop = brace_slice(chat, "async function runStreamLoop")
    require(
        "updateLastAssistant" in loop and "scheduleCard" in loop,
        "runStreamLoop no longer restores a schedule card through updateLastAssistant.",
    )
    require(
        "mergeStreamScheduleCard" in brace_slice(chat, "function updateLastAssistant"),
        "updateLastAssistant no longer keeps a settled schedule card.",
    )
    require(
        "overlaySettledScheduleCards" in loop,
        "runStreamLoop no longer keeps a settled card when it reloads messages.",
    )
    require(
        "publish_schedule_claim" in read("backend/app/api/chat.py"),
        "the chat stream no longer opens a confirm claim before showing the card.",
    )
    void_draft = py_block(calendar, "def void_draft_schedule_cards")
    require(
        'card["phase"] = "cancelled"' in void_draft,
        "void_draft_schedule_cards no longer assigns a cancelled phase.",
    )
    require(
        "cancel_open_schedule_claims" in void_draft,
        "void_draft_schedule_cards no longer cancels an open confirm claim.",
    )
    require(
        '"phase": "draft"' in py_block(tool, "def _base_card"),
        "_base_card no longer starts a draft.",
    )
    require(
        "create_event(" not in py_block(tool, "def execute_calendar"),
        "execute_calendar now calls create_event.",
    )
    for signature in (
        "def create_calendar_event",
        "def patch_event",
        "def delete_event",
        "def confirm_google_title_change",
        "def rsvp_google_event",
    ):
        body = py_block(api, signature)
        require(
            "_consume_schedule_claim(" in body,
            f"{signature} no longer consumes the confirm claim.",
        )
        require(
            "phase" not in body and "expires_at" not in body,
            f"{signature} now mentions phase or expires_at.",
        )
    consume = py_block(read("backend/app/services/schedule_confirm.py"), "def consume_schedule_claim")
    require(
        'raise ScheduleConfirmClosed("missing")' in consume,
        "consume_schedule_claim no longer rejects a missing token.",
    )
    opener = py_block(api, "def open_confirm_claim")
    require(
        "issue_schedule_claim(" in opener,
        "open_confirm_claim no longer issues through schedule_confirm.",
    )
    require(
        "continue_from_card(" in opener,
        "open_confirm_claim no longer continues a card click through schedule_confirm.",
    )
    continued = py_block(read("backend/app/services/schedule_confirm.py"), "def continue_from_card")
    require(
        "live_task_ids_for_session" not in continued,
        "continue_from_card borrows whatever task is live in the session.",
    )
    require(
        "insert_card_action(" in continued,
        "continue_from_card no longer opens a task for the click.",
    )
    require(
        "is_card_action(" in py_block(read("backend/app/services/task_run.py"), "def _insert_running"),
        "a new chat turn stops a card click.",
    )
    require(
        "sourceMessageId" in brace_slice(card_src, "async function useSlot"),
        "useSlot no longer names the stored card.",
    )
    require(
        "sourceMessageId" in brace_slice(card_src, "async function reply"),
        "reply no longer names the stored card.",
    )
    require(
        "open_schedule_claim(" not in api,
        "calendar.py still opens a claim without a fingerprint.",
    )
    update_event = py_block(local, "def update_event")
    if "status" in update_event:
        require(
            not returns_early_on_cancelled_status(update_event),
            "update_event returns early on a cancelled status.",
        )
    gate = gate_enabled(card_fn)
    lock = lock_enabled(confirm)
    if gate:
        stripped = card_fn.replace(SUBMIT_DISABLED, "", 1)
        if gate_enabled(stripped):
            fail_model("removing the submit disabled expression did not flip SUBMIT_GATE.")
    if lock:
        stripped = confirm.replace(CONFIRMED_PHASE, "")
        if lock_enabled(stripped):
            fail_model('removing phase: "confirmed" from confirm did not flip CONFIRMED_LOCK.')
    return gate, lock


def write_cfg(
    directory: Path,
    submit_gate: bool,
    confirmed_lock: bool,
    token_required: bool,
    invariants: tuple[str, ...],
) -> Path:
    lines = [
        "SPECIFICATION Spec",
        "CONSTANT",
        f"  SUBMIT_GATE = {'TRUE' if submit_gate else 'FALSE'}",
        f"  CONFIRMED_LOCK = {'TRUE' if confirmed_lock else 'FALSE'}",
        f"  TOKEN_REQUIRED = {'TRUE' if token_required else 'FALSE'}",
    ]
    for name in invariants:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "ScheduleConfirm.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def violated(output: str) -> list[str]:
    return re.findall(r"Invariant (\w+) is violated", output)


def run_tlc(
    submit_gate: bool,
    confirmed_lock: bool,
    token_required: bool,
    invariants: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH. Install a JDK 21 and rerun.")
    with tempfile.TemporaryDirectory(prefix="schedule-confirm-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, submit_gate, confirmed_lock, token_required, invariants)
        shutil.copy(SPEC_DIR / "ScheduleConfirm.tla", meta / "ScheduleConfirm.tla")
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
                "ScheduleConfirm.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def tlc_text(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def state_count(output: str) -> str:
    match = re.search(r"(\d+) states generated, (\d+) distinct states found", output)
    if match:
        return f"{match.group(1)} states generated, {match.group(2)} distinct"
    generated = re.search(r"(\d+) states generated", output)
    return f"{generated.group(1)} states generated" if generated else "unknown states generated"


def behavior(output: str) -> str:
    marker = "Error: The behavior up to this point is:"
    start = output.find(marker)
    if start < 0:
        return output.strip()
    end = output.find("states generated", start)
    if end < 0:
        return output[start:].strip()
    line_end = output.rfind("\n", start, end)
    return output[start:line_end].strip()


def classify(result: subprocess.CompletedProcess[str], output: str) -> list[str]:
    if "Deadlock reached" in output:
        print(output)
        fail_model("TLC deadlocked.")
    broken = violated(output)
    health = [name for name in broken if name in HEALTH]
    if health:
        print(output)
        fail_model(f"TLC violated {health[0]}. Fix the model before calling this a product bug.")
    if result.returncode != 0 and not broken and "No error has been found" not in output:
        print(output)
        fail_model(f"TLC exited {result.returncode} without a property violation.")
    return broken


def run_claim(break_name: str, invariants: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[str], str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH. Install a JDK 21 and rerun.")
    with tempfile.TemporaryDirectory(prefix="claim-gate-tlc-") as tmp:
        meta = Path(tmp)
        lines = [
            "SPECIFICATION Spec",
            "CONSTANT",
            f'  BREAK = "{break_name}"',
        ]
        for name in invariants:
            lines.append(f"INVARIANT {name}")
        lines.append("")
        cfg = meta / "ClaimGate.cfg"
        cfg.write_text("\n".join(lines), encoding="utf-8")
        shutil.copy(SPEC_DIR / "ClaimGate.tla", meta / "ClaimGate.tla")
        try:
            result = subprocess.run(
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
                    "ClaimGate.tla",
                ],
                cwd=meta,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail_model("claim TLC exceeded 120 seconds.")
    return result, tlc_text(result)


def run_continue(break_name: str, invariants: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[str], str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH. Install a JDK 21 and rerun.")
    with tempfile.TemporaryDirectory(prefix="card-continue-tlc-") as tmp:
        meta = Path(tmp)
        lines = [
            "SPECIFICATION Spec",
            "CONSTANT",
            f'  BREAK = "{break_name}"',
        ]
        for name in invariants:
            lines.append(f"INVARIANT {name}")
        lines.append("")
        cfg = meta / "CardContinue.cfg"
        cfg.write_text("\n".join(lines), encoding="utf-8")
        shutil.copy(SPEC_DIR / "CardContinue.tla", meta / "CardContinue.tla")
        try:
            result = subprocess.run(
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
                    "CardContinue.tla",
                ],
                cwd=meta,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail_model("continuation TLC exceeded 120 seconds.")
    return result, tlc_text(result)


def invoke(
    submit_gate: bool,
    confirmed_lock: bool,
    token_required: bool,
    invariants: tuple[str, ...],
) -> tuple[subprocess.CompletedProcess[str], str]:
    try:
        result = run_tlc(submit_gate, confirmed_lock, token_required, invariants)
    except subprocess.TimeoutExpired:
        fail_model("TLC exceeded 120 seconds.")
    return result, tlc_text(result)


def require_named_violation(result: subprocess.CompletedProcess[str], output: str, expected: str) -> None:
    broken = classify(result, output)
    if broken == [expected]:
        print(behavior(output))
        print(f"{expected} violated, as required. {state_count(output)}.")
        raise SystemExit(1)
    if "No error has been found" in output:
        print(output)
        fail_model(f"{expected} holds. A reachable write was dropped, or the constant did not change the model.")
    print(output)
    fail_model(f"expected {expected} violated and no other, got {broken}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the schedule confirm model.")
    parser.add_argument(
        "--break-submit-gate",
        action="store_true",
        help="Treat the submit button as enabled while busy.",
    )
    parser.add_argument(
        "--break-confirmed-lock",
        action="store_true",
        help="Treat a successful confirm as leaving phase unchanged.",
    )
    parser.add_argument(
        "--break-tokenless",
        action="store_true",
        help="Let a calendar write proceed when the card has no token.",
    )
    parser.add_argument(
        "--break-claim",
        choices=tuple(CLAIM_BREAKS),
        help="Enable one broken claim transition and require its invariant to fail.",
    )
    args = parser.parse_args()
    chosen = [
        name
        for name, enabled in (
            ("--break-submit-gate", args.break_submit_gate),
            ("--break-confirmed-lock", args.break_confirmed_lock),
            ("--break-tokenless", args.break_tokenless),
            ("--break-claim", args.break_claim is not None),
        )
        if enabled
    ]
    if len(chosen) > 1:
        fail_model("pass only one break flag.")
    require_definitions()
    claim_spec = read("verification/schedule-confirm/ClaimGate.tla")
    for name in CLAIM_HELD:
        if not re.search(rf"^{re.escape(name)} ==", claim_spec, re.M):
            fail_model(f"ClaimGate.tla has no {name} == definition.")
    submit_gate, confirmed_lock = check_anchors()

    def checked(lock_on: bool) -> tuple[str, ...]:
        return HELD if lock_on else LOCK_BROKEN

    if not submit_gate:
        result, output = invoke(False, confirmed_lock, True, checked(confirmed_lock))
        require_named_violation(result, output, "NoReentryWhileBusy")
    if not confirmed_lock:
        result, output = invoke(True, False, True, checked(False))
        require_named_violation(result, output, "SecondWriteImpliesRestore")
    if args.break_submit_gate:
        result, output = invoke(False, confirmed_lock, True, checked(confirmed_lock))
        require_named_violation(result, output, "NoReentryWhileBusy")
    if args.break_confirmed_lock:
        result, output = invoke(submit_gate, False, True, checked(False))
        require_named_violation(result, output, "SecondWriteImpliesRestore")
    if args.break_tokenless:
        result, output = invoke(True, True, False, HELD)
        require_named_violation(result, output, "NoWriteWithoutToken")
    if args.break_claim == "borrow":
        result, output = run_continue("borrow", CONTINUE_HELD)
        require_named_violation(result, output, "NoBorrow")
    elif args.break_claim:
        result, output = run_claim(args.break_claim, CLAIM_HELD)
        require_named_violation(result, output, CLAIM_BREAKS[args.break_claim])
    held_result, held_output = invoke(True, True, True, HELD)
    held_broken = classify(held_result, held_output)
    if held_broken:
        print(behavior(held_output))
        print(
            "property failed on code that still has the submit gate and the confirmed phase. "
            "Replay the trace as clicks and handler calls. "
            "If a step cannot happen, this is a modeling error.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if "No error has been found" not in held_output:
        print(held_output)
        fail_model("the held run did not report that no error has been found.")
    claim_result, claim_output = run_claim("none", CLAIM_HELD)
    claim_broken = classify(claim_result, claim_output)
    if claim_broken:
        print(behavior(claim_output))
        print(
            "claim property failed on the unmutated model. "
            "Replay the trace against issue_schedule_claim and consume_schedule_claim.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if "No error has been found" not in claim_output:
        print(claim_output)
        fail_model("the claim run did not report that no error has been found.")
    continue_spec = read("verification/schedule-confirm/CardContinue.tla")
    for name in CONTINUE_HELD:
        if not re.search(rf"^{re.escape(name)} ==", continue_spec, re.M):
            fail_model(f"CardContinue.tla has no {name} == definition.")
    continue_result, continue_output = run_continue("none", CONTINUE_HELD)
    continue_broken = classify(continue_result, continue_output)
    if continue_broken:
        print(behavior(continue_output))
        print(
            "continuation property failed on the unmutated model. "
            "Replay the trace against continue_from_card and begin_turn.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if "No error has been found" not in continue_output:
        print(continue_output)
        fail_model("the continuation run did not report that no error has been found.")
    print(
        "pass: NoWriteBeforeConfirm, NoReentryWhileBusy, SecondWriteImpliesRestore, "
        "AtMostOneSuccessfulWrite, NoWriteAfterRetire, and NoWriteWithoutToken hold. "
        f"{state_count(held_output)}. "
        "claim: AtMostOneSuccessfulWrite, NoWriteWithoutToken, WriteMatchedFingerprint, "
        "WriteSawLiveGeneration, OpenClaimHasLiveGeneration, AtMostOneOpenRow, and OwnerWrites hold. "
        f"{state_count(claim_output)}. "
        "continuation: NoBorrow and UnrelatedStopKeepsClick hold. "
        f"{state_count(continue_output)}. TLC {TLA_VERSION}."
    )


if __name__ == "__main__":
    main()
