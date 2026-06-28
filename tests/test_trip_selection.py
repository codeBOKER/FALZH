from app.whatsapp.trip_selection import build_trip_selection_list, parse_trip_action_reply


def test_parse_trip_action_reply():
    assert parse_trip_action_reply("DELETE_trip-1") == ("DELETE", "trip-1")
    assert parse_trip_action_reply("MODIFY_abc-123") == ("MODIFY", "abc-123")
    assert parse_trip_action_reply("bad") is None


def test_build_trip_selection_list_text_message():
    result = build_trip_selection_list(
        trips=[
            {
                "id": "trip-1",
                "departure": "Aden",
                "destination": "Very Long Destination Name Here",
                "departure_date": "2026-12-01",
                "departure_time": "noon",
            }
        ],
        action_type="DELETE",
    )

    assert result["type"] == "text"
    body = result["text"]["body"]
    assert "📋" in body
    assert "🗑️" in body
    assert "قائمة الرحلات" in body
