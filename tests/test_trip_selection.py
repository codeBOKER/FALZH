from app.whatsapp.trip_selection import format_trip_card


def test_format_trip_card():
    trip = {
        "id": "trip-1",
        "departure": "صنعاء",
        "destination": "تعز",
        "departure_date": "2026-12-01",
        "departure_time": "noon",
        "available_seats": 3,
        "total_seats": 4,
        "price": 5000,
        "driver_cars": [{"car_type": "باص"}],
        "drivers": [{"name": "أحمد"}],
        "selection_count": 2,
    }
    card = format_trip_card(trip)
    assert "صنعاء" in card
    assert "تعز" in card
    assert "2026-12-01" in card
    assert "ظهرا" in card
    assert "3 من 4" in card
    assert "5000" in card
    assert "باص" in card
    assert "أحمد" in card
    assert "2" in card
