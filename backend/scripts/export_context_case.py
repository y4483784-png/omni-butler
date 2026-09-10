#!/usr/bin/env python3
"""Export a real chat session into a context-replay case JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal, init_db
from app.models.models import Message
from app.models.models import Session as ChatSession

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "eval" / "context_replays"


def export_session(session_id: int, *, question: str, gold_keys: list[str], out: Path) -> Path:
    init_db()
    db = SessionLocal()
    try:
        row = db.get(ChatSession, session_id)
        if row is None:
            raise SystemExit(f"session {session_id} not found")
        msgs = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.id)
            .all()
        )
        history = [{"role": m.role, "content": m.content or "", "is_needle": False} for m in msgs]
        pending = None
        raw = getattr(row, "pending_calendar", "") or ""
        if str(raw).strip():
            try:
                pending = json.loads(raw)
            except Exception:
                pending = None
        case = {
            "id": f"replay_session_{session_id}",
            "split": "smoke",
            "type": "user_fact",
            "bucket_turns": max(1, len(history) // 2),
            "depth_pct": 0,
            "seed": 0,
            "history": history,
            "pending_calendar": pending,
            "evidence_pool": None,
            "probe": {
                "question": question,
                "gold_answer": ",".join(gold_keys),
                "gold_keys": gold_keys,
                "judge_mode": "strict",
                "expect_abstain": False,
            },
            "tags": ["online_export", f"session_{session_id}"],
            "meta": {
                "context_summary": getattr(row, "context_summary", "") or "",
                "context_summary_upto_message_id": int(
                    getattr(row, "context_summary_upto_message_id", 0) or 0
                ),
            },
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export session to context replay case")
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--question", required=True, help="Probe question for the exported case")
    parser.add_argument("--gold-keys", default="", help="Comma-separated gold keys")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    keys = [k.strip() for k in str(args.gold_keys).split(",") if k.strip()]
    out = args.out or (OUT_DIR / f"session_{args.session_id}.json")
    path = export_session(args.session_id, question=args.question, gold_keys=keys, out=out)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
