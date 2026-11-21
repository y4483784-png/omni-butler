#!/usr/bin/env python3

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "envisioned-calendar-flows.md"
CURRENT = ROOT / "docs" / "current-calendar-flows.md"

ANCHORS: tuple[tuple[str, str, str], ...] = (
    ("backend/app/api/chat.py", "chat", "def"),
    ("backend/app/api/chat.py", "_prepare_session_context", "def"),
    ("backend/app/agents/router.py", "plan_tools", "def"),
    ("backend/app/agents/router.py", "_decision_to_plan", "def"),
    ("backend/app/agents/router.py", "RouterDecision", "class"),
    ("backend/app/agents/workflow.py", "plan_node", "def"),
    ("backend/app/agents/workflow.py", "retrieve_node", "def"),
    ("backend/app/agents/workflow.py", "answer_node", "def"),
    ("backend/app/agents/tools/calendar_tool.py", "execute_calendar", "def"),
    ("backend/app/agents/tools/calendar_tool.py", "_base_card", "def"),
    ("backend/app/services/calendar.py", "classify_calendar_action", "def"),
    ("backend/app/services/calendar.py", "parse_action", "def"),
    ("backend/app/services/calendar.py", "unclear_action_reply", "def"),
    ("backend/app/services/calendar.py", "void_draft_schedule_cards", "def"),
    ("backend/app/services/calendar.py", "upcoming_events", "def"),
    ("backend/app/services/calendar.py", "draft_target", "def"),
    ("backend/app/services/calendar.py", "pick_event", "def"),
    ("backend/app/models/models.py", "pending_calendar", "text"),
    ("backend/app/services/schedule_confirm.py", "publish_schedule_claim", "def"),
    ("backend/app/services/schedule_confirm.py", "consume_schedule_claim", "def"),
    ("backend/app/services/schedule_confirm.py", "release_schedule_claim", "def"),
    ("backend/app/services/meeting_prep.py", "prepare_meeting", "def"),
    ("backend/app/services/meeting_prep.py", "pointed_utterance", "def"),
    ("backend/app/services/meeting_prep.py", "_hint", "def"),
    ("backend/app/services/gmail.py", "search_meeting_mail", "def"),
    ("backend/app/services/drive.py", "search_meeting_docs", "def"),
    ("backend/app/services/drive.py", "read_official_minutes", "def"),
    ("backend/app/services/meeting_followup.py", "follow_up", "def"),
    ("backend/app/services/meeting_followup.py", "follow_utterance", "def"),
    ("backend/app/services/meeting_followup.py", "to_wire", "def"),
    ("backend/app/api/calendar.py", "create_calendar_event", "def"),
    ("backend/app/api/calendar.py", "patch_event", "def"),
    ("backend/app/api/calendar.py", "delete_event", "def"),
    ("backend/app/api/calendar.py", "confirm_google_title_change", "def"),
    ("backend/app/api/calendar.py", "rsvp_google_event", "def"),
    ("backend/app/services/calendar_secrets.py", "connection_status", "def"),
    ("backend/app/services/calendar_secrets.py", "connect_google_account", "def"),
    ("backend/app/services/calendar_secrets.py", "disconnect_google", "def"),
    ("frontend/src/services/chatStream.ts", "voidOpenDrafts", "fn"),
    ("frontend/src/services/chatStream.ts", "prepareRegeneratePlaceholder", "fn"),
    ("frontend/src/api/client.ts", "formatPointedUtterance", "fn"),
    ("frontend/src/api/client.ts", "openConfirmClaim", "fn"),
    ("frontend/src/utils/followMail.ts", "formatFollowUtterance", "fn"),
    ("frontend/src/utils/followMail.ts", "claimFollowRows", "fn"),
    ("frontend/src/utils/followMail.ts", "mailtoHref", "fn"),
    ("frontend/src/components/ScheduleCard.tsx", "useSlot", "fn"),
    ("frontend/src/components/FollowupCard.tsx", "postFollowAction", "fn"),
)

PHRASES = (
    "docs/current-calendar-flows.md",
    "pending_calendar",
    "observe",
    "draft",
    "mandate",
    "ScheduleConfirmClaim",
    "prepare_meeting",
    "follow_up",
    "Role.PLANNER",
    "Role.CHAT",
)

FORBIDDEN = (
    "backend/app/tickets",
    "新仓库",
    "新服务",
)


def py_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
    return found


def ts_has(text: str, name: str) -> bool:
    needles = (
        f"function {name}",
        f"async function {name}",
        f"export function {name}",
        f"export async function {name}",
    )
    return any(item in text for item in needles)


def main() -> int:
    missing: list[str] = []
    if not CURRENT.is_file():
        missing.append("missing current map")
    if not DOC.is_file():
        print("missing envisioned map")
        return 1
    doc = DOC.read_text(encoding="utf-8")
    for phrase in PHRASES:
        if phrase not in doc:
            missing.append(f"doc omits {phrase}")
    for banned in FORBIDDEN:
        if banned in doc:
            missing.append(f"doc contains {banned}")
    for rel, name, kind in ANCHORS:
        path = ROOT / rel
        if not path.is_file():
            missing.append(f"missing file {rel}")
            continue
        if name not in doc:
            missing.append(f"doc omits {name}")
        if kind == "text":
            if name not in path.read_text(encoding="utf-8"):
                missing.append(f"{rel} has no {name}")
            continue
        if rel.endswith(".py"):
            if name not in py_names(path):
                missing.append(f"{rel} has no {kind} {name}")
        elif not ts_has(path.read_text(encoding="utf-8"), name):
            missing.append(f"{rel} has no function {name}")
    if missing:
        print("\n".join(missing))
        return 1
    print(f"pass: envisioned map stays on {len(ANCHORS)} current anchors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
