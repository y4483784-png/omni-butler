from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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

_NOT_FOUND = "没有找到这场会。这次没有读官方纪要，也没有编造纪要。"
_OFFICIAL_PASTE = "这场会还没有官方纪要。把会议记录或转写贴过来。不会编造不存在的纪要。"
_ATTACHMENT_CONSENT = "日历上有纪要附件。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。"
_MEET_CONSENT = "这场会有 Meet 智能纪要。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。"
_UNAVAILABLE = "暂时读不到这场会的官方纪要。可以把记录贴过来。不会编造官方纪要。"
_DOC = "application/vnd.google-apps.document"
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
    prompts: list[str] = []

    def _model(messages, *_args, **_kwargs):
        called["n"] += 1
        prompts.append(str(messages[0]["content"]))
        return {"points": [], "actions": []}

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    monkeypatch.setattr(
        "app.services.drive.scoped_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("google")),
    )
    short = follow_up("会后跟进一下昨天的客户会", [], _NOW)
    assert called["n"] == 0
    assert short.card["status"] == "not_found"
    assert short.card["phase"] == "info"
    assert short.card["mode"] == "followup"
    assert short.caption == _NOT_FOUND
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
    assert stripped.card["status"] == "not_found"
    assert stripped.caption == _NOT_FOUND
    assert "actions" not in stripped.card

    bare = follow_up(prefix, [], _NOW)
    assert called["n"] == 0
    assert bare.card["status"] == "not_found"

    under = follow_up("纪" * 79, [], _NOW)
    assert called["n"] == 0
    assert under.card["status"] == "not_found"

    ready = follow_up("纪" * 80, [], _NOW)
    assert called["n"] == 1
    assert "要点" in prompts[0]
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


class _File:
    def __init__(self, file_id: str, title: str, mime_type: str, file_url: str) -> None:
        self.file_id = file_id
        self.title = title
        self.mime_type = mime_type
        self.file_url = file_url


class _Row:
    def __init__(self, title: str, start: datetime, *, code: str = "", attachments: tuple = ()) -> None:
        self.title = title
        self.start_at = start
        self.end_at = start + timedelta(hours=1)
        self.participants = "[]"
        self.all_day = False
        self.meeting_code = code
        self.attachments = attachments


class _Response:
    def __init__(self, status: int, *, body: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self.text = text
        self._body = body
        self.content = text.encode() if text else (b"{}" if body is not None else b"")

    def json(self):
        return {} if self._body is None else self._body


def _files_collection(url: str) -> bool:
    return urlparse(url).path.rstrip("/") == "/drive/v3/files"


def _forbid_google(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.drive.scoped_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("google")),
    )


def _forbid_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.meeting_followup.complete_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("minutes model")),
    )


def _minutes_model(text: str):
    def _model(*_args, **_kwargs):
        return {
            "points": [text],
            "actions": [{"text": text, "owner": "张三", "due_said": "周五", "evidence": text}],
        }

    return _model


def test_official_miss_asks_for_paste_and_invents_nothing(monkeypatch):
    invented = "这是编造的官方纪要结论。"
    urls: list[str] = []

    def get(url, params=None):
        urls.append(str(url))
        query = dict(params or {})
        assert not _files_collection(str(url))
        if str(url).rstrip("/").endswith("/conferenceRecords"):
            assert int(query["pageSize"]) == 1
            assert query["filter"] == 'space.meeting_code="abc-defg-hij"'
            assert "pageToken" not in query
            return _Response(200, body={"conferenceRecords": [], "nextPageToken": "more"})
        raise AssertionError(url)

    def scoped(_db, _user_id, scope):
        if str(scope).endswith("meetings.space.readonly"):
            return type("Access", (), {"get": staticmethod(get)})()
        if str(scope).endswith("drive.readonly"):
            raise AssertionError("drive")
        raise AssertionError(scope)

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    _forbid_model(monkeypatch)
    row = _Row("客户会", datetime(2026, 10, 22, 15, 0), code="abc-defg-hij")
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 3)
    card = result.card
    assert card["mode"] == "followup"
    assert card["phase"] == "info"
    assert card["status"] == "needs_paste"
    assert card["checked"] == ["meet_smart_notes", "attachment_notes"]
    assert result.caption == _OFFICIAL_PASTE
    blob = json.dumps(card, ensure_ascii=False)
    assert invented not in blob
    assert "actions" not in card
    assert urls
    assert not any(url.endswith("/smartNotes") for url in urls)
    assert not any(_files_collection(url) for url in urls)


def test_agenda_attachment_is_not_minutes(monkeypatch):
    _forbid_google(monkeypatch)
    _forbid_model(monkeypatch)
    start = datetime(2026, 10, 22, 15, 0)
    message = follow_utterance("客户会", "2026-10-22T15:00:00")
    files = (
        _File("agenda", "议程", _DOC, "https://docs.google.com/document/d/agenda/edit"),
        _File("notes", "会议纪要", _DOC, "https://docs.google.com/document/d/notes/edit"),
    )
    result = follow_up(message, [_Row("客户会", start, attachments=files)], _NOW, object(), 1)
    assert result.card["status"] == "needs_paste"
    assert result.card["checked"] == ["attachment_notes"]
    assert result.caption == _OFFICIAL_PASTE
    blob = json.dumps(result.card, ensure_ascii=False)
    assert "议程" not in blob
    assert "会议纪要" not in blob

    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))
    agenda = follow_up(
        message,
        [_Row("客户会", start, attachments=(files[0],))],
        _NOW,
        object(),
        1,
    )
    assert agenda.card["status"] == "needs_paste"
    assert "议程" not in json.dumps(agenda.card, ensure_ascii=False)
    pdf = follow_up(
        message,
        [
            _Row(
                "客户会",
                start,
                attachments=(_File("p", "会议纪要", "application/pdf", "https://drive.google.com/file/d/p"),),
            )
        ],
        _NOW,
        object(),
        1,
    )
    assert pdf.card["status"] == "needs_paste"


def test_notes_attachment_without_drive_asks_consent(monkeypatch):
    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))
    seen: list[str] = []

    def scoped(_db, _user_id, scope):
        seen.append(str(scope))
        return "scope_missing"

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    _forbid_model(monkeypatch)
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 4)
    assert result.card["status"] == "needs_consent"
    assert result.card["consent"] == "attachment"
    assert result.card["phase"] == "info"
    assert result.caption == _ATTACHMENT_CONSENT
    assert result.card["status"] != "needs_paste"
    assert any(scope.endswith("drive.readonly") for scope in seen)
    assert "actions" not in result.card


def test_long_paste_does_not_call_meet_or_drive(monkeypatch):
    _forbid_google(monkeypatch)
    body = "张三周五前发报价。" + ("纪" * 80)
    assert len(body) >= 80
    monkeypatch.setattr("app.services.meeting_followup.complete_json", _minutes_model("张三周五前发报价"))
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        code="abc-defg-hij",
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    message = f"{follow_utterance('客户会', '2026-10-22T15:00:00')}\n{body}"
    result = follow_up(message, [row], _NOW, object(), 9)
    card = result.card
    assert card["status"] == "ready"
    assert card["source"] == {"kind": "pasted"}
    assert card["phase"] == "draft"
    for key in ("event_id", "external_id", "action", "rsvp"):
        assert key not in card


def test_missing_meet_scope_still_reads_attachment_notes(monkeypatch):
    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))
    urls: list[str] = []
    seen: list[str] = []
    export = "张三周五前发报价。"

    def get(url, params=None):
        urls.append(str(url))
        assert "meet.googleapis.com" not in str(url)
        assert not _files_collection(str(url))
        if str(url).endswith("/export"):
            return _Response(200, text=export)
        return _Response(
            200,
            body={
                "id": "doc-1",
                "name": "客户会纪要",
                "mimeType": _DOC,
                "webViewLink": "https://docs.google.com/document/d/doc-1/edit",
            },
        )

    def scoped(_db, _user_id, scope):
        seen.append(str(scope))
        if str(scope).endswith("meetings.space.readonly"):
            return "scope_missing"
        if str(scope).endswith("drive.readonly"):
            return type("Access", (), {"get": staticmethod(get)})()
        raise AssertionError(scope)

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    monkeypatch.setattr("app.services.meeting_followup.complete_json", _minutes_model("张三周五前发报价"))
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        code="abc-defg-hij",
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 5)
    card = result.card
    assert any(scope.endswith("meetings.space.readonly") for scope in seen)
    assert not any("meet.googleapis.com" in url for url in urls)
    assert urls
    assert not any(_files_collection(url) for url in urls)
    assert card["source"]["kind"] == "attachment_notes"
    assert card["source"]["title"] == "客户会纪要"
    assert card["source"]["url"].startswith("https://docs.google.com/document/d/doc-1")
    assert card["status"] == "ready"
    assert card["phase"] == "draft"
    assert card["points"] == ["张三周五前发报价"]
    assert card["actions"][0]["text"] == "张三周五前发报价"
    for key in ("event_id", "external_id", "action", "rsvp"):
        assert key not in card
    assert "participants" not in card["actions"][0]


def test_follow_up_names_exclude_create_and_send():
    assert "create_event" not in follow_up.__code__.co_names
    assert "search_meeting_docs" not in follow_up.__code__.co_names
    source = Path(follow_up.__code__.co_filename).read_text(encoding="utf-8")
    assert "gmail.send" not in source
    assert "gmail.compose" not in source
    assert "gmail.modify" not in source
    assert "drive_notes" not in source


def test_connect_url_requests_drive_and_meet_readonly_not_gmail_send():
    from app.services.calendar_secrets import (
        GOOGLE_CALENDAR_SCOPES,
        GOOGLE_MEET_SCOPES,
        GOOGLE_REQUESTED_SCOPES,
        _require_scopes,
        build_authorize_url,
    )

    assert GOOGLE_MEET_SCOPES == ("https://www.googleapis.com/auth/meetings.space.readonly",)
    assert "https://www.googleapis.com/auth/meetings.space.readonly" not in GOOGLE_CALENDAR_SCOPES
    assert set(GOOGLE_MEET_SCOPES) <= set(GOOGLE_REQUESTED_SCOPES)
    _require_scopes(" ".join(GOOGLE_CALENDAR_SCOPES))
    url = build_authorize_url("state-token")
    scope = unquote(parse_qs(urlparse(url).query)["scope"][0])
    assert "https://www.googleapis.com/auth/drive.readonly" in scope
    assert "https://www.googleapis.com/auth/meetings.space.readonly" in scope
    assert "gmail.send" not in url
    assert "gmail.compose" not in url
    assert "gmail.modify" not in url
    assert "include_granted_scopes=false" in url
    assert "prompt=consent" in url


def test_notes_markers_ship_empty():
    from app.services.drive import _NOTES_MARKERS

    assert _NOTES_MARKERS == ()


def test_official_minutes_reject_empty_text():
    from app.services.drive import OfficialMinutes

    with pytest.raises(ValueError):
        OfficialMinutes("meet_smart_notes", "标题", "https://docs.google.com/document/d/x/edit", "")
    with pytest.raises(ValueError):
        OfficialMinutes("attachment_notes", "标题", "https://docs.google.com/document/d/x/edit", "   ")
    assert not hasattr(OfficialMinutes, "from_drive")
    kept = OfficialMinutes("meet_smart_notes", "标题", "https://docs.google.com/document/d/x/edit", "有文字")
    assert kept.source == "meet_smart_notes"
    assert kept.text == "有文字"


def test_meet_smart_notes_are_the_card_source(monkeypatch):
    urls: list[str] = []
    export = "张三周五前发报价。"

    def get(url, params=None):
        urls.append(str(url))
        assert not _files_collection(str(url))
        if str(url).rstrip("/").endswith("/conferenceRecords"):
            query = dict(params or {})
            assert int(query["pageSize"]) == 1
            assert query["filter"] == 'space.meeting_code="abc-defg-hij"'
            assert "pageToken" not in query
            return _Response(
                200,
                body={
                    "conferenceRecords": [{"name": "conferenceRecords/rec-1"}],
                    "nextPageToken": "more",
                },
            )
        if str(url).endswith("/smartNotes"):
            query = dict(params or {})
            assert int(query["pageSize"]) == 1
            assert "pageToken" not in query
            return _Response(
                200,
                body={
                    "smartNotes": [
                        {
                            "state": "FILE_GENERATED",
                            "docsDestination": {
                                "documentId": "doc-9",
                                "exportUri": "https://docs.google.com/document/d/doc-9/edit",
                            },
                        }
                    ],
                    "nextPageToken": "more",
                },
            )
        if str(url).endswith("/export"):
            return _Response(200, text=export)
        return _Response(
            200,
            body={
                "id": "doc-9",
                "name": "Meet 纪要",
                "mimeType": _DOC,
                "webViewLink": "https://docs.google.com/document/d/doc-9/edit",
            },
        )

    def scoped(_db, _user_id, scope):
        if str(scope).endswith("meetings.space.readonly") or str(scope).endswith("drive.readonly"):
            return type("Access", (), {"get": staticmethod(get)})()
        raise AssertionError(scope)

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    monkeypatch.setattr("app.services.meeting_followup.complete_json", _minutes_model("张三周五前发报价"))
    row = _Row("客户会", datetime(2026, 10, 22, 15, 0), code="abc-defg-hij")
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 8)
    card = result.card
    assert card["source"] == {
        "kind": "meet_smart_notes",
        "title": "Meet 纪要",
        "url": "https://docs.google.com/document/d/doc-9/edit",
    }
    assert card["status"] == "ready"
    assert card["phase"] == "draft"
    assert card["points"] == ["张三周五前发报价"]
    assert sum(1 for url in urls if url.rstrip("/").endswith("/conferenceRecords")) == 1
    assert sum(1 for url in urls if url.endswith("/smartNotes")) == 1
    assert not any(_files_collection(url) for url in urls)
    for key in ("event_id", "external_id", "action", "rsvp"):
        assert key not in card


def test_meet_notes_without_drive_ask_consent(monkeypatch):
    urls: list[str] = []

    def get(url, params=None):
        urls.append(str(url))
        if "drive.googleapis.com" in str(url):
            raise AssertionError(url)
        if str(url).rstrip("/").endswith("/conferenceRecords"):
            return _Response(200, body={"conferenceRecords": [{"name": "conferenceRecords/rec-1"}]})
        if str(url).endswith("/smartNotes"):
            return _Response(
                200,
                body={
                    "smartNotes": [
                        {
                            "state": "FILE_GENERATED",
                            "docsDestination": {
                                "document": "doc-9",
                                "exportUri": "https://docs.google.com/document/d/doc-9/edit",
                            },
                        }
                    ]
                },
            )
        raise AssertionError(url)

    def scoped(_db, _user_id, scope):
        if str(scope).endswith("meetings.space.readonly"):
            return type("Access", (), {"get": staticmethod(get)})()
        if str(scope).endswith("drive.readonly"):
            return "scope_missing"
        raise AssertionError(scope)

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    _forbid_model(monkeypatch)
    row = _Row("客户会", datetime(2026, 10, 22, 15, 0), code="abc-defg-hij")
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 2)
    assert result.card["status"] == "needs_consent"
    assert result.card["consent"] == "meet"
    assert result.caption == _MEET_CONSENT
    assert result.card["status"] != "needs_paste"
    assert not any("drive.googleapis.com" in url for url in urls)


def test_meet_401_skips_and_attachment_is_still_read(monkeypatch):
    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))
    urls: list[str] = []
    export = "张三周五前发报价。"

    def get(url, params=None):
        urls.append(str(url))
        if "meet.googleapis.com" in str(url):
            return _Response(401, body={"error": "denied"})
        if str(url).endswith("/export"):
            return _Response(200, text=export)
        return _Response(
            200,
            body={"id": "doc-1", "name": "客户会纪要", "mimeType": _DOC, "webViewLink": "https://docs.google.com/document/d/doc-1/edit"},
        )

    monkeypatch.setattr(
        "app.services.drive.scoped_access",
        lambda *_args, **_kwargs: type("Access", (), {"get": staticmethod(get)})(),
    )
    monkeypatch.setattr("app.services.meeting_followup.complete_json", _minutes_model("张三周五前发报价"))
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        code="abc-defg-hij",
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 6)
    assert result.card["source"]["kind"] == "attachment_notes"
    assert result.card["status"] == "ready"
    assert any("meet.googleapis.com" in url for url in urls)
    assert "meet_smart_notes" not in result.card.get("checked", [])


def test_drive_export_503_is_unavailable(monkeypatch):
    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))

    def get(url, params=None):
        if str(url).endswith("/export"):
            return _Response(503, body={"error": "unavailable"})
        return _Response(
            200,
            body={"id": "doc-1", "name": "客户会纪要", "mimeType": _DOC, "webViewLink": "https://docs.google.com/document/d/doc-1/edit"},
        )

    def scoped(_db, _user_id, scope):
        if str(scope).endswith("drive.readonly"):
            return type("Access", (), {"get": staticmethod(get)})()
        return "scope_missing"

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    _forbid_model(monkeypatch)
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 1)
    assert result.card["status"] == "unavailable"
    assert result.card["phase"] == "info"
    assert result.caption == _UNAVAILABLE
    assert "重新连接" not in result.caption
    assert "重新连接" not in json.dumps(result.card, ensure_ascii=False)


def test_empty_or_missing_export_is_not_minutes(monkeypatch):
    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))
    mode = {"export": "empty"}

    def get(url, params=None):
        if str(url).endswith("/export"):
            if mode["export"] == "empty":
                return _Response(200, text="  \n")
            return _Response(404, body={})
        return _Response(200, body={"id": "doc-1", "name": "客户会纪要", "mimeType": _DOC})

    monkeypatch.setattr(
        "app.services.drive.scoped_access",
        lambda *_args, **_kwargs: type("Access", (), {"get": staticmethod(get)})(),
    )
    _forbid_model(monkeypatch)
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    message = follow_utterance("客户会", "2026-10-22T15:00:00")
    empty = follow_up(message, [row], _NOW, object(), 1)
    assert empty.card["status"] == "needs_paste"
    assert empty.caption == _OFFICIAL_PASTE
    mode["export"] = "missing"
    missing = follow_up(message, [row], _NOW, object(), 1)
    assert missing.card["status"] == "needs_paste"


def test_unready_meet_note_is_not_minutes(monkeypatch):
    def get(url, params=None):
        if "drive.googleapis.com" in str(url):
            raise AssertionError(url)
        if str(url).rstrip("/").endswith("/conferenceRecords"):
            return _Response(200, body={"conferenceRecords": [{"name": "conferenceRecords/rec-1"}]})
        if str(url).endswith("/smartNotes"):
            return _Response(
                200,
                body={
                    "smartNotes": [
                        {
                            "state": "STARTED",
                            "docsDestination": {
                                "document": "doc-9",
                                "exportUri": "https://docs.google.com/document/d/doc-9/edit",
                            },
                        }
                    ]
                },
            )
        raise AssertionError(url)

    def scoped(_db, _user_id, scope):
        if str(scope).endswith("meetings.space.readonly"):
            return type("Access", (), {"get": staticmethod(get)})()
        if str(scope).endswith("drive.readonly"):
            raise AssertionError("drive")
        raise AssertionError(scope)

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    _forbid_model(monkeypatch)
    row = _Row("客户会", datetime(2026, 10, 22, 15, 0), code="abc-defg-hij")
    result = follow_up(follow_utterance("客户会", "2026-10-22T15:00:00"), [row], _NOW, object(), 1)
    assert result.card["status"] == "needs_paste"
    assert result.caption == _OFFICIAL_PASTE
    assert "meet_smart_notes" in result.card["checked"]


def test_tie_returns_choose_and_reads_nothing(monkeypatch):
    def _model(messages, *_args, **_kwargs):
        assert "要点" not in messages[0]["content"]
        return {"title": "客户会", "day": "", "clock": ""}

    monkeypatch.setattr("app.services.meeting_followup.complete_json", _model)
    _forbid_google(monkeypatch)
    start = datetime(2026, 10, 22, 15, 0)
    later = datetime(2026, 10, 23, 15, 0)
    result = follow_up(
        "会后跟进一下客户会",
        [_Row("客户会", start), _Row("客户会", later)],
        _NOW,
        object(),
        1,
    )
    card = result.card
    assert card["status"] == "choose"
    assert card["phase"] == "info"
    assert len(card["candidates"]) == 2
    assert [item["title"] for item in card["candidates"]] == ["客户会", "客户会"]
    assert "actions" not in card
    assert "event_id" not in card
    for item in card["candidates"]:
        assert "external_id" not in item
        assert "rsvp" not in item
        assert "action" not in item


def test_invented_hint_title_is_ignored(monkeypatch):
    monkeypatch.setattr(
        "app.services.meeting_followup.complete_json",
        lambda *_args, **_kwargs: {"title": "编造的发布会", "day": "", "clock": ""},
    )
    _forbid_google(monkeypatch)
    result = follow_up(
        "会后跟进一下",
        [_Row("客户会", datetime(2026, 10, 22, 15, 0))],
        _NOW,
        object(),
        1,
    )
    assert result.card["status"] == "not_found"
    assert result.caption == _NOT_FOUND
    assert "actions" not in result.card


def test_google_event_view_keeps_attachment_fields_and_meeting_code():
    from app.services.calendar_google import _event_view

    def item(**extra):
        base = {
            "id": "evt-1",
            "status": "confirmed",
            "summary": "客户会",
            "start": {"dateTime": "2026-10-22T15:00:00+08:00"},
            "end": {"dateTime": "2026-10-22T16:00:00+08:00"},
        }
        base.update(extra)
        return base

    view = _event_view(
        item(
            hangoutLink="https://meet.google.com/abc-defg-hij",
            conferenceData={
                "entryPoints": [
                    {
                        "entryPointType": "video",
                        "uri": "https://meet.google.com/abc-defg-hij",
                        "meetingCode": "abc-defg-hij",
                    }
                ]
            },
            attachments=[
                {
                    "fileId": "a1",
                    "title": "议程",
                    "mimeType": _DOC,
                    "fileUrl": "https://docs.google.com/document/d/a1/edit",
                },
                {"fileId": "a2", "title": "纪要", "mimeType": _DOC, "fileUrl": "https://docs.google.com/document/d/a2/edit"},
                {"fileId": "", "title": "空", "mimeType": _DOC, "fileUrl": ""},
                {"fileId": "a3", "title": "三", "mimeType": "application/pdf", "fileUrl": "https://drive.google.com/file/d/a3"},
                {"fileId": "a4", "title": "四", "mimeType": _DOC, "fileUrl": ""},
                {"fileId": "a5", "title": "五", "mimeType": _DOC, "fileUrl": ""},
                {"fileId": "a6", "title": "六", "mimeType": _DOC, "fileUrl": ""},
            ],
        )
    )
    assert view is not None
    assert view.meeting_code == "abc-defg-hij"
    assert view.attachment_ids == ("a1", "a2", "a3", "a4", "a5")
    assert len(view.attachments) == 5
    assert view.attachments[0].file_id == "a1"
    assert view.attachments[0].title == "议程"
    assert view.attachments[0].mime_type == _DOC
    assert view.attachments[0].file_url == "https://docs.google.com/document/d/a1/edit"
    from_path = _event_view(
        item(conferenceData={"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/xyz-abcd-efg"}]})
    )
    assert from_path is not None
    assert from_path.meeting_code == "xyz-abcd-efg"
    code_only = _event_view(
        item(conferenceData={"entryPoints": [{"entryPointType": "video", "meetingCode": "abc-defg-hij"}]})
    )
    assert code_only is not None
    assert code_only.meeting_code == "abc-defg-hij"
    assert code_only.meet_link == ""
    other = _event_view(item(hangoutLink="https://example.com/abc-defg-hij"))
    assert other is not None
    assert other.meeting_code == ""


def test_execute_calendar_passes_the_caller_into_the_reader(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr("app.services.drive._NOTES_MARKERS", ("纪要",))
    seen: dict = {}

    def scoped(db, user_id, scope):
        seen["db"] = db
        seen["user_id"] = user_id
        seen["scope"] = scope
        return "scope_missing"

    monkeypatch.setattr("app.services.drive.scoped_access", scoped)
    _forbid_model(monkeypatch)
    _freeze_now(monkeypatch, _NOW)
    row = _Row(
        "客户会",
        datetime(2026, 10, 22, 15, 0),
        attachments=(_File("doc-1", "客户会纪要", _DOC, "https://docs.google.com/document/d/doc-1/edit"),),
    )
    monkeypatch.setattr(
        "app.agents.tools.calendar_tool.upcoming_events",
        lambda *_args, **_kwargs: ([row], ""),
    )
    init_db()
    db = SessionLocal()
    user_id = _user_id(auth_client)
    try:
        result = execute_calendar(
            ToolContext(
                db=db,
                message=follow_utterance("客户会", "2026-10-22T15:00:00"),
                history=[],
                user_id=user_id,
            ),
            {"action": "followup"},
        )
        assert seen["db"] is db
        assert seen["user_id"] == user_id
        assert result.schedule_card["status"] == "needs_consent"
        assert result.direct_answer == _ATTACHMENT_CONSENT
    finally:
        db.close()
