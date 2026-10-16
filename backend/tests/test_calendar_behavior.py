"""Pure calendar judge checks. The 150-item score stays on eval.py."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from app.eval.calendar_behavior import evaluate_case, summarize


def _obs(card, **extra):
    body = {
        "card": card,
        "direct_answer": "",
        "confirm_detail": "",
        "http_status": None,
        "google_writes": [],
        "pre_confirm_writes": 0,
        "pre_confirm_local_changed": False,
        "local_seed": [],
        "local_final": [],
        "seeds": {},
    }
    body.update(extra)
    return body


@pytest.mark.unit
def test_past_clock_mismatch_fails_and_stays_named_clock_exact():
    case = {
        "time_complete": True,
        "expect": {
            "confirm": "do_not_confirm",
            "card": {
                "type": "meeting_create",
                "phase": "draft",
                "fields": {
                    "start_at": "2026-10-15T09:00:00",
                    "end_at": "2026-10-15T10:00:00",
                },
            },
        },
    }
    card = {
        "mode": "create",
        "phase": "draft",
        "kind": "meeting",
        "title": "已过晨会",
        "start_at": "2026-10-15T10:00:00",
        "end_at": "2026-10-15T11:00:00",
        "participants": [],
        "location": "",
        "description": "",
        "external_id": "",
    }
    failed = evaluate_case(case, _obs(card))
    assert "clock_exact" in failed


@pytest.mark.unit
def test_past_clock_exact_match_passes():
    case = {
        "time_complete": True,
        "expect": {
            "confirm": "do_not_confirm",
            "before_confirm_google": "none",
            "card": {
                "type": "meeting_create",
                "phase": "draft",
                "fields": {
                    "title": "已过晨会",
                    "start_at": "2026-10-15T09:00:00",
                    "end_at": "2026-10-15T10:00:00",
                    "duration_minutes": 60,
                },
            },
            "google": {"op": "none", "external_id": "absent"},
            "local_row": "unchanged",
        },
    }
    card = {
        "mode": "create",
        "phase": "draft",
        "kind": "meeting",
        "title": "已过晨会",
        "start_at": "2026-10-15T09:00:00",
        "end_at": "2026-10-15T10:00:00",
        "participants": [],
        "location": "",
        "description": "",
        "external_id": "",
    }
    assert evaluate_case(case, _obs(card)) == []


@pytest.mark.unit
def test_touching_endpoints_are_not_an_overlap():
    case = {
        "time_complete": False,
        "expect": {
            "confirm": "do_not_confirm",
            "card": {
                "type": "list",
                "phase": "info",
                "suggested_slots": "non_overlapping",
                "must_not_overlap": [
                    {"start_at": "2026-10-16T15:00:00", "end_at": "2026-10-16T16:00:00"}
                ],
            },
        },
    }
    card = {
        "mode": "list",
        "phase": "info",
        "suggested_slots": [
            {"start_at": "2026-10-16T16:00:00", "end_at": "2026-10-16T17:00:00"}
        ],
        "items": [{"title": "端点甲"}],
    }
    assert evaluate_case(case, _obs(card)) == []


@pytest.mark.unit
def test_block_card_must_clear_people_and_place():
    case = {
        "time_complete": True,
        "expect": {
            "confirm": "do_not_confirm",
            "card": {
                "type": "block_create",
                "fields": {
                    "start_at": "2026-10-16T15:00:00",
                    "end_at": "2026-10-16T16:00:00",
                    "participants": [],
                    "location": "",
                },
            },
        },
    }
    card = {
        "mode": "create",
        "phase": "draft",
        "kind": "block",
        "start_at": "2026-10-16T15:00:00",
        "end_at": "2026-10-16T16:00:00",
        "participants": ["张三"],
        "location": "三号会议室",
    }
    failed = evaluate_case(case, _obs(card))
    assert "card.fields.participants" in failed
    assert "card.fields.location" in failed
    assert "clock_exact" not in failed


@pytest.mark.unit
def test_all_infra_does_not_score_as_a_pass():
    rows = [
        {
            "id": "CAL-001",
            "category": "wall_clock",
            "time_complete": True,
            "passed": False,
            "infra_error": True,
            "clock_exact": None,
            "failed_checks": [],
        }
    ]
    report = summarize(rows, model="planner", mode="live", split="full")
    assert report["n_scored"] == 0
    assert report["pass_rate"] == 0.0
    assert report["hard_pass_rate"] == 0.0
    assert report["clock_exact_rate"] == 0.0
    assert report["infra_error_rate"] == 1.0
    assert report["failures"] == []
    assert report["slices"]["wall_clock"]["n"] == 0
    assert set(report["slices"]) == {
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
    }


@pytest.mark.unit
def test_text_cache_hit_is_not_treated_as_a_key():
    from scripts.run_calendar_eval import CacheMissError, _LLMBridge, _fingerprint

    messages = [{"role": "user", "content": "advice"}]
    key = _fingerprint("text", messages, None)
    bridge = _LLMBridge(
        {key: {"kind": "text", "value": "看列表即可。"}},
        refresh=False,
        require=True,
    )
    assert bridge.complete_text(messages) == "看列表即可。"
    assert bridge.live == 0

    missing = _LLMBridge({}, refresh=False, require=True)
    with pytest.raises(CacheMissError):
        missing.complete_text(messages)


def test_require_cache_blocks_without_scoring(tmp_path: Path):
    from scripts.run_calendar_eval import DATASET, run

    args = argparse.Namespace(
        dataset=DATASET,
        cache=tmp_path / "empty.json",
        split="smoke",
        refresh_cache=False,
        require_cache=True,
        json=False,
    )
    result = run(args)
    assert result.exit_code == 2
    assert result.report["blocked"] == "calendar_cache_miss"


def test_runner_fails_a_wrong_clock_and_passes_an_exact_one(tmp_path: Path):
    import app.agents.tools.calendar_tool as tool
    import app.services.calendar as calendar
    from scripts.run_calendar_eval import run_calendar_eval

    original_json = calendar.complete_json
    original_text = tool.complete_text

    def stub_json(messages, model=None, *, raise_on_error=False):
        user = str(messages[-1].get("content") or "")
        if "九点" in user:
            start, end, title = "2026-10-16T09:00:00", "2026-10-16T10:00:00", "错点"
        else:
            start, end, title = "2026-10-16T15:00:00", "2026-10-16T16:00:00", "未连甲"
        return {
            "title": title,
            "start_at": start,
            "end_at": end,
            "participants": [],
            "missing_fields": [],
            "action": "create",
            "day": "2026-10-16",
            "kind": "meeting",
            "description": "",
            "location": "",
        }

    def stub_text(*_args, **_kwargs):
        return "看列表即可。"

    def case(utterance, title, start, end):
        return {
            "id": "CAL-TEST",
            "split": "full",
            "category": "create_meeting",
            "tags": ["create_meeting", "disconnected"],
            "time_complete": True,
            "now": "2026-10-15T10:00:00",
            "zone": "Asia/Singapore",
            "connection": "disconnected",
            "fault": "none",
            "seeds": [],
            "turns": [{"utterance": utterance}],
            "expect": {
                "confirm": "do_not_confirm",
                "before_confirm_google": "none",
                "card": {
                    "type": "meeting_create",
                    "phase": "draft",
                    "fields": {
                        "title": title,
                        "start_at": start,
                        "end_at": end,
                        "duration_minutes": 60,
                        "kind": "meeting",
                    },
                },
                "google": {"op": "none", "external_id": "absent"},
                "local_row": "unchanged",
            },
        }

    calendar.complete_json = stub_json
    tool.complete_text = stub_text
    try:
        report = run_calendar_eval(
            [
                case("明天下午三点开会一小时，主题是未连甲", "未连甲", "2026-10-16T15:00:00", "2026-10-16T16:00:00"),
                case("明天上午九点开会一小时，主题是错点", "错点", "2026-10-16T15:00:00", "2026-10-16T16:00:00"),
            ],
            cache_path=tmp_path / "cache.json",
            refresh_cache=False,
            require_cache=False,
            split="full",
        )
    finally:
        calendar.complete_json = original_json
        tool.complete_text = original_text
    assert report["passed"] == 1
    assert report["n_scored"] == 2
    assert report["failures"][0]["failed_checks"]
    assert "clock_exact" in report["failures"][0]["failed_checks"]
