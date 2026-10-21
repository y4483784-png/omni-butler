#!/usr/bin/env python3
"""Score calendar behavior with the real extractor and a fake Google client.

Smoke replays ``calendar_model_cache.json``. A missing or stale fingerprint
exits 2. Full refreshes that cache from the planner and chat roles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.env import ensure_bootstrap

ensure_bootstrap(isolate_app_db=True)

from app.core.llm import LLMInfraError, Role, Spoken, cache_identity, resolve  # noqa: E402
from app.eval.calendar_behavior import evaluate_case, summarize  # noqa: E402
from app.eval.report import RunContext, SuiteResult, make_run_dir, write_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "eval" / "calendar_behavior.jsonl"
DEFAULT_DATASET = DATASET
DEFAULT_CACHE = ROOT / "data" / "eval" / "calendar_model_cache.json"
DEFAULT_NOW = datetime(2026, 10, 15, 10, 0, 0)
_WRITE_OK = frozenset({200, 201, 204})


class CacheMissError(BaseException):
    """Smoke replay has no entry for this model, prompt, utterance, and clock."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calendar behavior eval (real model, fake Google)")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--split", choices=("smoke", "full"), default="full")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument(
        "--require-cache",
        action="store_true",
        help="Replay the cache. A miss or a changed fingerprint blocks the run.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def load_cases(path: Path, *, split: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        kind = row.get("split") or "full"
        if split == "smoke" and kind != "smoke":
            continue
        rows.append(row)
    return rows


def _fingerprint(kind: str, messages: list[dict], role: Role) -> str:
    identity = cache_identity(role)
    payload = {
        "kind": kind,
        "model": identity.model,
        "thinking": identity.thinking,
        "messages": messages,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict:
    from app.eval.disk_cache import load_or_drop_entries

    return load_or_drop_entries(path)


def _save_cache(path: Path, data: dict) -> None:
    from app.eval.disk_cache import save_entries

    save_entries(path, data)


class _LLMBridge:
    """Forward complete_json / complete_text with raise_on_error and a disk cache.

    The bridge does not read expectations and does not invent a JSON object.
    """

    def __init__(self, cache: dict, *, refresh: bool, require: bool) -> None:
        self.cache = cache
        self.refresh = refresh
        self.require = require
        self.live = 0
        self.hits = 0
        self.infra = False
        self._json = None
        self._text = None

    def install(self) -> None:
        import app.agents.tools.calendar_tool as tool
        import app.services.calendar as calendar

        self._json = calendar.complete_json
        self._text = tool.complete_text
        calendar.complete_json = self.complete_json
        tool.complete_text = self.complete_text

    def restore(self) -> None:
        import app.agents.tools.calendar_tool as tool
        import app.services.calendar as calendar

        if self._json is not None:
            calendar.complete_json = self._json
        if self._text is not None:
            tool.complete_text = self._text

    def _lookup(self, kind: str, messages: list[dict], role: Role):
        """Return ``(hit, payload)``.

        Advice text is a string, and a JSON miss is ``None``. Neither can be
        told apart from a cache key by its type, so the hit flag is separate.
        """
        key = _fingerprint(kind, messages, role)
        if not self.refresh and key in self.cache:
            self.hits += 1
            entry = self.cache[key]
            value = entry.get("value") if isinstance(entry, dict) else entry
            if (
                kind == "text"
                and isinstance(value, dict)
                and "content" in value
                and "reasoning" in value
            ):
                return True, Spoken(str(value.get("content") or ""), str(value.get("reasoning") or ""), None)
            return True, value
        if self.require:
            raise CacheMissError(
                "calendar cache miss/stale (model, prompt, utterance, now, or zone changed); "
                "run `python scripts/eval.py run calendar --profile full` to refresh"
            )
        return False, key

    def complete_json(self, messages, *, role, raise_on_error=False, tools=None):
        hit, found = self._lookup("json", messages, role)
        if hit:
            return found
        self.live += 1
        try:
            value = self._json(messages, role=role, raise_on_error=True, tools=tools)
        except LLMInfraError:
            self.infra = True
            raise
        self.cache[found] = {"kind": "json", "value": value}
        return value

    def complete_text(
        self,
        messages,
        *,
        role,
        timeout=None,
        retries=0,
        raise_on_error=False,
        tools=None,
    ):
        hit, found = self._lookup("text", messages, role)
        if hit:
            return found
        self.live += 1
        try:
            value = self._text(
                messages,
                role=role,
                timeout=timeout,
                retries=retries,
                raise_on_error=True,
                tools=tools,
            )
        except LLMInfraError:
            self.infra = True
            raise
        if isinstance(value, Spoken):
            self.cache[found] = {
                "kind": "text",
                "value": {"content": value.content, "reasoning": value.reasoning},
            }
            return value
        self.cache[found] = {"kind": "text", "value": value}
        return value


class FakeGoogle:
    """In-process Google HTTP. List, insert, patch, delete, timezone, and refresh."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.token_status = 200
        self.timezone_status = 200
        self.timezone_value = "Asia/Singapore"
        self.events_status = 200
        self.events_items: list[dict] = []
        self.insert_status = 200
        self.insert_id = "evt-created"
        self.patch_status = 200
        self.delete_status = 204

    def reset_calls(self) -> None:
        self.calls = []

    def __call__(self, method, url, *, data=None, params=None, headers=None, json=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "data": data,
                "params": params,
                "headers": headers,
                "json": json,
            }
        )
        path = (url or "").split("?", 1)[0]
        if path.endswith("/token"):
            if self.token_status != 200:
                return _response(self.token_status, {"error": "invalid_grant"})
            return _response(
                200,
                {
                    "access_token": "eval-access",
                    "expires_in": 3600,
                    "refresh_token": "eval-refresh",
                    "token_type": "Bearer",
                },
            )
        if path.endswith("/users/me/settings/timezone"):
            if self.timezone_status != 200:
                return _response(self.timezone_status, {"error": {"message": "timezone"}})
            return _response(200, {"id": "timezone", "value": self.timezone_value})
        if path.rstrip("/").endswith("/users/me/settings"):
            return _response(404, {"error": {"message": "not found"}})
        if path.rstrip("/").endswith("/calendars/primary/events") and method == "GET":
            if self.events_status != 200:
                return _response(self.events_status, {"error": {"message": "list failed"}})
            items = [item for item in self.events_items if _item_overlaps(item, params)]
            return _response(200, {"items": items})
        if path.rstrip("/").endswith("/calendars/primary/events") and method == "POST":
            if self.insert_status not in (200, 201):
                return _response(self.insert_status, {"error": {"message": "insert failed"}})
            return _response(self.insert_status, {"id": self.insert_id, "status": "confirmed"})
        if "/calendars/primary/events/" in path and method == "PATCH":
            if self.patch_status not in (200, 201):
                return _response(self.patch_status, {"error": {"message": "patch failed"}})
            event_id = path.rstrip("/").rsplit("/", 1)[-1]
            payload = {"id": event_id, "status": "confirmed"}
            if isinstance(json, dict):
                payload.update({key: value for key, value in json.items() if key != "id"})
            return _response(self.patch_status, payload)
        if "/calendars/primary/events/" in path and method == "DELETE":
            if self.delete_status not in (200, 204):
                return _response(self.delete_status, {"error": {"message": "delete failed"}})
            return _response(self.delete_status, {})
        return _response(404, {"error": "not-found"})


def _response(status: int, payload: dict):
    import httpx

    return httpx.Response(status, json=payload)


def _parse_stamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _item_span(item: dict) -> tuple[datetime, datetime] | None:
    start_info = item.get("start") if isinstance(item.get("start"), dict) else {}
    end_info = item.get("end") if isinstance(item.get("end"), dict) else {}
    if start_info.get("date") and end_info.get("date"):
        start = _parse_stamp(str(start_info.get("date")) + "T00:00:00")
        end = _parse_stamp(str(end_info.get("date")) + "T00:00:00")
    else:
        start = _parse_stamp(str(start_info.get("dateTime") or ""))
        end = _parse_stamp(str(end_info.get("dateTime") or ""))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _item_overlaps(item: dict, params: dict | None) -> bool:
    if not params:
        return True
    span = _item_span(item)
    if span is None:
        return False
    start, end = span
    window_start = _parse_stamp(str(params.get("timeMin") or "")) or datetime(2000, 1, 1)
    window_end = _parse_stamp(str(params.get("timeMax") or "")) or datetime(2100, 1, 1)
    return start < window_end and end > window_start


def _write_rows(calls: list[dict]) -> list[dict]:
    rows = []
    for call in calls:
        method = call.get("method")
        url = str(call.get("url") or "")
        path = url.split("?", 1)[0]
        if "/calendars/primary/events" not in path:
            continue
        if method == "POST" and path.rstrip("/").endswith("/calendars/primary/events"):
            op = "insert"
            event_id = ""
        elif method == "PATCH":
            op = "patch"
            event_id = path.rstrip("/").rsplit("/", 1)[-1]
        elif method == "DELETE":
            op = "delete"
            event_id = path.rstrip("/").rsplit("/", 1)[-1]
        else:
            continue
        rows.append(
            {
                "op": op,
                "id": event_id,
                "json": call.get("json") if isinstance(call.get("json"), dict) else None,
                "status": None,
            }
        )
    return rows


def _attach_status(fake: FakeGoogle, rows: list[dict]) -> list[dict]:
    """Pair recorded write calls with the status the fake returned.

    The fake appends the call before building the response, so the status is
    recovered from the same knobs the response used.
    """
    for row in rows:
        if row["op"] == "insert":
            row["status"] = fake.insert_status
            if row["status"] in _WRITE_OK:
                row["id"] = fake.insert_id
        elif row["op"] == "patch":
            row["status"] = fake.patch_status
        elif row["op"] == "delete":
            row["status"] = fake.delete_status
    return rows


def _parse_now(case: dict) -> datetime:
    raw = str(case.get("now") or "")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return DEFAULT_NOW
    return parsed.replace(tzinfo=None, microsecond=0)


def _configure_fault(fake: FakeGoogle, case: dict) -> None:
    fault = case.get("fault") or "none"
    fake.timezone_status = 200
    fake.timezone_value = str(case.get("zone") or "Asia/Singapore")
    fake.events_status = 200
    fake.insert_status = 200
    fake.token_status = 200
    fake.insert_id = "evt-" + str(case.get("id") or "new").replace(" ", "")
    if fault == "blackhole":
        fake.timezone_status = 500
        fake.events_status = 500
    elif fault == "revoke":
        confirm = (case.get("expect") or {}).get("confirm")
        if confirm == "do_not_confirm":
            fake.timezone_status = 401
    elif fault == "insert_500":
        fake.insert_status = 500


def _arm_confirm_fault(fake: FakeGoogle, case: dict) -> None:
    if case.get("fault") == "revoke" and (case.get("expect") or {}).get("confirm") != "do_not_confirm":
        fake.timezone_status = 401


def _google_item(seed: dict, zone: str) -> dict:
    if seed.get("all_day"):
        start = {"date": str(seed["start_at"])[:10]}
        end = {"date": str(seed["end_at"])[:10]}
    else:
        start = {"dateTime": seed["start_at"], "timeZone": zone}
        end = {"dateTime": seed["end_at"], "timeZone": zone}
    item = {
        "id": seed.get("external_id") or "",
        "status": "confirmed",
        "summary": seed.get("title") or "",
        "start": start,
        "end": end,
    }
    if seed.get("location"):
        item["location"] = seed["location"]
    if seed.get("description"):
        item["description"] = seed["description"]
    return item


def _snap_local(db, user_id: int) -> list[dict]:
    from app.models.models import CalendarEvent
    from app.services.calendar_provider import invitee_labels

    db.expire_all()
    rows = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.user_id == user_id)
        .order_by(CalendarEvent.id)
        .all()
    )
    snapped = []
    for row in rows:
        snapped.append(
            {
                "id": row.id,
                "title": row.title or "",
                "start_at": row.start_at.replace(microsecond=0).isoformat() if row.start_at else "",
                "end_at": row.end_at.replace(microsecond=0).isoformat() if row.end_at else "",
                "status": row.status or "",
                "description": row.description or "",
                "participants": invitee_labels(row.participants),
                "external_id": row.external_id or "",
            }
        )
    return snapped


def _seed_user(db, user_id: int, case: dict, fake: FakeGoogle) -> dict[str, dict]:
    from app.models.models import CalendarConnection, CalendarEvent, User
    from app.services.calendar_secrets import GOOGLE_CALENDAR_SCOPES, encrypt_token

    existing = db.get(User, user_id)
    if existing is None:
        db.add(User(id=user_id, external_id=f"cal_eval_{user_id}", name="calendar-eval", is_active=1))
        db.commit()
    db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).delete(synchronize_session=False)
    db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    zone = str(case.get("zone") or "Asia/Singapore")
    seeds: dict[str, dict] = {}
    items = []
    for seed in case.get("seeds") or []:
        key = str(seed["key"])
        stored = dict(seed)
        if not stored.get("external_id"):
            stored["external_id"] = f"gcal-{key}"
        seeds[key] = stored
        if case.get("connection") == "connected":
            items.append(_google_item(stored, zone))
        if stored.get("local"):
            start = datetime.fromisoformat(stored["start_at"])
            end = datetime.fromisoformat(stored["end_at"])
            db.add(
                CalendarEvent(
                    user_id=user_id,
                    title=stored.get("title") or "",
                    start_at=start,
                    end_at=end,
                    participants=json.dumps(list(stored.get("participants") or []), ensure_ascii=False),
                    status="active",
                    timezone=zone if case.get("connection") == "connected" else "",
                    location=stored.get("location") or "",
                    description=stored.get("description") or "",
                    external_id=stored["external_id"] if case.get("connection") == "connected" else "",
                    provider="google" if case.get("connection") == "connected" and stored["external_id"] else "local",
                )
            )
    if case.get("connection") == "connected":
        db.add(
            CalendarConnection(
                user_id=user_id,
                provider="google",
                google_sub="eval-google-sub",
                account_email="calendar-eval@example.com",
                access_token=encrypt_token("eval-access"),
                refresh_token=encrypt_token("eval-refresh"),
                token_expiry=datetime.now(timezone.utc) + timedelta(hours=6),
                scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
            )
        )
    db.commit()
    fake.events_items = items if case.get("connection") == "connected" else []
    return seeds


def _cleanup_user(db, user_id: int) -> None:
    from app.models.models import CalendarConnection, CalendarEvent, User

    db.rollback()
    db.query(CalendarEvent).filter(CalendarEvent.user_id == user_id).delete(synchronize_session=False)
    db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).delete(synchronize_session=False)
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.commit()
    # synchronize_session=False leaves the deleted User in the identity map.
    # Drop it so the next _seed_user can insert the same primary key.
    db.expunge_all()


def _freeze_clock(now: datetime):
    import app.agents.tools.calendar_tool as tool
    import app.api.calendar as api
    import app.services.calendar as calendar
    import app.services.calendar_google as google

    previous = (calendar.calendar_now, tool.calendar_now, api.calendar_now, google.datetime)

    def frozen(_db=None, _user_id=None):
        return now

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now
            return now.replace(tzinfo=tz)

    calendar.calendar_now = frozen
    tool.calendar_now = frozen
    api.calendar_now = frozen
    google.datetime = _FrozenDateTime
    return previous


def _restore_clock(previous) -> None:
    import app.agents.tools.calendar_tool as tool
    import app.api.calendar as api
    import app.services.calendar as calendar
    import app.services.calendar_google as google

    calendar.calendar_now, tool.calendar_now, api.calendar_now, google.datetime = previous


def _confirm(client, case: dict, card: dict | None) -> tuple[int | None, str]:
    expect = case.get("expect") or {}
    action = expect.get("confirm")
    if action == "do_not_confirm":
        return None, ""
    if not card:
        return None, ""
    mode = str(card.get("mode") or "")
    body = {
        "title": card.get("title") or "",
        "start_at": card.get("start_at") or None,
        "end_at": card.get("end_at") or None,
        "participants": list(card.get("participants") or []),
        "location": card.get("location") or "",
        "description": card.get("description") or "",
    }
    connected = case.get("connection") == "connected"
    if connected and mode in {"update", "cancel"}:
        body["action"] = "cancel" if mode == "cancel" or action == "confirm_cancel" else "update"
        body["external_id"] = card.get("external_id") or ""
        response = client.post("/api/calendar/google/confirm", json=body)
    elif not connected and mode == "update" and card.get("event_id"):
        response = client.patch(f"/api/calendar/{card['event_id']}", json=body)
    elif not connected and mode == "cancel" and card.get("event_id"):
        response = client.delete(f"/api/calendar/{card['event_id']}")
    else:
        response = client.post("/api/calendar", json=body)
    detail = ""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        raw = payload.get("detail")
        detail = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False) if raw is not None else ""
    return response.status_code, detail


def _run_case(case: dict, *, db, client, fake: FakeGoogle, bridge: _LLMBridge, user_id: int) -> dict:
    from app.agents.harness.types import ToolContext
    from app.agents.tools.calendar_tool import execute_calendar
    from app.services.calendar_secrets import CalendarTokenError, CalendarTransientError

    bridge.infra = False
    now = _parse_now(case)
    _configure_fault(fake, case)
    seeds = _seed_user(db, user_id, case, fake)
    seeded = _snap_local(db, user_id)
    fake.reset_calls()
    history: list[dict] = []
    card = None
    answer = ""
    pre_writes = 0
    previous_clock = _freeze_clock(now)
    try:
        for index, turn in enumerate(case.get("turns") or [], start=1):
            utterance = str(turn.get("utterance") or "")
            ctx = ToolContext(
                db=db,
                message=utterance,
                history=list(history),
                user_id=user_id,
                iteration=index,
            )
            before = len(_write_rows(fake.calls))
            try:
                result = execute_calendar(ctx, None)
                answer = result.direct_answer or ""
                card = result.schedule_card
            except CacheMissError:
                raise
            except LLMInfraError:
                bridge.infra = True
                break
            except (CalendarTokenError, CalendarTransientError) as exc:
                answer = str(exc)
                card = None
            if bridge.infra:
                break
            pre_writes += len(_write_rows(fake.calls)) - before
            history.append({"role": "user", "content": utterance})
            history.append({"role": "assistant", "content": answer})
        local_after_turns = _snap_local(db, user_id)
        http_status = None
        detail = ""
        confirm_writes: list[dict] = []
        if not bridge.infra:
            fake.reset_calls()
            _arm_confirm_fault(fake, case)
            http_status, detail = _confirm(client, case, card)
            confirm_writes = _attach_status(fake, _write_rows(fake.calls))
        local_final = _snap_local(db, user_id)
    finally:
        _restore_clock(previous_clock)
    if bridge.infra:
        return {
            "id": case["id"],
            "category": case["category"],
            "time_complete": bool(case.get("time_complete")),
            "passed": False,
            "infra_error": True,
            "clock_exact": None,
            "failed_checks": [],
        }
    pre_changed = _identity(seeded) != _identity(local_after_turns)
    obs = {
        "card": card,
        "direct_answer": answer,
        "confirm_detail": detail,
        "http_status": http_status,
        "google_writes": confirm_writes,
        "pre_confirm_writes": pre_writes,
        "pre_confirm_local_changed": pre_changed,
        "local_seed": seeded,
        "local_final": local_final,
        "seeds": seeds,
    }
    failed = evaluate_case(case, obs)
    clock = None
    if case.get("time_complete"):
        clock = "clock_exact" not in failed
    return {
        "id": case["id"],
        "category": case["category"],
        "time_complete": bool(case.get("time_complete")),
        "passed": not failed,
        "infra_error": False,
        "clock_exact": clock,
        "failed_checks": failed,
    }


def _identity(rows: list[dict]) -> list[tuple]:
    return [
        (
            row.get("id"),
            row.get("title"),
            row.get("start_at"),
            row.get("end_at"),
            row.get("status"),
            row.get("description"),
        )
        for row in rows
    ]


def _ensure_token_key() -> None:
    from cryptography.fernet import Fernet

    from app.core.config import settings

    current = (settings.calendar_token_key or "").strip()
    if current:
        try:
            Fernet(current.encode("utf-8"))
            return
        except Exception:
            pass
    settings.calendar_token_key = Fernet.generate_key().decode("ascii")
    if not (settings.google_oauth_client_id or "").strip():
        settings.google_oauth_client_id = "calendar-eval-client"
    if not (settings.google_oauth_client_secret or "").strip():
        settings.google_oauth_client_secret = "calendar-eval-secret"


def run_calendar_eval(
    cases: list[dict],
    *,
    cache_path: Path,
    refresh_cache: bool = False,
    require_cache: bool = False,
    split: str = "full",
) -> dict:
    """Score ``cases``. Raises ``CacheMissError`` when smoke replay cannot continue."""
    from fastapi.testclient import TestClient

    from app.core.auth import create_session_token
    from app.core.config import settings
    from app.core.db import SessionLocal, init_db
    from app.main import app

    _ensure_token_key()
    init_db()
    cache = {} if refresh_cache else _load_cache(cache_path)
    bridge = _LLMBridge(cache, refresh=refresh_cache, require=require_cache)
    fake = FakeGoogle()
    import app.services.calendar_secrets as secrets

    previous_http = secrets._http_request
    secrets._http_request = fake
    bridge.install()
    db = SessionLocal()
    user_id = 94021
    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, create_session_token(user_id))
    rows: list[dict] = []
    try:
        for case in cases:
            try:
                rows.append(_run_case(case, db=db, client=client, fake=fake, bridge=bridge, user_id=user_id))
            finally:
                _cleanup_user(db, user_id)
                db.commit()
    finally:
        bridge.restore()
        secrets._http_request = previous_http
        db.close()
    if refresh_cache or bridge.live:
        _save_cache(cache_path, cache)
    mode = "live" if bridge.live else "cache"
    report = summarize(rows, model=resolve(Role.PLANNER).model, mode=mode, split=split)
    report["cache_hits"] = bridge.hits
    report["cache_misses"] = bridge.live
    return report


def _format(report: dict) -> str:
    lines = [
        f"=== calendar split={report.get('split')} n={report.get('n')} scored={report.get('n_scored')} ===",
        f"  model {report.get('model')}  mode {report.get('mode')}",
        f"  pass_rate {report.get('pass_rate'):.2%}  hard {report.get('hard_pass_rate'):.2%}",
        f"  clock_exact {report.get('clock_exact_rate'):.2%}  infra {report.get('infra_error_rate'):.2%}",
    ]
    failures = report.get("failures") or []
    if failures:
        lines.append(f"Failures ({len(failures)}):")
        for row in failures[:20]:
            lines.append(f"  - {row.get('id')} {row.get('category')} {row.get('failed_checks')}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> SuiteResult:
    cases = load_cases(args.dataset, split=args.split)
    try:
        report = run_calendar_eval(
            cases,
            cache_path=Path(args.cache),
            refresh_cache=bool(args.refresh_cache),
            require_cache=bool(args.require_cache),
            split=args.split,
        )
    except CacheMissError as exc:
        print(
            f"\nBLOCKED: {exc}\n"
            "提示：请先运行完整档刷新缓存："
            "python scripts/eval.py run calendar --profile full",
            file=sys.stderr,
        )
        return SuiteResult(
            report={
                "error": str(exc),
                "blocked": "calendar_cache_miss",
                "n": len(cases),
                "split": args.split,
                "failures": [
                    {
                        "id": "calendar_cache",
                        "status": "blocked",
                        "message": "calendar cache missing/stale; run --profile full to refresh",
                    }
                ],
            },
            samples=[{"id": "calendar_cache", "status": "blocked", "message": str(exc)}],
            exit_code=2,
            dataset_path=Path(args.dataset),
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format(report))
    samples = list(report.get("failures") or [])
    return SuiteResult(
        report=report,
        samples=samples,
        exit_code=0,
        dataset_path=Path(args.dataset),
    )


def main() -> int:
    args = build_parser().parse_args()
    run_dir = make_run_dir("calendar", "adhoc")
    result = run(args)
    write_run(
        RunContext(
            suite="calendar",
            profile="adhoc",
            target="venv",
            argv=sys.argv,
            started_at=datetime.now(timezone.utc),
            run_dir=run_dir,
            dataset_path=Path(args.dataset),
            key_metrics=["raw.pass_rate", "raw.clock_exact_rate"],
        ),
        result,
    )
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
