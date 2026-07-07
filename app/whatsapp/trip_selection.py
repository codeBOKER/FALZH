from typing import Any

from app.utils.departure import trip_departure_bucket, trip_departure_date

_BUCKET_LABELS = {
    "morning": "صباحا",
    "noon": "ظهرا",
    "night": "مساء",
}

def format_trip_card(trip: dict[str, Any]) -> str:
    departure = str(trip.get("departure") or "")
    destination = str(trip.get("destination") or "")
    parsed_date = trip_departure_date(trip)
    date_text = parsed_date.isoformat() if parsed_date else ""
    bucket = trip_departure_bucket(trip)
    bucket_text = _BUCKET_LABELS.get(bucket, "") if bucket else ""

    driver = _first_dict_value(trip.get("drivers")) or {}
    car = _first_dict_value(trip.get("driver_cars")) or {}
    driver_name = driver.get("name") or trip.get("driver_name") or ""
    car_type = car.get("car_type") or trip.get("car_type") or ""

    available = trip.get("available_seats") or 0
    total = trip.get("total_seats") or 0
    price = trip.get("price") or ""
    selection_count = trip.get("selection_count")

    lines = [
        "─" * 14,
        f"من: {departure} ← إلى: {destination}",
        f"التاريخ: {date_text} | الوقت: {bucket_text}",
        f"المقاعد: {available} من {total} متاحة",
        f"السعر: {price}" if price else "",
        f"السيارة: {car_type}" if car_type else "",
        f"السائق: {driver_name}" if driver_name else "",
        f"عدد المهتمين: {selection_count}" if selection_count is not None else "",
        "─" * 14,
    ]
    return "\n".join(line for line in lines if line)


def _first_dict_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        return value
    return None


