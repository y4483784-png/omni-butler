from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.db import SessionLocal
from app.models.models import Session as ChatSession
from app.services import task_run

_EVENT = re.compile(r"^/api/calendar/(\d+)$")


class BindingClient:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def post(self, url, **kwargs):
        kwargs = self._bind("POST", url, kwargs)
        return self._client.post(url, **kwargs)

    def patch(self, url, **kwargs):
        kwargs = self._bind("PATCH", url, kwargs)
        return self._client.patch(url, **kwargs)

    def delete(self, url, **kwargs):
        url = self._bind_delete(url)
        return self._client.delete(url, **kwargs)

    def _bind(self, method: str, url: str, kwargs: dict) -> dict:
        body = kwargs.get("json")
        if not isinstance(body, dict) or "confirm_token" in body:
            return kwargs
        action, extra = _action_for(method, url, body)
        if action is None:
            return kwargs
        token = self._issue(action, {**body, **extra})
        kwargs["json"] = {**body, "confirm_token": token}
        return kwargs

    def _bind_delete(self, url: str) -> str:
        path, query = _split(url)
        if "confirm_token" in dict(parse_qsl(query)):
            return url
        match = _EVENT.fullmatch(path)
        if match is None:
            return url
        token = self._issue("delete", {"event_id": int(match.group(1))})
        joined = urlencode([*parse_qsl(query), ("confirm_token", token)])
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, joined, parts.fragment))

    def _issue(self, action: str, raw: dict) -> str:
        me = self._client.get("/api/auth/me")
        if me.status_code != 200:
            raise AssertionError(me.text)
        user_id = me.json()["id"]
        db = SessionLocal()
        try:
            row = ChatSession(user_id=user_id, title="测试确认")
            db.add(row)
            db.commit()
            db.refresh(row)
            session_id = row.id
        finally:
            db.close()
        task_run.begin_turn(user_id=user_id, session_id=session_id)
        payload = {
            "session_id": session_id,
            "action": action,
            "title": raw.get("title"),
            "start_at": raw.get("start_at"),
            "end_at": raw.get("end_at"),
            "participants": raw.get("participants") or [],
            "location": raw.get("location") or "",
            "description": raw.get("description") or "",
            "meet": bool(raw.get("meet")),
            "event_id": raw.get("event_id"),
            "external_id": raw.get("external_id"),
            "response": raw.get("response"),
        }
        opened = self._client.post("/api/calendar/confirm-claims", json=payload)
        if opened.status_code != 200:
            raise AssertionError(opened.text)
        return opened.json()["confirm_token"]


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.path, parts.query


def _action_for(method: str, url: str, body: dict) -> tuple[str | None, dict]:
    path, _query = _split(url)
    if method == "POST" and path == "/api/calendar":
        return "create", {}
    if method == "PATCH":
        match = _EVENT.fullmatch(path)
        if match is None:
            return None, {}
        return "update", {"event_id": int(match.group(1))}
    if method == "POST" and path == "/api/calendar/google/confirm":
        kind = "cancel" if str(body.get("action") or "") == "cancel" else "update"
        return kind, {}
    if method == "POST" and path == "/api/calendar/google/rsvp":
        return "rsvp", {}
    return None, {}
