#!/usr/bin/env python3

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "current-calendar-flows.md"

ANCHORS: tuple[tuple[str, str, str], ...] = (
    ("backend/app/api/chat.py", "chat", "def"),
    ("backend/app/api/chat.py", "publish_schedule_claim", "call"),
    ("backend/app/agents/router.py", "plan_tools", "def"),
    ("backend/app/agents/router.py", "route", "def"),
    ("backend/app/agents/router.py", "intent_from_decision", "def"),
    ("backend/app/agents/workflow.py", "run_agent_workflow", "def"),
    ("backend/app/agents/workflow.py", "retrieve_node", "def"),
    ("backend/app/agents/workflow.py", "answer_node", "def"),
    ("backend/app/agents/harness/gateway.py", "invoke", "def"),
    ("backend/app/agents/tools/calendar_tool.py", "execute_calendar", "def"),
    ("backend/app/agents/tools/calendar_tool.py", "classify_calendar_action", "call"),
    ("backend/app/agents/tools/calendar_tool.py", "_unbound_mutate", "def"),
    ("backend/app/agents/tools/calendar_tool.py", "_list_card", "def"),
    ("backend/app/agents/tools/calendar_tool.py", "_fill_conflicts", "def"),
    ("backend/app/services/calendar.py", "CalendarAction", "class"),
    ("backend/app/services/calendar.py", "parse_action", "def"),
    ("backend/app/services/calendar.py", "classify_calendar_action", "def"),
    ("backend/app/services/calendar.py", "draft_target", "def"),
    ("backend/app/services/calendar.py", "pick_event", "def"),
    ("backend/app/services/calendar.py", "void_draft_schedule_cards", "def"),
    ("backend/app/services/calendar.py", "BindCreate", "class"),
    ("backend/app/services/calendar.py", "GoogleConfirm", "class"),
    ("backend/app/services/calendar.py", "LocalDraft", "class"),
    ("backend/app/services/calendar.py", "confirm_titled_change", "def"),
    ("backend/app/services/schedule_confirm.py", "schedule_card_needs_claim", "def"),
    ("backend/app/services/schedule_confirm.py", "publish_schedule_claim", "def"),
    ("backend/app/services/schedule_confirm.py", "consume_schedule_claim", "def"),
    ("backend/app/services/schedule_confirm.py", "release_schedule_claim", "def"),
    ("backend/app/services/schedule_confirm.py", "cancel_open_schedule_claims", "def"),
    ("backend/app/services/calendar_factory.py", "calendar_provider_for", "def"),
    ("backend/app/services/calendar_factory.py", "calendar_writes_remote", "def"),
    ("backend/app/services/calendar_google.py", "create_event", "def"),
    ("backend/app/services/calendar_google.py", "update_event", "def"),
    ("backend/app/api/calendar.py", "create_calendar_event", "def"),
    ("backend/app/api/calendar.py", "patch_event", "def"),
    ("backend/app/api/calendar.py", "delete_event", "def"),
    ("backend/app/api/calendar.py", "confirm_google_title_change", "def"),
    ("backend/app/api/calendar.py", "rsvp_google_event", "def"),
    ("backend/app/api/calendar.py", "google_calendar_status", "def"),
    ("backend/app/api/calendar.py", "google_calendar_connect", "def"),
    ("backend/app/api/calendar.py", "google_calendar_callback", "def"),
    ("backend/app/services/calendar_secrets.py", "connection_status", "def"),
    ("backend/app/services/calendar_secrets.py", "_status_body", "def"),
    ("backend/app/services/calendar_secrets.py", "connect_google_account", "def"),
    ("backend/app/services/calendar_secrets.py", "_require_scopes", "def"),
    ("backend/app/services/calendar_secrets.py", "build_authorize_url", "def"),
    ("backend/app/services/calendar_secrets.py", "disconnect_google", "def"),
    ("backend/app/services/meeting_prep.py", "prepare_meeting", "def"),
    ("backend/app/services/meeting_prep.py", "to_wire", "def"),
    ("backend/app/services/meeting_prep.py", "pointed_utterance", "def"),
    ("backend/app/services/gmail.py", "search_meeting_mail", "def"),
    ("backend/app/services/drive.py", "search_meeting_docs", "def"),
    ("backend/app/services/drive.py", "read_official_minutes", "def"),
    ("backend/app/services/meeting_followup.py", "follow_up", "def"),
    ("backend/app/services/meeting_followup.py", "to_wire", "def"),
    ("backend/app/services/meeting_followup.py", "follow_utterance", "def"),
    ("frontend/src/services/chatStream.ts", "voidOpenDrafts", "fn"),
    ("frontend/src/services/chatStream.ts", "prepareRegeneratePlaceholder", "fn"),
    ("frontend/src/components/ScheduleCard.tsx", "confirm", "fn"),
    ("frontend/src/components/ScheduleCard.tsx", "useSlot", "fn"),
    ("frontend/src/components/ScheduleCard.tsx", "declineCancel", "fn"),
    ("frontend/src/components/ScheduleCard.tsx", "chooseCandidate", "fn"),
    ("frontend/src/components/FollowupCard.tsx", "confirm", "fn"),
    ("frontend/src/components/FollowupCard.tsx", "postFollowAction", "fn"),
    ("frontend/src/utils/followMail.ts", "claimFollowRows", "fn"),
    ("frontend/src/utils/followMail.ts", "formatFollowUtterance", "fn"),
    ("frontend/src/api/client.ts", "formatPointedUtterance", "fn"),
    ("frontend/src/api/client.ts", "connectGoogle", "fn"),
    ("frontend/src/utils/briefFromWire.ts", "consentLabel", "fn"),
    ("frontend/src/utils/scheduleExpiry.ts", "isExpired", "fn"),
    ("frontend/src/components/MeetingBriefCard.tsx", "MeetingBriefCard", "fn"),
    ("frontend/src/components/SessionSidebar.tsx", "mail_access", "text"),
    ("frontend/src/components/ChatWindow.tsx", "onAsk", "text"),
)


def py_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                found.add(func.id)
            elif isinstance(func, ast.Attribute):
                found.add(func.attr)
    return found


def ts_has(text: str, name: str, kind: str) -> bool:
    if kind == "text":
        return name in text
    needles = (
        f"function {name}",
        f"async function {name}",
        f"export function {name}",
        f"export async function {name}",
    )
    return any(item in text for item in needles)


def main() -> int:
    doc = DOC.read_text(encoding="utf-8")
    missing: list[str] = []
    for rel, name, kind in ANCHORS:
        path = ROOT / rel
        if not path.is_file():
            missing.append(f"missing file {rel}")
            continue
        if f"`{name}`" not in doc and name not in doc:
            missing.append(f"doc omits {name}")
        if rel.endswith(".py"):
            names = py_names(path)
            if kind == "class" and name not in names:
                missing.append(f"{rel} has no class {name}")
            elif kind in {"def", "call"} and name not in names:
                missing.append(f"{rel} has no {name}")
        else:
            text = path.read_text(encoding="utf-8")
            if not ts_has(text, name, kind):
                missing.append(f"{rel} has no {kind} {name}")
    if missing:
        print("\n".join(missing))
        return 1
    print(f"pass: {len(ANCHORS)} current-flow anchors still match the cited files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
