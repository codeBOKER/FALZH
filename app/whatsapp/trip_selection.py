from typing import Any

from app.utils.departure import trip_departure_bucket, trip_departure_date

_BUCKET_LABELS = {
    "morning": "صباحا",
    "noon": "ظهرا",
    "night": "مساء",
}

_ACTION_DESCRIPTIONS = {
    "DELETE": "🗑️ اختر رقم الرحله التي تريد حذفها من فضلك",
    "MODIFY": "✏️ يمكنك ارسال رقم الرحلة التي تريد تعديلها",
}
_PERFFIX= "قائمة الرحلات المسجله في حسابك لدينا\n\n"+("-"*7)


def parse_trip_action_reply(reply_id: str) -> tuple[str, str] | None:
    if "_" not in reply_id:
        return None
    action, trip_id = reply_id.split("_", 1)
    if action not in {"DELETE", "MODIFY"} or not trip_id:
        return None
    return action, trip_id


def build_trip_selection_list(
    *,
    trips: list[dict[str, Any]],
    action_type: str,
) -> dict[str, Any]:
    action = action_type.upper()
    suffix = _ACTION_DESCRIPTIONS.get(action, "")
    rows = [
        f"""
        [{str(trips.index(trip))}] :الرحلة رقم
        \n
        {_trip_row_description(trip)}
        """
        for trip in trips[:10]
    ]
    rows_text = "\n".join(rows)
    return {
        "type": "text",
        "text": {
            "body": f"{_PERFFIX} {rows_text} {suffix}"

        }
    }


def build_trip_selection_text(
    *,
    trips: list[dict[str, Any]],
    action_type: str,
) -> str:
    action = action_type.upper()
    suffix = {
        "DELETE": "ارسل رقم الرحلة التي تريد حذفها.",
        "MODIFY": "ارسل رقم الرحلة التي تريد تعديلها.",
    }.get(action, "")

    lines = [
        f"قائمة الرحلات المسجلة لك:\n"
    ]
    for index, trip in enumerate(trips, start=1):
        parsed_date = trip_departure_date(trip)
        bucket = trip_departure_bucket(trip)
        bucket_text = _BUCKET_LABELS.get(bucket, "") if bucket else ""
        date_text = parsed_date.isoformat() if parsed_date else str(trip.get("departure_date") or "")
        lines.append(
            f"{index}. {trip.get('departure', '')} → {trip.get('destination', '')} | {date_text} | {bucket_text} | seats: {trip.get('available_seats')} / {trip.get('total_seats')} | price: {trip.get('price')}"
        )
    lines.append("")
    lines.append(suffix)
    return "\n".join(lines)


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


def _trip_row_description(trip: dict[str, Any]) -> str:
    departure = str(trip.get("departure") or "")
    destination = str(trip.get("destination") or "")
    parsed_date = trip_departure_date(trip)
    date_text = parsed_date.isoformat() if parsed_date else ""
    bucket = trip_departure_bucket(trip)
    bucket_text = _BUCKET_LABELS.get(bucket, "") if bucket else ""
    
    return (
        f"🚌 {departure} → {destination}\n"
        f"📅 التاريخ: {date_text}\n"
        f"🕰️ الوقت: {bucket_text}"
    )