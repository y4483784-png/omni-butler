"""Read-only Drive lookup for one meeting. Official minutes stay out of this module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.services.calendar_secrets import (
    GOOGLE_DRIVE_SCOPES,
    CalendarTokenError,
    CalendarTransientError,
    scoped_access,
)
from app.services.gmail import _sentences
from app.services.meeting_signals import MeetingSignals, capped_mail_people

_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DOC_MIME = "application/vnd.google-apps.document"
_GET_FIELDS = "id,name,mimeType,webViewLink"
_LIST_FIELDS = "files(id,name,mimeType,webViewLink)"
_PAGE_SIZE = 5
_EXPORT_CAP = 3


class _Rejected(Exception):
    pass


class _Down(Exception):
    pass


@dataclass(frozen=True)
class DriveListQuery:
    """A files.list q value. An empty string cannot be constructed."""

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("drive list query is empty")


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    title: str
    url: str
    via: Literal["attachment", "title_and_people", "title"]
    sentences: tuple[str, ...]


@dataclass(frozen=True)
class DriveDocs:
    state: Literal["found", "empty", "needs_consent", "unavailable"]
    docs: tuple[DriveFile, ...] = ()
    connected: bool = True

    def __post_init__(self) -> None:
        if self.state == "found" and not self.docs:
            raise ValueError("found docs need a file")
        if self.state != "found" and self.docs:
            raise ValueError("only a found docs section carries files")


def list_queries(signals: MeetingSignals) -> tuple[DriveListQuery, ...]:
    """Title atoms are required. Owner emails are added only beside those atoms."""
    terms = [term for term in signals.topic_terms() if "'" not in term and "\\" not in term]
    if not terms:
        return ()
    names = " or ".join(f"name contains '{_quote(term)}'" for term in terms)
    emails, _names = capped_mail_people(signals.invitees)
    modified = _modified_after(signals)
    title_only = DriveListQuery(f"trashed = false and {modified} and ({names})")
    if not emails:
        return (title_only,)
    owners = " or ".join(f"'{_quote(email)}' in owners" for email in emails)
    both = DriveListQuery(f"trashed = false and {modified} and ({names}) and ({owners})")
    return (both, title_only)


def search_meeting_docs(db: Session, user_id: int, signals: MeetingSignals) -> DriveDocs:
    """Attachment reads, then at most two files.list calls. No HTTP without the scope."""
    try:
        access = scoped_access(db, user_id, GOOGLE_DRIVE_SCOPES[0])
    except CalendarTokenError:
        return DriveDocs("needs_consent", connected=True)
    except CalendarTransientError:
        return DriveDocs("unavailable", connected=True)
    if access == "not_connected":
        return DriveDocs("needs_consent", connected=False)
    if access == "scope_missing":
        return DriveDocs("needs_consent", connected=True)
    queries = list_queries(signals)
    try:
        found = _collect(access, signals, queries)
    except _Rejected:
        return DriveDocs("needs_consent", connected=True)
    except _Down:
        return DriveDocs("unavailable", connected=True)
    if not found:
        return DriveDocs("empty", connected=True)
    return DriveDocs("found", tuple(found), connected=True)


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _modified_after(signals: MeetingSignals) -> str:
    day = signals.start_day - timedelta(days=120)
    return f"modifiedTime > '{day.isoformat()}T00:00:00'"


def _collect(access, signals: MeetingSignals, queries: tuple[DriveListQuery, ...]) -> list[DriveFile]:
    staged: list[tuple[dict, Literal["attachment", "title_and_people", "title"]]] = []
    seen: set[str] = set()
    for file_id in signals.attachment_ids[:5]:
        meta = _get_file(access, file_id)
        if meta is None:
            continue
        found_id = str(meta.get("id") or "").strip()
        if not found_id or found_id in seen:
            continue
        seen.add(found_id)
        staged.append((meta, "attachment"))
    via: Literal["title_and_people", "title"] = "title"
    listed: list[dict] = []
    for index, query in enumerate(queries):
        page = _list_files(access, query)
        if page:
            listed = page
            via = "title_and_people" if index == 0 and len(queries) == 2 else "title"
            break
    for meta in listed:
        file_id = str(meta.get("id") or "").strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        staged.append((meta, via))
    return _with_sentences(access, staged)


def _with_sentences(
    access,
    staged: list[tuple[dict, Literal["attachment", "title_and_people", "title"]]],
) -> list[DriveFile]:
    exported = 0
    docs: list[DriveFile] = []
    for meta, via in staged:
        file_id = str(meta.get("id") or "").strip()
        title = str(meta.get("name") or "").strip() or "（无标题）"
        url = str(meta.get("webViewLink") or "").strip() or f"https://drive.google.com/open?id={quote(file_id)}"
        sentences: tuple[str, ...] = ()
        if str(meta.get("mimeType") or "") == _DOC_MIME and exported < _EXPORT_CAP:
            text = _export_text(access, file_id)
            sentences = _sentences(text, "", title)[:2]
            exported += 1
        docs.append(DriveFile(file_id=file_id, title=title, url=url, via=via, sentences=sentences))
    return docs


def _status(response) -> None:
    if response.status_code in (401, 403):
        raise _Rejected()
    if response.status_code == 404:
        return
    if response.status_code != 200:
        raise _Down()


def _get_file(access, file_id: str) -> dict | None:
    response = access.get(
        f"{_FILES_URL}/{quote(file_id, safe='')}",
        params={"fields": _GET_FIELDS},
    )
    if response.status_code == 404:
        return None
    _status(response)
    body = response.json() if response.content else {}
    if not isinstance(body, dict) or not str(body.get("id") or "").strip():
        return None
    return body


def _list_files(access, query: DriveListQuery) -> list[dict]:
    response = access.get(
        _FILES_URL,
        params={
            "q": query.text,
            "pageSize": _PAGE_SIZE,
            "fields": _LIST_FIELDS,
            "supportsAllDrives": "false",
        },
    )
    _status(response)
    if response.status_code == 404:
        raise _Down()
    body = response.json() if response.content else {}
    files = body.get("files") if isinstance(body, dict) else None
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _export_text(access, file_id: str) -> str:
    response = access.get(
        f"{_FILES_URL}/{quote(file_id, safe='')}/export",
        params={"mimeType": "text/plain"},
    )
    _status(response)
    if response.status_code != 200:
        return ""
    return str(getattr(response, "text", "") or "")
