"""Drive list queries for one meeting. Empty terms make no files.list call."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.calendar_provider import Invitee
from app.services.drive import DriveListQuery, list_queries, search_meeting_docs
from app.services.meeting_signals import MeetingSignals

_DAY = date(2026, 10, 22)
_MODIFIED = "modifiedTime > '2026-06-24T00:00:00'"


def _signals(
    title: str,
    invitees: tuple[Invitee, ...] = (),
    attachment_ids: tuple[str, ...] = (),
) -> MeetingSignals:
    return MeetingSignals(
        title=title,
        invitees=invitees,
        attachment_ids=attachment_ids,
        start_day=_DAY,
    )


def test_drive_list_query_rejects_an_empty_string():
    with pytest.raises(ValueError):
        DriveListQuery("")
    with pytest.raises(ValueError):
        DriveListQuery("   ")


def test_title_and_owner_query_then_title_only():
    queries = list_queries(
        _signals("客户周会", (Invitee(email="li@acme.com", name="小李"),))
    )
    assert len(queries) == 2
    assert queries[0].text == (
        f"trashed = false and {_MODIFIED} and (name contains '客户周会') and ('li@acme.com' in owners)"
    )
    assert queries[1].text == f"trashed = false and {_MODIFIED} and (name contains '客户周会')"
    assert "in owners" not in queries[1].text


def test_title_only_when_there_is_no_owner_email():
    queries = list_queries(_signals("客户周会", (Invitee(email="", name="小李"),)))
    assert len(queries) == 1
    assert "in owners" not in queries[0].text
    assert "name contains '客户周会'" in queries[0].text


def test_generic_title_compiles_no_list_query():
    assert list_queries(_signals("周会", (Invitee(email="li@acme.com", name=""),))) == ()
    assert list_queries(_signals("周会")) == ()


def test_attendee_only_is_not_a_query():
    queries = list_queries(_signals("", (Invitee(email="li@acme.com", name="小李"),)))
    assert queries == ()
    assert list_queries(_signals("会议", (Invitee(email="li@acme.com", name="小李"),))) == ()


class _Access:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.params: list[dict] = []

    def get(self, url, params=None):
        self.urls.append(str(url))
        self.params.append(dict(params or {}))

        class _Response:
            status_code = 200
            content = b"{}"

            @staticmethod
            def json():
                return {"files": []}

        return _Response()


def test_empty_terms_make_no_files_list_call(monkeypatch):
    access = _Access()
    monkeypatch.setattr("app.services.drive.scoped_access", lambda *args, **kwargs: access)
    found = search_meeting_docs(None, 1, _signals("周会", (Invitee(email="li@acme.com", name=""),)))
    assert found.docs == ()
    assert found.state == "empty"
    assert access.urls == []


def test_attachment_ids_are_read_without_a_match_all_list(monkeypatch):
    access = _Access()

    def get(url, params=None):
        access.urls.append(str(url))
        access.params.append(dict(params or {}))

        class _Response:
            status_code = 200
            content = b"{}"

            @staticmethod
            def json():
                if str(url).rstrip("/").endswith("/files"):
                    return {"files": []}
                return {
                    "id": "file-1",
                    "name": "客户提纲",
                    "mimeType": "application/pdf",
                    "webViewLink": "https://drive.google.com/file/d/file-1/view",
                }

        return _Response()

    access.get = get
    monkeypatch.setattr("app.services.drive.scoped_access", lambda *args, **kwargs: access)
    found = search_meeting_docs(
        None,
        1,
        _signals("周会", attachment_ids=("file-1",)),
    )
    assert found.state == "found"
    assert found.docs[0].file_id == "file-1"
    assert found.docs[0].via == "attachment"
    assert not any(str(url).rstrip("/").endswith("/files") for url in access.urls)
