#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = Path(__file__).resolve().parent

TLA_VERSION = "1.8.0"
JAR_URL = f"https://github.com/tlaplus/tlaplus/releases/download/v{TLA_VERSION}/tla2tools.jar"
# GitHub replaces v1.8.0/tla2tools.jar without moving the tag. Pin the release asset digest.
JAR_SHA256 = "fc6878c603f0fdc18b9fe3e5bf51860cb90ab5b1154e3c371660c036d6bdd63e"
JAR_CACHE = SPEC_DIR / ".tools" / "tla2tools.jar"

PROPERTY = ("AtMostOneEvent", "NoWriteBeforeConfirm", "NeedsPasteWritesNothing")
MODEL_HEALTH = ("TypeOK", "OneRequestPerLoop")

CLAIM_GUARD = re.compile(r"if\s*\(\s*inflight\.current\s*\)\s*return\s*\[\s*\]\s*;")


def fail_model(message: str) -> None:
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
    return ""


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def has_before(text: str, earlier: str, later: str) -> bool:
    if earlier not in text or later not in text:
        return False
    return text.index(earlier) < text.index(later)


def require_before(text: str, earlier: str, later: str, message: str) -> None:
    if earlier not in text or later not in text or text.index(earlier) > text.index(later):
        fail_model(message)


def claim_is_atomic(claim_src: str) -> bool:
    require("await" not in claim_src, "claimFollowRows now awaits. It is no longer one step.")
    require(
        "inflight.current = true" in claim_src,
        "claimFollowRows no longer sets inflight.current = true.",
    )
    return CLAIM_GUARD.search(claim_src) is not None


def check_anchors() -> tuple[bool, bool]:
    follow_mail = read("frontend/src/utils/followMail.ts")
    card = read("frontend/src/components/FollowupCard.tsx")
    expiry = read("frontend/src/utils/scheduleExpiry.ts")
    chat = read("frontend/src/services/chatStream.ts")
    follow = read("backend/app/services/meeting_followup.py")
    api = read("backend/app/api/calendar.py")
    calendar = read("backend/app/services/calendar.py")

    claim = brace_slice(follow_mail, "export function claimFollowRows")
    confirm = brace_slice(card, "async function confirm")
    post = brace_slice(card, "async function postFollowAction")
    copy_mail = brace_slice(card, "function copyFollowMail")
    card_fn = brace_slice(card, "export function FollowupCard")

    require("if (expired) return;" in confirm, "confirm no longer returns on expired.")
    require_before(
        confirm,
        "claimFollowRows",
        "postFollowAction",
        "confirm no longer claims before postFollowAction.",
    )
    require_before(
        confirm,
        "await postFollowAction",
        "done.current.add",
        "done.current.add is no longer after a successful postFollowAction.",
    )
    require(
        "ticked.has(action.id) && action.start_at" in confirm,
        "confirm no longer limits the claim to ticked rows with start_at.",
    )
    require('card.phase === "draft"' in card_fn, "the confirm button is no longer gated on phase draft.")
    require("disabled={posting || expired}" in card_fn, "the confirm button no longer disables while posting or expired.")
    require("void confirm()" in card_fn, "the draft button no longer calls confirm.")
    require("phase" not in post, "postFollowAction now mentions phase.")
    require("createCalendarEvent" not in copy_mail, "copyFollowMail now creates a calendar event.")
    require("gmail.send" not in card, "FollowupCard now mentions gmail.send.")
    require("gmail.send" not in follow, "meeting_followup now mentions gmail.send.")
    require('card.phase !== "draft"' in brace_slice(expiry, "export function isExpired"), "isExpired no longer ignores a card that is not draft.")
    void_open = brace_slice(chat, "function voidOpenDrafts")
    require(
        'card.phase !== "draft"' in void_open and 'phase: "cancelled"' in void_open,
        "voidOpenDrafts no longer cancels a draft card.",
    )
    void_draft = py_block(calendar, "def void_draft_schedule_cards")
    require(
        'card["phase"] = "cancelled"' in void_draft,
        "void_draft_schedule_cards no longer sets phase to cancelled.",
    )
    follow_up = py_block(follow, "def follow_up")
    require("create_event" not in follow_up, "follow_up now calls create_event.")
    write_body = py_block(api, "class CalendarWrite")
    require(not re.search(r"^\s*phase\s*:", write_body, re.M), "CalendarWrite now has a phase field.")
    require(
        not re.search(r"^\s*expires_at\s*:", write_body, re.M),
        "CalendarWrite now has an expires_at field.",
    )
    create = py_block(api, "def create_calendar_event")
    require("phase" not in create and "expires_at" not in create, "create_calendar_event now reads phase or expires_at.")
    google = py_block(read("backend/app/services/calendar_google.py"), "def create_event(")
    atomic = claim_is_atomic(claim)
    if atomic:
        stripped = CLAIM_GUARD.sub("", claim, count=1)
        if stripped == claim or claim_is_atomic(stripped):
            fail_model("removing the inflight return did not flip the model.")
    overlap = has_before(create, "check_conflict(", "create_event(") and has_before(
        google, "conflicts_for_create(", "_insert_google_event("
    )
    if overlap:
        route_without_check = create.replace("check_conflict(", "", 1)
        google_without_check = google.replace("conflicts_for_create(", "", 1)
        if has_before(route_without_check, "check_conflict(", "create_event(") and has_before(
            google_without_check, "conflicts_for_create(", "_insert_google_event("
        ):
            fail_model("removing the overlap checks did not flip the model.")
    return atomic, overlap


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def jar_path() -> Path:
    if JAR_CACHE.is_file() and sha256(JAR_CACHE) == JAR_SHA256:
        return JAR_CACHE
    JAR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JAR_CACHE.with_suffix(".jar.partial")
    urllib.request.urlretrieve(JAR_URL, tmp)
    if sha256(tmp) != JAR_SHA256:
        tmp.unlink(missing_ok=True)
        fail_model(f"tla2tools.jar sha256 does not match the {TLA_VERSION} pin")
    tmp.replace(JAR_CACHE)
    return JAR_CACHE


def write_cfg(directory: Path, atomic: bool, overlap: bool) -> Path:
    path = directory / "FollowupConfirm.cfg"
    path.write_text(
        "\n".join(
            [
                "SPECIFICATION Spec",
                "CONSTANT",
                f"  ATOMIC_CLAIM = {'TRUE' if atomic else 'FALSE'}",
                f"  OVERLAP_CHECK = {'TRUE' if overlap else 'FALSE'}",
                "INVARIANT TypeOK",
                "INVARIANT OneRequestPerLoop",
                "INVARIANT AtMostOneEvent",
                "INVARIANT NoWriteBeforeConfirm",
                "INVARIANT NeedsPasteWritesNothing",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def violated(output: str) -> list[str]:
    return re.findall(r"Invariant (\w+) is violated", output)


def run_tlc(atomic: bool, overlap: bool) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH. Install a JDK 21 and rerun.")
    with tempfile.TemporaryDirectory(prefix="followup-confirm-tlc-") as tmp:
        meta = Path(tmp)
        cfg = write_cfg(meta, atomic, overlap)
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
                "FollowupConfirm.tla",
            ],
            cwd=SPEC_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def tlc_text(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def state_count(output: str) -> str:
    match = re.search(r"(\d+) states generated", output)
    return match.group(1) if match else "unknown"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the follow-up confirm model.")
    parser.add_argument(
        "--break-claim",
        action="store_true",
        help="Treat claimFollowRows as if it did not return while inflight is set.",
    )
    parser.add_argument(
        "--drop-overlap",
        action="store_true",
        help="Treat create as if it inserted without an overlap check.",
    )
    args = parser.parse_args()
    if args.break_claim and args.drop_overlap:
        fail_model("pass only one of --break-claim and --drop-overlap.")
    atomic, overlap = check_anchors()
    if args.break_claim:
        atomic = False
    if args.drop_overlap:
        overlap = False
    try:
        result = run_tlc(atomic, overlap)
    except subprocess.TimeoutExpired:
        fail_model("TLC exceeded 120 seconds.")
    output = tlc_text(result)
    broken = violated(output)
    health = [name for name in broken if name in MODEL_HEALTH]
    props = [name for name in broken if name in PROPERTY]
    if health:
        print(output)
        fail_model(f"TLC violated {health[0]}. Fix the model before calling this a product bug.")
    if result.returncode != 0 and not props and "No error has been found" not in output:
        print(output)
        fail_model(f"TLC exited {result.returncode} without a property violation.")
    demo = args.break_claim or args.drop_overlap
    if demo:
        if not props:
            print(output)
            fail_model("the weakened model did not violate a property. The model is too weak.")
        print(behavior(output))
        label = "break-claim" if args.break_claim else "drop-overlap"
        print(f"{label}: {props[0]} violated, as required. {state_count(output)} states generated.")
        return
    if props:
        print(behavior(output))
        if not overlap:
            print(
                "property failed: the overlap check no longer sits before the insert. "
                "A lost response then lets confirm post the same row again. "
                "If the check still runs under another name, this is a modeling error.",
                file=sys.stderr,
            )
        elif atomic:
            print(
                "property failed on code that still has the inflight guard and the overlap check. "
                "Replay the trace against FollowupCard.confirm and create_calendar_event. "
                "If a step cannot happen, this is a modeling error.",
                file=sys.stderr,
            )
        else:
            print(
                "property failed: claimFollowRows no longer returns when inflight.current is set, "
                "and a second confirm can insert a second event.",
                file=sys.stderr,
            )
        raise SystemExit(1)
    if not atomic:
        print(output)
        fail_model("the inflight guard is gone and TLC still passed. The model is too weak.")
    if not overlap:
        print(output)
        fail_model("the overlap check is gone and TLC still passed. The model is too weak.")
    print(f"pass: follow-up confirm holds. {state_count(output)} states generated. TLC {TLA_VERSION}.")


if __name__ == "__main__":
    main()
