from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.harness.types import ToolContext
from app.agents.tools.calendar_tool import execute_calendar
from app.api.calendar import CalendarWrite
from app.core.db import SessionLocal, init_db
from app.core.prompts import router_system
from app.eval.calendar_behavior import CARD_TYPES, card_type
from app.models.models import CalendarConnection, CalendarEvent
from app.services.calendar import draft_expires_at, parse_action
from app.services.calendar import _classify_prompt
from app.services.calendar_secrets import CalendarTransientError
from app.services.meeting_followup import (
    ActionItem,
    FollowMail,
    PastedMinutes,
    follow_up,
    follow_utterance,
)

_CAPTION = "把纪要或转写贴在这条消息里。这次没有整理待办，也没有编造纪要。"
_SPAN = "张三周五前发报价"
_NOW = datetime(2026, 10, 22, 8, 0)
_SEED = Path(__file__).resolve().parents[1] / "data" / "eval" / "seeds" / "routing_utterances.jsonl"
_FEW_SHOTS = (
    "输入：帮我整理一下这段会议纪要 | → needs_calendar=true",
    "输入：会后跟进一下昨天的客户会 | → needs_calendar=true",
    "输入：把这段转写整理成待办 | → needs_calendar=true",
)


def _pad(text: str) -> str:
    if len(text) < 80:
        text += "。" * (80 - len(text))
    return text


def _count(db, user_id: int) -> int:
    return db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).count()


def _user_id(auth_client: TestClient) -> int:
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200
    return me.json()["id"]


def _use_local_calendar(db, user_id: int) -> None:
    db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).delete()
    db.commit()


def _freeze_now(monkeypatch, when: datetime) -> None:
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.calendar_now",
        lambda *_args, **_kwargs: when,
    )


def test_follow_paste_writes_nothing_before_confirm(auth_client: TestClient, monkeypatch):
    paste = _pad(f"会议记录。{_SPAN}。请把报价发给客户，并确认范围和交付时间。")
    assert _SPAN in paste and len(paste) >= 80
    invented = "不在纪要里的待办"

    def _model(*_args, **_kwargs):
        return {
            "points": [_SPAN, "编造的要点"],
            "actions": [
                {
                    "text": _SPAN,
                    "owner": "张三",
                    "due_said": "周五",
                    "evidence": _SPAN,
                    "start_at": "1999-01-01T00:00:00",
                },
                {"text": invented, "owner": "", "due_said": "", "evidence": invented},
            ],
        }

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.extract_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("extract_event")),
    )
    _freeze_now(monkeypatch, _NOW)
    init_db()
    db = SessionLocal()
    user_id = _user_id(auth_client)
    try:
        _use_local_calendar(db, user_id)
        before = _count(db, user_id)
        result = execute_calendar(
            ToolContext(
                db=db,
                message=paste,
                history=[{"role": "user", "content": invented}],
                user_id=user_id,
            ),
            {"action": "followup"},
        )
        card = result.schedule_card
        assert card["mode"] == "followup"
        assert card["phase"] == "draft"
        assert card["status"] == "ready"
        assert card["source"] == {"kind": "pasted"}
        assert card["expires_at"] == draft_expires_at(_NOW)
        assert card["points"] == [_SPAN]
        action = card["actions"][0]
        assert action["text"] == _SPAN
        assert action["owner"] == "张三"
        assert action["due_said"] == "周五"
        assert action["evidence"] == _SPAN
        assert action["start_at"] == "2026-10-23T09:00:00"
        assert action["end_at"] == "2026-10-23T10:00:00"
        assert len(card["actions"]) == 1
        assert card["mail"]["subject"] == "跟进：会议"
        assert card["mail"]["body"] == _SPAN
        assert card["mail"]["to"] == []
        for key in ("event_id", "external_id", "action", "rsvp"):
            assert key not in card
        blob = json.dumps(card, ensure_ascii=False)
        assert "编造的要点" not in blob
        assert invented not in blob
        assert "1999-01-01" not in blob
        assert card_type(card) == "followup"
        assert "followup" in CARD_TYPES
        assert _count(db, user_id) == before
        assert "create_event" not in follow_up.__code__.co_names
        assert "search_meeting_docs" not in follow_up.__code__.co_names
        source = Path(follow_up.__code__.co_filename).read_text(encoding="utf-8")
        assert "gmail.send" not in source
    finally:
        db.close()


def test_follow_drops_an_owner_absent_from_the_text(auth_client: TestClient, monkeypatch):
    body = _pad("会上只记录了要发报价这一件事，没有写负责人姓名。")
    assert "李四" not in body and "组织者" not in body
    message = f"{follow_utterance('客户会', '2026-10-22T15:00:00')}\n{body}"

    def _model(*_args, **_kwargs):
        return {
            "points": ["要发报价这一件事"],
            "actions": [
                {
                    "text": "要发报价这一件事",
                    "owner": "李四",
                    "due_said": "",
                    "evidence": "要发报价这一件事",
                }
            ],
        }

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    _freeze_now(monkeypatch, _NOW)
    init_db()
    db = SessionLocal()
    user_id = _user_id(auth_client)
    start = datetime(2026, 10, 22, 15, 0)
    event_id = None
    try:
        _use_local_calendar(db, user_id)
        row = CalendarEvent(
            user_id=user_id,
            title="客户会",
            start_at=start,
            end_at=start + timedelta(hours=1),
            participants=json.dumps(
                [
                    {"email": "lisi@example.com", "name": "李四"},
                    {"email": "org@example.com", "name": "组织者"},
                ],
                ensure_ascii=False,
            ),
            status="active",
        )
        db.add(row)
        db.commit()
        event_id = row.id
        before = _count(db, user_id)
        result = execute_calendar(
            ToolContext(db=db, message=message, history=[], user_id=user_id),
            {"action": "followup"},
        )
        card = result.schedule_card
        assert card["actions"][0]["owner"] == ""
        assert card["actions"][0]["text"] == "要发报价这一件事"
        assert card["actions"][0]["start_at"] == ""
        assert card["mail"]["subject"] == "跟进：客户会"
        assert card["mail"]["to"] == []
        assert card["phase"] == "info"
        assert "expires_at" not in card
        blob = json.dumps(card, ensure_ascii=False)
        assert "李四" not in blob
        assert "lisi@example.com" not in blob
        assert "org@example.com" not in blob
        assert _count(db, user_id) == before
    finally:
        if event_id is not None:
            stored = db.get(CalendarEvent, event_id)
            if stored is not None:
                db.delete(stored)
                db.commit()
        db.close()


def test_follow_recipients_match_paste_emails_and_exact_owners(auth_client: TestClient, monkeypatch):
    body = _pad(f"请抄送 ada@example.com。{_SPAN}。组织者没有被点名。")
    assert "org@example.com" not in body and "zhangsan@example.com" not in body
    message = f"{follow_utterance('客户会', '2026-10-22T15:00:00')}\n{body}"

    def _model(*_args, **_kwargs):
        return {
            "points": [_SPAN],
            "actions": [
                {"text": _SPAN, "owner": "张三", "due_said": "周五", "evidence": _SPAN}
            ],
        }

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    _freeze_now(monkeypatch, _NOW)
    init_db()
    db = SessionLocal()
    user_id = _user_id(auth_client)
    start = datetime(2026, 10, 22, 15, 0)
    event_id = None
    try:
        _use_local_calendar(db, user_id)
        row = CalendarEvent(
            user_id=user_id,
            title="客户会",
            start_at=start,
            end_at=start + timedelta(hours=1),
            participants=json.dumps(
                [
                    {"email": "zhangsan@example.com", "name": "张三"},
                    {"email": "org@example.com", "name": "组织者"},
                ],
                ensure_ascii=False,
            ),
            status="active",
        )
        db.add(row)
        db.commit()
        event_id = row.id
        result = execute_calendar(
            ToolContext(db=db, message=message, history=[], user_id=user_id),
            {"action": "followup"},
        )
        mail = result.schedule_card["mail"]
        assert mail["subject"] == "跟进：客户会"
        assert mail["body"] == _SPAN
        assert mail["to"] == [
            {"email": "ada@example.com", "name": ""},
            {"email": "zhangsan@example.com", "name": "张三"},
        ]
    finally:
        if event_id is not None:
            stored = db.get(CalendarEvent, event_id)
            if stored is not None:
                db.delete(stored)
                db.commit()
        db.close()


def test_short_note_is_not_minutes(monkeypatch):
    called = {"n": 0}

    def _model(*_args, **_kwargs):
        called["n"] += 1
        return {"points": [], "actions": []}

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    short = follow_up("会后跟进一下昨天的客户会", [], _NOW)
    assert called["n"] == 0
    assert short.card["status"] == "needs_paste"
    assert short.card["phase"] == "info"
    assert short.card["mode"] == "followup"
    assert short.caption == _CAPTION
    assert "id" not in short.card
    assert "actions" not in short.card
    assert "points" not in short.card
    assert "expires_at" not in short.card

    prefix = follow_utterance("客户会", "2026-10-22T15:00:00")
    remainder = "短" * 60
    message = f"{prefix}\n{remainder}"
    assert len(remainder) < 80 <= len(message)
    stripped = follow_up(message, [], _NOW)
    assert called["n"] == 0
    assert stripped.card["status"] == "needs_paste"
    assert stripped.caption == _CAPTION

    bare = follow_up(prefix, [], _NOW)
    assert called["n"] == 0
    assert bare.card["status"] == "needs_paste"

    under = follow_up("纪" * 79, [], _NOW)
    assert called["n"] == 0
    assert under.card["status"] == "needs_paste"

    ready = follow_up("纪" * 80, [], _NOW)
    assert called["n"] == 1
    assert ready.card["status"] == "ready"
    assert ready.card["phase"] == "info"
    assert ready.card["source"] == {"kind": "pasted"}


def test_follow_due_resolves_from_now(monkeypatch):
    paste = _pad("纪要包含这些期限：今天，明天，后天，周五，星期一，下周三，周五前，尽快，下周五。")
    phrases = ("今天", "明天", "后天", "周五", "星期一", "下周三", "周五前", "尽快", "下周五")
    for phrase in phrases:
        assert phrase in paste

    def _model(*_args, **_kwargs):
        return {
            "points": [],
            "actions": [
                {
                    "text": phrase,
                    "owner": "",
                    "due_said": phrase,
                    "evidence": phrase,
                    "start_at": "1999-01-01T00:00:00",
                }
                for phrase in phrases
            ],
        }

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    evening = datetime(2026, 10, 22, 18, 45)
    card = follow_up(paste, [], evening).card
    by_due = {item["due_said"]: item for item in card["actions"]}
    expected = {
        "今天": "2026-10-22T09:00:00",
        "明天": "2026-10-23T09:00:00",
        "后天": "2026-10-24T09:00:00",
        "周五": "2026-10-23T09:00:00",
        "星期一": "2026-10-19T09:00:00",
        "下周三": "2026-10-28T09:00:00",
        "周五前": "2026-10-23T09:00:00",
        "尽快": "",
        "下周五": "2026-10-30T09:00:00",
    }
    for phrase, start in expected.items():
        assert by_due[phrase]["start_at"] == start
        if start:
            end = datetime.fromisoformat(start) + timedelta(hours=1)
            assert by_due[phrase]["end_at"] == end.isoformat()
        else:
            assert by_due[phrase]["end_at"] == ""
    assert "1999-01-01" not in json.dumps(card)

    friday = datetime(2026, 10, 23, 8, 0)
    again = follow_up(paste, [], friday).card
    on_friday = {item["due_said"]: item["start_at"] for item in again["actions"]}
    assert on_friday["周五"] == "2026-10-23T09:00:00"
    assert on_friday["下周五"] == "2026-10-30T09:00:00"


def test_follow_minutes_refuse_a_leftover():
    paste = _pad("要发报价这一件事，周五前完成。")
    kept = ActionItem(
        "a1",
        "要发报价这一件事",
        "",
        "周五",
        "要发报价这一件事",
        "2026-10-23T09:00:00",
        "2026-10-23T10:00:00",
    )
    made = PastedMinutes(paste, ("要发报价这一件事",), (kept,), FollowMail((), "跟进：会议", kept.text))
    assert made.actions[0].due_said == "周五"
    with pytest.raises(ValueError):
        PastedMinutes(paste, ("编造的要点",), (), FollowMail((), "跟进：会议", ""))
    with pytest.raises(ValueError):
        PastedMinutes(
            paste,
            (),
            (ActionItem("a1", "编造待办", "", "", "编造待办", "", ""),),
            FollowMail((), "跟进：会议", ""),
        )
    with pytest.raises(ValueError):
        PastedMinutes(
            paste,
            (),
            (ActionItem("a1", "要发报价这一件事", "", "", "编造证据", "", ""),),
            FollowMail((), "跟进：会议", ""),
        )
    with pytest.raises(ValueError):
        PastedMinutes(
            paste,
            (),
            (ActionItem("a1", "要发报价这一件事", "", "下个月", "要发报价这一件事", "", ""),),
            FollowMail((), "跟进：会议", ""),
        )
    with pytest.raises(ValueError):
        PastedMinutes(
            paste,
            (),
            (ActionItem("a1", "要发报价这一件事", "李四", "", "要发报价这一件事", "", ""),),
            FollowMail((), "跟进：会议", ""),
        )


def test_direct_post_still_creates_when_phase_is_sent(auth_client: TestClient):
    init_db()
    user_id = _user_id(auth_client)
    db = SessionLocal()
    created: list[int] = []
    description = f"follow_action: a1\n{_SPAN}"
    try:
        before = _count(db, user_id)
        first = auth_client.post(
            "/api/calendar",
            json={
                "title": "跟进：第一件",
                "start_at": "2099-03-02T09:00:00",
                "end_at": "2099-03-02T10:00:00",
                "participants": [],
                "meet": False,
                "description": description,
                "phase": "draft",
                "expires_at": "2020-01-01T00:00:00+00:00",
            },
        )
        assert first.status_code == 200, first.text
        created.append(first.json()["id"])
        second = auth_client.post(
            "/api/calendar",
            json={
                "title": "跟进：第二件",
                "start_at": "2099-03-03T09:00:00",
                "end_at": "2099-03-03T10:00:00",
                "participants": [],
                "meet": False,
                "description": description,
                "phase": "info",
                "expires_at": "2020-01-01T00:00:00+00:00",
            },
        )
        assert second.status_code == 200, second.text
        created.append(second.json()["id"])
        assert created[0] != created[1]
        assert "phase" not in CalendarWrite.model_fields
        assert "expires_at" not in CalendarWrite.model_fields
        db.expire_all()
        assert _count(db, user_id) == before + 2
        stored = [db.get(CalendarEvent, event_id) for event_id in created]
        assert all(row is not None and row.description == description for row in stored)
    finally:
        for event_id in created:
            row = db.get(CalendarEvent, event_id)
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


def test_pointed_prefix_matches_the_frontend_string():
    assert follow_utterance("客户会", "2026-10-22T15:00:00") == "帮我整理这场会的纪要：客户会，2026-10-22 15:00 开始"
    assert follow_utterance("客户会", "2026-10-22", all_day=True) == "帮我整理这场会的纪要：客户会，2026-10-22 全天"


def test_follow_clock_failure_still_returns_the_card(auth_client: TestClient, monkeypatch):
    paste = _pad(f"会议记录。{_SPAN}。")

    def _down(*_args, **_kwargs):
        raise CalendarTransientError()

    def _model(*_args, **_kwargs):
        return {
            "points": [_SPAN],
            "actions": [{"text": _SPAN, "owner": "张三", "due_said": "周五", "evidence": _SPAN}],
        }

    monkeypatch.setattr("app.agents.tools.calendar_tool.calendar_now", _down)
    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    init_db()
    db = SessionLocal()
    user_id = _user_id(auth_client)
    try:
        before = _count(db, user_id)
        result = execute_calendar(
            ToolContext(db=db, message=paste, history=[], user_id=user_id),
            {"action": "followup"},
        )
        card = result.schedule_card
        assert card["status"] == "ready"
        assert card["phase"] == "draft"
        assert card["actions"][0]["start_at"]
        assert "暂时连不上" not in (result.direct_answer or "")
        assert _count(db, user_id) == before
    finally:
        db.close()


def test_follow_classify_prompt_names_followup():
    prompt = _classify_prompt(datetime(2026, 10, 22), "Asia/Shanghai")
    assert "followup" in prompt
    assert "会后跟进一下昨天的客户会" in prompt
    assert "帮我整理一下这段会议纪要" in prompt
    assert "把这段转写整理成待办" in prompt
    assert parse_action("followup") == "followup"


def test_follow_router_prompt_has_the_three_few_shots():
    text = router_system(has_kb_docs=False)
    for line in _FEW_SHOTS:
        assert line in text
    rows = [json.loads(line) for line in _SEED.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_message = {row["message"]: row for row in rows}
    for line in _FEW_SHOTS:
        message = line.split(" | → ", 1)[0].removeprefix("输入：")
        assert by_message[message]["gold"]["tools"] == ["calendar"]
    assert by_message["生成会议纪要结构"]["gold"]["tools"] == []
    assert by_message["帮我列个待办模板"]["gold"]["tools"] == []
