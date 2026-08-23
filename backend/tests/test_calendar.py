from datetime import datetime

from app.services.calendar import EventDraft, serialize_event_card, suggest_next_slot


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
    assert card["participants"] == ["张三", "李四"]


def test_suggest_next_slot_keeps_duration():
    start = datetime(2026, 8, 6, 10, 0)
    end = datetime(2026, 8, 6, 11, 30)
    next_start, next_end = suggest_next_slot(start, end)
    assert next_start == end
    assert (next_end - next_start).seconds == 5400


if __name__ == "__main__":
    test_serialize_event_card_reads_participants()
    test_suggest_next_slot_keeps_duration()
    print("ok")
