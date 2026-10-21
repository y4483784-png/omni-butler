"""Score one calendar case from a card, a fake Google log, and local rows.

The comparison is pure: it does not call a model and it does not read a
case's expected JSON as if it were a model response. ``time_complete`` items
pass the clock check only when the card's start and end equal the gold wall
times, including times earlier than ``now``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

HARD_CATEGORIES = frozenset(
    {
        "confirm_before_write",
        "meeting_block",
        "id_mutate",
        "free_busy",
        "notes_description",
        "wall_clock",
    }
)

CARD_TYPES = frozenset(
    {
        "meeting_create",
        "block_create",
        "meeting_update",
        "block_update",
        "cancel",
        "list",
        "none",
    }
)

_WRITE_OK = frozenset({200, 201, 204})


def card_type(card: dict | None) -> str:
    """Map a product schedule card onto the suite's type names.

    An empty ``kind`` is a meeting. A cancel card stays ``cancel`` even when
    the matched event was a block.
    """
    if not card:
        return "none"
    mode = str(card.get("mode") or card.get("action") or "")
    kind = str(card.get("kind") or "").strip().lower()
    if mode == "list":
        return "list"
    if mode == "cancel":
        return "cancel"
    if mode == "create":
        return "block_create" if kind == "block" else "meeting_create"
    if mode == "update":
        return "block_update" if kind == "block" else "meeting_update"
    return "none"


def card_phase(card: dict | None) -> str:
    if not card:
        return "absent"
    phase = str(card.get("phase") or "")
    if phase in {"draft", "info"}:
        return phase
    return "absent"


def _parse_wall(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text or "+" in text or text.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return None
    return parsed


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    """Touching endpoints do not overlap."""
    return start < other_end and end > other_start


def _slot_span(slot: dict) -> tuple[datetime, datetime] | None:
    start = _parse_wall(str(slot.get("start_at") or ""))
    end = _parse_wall(str(slot.get("end_at") or ""))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _minutes(start: str, end: str) -> int | None:
    left = _parse_wall(start)
    right = _parse_wall(end)
    if left is None or right is None:
        return None
    return int((right - left).total_seconds() // 60)


def _successful_writes(writes: list[dict]) -> list[dict]:
    kept = []
    for row in writes or []:
        status = row.get("status")
        try:
            code = int(status) if status is not None else 0
        except (TypeError, ValueError):
            code = 0
        if code in _WRITE_OK:
            kept.append(row)
    return kept


def _visible_text(obs: dict) -> str:
    parts = [str(obs.get("direct_answer") or ""), str(obs.get("confirm_detail") or "")]
    return "\n".join(part for part in parts if part)


def _seed_map(obs: dict) -> dict[str, dict]:
    seeds = obs.get("seeds") or {}
    return seeds if isinstance(seeds, dict) else {}


def _check_clock(card: dict | None, fields: dict, failed: list[str]) -> bool:
    """Exact wall-clock match for a draft card.

    A list card has no start of its own. A suggested slot that cuts the gold
    window must be that window; a different clock is ``clock_exact``. Empty
    suggestions are not a rewritten clock.
    """
    gold_start = str(fields.get("start_at") or "")
    gold_end = str(fields.get("end_at") or "")
    kind = card_type(card)
    if kind == "list":
        gold_span = _slot_span({"start_at": gold_start, "end_at": gold_end})
        if gold_span is None:
            failed.append("clock_exact")
            return False
        for slot in (card or {}).get("suggested_slots") or []:
            if not isinstance(slot, dict):
                continue
            span = _slot_span(slot)
            if span is None:
                continue
            if _overlaps(*span, *gold_span):
                if str(slot.get("start_at") or "") != gold_start or str(slot.get("end_at") or "") != gold_end:
                    failed.append("clock_exact")
                    return False
        return True
    observed_start = str((card or {}).get("start_at") or "")
    observed_end = str((card or {}).get("end_at") or "")
    if observed_start != gold_start or observed_end != gold_end:
        failed.append("clock_exact")
        return False
    return True


def _check_fields(card: dict | None, fields: dict, failed: list[str]) -> None:
    if not isinstance(fields, dict):
        return
    current = card or {}
    for key, gold in fields.items():
        if key in {"start_at", "end_at"}:
            continue
        if key == "duration_minutes":
            got = _minutes(str(current.get("start_at") or ""), str(current.get("end_at") or ""))
            if got != int(gold):
                failed.append("card.fields.duration_minutes")
            continue
        if key == "participants":
            got = list(current.get("participants") or [])
            if got != list(gold or []):
                failed.append("card.fields.participants")
            continue
        if key == "external_id":
            got = str(current.get("external_id") or "")
            want = "" if gold in ("", "absent", None) else str(gold)
            if got != want:
                failed.append("card.fields.external_id")
            continue
        if str(current.get(key) or "") != str(gold or ""):
            failed.append(f"card.fields.{key}")


def _check_slots(card: dict | None, card_expect: dict, failed: list[str]) -> None:
    slots = [slot for slot in ((card or {}).get("suggested_slots") or []) if isinstance(slot, dict)]
    mode = card_expect.get("suggested_slots")
    if mode == "absent" and slots:
        failed.append("card.suggested_slots")
    elif mode == "present_ok" and not slots:
        failed.append("card.suggested_slots")
    spans = []
    for slot in slots:
        span = _slot_span(slot)
        if span is not None:
            spans.append(span)
    if mode == "non_overlapping":
        for index, left in enumerate(spans):
            for right in spans[index + 1 :]:
                if _overlaps(*left, *right):
                    failed.append("card.suggested_slots")
                    break
    ranges = card_expect.get("must_not_overlap") or []
    if ranges:
        busy = []
        for item in ranges:
            if not isinstance(item, dict):
                failed.append("card.must_not_overlap")
                continue
            span = _slot_span(item)
            if span is None:
                failed.append("card.must_not_overlap")
                continue
            busy.append(span)
        for slot in spans:
            for block in busy:
                if _overlaps(*slot, *block):
                    failed.append("card.must_not_overlap")
                    return
    titles = card_expect.get("must_list_titles")
    if titles is not None:
        have = {
            str(item.get("title") or "")
            for item in ((card or {}).get("items") or [])
            if isinstance(item, dict)
        }
        missing = [title for title in titles if title not in have]
        if missing:
            failed.append("card.must_list_titles")


def _check_google(expect: dict, obs: dict, failed: list[str]) -> None:
    google = expect.get("google") or {}
    if not isinstance(google, dict):
        return
    writes = _successful_writes(list(obs.get("google_writes") or []))
    ops = [str(row.get("op") or "") for row in writes]
    want_op = google.get("op")
    if want_op == "none" and ops:
        failed.append("google.op")
    elif want_op == "create" and ops != ["insert"]:
        failed.append("google.op")
    elif want_op == "update" and ops != ["patch"]:
        failed.append("google.op")
    elif want_op == "delete" and ops != ["delete"]:
        failed.append("google.op")

    seeds = _seed_map(obs)
    want_id = google.get("external_id")
    if want_id is not None:
        card_id = str((obs.get("card") or {}).get("external_id") or "")
        write_ids = [str(row.get("id") or "") for row in writes if str(row.get("id") or "")]
        if want_id == "absent":
            # The card carries no Google id, and confirm did not write one.
            if card_id or write_ids:
                failed.append("google.external_id")
        elif want_id == "new":
            seed_ids = {str(seed.get("external_id") or "") for seed in seeds.values() if seed.get("external_id")}
            if len(write_ids) != 1 or write_ids[0] in seed_ids:
                failed.append("google.external_id")
        elif str(want_id).startswith("seed:"):
            key = str(want_id).split(":", 1)[1]
            seed = seeds.get(key) or {}
            gid = str(seed.get("external_id") or "")
            if not gid or card_id != gid or (write_ids and write_ids != [gid]):
                failed.append("google.external_id")
        else:
            failed.append("google.external_id")

    match = google.get("match") or {}
    if match:
        body = {}
        for row in writes:
            if isinstance(row.get("json"), dict):
                body = row["json"]
        if not _match_body(match, body, obs):
            failed.append("google.match")

    unchanged = google.get("unchanged")
    if unchanged is not None:
        touched = {str(row.get("id") or "") for row in writes if row.get("op") in {"patch", "delete"}}
        for key in unchanged:
            seed = seeds.get(key) or {}
            gid = str(seed.get("external_id") or "")
            if gid and gid in touched:
                failed.append("google.unchanged")
                break


def _match_body(match: dict, body: dict, obs: dict) -> bool:
    if "title" in match and str(body.get("summary") or "") != str(match.get("title") or ""):
        return False
    if "description" in match and str(body.get("description") or "") != str(match.get("description") or ""):
        return False
    if "location" in match and str(body.get("location") or "") != str(match.get("location") or ""):
        return False
    if "start_at" in match:
        got = str(((body.get("start") or {}) if isinstance(body.get("start"), dict) else {}).get("dateTime") or "")
        if got != str(match.get("start_at") or ""):
            return False
    if "end_at" in match:
        got = str(((body.get("end") or {}) if isinstance(body.get("end"), dict) else {}).get("dateTime") or "")
        if got != str(match.get("end_at") or ""):
            return False
    if "attendees" in match:
        attendees = body.get("attendees")
        if match.get("attendees") in ([], None, ""):
            if attendees:
                return False
        elif attendees != match.get("attendees"):
            return False
    if "participants" in match or "description" in match or "title" in match:
        # Bare names stay on the local row. Emails are attendees when the case
        # asks. Description on the Google body is checked above. A local
        # description is checked with local_row.
        if "participants" in match:
            rows = list(obs.get("local_final") or [])
            wanted = list(match.get("participants") or [])
            if not any(list(row.get("participants") or []) == wanted for row in rows):
                return False
    return True


def _check_local(expect: dict, obs: dict, failed: list[str]) -> None:
    mode = expect.get("local_row")
    if mode is None:
        return
    before = list(obs.get("local_seed") or [])
    final = list(obs.get("local_final") or [])
    if mode == "unchanged":
        if _row_identity(before) != _row_identity(final):
            failed.append("local_row")
        return
    if mode == "absent":
        seeded_ids = {row.get("id") for row in before}
        extra = [row for row in final if row.get("id") not in seeded_ids]
        if extra:
            failed.append("local_row")
        return
    if mode == "inserted":
        seeded_ids = {row.get("id") for row in before}
        extra = [row for row in final if row.get("id") not in seeded_ids and row.get("status") != "cancelled"]
        if len(extra) != 1:
            failed.append("local_row")
            return
        fields = ((expect.get("card") or {}).get("fields") or {}) if isinstance(expect.get("card"), dict) else {}
        match = ((expect.get("google") or {}).get("match") or {}) if isinstance(expect.get("google"), dict) else {}
        row = extra[0]
        title = fields.get("title", match.get("title"))
        start = fields.get("start_at", match.get("start_at"))
        end = fields.get("end_at", match.get("end_at"))
        if title is not None and str(row.get("title") or "") != str(title):
            failed.append("local_row")
        if start is not None and str(row.get("start_at") or "") != str(start):
            failed.append("local_row")
        if end is not None and str(row.get("end_at") or "") != str(end):
            failed.append("local_row")
        description = fields.get("description", match.get("description"))
        if description is not None and str(row.get("description") or "") != str(description):
            failed.append("local_row")
        return
    if mode == "updated":
        if len(final) != len(before) or not before:
            failed.append("local_row")
            return
        fields = ((expect.get("card") or {}).get("fields") or {}) if isinstance(expect.get("card"), dict) else {}
        changed = [row for row in final if _row_identity([row]) != _row_identity_by_id(before, row.get("id"))]
        if not changed:
            failed.append("local_row")
            return
        row = changed[0]
        if "start_at" in fields and str(row.get("start_at") or "") != str(fields["start_at"]):
            failed.append("local_row")
        if "end_at" in fields and str(row.get("end_at") or "") != str(fields["end_at"]):
            failed.append("local_row")
        if "description" in fields and str(row.get("description") or "") != str(fields["description"]):
            failed.append("local_row")
        if "title" in fields and str(row.get("title") or "") != str(fields["title"]):
            failed.append("local_row")
        return
    failed.append("local_row")


def _row_identity(rows: list[dict]) -> list[tuple]:
    return sorted(
        (
            row.get("id"),
            str(row.get("title") or ""),
            str(row.get("start_at") or ""),
            str(row.get("end_at") or ""),
            str(row.get("status") or ""),
            str(row.get("description") or ""),
        )
        for row in rows
    )


def _row_identity_by_id(rows: list[dict], row_id: Any) -> list[tuple]:
    return _row_identity([row for row in rows if row.get("id") == row_id])


def evaluate_case(case: dict, obs: dict) -> list[str]:
    """Return failed check names. An empty list is a pass.

    Transport failures are not scored here; the runner marks those ``infra_error``
    and leaves them out of the denominators.
    """
    failed: list[str] = []
    expect = case.get("expect") or {}
    card = obs.get("card")
    if obs.get("pre_confirm_writes"):
        failed.append("before_confirm_google")
    if obs.get("pre_confirm_local_changed"):
        failed.append("local_pre_confirm")
    if expect.get("before_confirm_google") == "none" and obs.get("pre_confirm_writes"):
        if "before_confirm_google" not in failed:
            failed.append("before_confirm_google")

    card_expect = expect.get("card") or {}
    if isinstance(card_expect, dict):
        if card_expect.get("type") is not None and card_type(card) != card_expect.get("type"):
            failed.append("card.type")
        if card_expect.get("phase") is not None and card_phase(card) != card_expect.get("phase"):
            failed.append("card.phase")
        fields = card_expect.get("fields") or {}
        if case.get("time_complete") and isinstance(fields, dict) and "start_at" in fields and "end_at" in fields:
            _check_clock(card, fields, failed)
        if isinstance(fields, dict):
            _check_fields(card, fields, failed)
        _check_slots(card, card_expect, failed)

    _check_google(expect, obs, failed)
    if "http_status" in expect:
        if obs.get("http_status") != expect.get("http_status"):
            failed.append("http_status")
    visible = _visible_text(obs)
    for snippet in expect.get("copy_must") or []:
        if snippet not in visible:
            failed.append("copy_must")
            break
    for snippet in expect.get("copy_must_not") or []:
        if snippet and snippet in visible:
            failed.append("copy_must_not")
            break
    _check_local(expect, obs, failed)
    confirm = expect.get("confirm")
    if confirm == "do_not_confirm" and obs.get("http_status") is not None:
        failed.append("confirm")
    if confirm in {"confirm_create", "confirm_update", "confirm_cancel"} and obs.get("http_status") is None:
        failed.append("confirm")
    return list(dict.fromkeys(failed))


def _rate(passed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return passed / total


def summarize(rows: list[dict], *, model: str, mode: str, split: str) -> dict[str, Any]:
    """Top-level suite report. Infra rows stay out of every accuracy denominator."""
    scored = [row for row in rows if not row.get("infra_error")]
    passed_rows = [row for row in scored if row.get("passed")]
    hard = [row for row in scored if row.get("category") in HARD_CATEGORIES]
    hard_passed = [row for row in hard if row.get("passed")]
    clock = [row for row in scored if row.get("time_complete")]
    clock_ok = [row for row in clock if row.get("clock_exact")]
    n_infra = sum(1 for row in rows if row.get("infra_error"))
    categories = (
        "confirm_before_write",
        "meeting_block",
        "id_mutate",
        "free_busy",
        "notes_description",
        "wall_clock",
        "create_meeting",
        "create_block",
        "list_schedule",
        "conflict",
        "attendees",
        "error_paths",
    )
    slices: dict[str, dict[str, Any]] = {}
    for category in categories:
        group = [row for row in scored if row.get("category") == category]
        group_passed = [row for row in group if row.get("passed")]
        slices[category] = {
            "n": len(group),
            "passed": len(group_passed),
            "pass_rate": _rate(len(group_passed), len(group)),
        }
    failures = [
        {
            "id": row.get("id"),
            "category": row.get("category"),
            "failed_checks": list(row.get("failed_checks") or []),
        }
        for row in rows
        if row.get("infra_error") or not row.get("passed")
    ]
    return {
        "n": len(rows),
        "n_scored": len(scored),
        "passed": len(passed_rows),
        "pass_rate": _rate(len(passed_rows), len(scored)),
        "hard_n": len(hard),
        "hard_pass_rate": _rate(len(hard_passed), len(hard)),
        "n_clock": len(clock),
        "clock_exact_rate": _rate(len(clock_ok), len(clock)),
        "slices": slices,
        "infra_error_rate": (n_infra / len(rows)) if rows else 0.0,
        "model": model,
        "mode": mode,
        "degraded": split == "full" and mode != "live",
        "split": split,
        "failures": [row for row in failures if not _is_infra_failure(rows, row)],
    }


def _is_infra_failure(rows: list[dict], failure: dict) -> bool:
    for row in rows:
        if row.get("id") == failure.get("id"):
            return bool(row.get("infra_error"))
    return False
