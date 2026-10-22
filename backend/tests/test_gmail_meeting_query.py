"""Gmail q for one meeting. Any one of name, email, or title is enough."""

from __future__ import annotations

from datetime import date

from app.services.gmail import _queries, search_meeting_mail

_DAY = date(2026, 10, 22)
_SUFFIX_120 = (
    "newer_than:120d before:2026/10/23 -filename:ics -in:spam -in:trash -in:drafts -in:chats"
)
_COMBINED = (
    "(from:zhangsan@example.com OR to:zhangsan@example.com OR cc:zhangsan@example.com "
    'OR from:"张三" OR to:"张三" OR cc:"张三" OR "会前准备手测-1852") '
    + _SUFFIX_120
)
_TITLE = '("会前准备手测-1852") ' + _SUFFIX_120
_EMAIL_AND_TITLE = (
    "(from:zhangsan@example.com OR to:zhangsan@example.com OR cc:zhangsan@example.com "
    'OR "会前准备手测-1852") '
    + _SUFFIX_120
)
_EMAIL = (
    "(from:zhangsan@example.com OR to:zhangsan@example.com OR cc:zhangsan@example.com) "
    + _SUFFIX_120
)


def _compiled(attendees: tuple[str, ...], title: str, days: int = 120) -> tuple[str, ...]:
    return _queries(attendees, title, days, _DAY)


def test_labeled_attendee_ors_email_name_and_title():
    assert _compiled(("张三 <zhangsan@example.com>",), "会前准备手测-1852") == (_COMBINED,)


def test_labeled_attendee_with_a_tail_keeps_email_and_name():
    assert _compiled(("张三 <zhangsan@example.com> (organizer)",), "会前准备手测-1852") == (_COMBINED,)


def test_title_still_matches_when_attendees_exist():
    queries = _compiled(("张三 <zhangsan@example.com>",), "会前准备手测-1852")
    assert len(queries) == 1
    assert '"会前准备手测-1852"' in queries[0]
    assert " OR " in queries[0].split(_SUFFIX_120)[0]


def test_bare_email_ors_with_title():
    assert _compiled(("zhangsan@example.com",), "会前准备手测-1852") == (_EMAIL_AND_TITLE,)


def test_bare_email_without_a_title():
    assert _compiled(("zhangsan@example.com",), "会议") == (_EMAIL,)


def test_title_without_attendees():
    assert _compiled((), "会前准备手测-1852") == (_TITLE,)


def test_no_usable_person_and_no_title_compiles_nothing():
    assert _compiled((), "会议") == ()
    assert _compiled(("李",), "") == ()


def test_display_names_that_casefold_together_are_one_atom():
    queries = _compiled(("Zhang San", "zhang san"), "")
    assert queries == (
        '(from:"Zhang San" OR to:"Zhang San" OR cc:"Zhang San") ' + _SUFFIX_120,
    )


def test_compiled_queries_never_use_a_labeled_operand():
    queries = _compiled(("张三 <zhangsan@example.com>",), "会前准备手测-1852")
    blob = "\n".join(queries)
    assert "<" not in blob
    assert ">" not in blob
    assert "from:张三 <" not in blob
    assert "张三 <zhangsan@example.com>" not in blob


class _Access:
    def __init__(self) -> None:
        self.account_email = "owner@example.com"
        self.queries: list[str] = []

    def get(self, url, params=None):
        self.queries.append(str((params or {}).get("q") or ""))

        class _Response:
            status_code = 200
            content = b"{}"

            @staticmethod
            def json():
                return {}

        return _Response()


def test_blank_signals_return_empty_without_listing_mail(monkeypatch):
    access = _Access()
    monkeypatch.setattr("app.services.gmail.scoped_access", lambda *args, **kwargs: access)
    found = search_meeting_mail(
        None,
        1,
        title="会议",
        attendees=(),
        start_day=_DAY,
    )
    assert found == ()
    assert access.queries == []


def test_one_window_lists_the_same_or_then_widens(monkeypatch):
    access = _Access()
    monkeypatch.setattr("app.services.gmail.scoped_access", lambda *args, **kwargs: access)
    found = search_meeting_mail(
        None,
        1,
        title="会前准备手测-1852",
        attendees=("张三 <zhangsan@example.com>",),
        start_day=_DAY,
    )
    assert found == ()
    assert access.queries == [
        _COMBINED,
        _COMBINED.replace("newer_than:120d", "newer_than:365d"),
    ]
