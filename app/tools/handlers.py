import logging
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

from app.database.supabase import SupabaseRepository
from app.models.domain import ToolResult
from app.services.embedding_service import JinaEmbeddingService
from app.services.trip_indexing import index_trip, unindex_trip
from app.utils.departure import (
    _parse_date_value,
    normalize_departure_bucket,
    parse_departure_request,
    parse_requested_clock_time,
    trip_departure_bucket,
    trip_departure_date,
    trip_satisfies_departure_request,
)
from app.whatsapp.client import WhatsAppClient, WhatsAppClientError
from app.whatsapp.trip_selection import build_trip_selection_text, format_trip_card


class FalsaToolHandlers:
    def __init__(
        self,
        *,
        repository: SupabaseRepository,
        embeddings: JinaEmbeddingService,
        whatsapp: WhatsAppClient,
        customer: dict[str, Any],
        remoteJid: str,
        embedding_model: str,
        current_message: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.embeddings = embeddings
        self.whatsapp = whatsapp
        self.customer = customer
        self.remoteJid = remoteJid
        self.embedding_model = embedding_model
        self.current_message = current_message

    async def _resolve_trip_id_from_reply(self) -> str | None:
        if not self.current_message:
            return None
        metadata = self.current_message.get("metadata") or {}
        context_message_id = metadata.get("context_message_id")
        if not context_message_id:
            return None
        original = await self.repository.get_message_by_whatsapp_id(context_message_id)
        if not original:
            return None
        original_meta = original.get("metadata") or {}
        return original_meta.get("trip_id")

    async def about_falsa(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, data={}, error="query is required")

        query_embedding = await self.embeddings.embed_query(query)
        matches = await self.repository.search_info_chunks_by_vector(
            query_embedding=query_embedding,
            match_count=5,
        )
        if not matches:
            return ToolResult(
                ok=True,
                data={
                    "answer": "No matching FALSA policy or FAQ content was found.",
                    "sources": [],
                },
            )

        return ToolResult(
            ok=True,
            data={
                "answer_context": [
                    {
                        "text": match.get("chunk_text")
                        or match.get("metadata", {}).get("chunk_text"),
                        "source": match.get("source") or match.get("metadata", {}).get("source"),
                        "score": match.get("score") or match.get("similarity"),
                    }
                    for match in matches
                ],
            },
        )

    async def search_trips(self, arguments: dict[str, Any]) -> ToolResult:
        departure = _optional_string(arguments.get("departure"))
        destination = _optional_string(arguments.get("destination"))
        travel_date = _optional_string(arguments.get("travel_date"))
        travel_time = _optional_string(arguments.get("travel_time"))
        travel_time_exact = _optional_string(arguments.get("travel_time_exact"))
        travel_datetime = _optional_string(arguments.get("travel_datetime"))
        seats = _optional_int(arguments.get("seats")) or 1
        vehicle_type = _optional_string(arguments.get("vehicle_type"))
        vector_query_text = _optional_string(arguments.get("vector_query_text"))
        departure_request = parse_departure_request(
            travel_date=travel_date,
            travel_time=travel_time,
            travel_datetime=travel_datetime,
        )
        requested_time = parse_requested_clock_time(
            travel_time=travel_time,
            travel_datetime=travel_datetime,
            exact_time=travel_time_exact,
        )

        if not departure and not destination:
            return ToolResult(
                ok=False,
                data={},
                error="At least departure or destination is required before searching trips",
            )

        query = vector_query_text or _trip_vector_query_text(
            departure=departure,
            destination=destination,
            travel_date=travel_date,
            travel_time=travel_time,
            travel_time_exact=travel_time_exact,
            travel_datetime=travel_datetime,
            seats=seats,
            vehicle_type=vehicle_type,
        )
        query_embedding = await self.embeddings.embed_query(query)
        trips = await self.repository.search_trips_by_vector(
            query_embedding=query_embedding,
            departure=departure,
            destination=destination,
            departure_date=departure_request.departure_date,
            departure_time=departure_request.departure_time,
            requested_time=requested_time,
            seats=seats,
            vehicle_type=vehicle_type,
            match_count=10,
        )
        if not trips:
            trips = await self.repository.search_active_trips(
                departure=departure,
                destination=destination,
                seats=seats,
                vehicle_type=vehicle_type,
                departure_request=departure_request,
            )

        alternate_alert = _alternate_time_alert(trips)
        filtered = _sort_trip_summaries([
            _trip_summary(trip)
            for trip in trips
            if _is_trip_match(
                trip,
                departure=departure,
                destination=destination,
                seats=seats,
                vehicle_type=vehicle_type,
                departure_request=departure_request,
            )
        ])

        top_trips = filtered[:5]

        if top_trips:
            for trip_summary in top_trips:
                trip_id = trip_summary["trip_id"]
                trip = next((t for t in trips if (t.get("trip_id") or t.get("id")) == trip_id), {})
                card = format_trip_card(trip)
                try:
                    resp = await self.whatsapp.send_text(self.remoteJid, card)
                    wam_id = resp.get("messages", [{}])[0].get("id")
                    if wam_id:
                        await self.repository.create_message(
                            customer_id=str(self.customer["id"]),
                            sender_type="assistant",
                            message=card,
                            whatsapp_message_id=wam_id,
                            metadata={"trip_id": trip_id, "type": "trip_card"},
                        )
                except WhatsAppClientError:
                    logger.warning("Failed to send trip card for trip %s", trip_id)

            prompt = "يرجى الرد على إحدى بطاقات الرحلات أعلاه لاختيار رحلتك"
            await self.whatsapp.send_text(self.remoteJid, prompt)
            await self.repository.create_message(
                customer_id=str(self.customer["id"]),
                sender_type="assistant",
                message=prompt,
                metadata={"type": "trip_selection_prompt"},
            )

            return ToolResult(
                ok=True,
                data={
                    "count": len(top_trips),
                    "matches": top_trips,
                    "alternate_alert": alternate_alert,
                    "sent_as_messages": True,
                    "note": "Cards sent. No text reply needed.",
                },
                suppress_llm_reply=True,
            )

        return ToolResult(
            ok=True,
            data={
                "count": 0,
                "matches": [],
                "alternate_alert": alternate_alert,
                "sent_as_messages": False,
                "note": "No active matching trips were found.",
            },
        )

    async def create_booking_lead(self, arguments: dict[str, Any]) -> ToolResult:
        trip_id = _optional_string(arguments.get("trip_id"))
        requested_seats = _optional_int(arguments.get("requested_seats")) or 1
        notes = _optional_string(arguments.get("notes"))

        if not trip_id:
            trip_id = await self._resolve_trip_id_from_reply()
        if not trip_id:
            return ToolResult(ok=False, data={}, error="trip_id is required. Ask the user to reply to a trip card message or provide the trip ID.")
        if requested_seats < 1:
            return ToolResult(ok=False, data={}, error="requested_seats must be at least 1")

        trip = await self.repository.get_trip_by_id(trip_id)
        if not trip:
            return ToolResult(ok=False, data={}, error="Trip was not found")
        if trip.get("status") != "active":
            return ToolResult(ok=False, data={}, error="Trip is not active")
        if int(trip.get("available_seats") or 0) < requested_seats:
            return ToolResult(
                ok=False,
                data={"available_seats": trip.get("available_seats")},
                error="Not enough available seats",
            )

        lead = await self.repository.create_booking_lead(
            customer_id=str(self.customer["id"]),
            trip_id=trip_id,
            requested_seats=requested_seats,
            notes=notes,
        )

        driver_phone: str | None = None
        notification_status = "sent"
        notification_error = None
        try:
            driver_record = _first_or_dict(trip.get("drivers")) or {}
            driver_customer = driver_record.get("customers") or {}
            driver_recipient = driver_customer.get("phone_number") or driver_customer.get("remoteJid") or driver_record.get("remoteJid")
            if not driver_recipient:
                raise WhatsAppClientError("Driver recipient is missing")
            driver_phone = driver_recipient.split("@")[0]
            await self.whatsapp.send_text(
                driver_recipient,
                _driver_notification_text(
                    customer=self.customer,
                    trip=trip,
                    requested_seats=requested_seats,
                    notes=notes,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            notification_status = "failed"
            notification_error = str(exc)

        await self.repository.update_booking_lead_notification(
            lead_id=str(lead["id"]),
            status=notification_status,
            metadata={"error": notification_error} if notification_error else None,
        )

        return ToolResult(
            ok=True,
            data={
                "lead_id": lead["id"],
                "status": "pending",
                "driver_notification_status": notification_status,
                "driver_notification_error": notification_error,
                "driver_phone": driver_phone,
                "message": "Booking lead created. Seats are not reserved until confirmed.",
            },
        )

    async def create_driver_account(self, arguments: dict[str, Any]) -> ToolResult:
        name = _optional_string(arguments.get("name"))
        if not name:
            return ToolResult(ok=False, data={}, error="name is required")

        remote_jid = self.remoteJid
        existing = await self.repository.get_driver_by_remoteJid(remote_jid)
        if existing:
            return ToolResult(
                ok=False,
                data={"driver_id": existing["id"]},
                error="Driver account already exists for this WhatsApp number",
            )

        driver = await self.repository.create_driver(customer_id=str(self.customer["id"]))
        return ToolResult(
            ok=True,
            data={
                "driver_id": driver["id"],
                "name": self.customer.get("name"),
                "remoteJid": self.customer.get("remoteJid"),
                "message": "Driver account created successfully.",
            },
        )

    async def _summarize_trips(self, trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            _trip_summary(trip, trip_number=index + 1)
            for index, trip in enumerate(trips)
        ]

    async def check_driver_info(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        customer_info = driver.get("customers", {})
        cars = await self.repository.list_driver_cars(str(driver["id"]))
        upcoming_trips = await self.repository.list_driver_trips(str(driver["id"]))

        return ToolResult(
            ok=True,
            data={
                "driver_id": driver["id"],
                "name": customer_info.get("name") or driver.get("name"),
                "remoteJid": customer_info.get("remoteJid") or driver.get("remoteJid"),
                "status": driver.get("status"),
                "vehicle_count": len(cars),
                "active_trip_count": len(upcoming_trips),
                "vehicles": [
                    {
                        "car_id": str(car.get("id")),
                        "name": car.get("car_type"),
                        "plate_number": car.get("plate_number"),
                        "seat_count": car.get("seat_count"),
                    }
                    for car in cars
                ],
                "active_trips": await self._summarize_trips(upcoming_trips),
            },
        )

    async def check_driver_trips(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        trips = await self.repository.list_driver_trips(str(driver["id"]))
        return ToolResult(
            ok=True,
            data={
                "driver_id": driver["id"],
                "upcoming_trips": await self._summarize_trips(trips),
                "count": len(trips),
                "message": (
                    "No upcoming active trips found."
                    if not trips
                    else "Upcoming active trips retrieved successfully."
                ),
            },
        )

    async def add_driver_car(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        car_type = _optional_string(arguments.get("name"))
        if not car_type:
            return ToolResult(ok=False, data={}, error="name is required")

        plate_number = _optional_string(arguments.get("plate_number"))
        seat_count = _optional_int(arguments.get("seat_count"))
        if seat_count is not None and seat_count < 1:
            return ToolResult(ok=False, data={}, error="seat_count must be at least 1")

        car = await self.repository.create_driver_car(
            driver_id=str(driver["id"]),
            car_type=car_type,
            plate_number=plate_number,
            seat_count=seat_count,
        )

        return ToolResult(
            ok=True,
            data={
                "car_id": car.get("id"),
                "name": car.get("car_type"),
                "plate_number": car.get("plate_number"),
                "seat_count": car.get("seat_count"),
                "message": "Driver vehicle registered successfully.",
            },
        )

    async def add_trip_by_driver(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        departure = _optional_string(arguments.get("departure"))
        destination = _optional_string(arguments.get("destination"))
        if not departure or not destination:
            return ToolResult(
                ok=False,
                data={},
                error="departure and destination are required",
            )

        parsed_date = _parse_date_value(arguments.get("departure_date"))
        if not parsed_date:
            return ToolResult(
                ok=False,
                data={},
                error="departure_date is required as YYYY-MM-DD",
            )

        departure_time = normalize_departure_bucket(arguments.get("departure_time"))
        if not departure_time:
            return ToolResult(
                ok=False,
                data={},
                error="departure_time must be morning, noon, night, or Arabic صباح / ظهر / ليل",
            )

        existing = await self.repository.get_driver_trip_by_datetime(
            driver_id=str(driver["id"]),
            departure_date=parsed_date,
            departure_time=departure_time,
        )
        if existing:
            return ToolResult(
                ok=False,
                data={"existing_trip_id": existing.get("id")},
                error=(
                    "You already have a trip scheduled at this date and time. "
                    "Cancel or modify the existing trip before creating a new one."
                ),
            )

        latest_trip = await self.repository.get_driver_latest_trip(str(driver["id"]))
        cars = await self.repository.list_driver_cars(str(driver["id"]))

        vehicle_type = _optional_string(arguments.get("vehicle_type"))
        matched_car = _resolve_driver_car(cars, vehicle_type=vehicle_type)
        if matched_car is None and latest_trip:
            matched_car = _resolve_driver_car(
                cars,
                vehicle_type=None,
                car_id=_optional_string(latest_trip.get("car_id")),
            )
        if matched_car is None and len(cars) == 1:
            matched_car = cars[0]

        car_id = str(matched_car["id"]) if matched_car else None

        available_seats = _optional_int(arguments.get("available_seats"))
        if available_seats is None and latest_trip is not None:
            available_seats = _optional_int(latest_trip.get("available_seats"))

        total_seats = _optional_int(arguments.get("total_seats"))
        if total_seats is None and latest_trip is not None:
            total_seats = _optional_int(latest_trip.get("total_seats"))

        if total_seats is None and matched_car is not None:
            total_seats = _optional_int(matched_car.get("seat_count"))

        price = _optional_price(arguments.get("price"))
        if price is None and latest_trip is not None:
            price = _optional_price(latest_trip.get("price"))

        missing = [
            field
            for field, value in [
                ("available_seats", available_seats),
                ("total_seats", total_seats),
                ("price", price),
            ]
            if value is None
        ]
        if matched_car is None and not vehicle_type:
            missing.insert(0, "vehicle_type")
        if missing:
            return ToolResult(
                ok=False,
                data={"missing_fields": missing},
                error=f"Missing required trip fields: {', '.join(missing)}",
            )

        if not matched_car:
            if vehicle_type:
                return ToolResult(
                    ok=False,
                    data={},
                    error=(
                        f"No registered vehicle matches '{vehicle_type}'. "
                        "Ask the driver to use the exact car type or plate from their account."
                    ),
                )
            return ToolResult(
                ok=False,
                data={},
                error="No registered vehicle found for this driver",
            )

        if total_seats is None or total_seats < 1:
            return ToolResult(ok=False, data={}, error="total_seats must be at least 1")
        if available_seats is None or available_seats < 0:
            return ToolResult(ok=False, data={}, error="available_seats must be at least 0")
        if available_seats > total_seats:
            return ToolResult(
                ok=False,
                data={},
                error="available_seats cannot exceed total_seats",
            )
        if price is None or price < 0:
            return ToolResult(ok=False, data={}, error="price must be zero or greater")

        trip = await self.repository.create_driver_trip(
            driver_id=str(driver["id"]),
            car_id=car_id,
            departure=departure,
            destination=destination,
            departure_date=parsed_date,
            departure_time=departure_time,
            available_seats=available_seats,
            total_seats=total_seats,
            price=price,
        )

        await index_trip(
            repository=self.repository,
            embeddings=self.embeddings,
            embedding_model=self.embedding_model,
            trip=trip,
        )

        return ToolResult(
            ok=True,
            data={
                "trip_id": trip.get("id"),
                "departure": trip.get("departure"),
                "destination": trip.get("destination"),
                "departure_date": parsed_date.isoformat(),
                "departure_time": departure_time,
                "available_seats": available_seats,
                "total_seats": total_seats,
                "price": price,
                "car_id": car_id,
                "indexed": True,
                "message": "Trip created and indexed for search.",
            },
        )

    async def initiate_trip_action(self, arguments: dict[str, Any]) -> ToolResult:
        action_type = _optional_string(arguments.get("action_type"))
        if action_type not in {"DELETE", "MODIFY"}:
            return ToolResult(
                ok=False,
                data={},
                error="action_type must be DELETE or MODIFY",
            )

        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        trips = await self.repository.list_driver_trips(str(driver["id"]))
        travel_date = _optional_string(arguments.get("travel_date"))
        travel_time = _optional_string(arguments.get("travel_time"))
        departure_request = parse_departure_request(
            travel_date=travel_date,
            travel_time=travel_time,
        )
        if departure_request.departure_date or departure_request.departure_time:
            trips = [
                trip
                for trip in trips
                if trip_satisfies_departure_request(trip, departure_request)
            ]

        if not trips:
            return ToolResult(
                ok=True,
                data={
                    "count": 0,
                    "message": "No trips found. Ask the user for clarification.",
                },
            )

        body = build_trip_selection_text(trips=trips, action_type=action_type)
        try:
            await self.whatsapp.send_text(self.remoteJid, body)
        except WhatsAppClientError as exc:
            return ToolResult(
                ok=False,
                data={"count": len(trips)},
                error=f"Failed to send WhatsApp trip selection text: {exc}",
            )

        return ToolResult(
            ok=True,
            data={
                "count": len(trips),
                "action_type": action_type,
                "message": (
                    "Sent a numbered trip list to the driver via WhatsApp. "
                    "Ask them to reply with the trip number."
                ),
            },
        )

    async def delete_trip_by_number(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        trip_number = _optional_int(arguments.get("trip_number"))
        if trip_number is None or trip_number < 1:
            return ToolResult(ok=False, data={}, error="trip_number must be an integer >= 1")

        trips = await self.repository.list_driver_trips(str(driver["id"]))
        if trip_number > len(trips):
            return ToolResult(
                ok=False,
                data={"count": len(trips)},
                error=f"trip_number must be between 1 and {len(trips)}",
            )

        trip = trips[trip_number - 1]
        await self.repository.cancel_driver_trip(str(trip["id"]))
        await unindex_trip(repository=self.repository, trip_id=str(trip["id"]))

        return ToolResult(
            ok=True,
            data={
                "trip_number": trip_number,
                "trip_id": trip.get("id"),
                "trip": _trip_summary(trip, trip_number=trip_number),
                "message": "Success! Your trip has been canceled.",
            },
        )

    async def modify_trip_by_number(self, arguments: dict[str, Any]) -> ToolResult:
        print("\n  modefing our new \n")
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        trip_number = _optional_int(arguments.get("trip_number"))
        field = _optional_string(arguments.get("field"))
        value = _optional_string(arguments.get("value"))
        if trip_number is None or trip_number < 1:
            return ToolResult(ok=False, data={}, error="trip_number must be an integer >= 1")
        if not field or value is None:
            return ToolResult(ok=False, data={}, error="field and value are required")

        trips = await self.repository.list_driver_trips(str(driver["id"]))
        if trip_number > len(trips):
            return ToolResult(
                ok=False,
                data={"count": len(trips)},
                error=f"trip_number must be between 1 and {len(trips)}",
            )

        trip = trips[trip_number - 1]
        updates, error = await self._build_trip_field_update(
            driver_id=str(driver["id"]),
            field=field,
            value=value,
        )
        if error:
            return ToolResult(ok=False, data={}, error=error)

        updated_trip = await self.repository.update_driver_trip(str(trip["id"]), updates)
        await index_trip(
            repository=self.repository,
            embeddings=self.embeddings,
            embedding_model=self.embedding_model,
            trip=updated_trip,
        )

        return ToolResult(
            ok=True,
            data={
                "trip_number": trip_number,
                "trip_id": updated_trip.get("id"),
                "field": field,
                "value": value,
                "trip": _trip_summary(updated_trip, trip_number=trip_number),
                "message": "Trip updated successfully.",
            },
        )

    async def update_trip_field(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Ask the sender to register with create_driver_account first."
                ),
            )

        session = await self.repository.get_customer_session(str(self.customer["id"]))
        trip_id = _optional_string(session.get("active_edit_trip_id"))
        if not trip_id:
            return ToolResult(
                ok=False,
                data={},
                error=(
                    "No trip is selected for editing. "
                    "Use initiate_trip_action with MODIFY first."
                ),
            )

        trip = await self.repository.get_trip_by_id(trip_id)
        if not trip:
            return ToolResult(ok=False, data={}, error="Trip was not found")
        if str(trip.get("driver_id")) != str(driver["id"]):
            return ToolResult(ok=False, data={}, error="Trip does not belong to this driver")
        if trip.get("status") != "active":
            return ToolResult(ok=False, data={}, error="Trip is not active")

        field = _optional_string(arguments.get("field"))
        value = _optional_string(arguments.get("value"))
        if not field or value is None:
            return ToolResult(ok=False, data={}, error="field and value are required")

        updates, error = await self._build_trip_field_update(
            driver_id=str(driver["id"]),
            field=field,
            value=value,
        )
        if error:
            return ToolResult(ok=False, data={}, error=error)

        updated_trip = await self.repository.update_driver_trip(trip_id, updates)
        await index_trip(
            repository=self.repository,
            embeddings=self.embeddings,
            embedding_model=self.embedding_model,
            trip=updated_trip,
        )
        await self.repository.clear_customer_session_field(
            customer_id=str(self.customer["id"]),
            key="active_edit_trip_id",
        )

        return ToolResult(
            ok=True,
            data={
                "trip_id": trip_id,
                "field": field,
                "value": value,
                "trip": _trip_summary(updated_trip),
                "message": "Trip updated successfully.",
            },
        )

    async def _build_trip_field_update(
        self,
        *,
        driver_id: str,
        field: str,
        value: str,
    ) -> tuple[dict[str, Any], str | None]:
        if field in {"departure", "destination"}:
            return {field: value}, None

        if field == "departure_date":
            parsed_date = _parse_date_value(value)
            if not parsed_date:
                return {}, "departure_date must be YYYY-MM-DD"
            return {"departure_date": parsed_date.isoformat()}, None

        if field in {"departure_time", "pickup_time"}:
            bucket = normalize_departure_bucket(value)
            if not bucket:
                return {}, "departure_time must be morning, noon, night, or HH:MM"
            return {"departure_time": bucket}, None

        if field == "vehicle_type":
            cars = await self.repository.list_driver_cars(driver_id)
            matched_car = _resolve_driver_car(cars, vehicle_type=value)
            if not matched_car:
                return {}, f"No registered vehicle matches '{value}'"
            return {"car_id": str(matched_car["id"])}, None

        if field in {"available_seats", "total_seats"}:
            seats = _optional_int(value)
            if seats is None:
                return {}, f"{field} must be an integer"
            if field == "available_seats" and seats < 0:
                return {}, "available_seats must be at least 0"
            if field == "total_seats" and seats < 1:
                return {}, "total_seats must be at least 1"
            return {field: seats}, None

        if field == "price":
            price = _optional_price(value)
            if price is None or price < 0:
                return {}, "price must be zero or greater"
            return {"price": price}, None

        return {}, f"Unsupported field: {field}"

    async def switch_to_driver(self, arguments: dict[str, Any]) -> ToolResult:
        driver = await self.repository.get_driver_by_remoteJid(self.remoteJid)
        if not driver:
            return ToolResult(
                ok=False,
                data={"action": "create_driver_account"},
                error=(
                    "No driver account for this WhatsApp number. "
                    "Use create_driver_account first, then switch_to_driver."
                ),
            )

        await self.repository.update_customer_user_mode(
            customer_id=str(self.customer["id"]),
            user_mode="driver",
        )
        self.customer["user_mode"] = "driver"
        return ToolResult(
            ok=True,
            data={
                "user_mode": "driver",
                "driver_id": driver["id"],
                "message": "Switched to driver mode.",
            },
        )

    async def switch_to_passenger(self, arguments: dict[str, Any]) -> ToolResult:
        name = _optional_string(arguments.get("name"))
        if name:
            await self.repository.update_customer_name(
                customer_id=str(self.customer["id"]),
                name=name,
            )
            self.customer["name"] = name

        await self.repository.update_customer_user_mode(
            customer_id=str(self.customer["id"]),
            user_mode="passenger",
        )
        self.customer["user_mode"] = "passenger"
        return ToolResult(
            ok=True,
            data={
                "user_mode": "passenger",
                "name": self.customer.get("name"),
                "message": "Switched to passenger mode.",
            },
        )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _resolve_driver_car(
    cars: list[dict[str, Any]],
    *,
    vehicle_type: str | None = None,
    car_id: str | None = None,
) -> dict[str, Any] | None:
    if car_id:
        for car in cars:
            if str(car.get("id")) == car_id:
                return car
        return None

    if not vehicle_type:
        return None

    query = vehicle_type.lower()
    matches = [
        car
        for car in cars
        if query in str(car.get("car_type") or "").lower()
        or query in str(car.get("plate_number") or "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None
    return None


def _trip_vector_query_text(
    *,
    departure: str | None,
    destination: str | None,
    travel_date: str | None,
    travel_time: str | None,
    travel_time_exact: str | None,
    travel_datetime: str | None,
    seats: int,
    vehicle_type: str | None,
) -> str:
    return " ".join(
        part
        for part in [
            departure,
            destination,
            travel_date,
            travel_time_exact,
            travel_time,
            travel_datetime,
            f"{seats} seats",
            vehicle_type,
        ]
        if part
    )


def _is_trip_match(
    trip: dict[str, Any],
    *,
    departure: str | None,
    destination: str | None,
    seats: int,
    vehicle_type: str | None,
    departure_request: Any,
) -> bool:
    if trip.get("status") != "active":
        return False
    if int(trip.get("available_seats") or 0) < seats:
        return False
    if departure and departure.lower() not in str(trip.get("departure") or "").lower():
        return False
    if destination and destination.lower() not in str(trip.get("destination") or "").lower():
        return False
    if vehicle_type:
        car = _first_or_dict(trip.get("driver_cars")) or {}
        car_type = car.get("car_type") or trip.get("car_type")
        if vehicle_type.lower() not in str(car_type or "").lower():
            return False
    if not trip_satisfies_departure_request(trip, departure_request):
        return False
    return True


def _trip_summary(trip: dict[str, Any], *, trip_number: int | None = None) -> dict[str, Any]:
    driver = _first_or_dict(trip.get("drivers")) or {}
    car = _first_or_dict(trip.get("driver_cars")) or {}
    summary = {
        "trip_id": trip.get("trip_id") or trip.get("id"),
        "departure": trip.get("departure"),
        "destination": trip.get("destination"),
        "departure_date": (
            parsed_date.isoformat() if (parsed_date := trip_departure_date(trip)) else None
        ),
        "departure_time": trip.get("departure_time"),
        "departure_time_type": trip_departure_bucket(trip),
        "available_seats": trip.get("available_seats"),
        "total_seats": trip.get("total_seats"),
        "price": trip.get("price"),
        "driver_name": driver.get("name") or trip.get("driver_name"),
        "car_type": car.get("car_type") or trip.get("car_type"),
        "status": trip.get("status"),
        "similarity": trip.get("similarity"),
        "time_difference_minutes": trip.get("time_difference_minutes"),
    }
    if trip_number is not None:
        summary["trip_number"] = trip_number
    return summary


def _sort_trip_summaries(trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_order = {"morning": 0, "noon": 1, "night": 2}
    return sorted(
        trips,
        key=lambda trip: (
            str(trip.get("departure_date") or ""),
            bucket_order.get(str(trip.get("departure_time_type") or ""), 99),
        ),
    )


def _first_or_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        return value
    return None


def _alternate_time_alert(trips: list[dict[str, Any]]) -> str | None:
    if not trips:
        return None
    raw_difference = trips[0].get("time_difference_minutes")
    if raw_difference is None:
        return None
    try:
        difference = int(raw_difference)
    except (TypeError, ValueError):
        return None
    if difference <= 60:
        return None
    return (
        "The closest available trip is more than 60 minutes away from the requested time. "
        "Mention that it is an alternate time before listing the options."
    )


def _trip_search_note(alternate_alert: str | None) -> str:
    if alternate_alert:
        return alternate_alert
    return "Trips are available for handoff only; seats are not reserved yet."


def _driver_notification_text(
    *,
    customer: dict[str, Any],
    trip: dict[str, Any],
    requested_seats: int,
    notes: str | None,
) -> str:
    return (
        "🔔 حجز جديد في فلسا\n"
        f"العميل: {customer.get('name') or 'عميل جديد'}\n"
        f"رقم العميل: {customer.get('phone_number') or 'غير متوفر'}\n"
        f"الرحلة: {trip.get('departure')} ← {trip.get('destination')}\n"
        f"التاريخ: {trip_departure_date(trip)} {trip_departure_bucket(trip)}\n"
        f"المقاعد المطلوبة: {requested_seats}\n"
        f"ملاحظات: {notes or 'لا يوجد'}\n"
        "الحالة: قيد الانتظار"
    )
