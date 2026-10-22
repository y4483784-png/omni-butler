from datetime import datetime

from app.services.calendar import EventDraft, _classify_prompt, serialize_event_card, suggest_next_slot


def test_serialize_event_card_reads_participants():
    class E:
        id = 7
        title = "周会"
        start_at = datetime(2026, 8, 6, 10, 0)
        end_at = datetime(2026, 8, 6, 11, 0)
        participants = '["张三","李四"]'
        status = "active"

    card = serialize_event_card(E())
    assert card["id"] == 7
    assert card["title"] == "周会"
    assert card["participants"] == [
        {"email": "", "name": "张三"},
        {"email": "", "name": "李四"},
    ]
    assert card["status"] == "active"
    # Objects from before the reserved columns still serialize; missing fields stay blank.
    assert card["timezone"] == ""
    assert card["location"] == ""
    assert card["description"] == ""
    assert card["external_id"] == ""
    assert card["provider"] == ""


def test_pick_event_title_only_matches_the_old_rule():
    from types import SimpleNamespace

    from app.services.calendar import EventHint, pick_event

    monday = datetime(2026, 10, 19, 10, 0)
    later = datetime(2026, 10, 19, 15, 0)
    rows = [
        SimpleNamespace(title="客户周会", start_at=monday),
        SimpleNamespace(title="客户周会", start_at=later),
        SimpleNamespace(title="内部同步", start_at=monday),
    ]
    assert pick_event(rows, EventHint(title="")) is None
    assert pick_event(rows, EventHint(title="客户周会")) is None
    assert pick_event(rows, EventHint(title="内部同步")).title == "内部同步"
    assert pick_event(rows, EventHint(title="周会")) is None
    only = [rows[0], rows[2]]
    assert pick_event(only, EventHint(title="周会")).title == "客户周会"
    assert pick_event(rows, EventHint(day=later.date(), clock=later.time())).title == "客户周会"


def test_suggest_next_slot_keeps_duration():
    start = datetime(2026, 8, 6, 10, 0)
    end = datetime(2026, 8, 6, 11, 30)
    next_start, next_end = suggest_next_slot(start, end)
    assert next_start == end
    assert (next_end - next_start).seconds == 5400


def test_classify_prompt_names_the_short_prepare_phrase():
    prompt = _classify_prompt(datetime(2026, 10, 22), "Asia/Shanghai")
    assert "帮我准备一下" in prompt
    assert "prep" in prompt


if __name__ == "__main__":
    test_serialize_event_card_reads_participants()
    test_suggest_next_slot_keeps_duration()
    print("ok")
