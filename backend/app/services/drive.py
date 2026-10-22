from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.services.calendar_secrets import (
    GOOGLE_DRIVE_SCOPES,
    GOOGLE_MEET_SCOPES,
    CalendarTokenError,
    CalendarTransientError,
    scoped_access,
)
from app.services.gmail import _sentences
from app.services.meeting_signals import MeetingSignals, capped_mail_people

_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DOC_MIME = "application/vnd.google-apps.document"
_MEET_RECORDS = "https://meet.googleapis.com/v2/conferenceRecords"
_NOTES_MARKERS: tuple[str, ...] = ()
_MEETING_CODE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
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


@dataclass(frozen=True)
class OfficialMinutes:
    source: Literal["meet_smart_notes", "attachment_notes"]
    title: str
    url: str
    text: str

    def __post_init__(self) -> None:
        if self.source not in ("meet_smart_notes", "attachment_notes"):
            raise ValueError("official source")
        if not str(self.text or "").strip():
            raise ValueError("official minutes text is empty")


@dataclass(frozen=True)
class OfficialRead:
    status: Literal["found", "needs_paste", "needs_consent", "unavailable"]
    minutes: OfficialMinutes | None = None
    checked: tuple[str, ...] = ()
    consent: Literal["", "attachment", "meet"] = ""

    def __post_init__(self) -> None:
        if self.status == "found" and self.minutes is None:
            raise ValueError("found minutes are missing")
        if self.status != "found" and self.minutes is not None:
            raise ValueError("only a found read carries minutes")
        if self.status == "needs_consent" and self.consent not in ("attachment", "meet"):
            raise ValueError("consent source")
        if self.status != "needs_consent" and self.consent:
            raise ValueError("consent source")


@dataclass(frozen=True)
class _MeetDoc:
    document_id: str
    export_uri: str


def read_official_minutes(db: Session | None, user_id: int | None, *, meeting_code: str, attachments) -> OfficialRead:
    checked: list[str] = []
    meet_down = False
    code = _meeting_code_text(meeting_code)
    if code:
        meet = _open_scope(db, user_id, GOOGLE_MEET_SCOPES[0])
        if meet == "down":
            meet_down = True
        elif _usable(meet):
            found = _smart_note(meet, code)
            if found == "down":
                checked.append("meet_smart_notes")
                meet_down = True
            elif found != "skip":
                checked.append("meet_smart_notes")
                if isinstance(found, _MeetDoc):
                    opened = _export_found(
                        db,
                        user_id,
                        found.document_id,
                        title="智能纪要",
                        url=found.export_uri,
                        source="meet_smart_notes",
                        checked=tuple(checked),
                        consent="meet",
                    )
                    if opened is not None:
                        return opened
    notes = _notes_files(attachments)
    checked.append("attachment_notes")
    if notes:
        drive = _open_scope(db, user_id, GOOGLE_DRIVE_SCOPES[0])
        if drive == "down":
            return OfficialRead("unavailable", checked=tuple(checked))
        if not _usable(drive):
            return OfficialRead("needs_consent", checked=tuple(checked), consent="attachment")
        for note in notes:
            opened = _open_with_access(
                drive,
                str(getattr(note, "file_id", "") or "").strip(),
                title=str(getattr(note, "title", "") or "").strip() or "纪要",
                url=str(getattr(note, "file_url", "") or "").strip(),
                source="attachment_notes",
                checked=tuple(checked),
                consent="attachment",
            )
            if opened is not None:
                return opened
    if meet_down:
        return OfficialRead("unavailable", checked=tuple(checked))
    return OfficialRead("needs_paste", checked=tuple(checked))


def _meeting_code_text(value: str) -> str:
    text = (value or "").strip().lower()
    if _MEETING_CODE.fullmatch(text):
        return text
    return ""


def _open_scope(db: Session | None, user_id: int | None, scope: str):
    if db is None or user_id is None:
        return "scope_missing"
    try:
        return scoped_access(db, user_id, scope)
    except CalendarTokenError:
        return "token"
    except CalendarTransientError:
        return "down"


def _usable(access) -> bool:
    return access not in ("not_connected", "scope_missing", "token", "down")


def _notes_files(attachments) -> list:
    kept = []
    for item in attachments or ():
        file_id = str(getattr(item, "file_id", "") or "").strip()
        title = str(getattr(item, "title", "") or "")
        mime = str(getattr(item, "mime_type", "") or "")
        if not file_id or mime != _DOC_MIME:
            continue
        if any(marker and marker in title for marker in _NOTES_MARKERS):
            kept.append(item)
    return kept


def _export_found(
    db,
    user_id,
    file_id: str,
    *,
    title: str,
    url: str,
    source: Literal["meet_smart_notes", "attachment_notes"],
    checked: tuple[str, ...],
    consent: Literal["attachment", "meet"],
) -> OfficialRead | None:
    drive = _open_scope(db, user_id, GOOGLE_DRIVE_SCOPES[0])
    if drive == "down":
        return OfficialRead("unavailable", checked=checked)
    if not _usable(drive):
        return OfficialRead("needs_consent", checked=checked, consent=consent)
    return _open_with_access(
        drive,
        file_id,
        title=title,
        url=url,
        source=source,
        checked=checked,
        consent=consent,
    )


def _open_with_access(
    access,
    file_id: str,
    *,
    title: str,
    url: str,
    source: Literal["meet_smart_notes", "attachment_notes"],
    checked: tuple[str, ...],
    consent: Literal["attachment", "meet"],
) -> OfficialRead | None:
    outcome = _open_doc(access, file_id, title=title, url=url, source=source)
    if isinstance(outcome, OfficialMinutes):
        return OfficialRead("found", minutes=outcome, checked=checked)
    if outcome == "consent":
        return OfficialRead("needs_consent", checked=checked, consent=consent)
    if outcome == "down":
        return OfficialRead("unavailable", checked=checked)
    return None


def _open_doc(access, file_id: str, *, title: str, url: str, source: Literal["meet_smart_notes", "attachment_notes"]):
    meta = _optional_meta(access, file_id)
    if meta == "consent":
        return "consent"
    if isinstance(meta, dict):
        name = str(meta.get("name") or "").strip()
        if name:
            title = name
        link = str(meta.get("webViewLink") or "").strip()
        if not url and link:
            url = link
    kind, text = _export_outcome(access, file_id)
    if kind != "text":
        return kind
    if not url:
        url = f"https://drive.google.com/open?id={quote(file_id, safe='')}"
    return OfficialMinutes(source, title or "纪要", url, text)


def _optional_meta(access, file_id: str):
    try:
        return _get_file(access, file_id)
    except _Rejected:
        return "consent"
    except _Down:
        return None


def _export_outcome(access, file_id: str) -> tuple[str, str]:
    try:
        text = _export_text(access, file_id)
    except _Rejected:
        return "consent", ""
    except _Down:
        return "down", ""
    if not text.strip():
        return "empty", ""
    return "text", text


def _smart_note(access, code: str):
    response = access.get(
        _MEET_RECORDS,
        params={"pageSize": 1, "filter": f'space.meeting_code="{code}"'},
    )
    parsed = _meet_body(response)
    if parsed in ("skip", "down") or not isinstance(parsed, dict):
        return parsed
    records = parsed.get("conferenceRecords")
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return None
    name = str(records[0].get("name") or "").strip().strip("/")
    if not name.startswith("conferenceRecords/") or ".." in name or "://" in name:
        return None
    response = access.get(
        f"https://meet.googleapis.com/v2/{name}/smartNotes",
        params={"pageSize": 1},
    )
    parsed = _meet_body(response)
    if parsed in ("skip", "down") or not isinstance(parsed, dict):
        return parsed
    notes = parsed.get("smartNotes")
    if not isinstance(notes, list) or not notes or not isinstance(notes[0], dict):
        return None
    note = notes[0]
    if str(note.get("state") or "") != "FILE_GENERATED":
        return None
    dest = note.get("docsDestination") if isinstance(note.get("docsDestination"), dict) else {}
    document_id = str(dest.get("document") or dest.get("documentId") or "").strip()
    export_uri = str(dest.get("exportUri") or "").strip()
    if not document_id or not export_uri:
        return None
    return _MeetDoc(document_id, export_uri)


def _meet_body(response):
    if response.status_code in (401, 403):
        return "skip"
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        return "down"
    try:
        body = response.json() if getattr(response, "content", b"") else {}
    except ValueError:
        return "down"
    if not isinstance(body, dict):
        return None
    return body
