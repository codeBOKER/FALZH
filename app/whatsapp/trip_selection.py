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