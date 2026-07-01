import logging
from datetime import date, time
from typing import Any

from app.config import Settings
from app.utils.departure import (
    DepartureRequest,
    not_departed_bucket_filter,
)

logger = logging.getLogger(__name__)


async def create_supabase_client(settings: Settings) -> Any:
    from supabase import acreate_client

    return await acreate_client(
        str(settings.supabase_url),
        settings.supabase_service_role_key,
    )


def _response_data(response: Any) -> Any:
    if hasattr(response, "data"):
        return response.data
    if isinstance(response, dict):
        return response.get("data", response)
    return response


class SupabaseRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    async def upsert_customer(
        self,
        *,
        remote_jid: str,
        name: str | None = None,
        preferred_language: str | None = None,
        phone_number: str | None = None,
    ) -> dict[str, Any]:
        if phone_number:
            phone_number = phone_number.split("@")[0]
        payload = {
            "remoteJid": remote_jid,
            "name": name,
            "preferred_language": preferred_language,
            "phone_number": phone_number,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        response = await (
            self.client.table("customers")
            .upsert(payload, on_conflict="remoteJid")
            .execute()
        )
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def update_customer_user_mode(
        self,
        *,
        customer_id: str,
        user_mode: str,
    ) -> dict[str, Any]:
        response = await (
            self.client.table("customers")
            .update({"user_mode": user_mode})
            .eq("id", customer_id)
            .execute()
        )
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def update_customer_name(
        self,
        *,
        customer_id: str,
        name: str,
    ) -> dict[str, Any]:
        response = await (
            self.client.table("customers")
            .update({"name": name})
            .eq("id", customer_id)
            .execute()
        )
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def get_customer_session(self, customer_id: str) -> dict[str, Any]:
        response = await (
            self.client.table("customers")
            .select("session_data")
            .eq("id", customer_id)
            .maybe_single()
            .execute()
        )
        data = _response_data(response) or {}
        session_data = data.get("session_data")
        return session_data if isinstance(session_data, dict) else {}

    async def update_customer_session(
        self,
        *,
        customer_id: str,
        session_data: dict[str, Any],
    ) -> dict[str, Any]:
        response = await (
            self.client.table("customers")
            .update({"session_data": session_data})
            .eq("id", customer_id)
            .execute()
        )
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def set_customer_session_field(
        self,
        *,
        customer_id: str,
        key: str,
        value: Any,
    ) -> dict[str, Any]:
        session_data = await self.get_customer_session(customer_id)
        session_data[key] = value
        return await self.update_customer_session(
            customer_id=customer_id,
            session_data=session_data,
        )

    async def clear_customer_session_field(
        self,
        *,
        customer_id: str,
        key: str,
    ) -> dict[str, Any]:
        session_data = await self.get_customer_session(customer_id)
        session_data.pop(key, None)
        return await self.update_customer_session(
            customer_id=customer_id,
            session_data=session_data,
        )

    async def message_exists(self, whatsapp_message_id: str) -> bool:
        response = await (
            self.client.table("messages")
            .select("id")
            .eq("whatsapp_message_id", whatsapp_message_id)
            .limit(1)
            .execute()
        )
        data = _response_data(response)
        return bool(data)

    async def create_message(
        self,
        *,
        customer_id: str,
        sender_type: str,
        message: str,
        whatsapp_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "customer_id": customer_id,
            "sender_type": sender_type,
            "message": message,
            "whatsapp_message_id": whatsapp_message_id,
            "metadata": metadata or {},
        }
        response = await self.client.table("messages").insert(payload).execute()
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def get_recent_context_messages(
        self,
        *,
        customer_id: str,
        current_message_id: str,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        current_response = await (
            self.client.table("messages")
            .select("*")
            .eq("id", current_message_id)
            .single()
            .execute()
        )
        current = _response_data(current_response)
        current_created_at = current.get("created_at")

        prior_query = (
            self.client.table("messages")
            .select("*")
            .eq("customer_id", customer_id)
            .neq("id", current_message_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if current_created_at:
            prior_query = prior_query.lt("created_at", current_created_at)

        prior_response = await prior_query.execute()
        prior = _response_data(prior_response) or []
        return list(reversed(prior)) + [current]

    async def get_message_by_whatsapp_id(
        self,
        whatsapp_message_id: str,
    ) -> dict[str, Any] | None:
        response = await (
            self.client.table("messages")
            .select("*")
            .eq("whatsapp_message_id", whatsapp_message_id)
            .maybe_single()
            .execute()
        )
        return _response_data(response)

    async def list_active_trips(self) -> list[dict[str, Any]]:
        query = (
            self.client.table("driver_trips")
            .select("*, drivers(*, customers(*)), driver_cars(*)")
            .eq("status", "active")
            .gt("available_seats", 0)
        )
        query = (
            self._apply_not_departed_filter(query)
            .order("departure_date")
            .order("departure_time")
        )
        response = await query.execute()
        return _response_data(response) or []

    async def get_trips_by_ids(self, trip_ids: list[str]) -> list[dict[str, Any]]:
        if not trip_ids:
            return []
        response = await (
            self.client.table("driver_trips")
            .select("*, drivers(*, customers(*)), driver_cars(*)")
            .in_("id", trip_ids)
            .execute()
        )
        return _response_data(response) or []

    async def search_active_trips(
        self,
        *,
        departure: str | None = None,
        destination: str | None = None,
        seats: int | None = None,
        vehicle_type: str | None = None,
        departure_request: DepartureRequest | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            self.client.table("driver_trips")
            .select("*, drivers(*, customers(*)), driver_cars(*)")
            .eq("status", "active")
            .gt("available_seats", 0)
        )
        query = self._apply_departure_request_filter(query, departure_request)
        query = query.order("departure_date").order("departure_time")
        if departure:
            query = query.ilike("departure", f"%{departure}%")
        if destination:
            query = query.ilike("destination", f"%{destination}%")
        if seats:
            query = query.gte("available_seats", seats)
        if vehicle_type:
            query = query.ilike("driver_cars.car_type", f"%{vehicle_type}%")
        response = await query.limit(10).execute()
        return _response_data(response) or []

    async def search_info_chunks_by_vector(
        self,
        *,
        query_embedding: list[float],
        match_count: int = 5,
    ) -> list[dict[str, Any]]:
        response = await self.client.rpc(
            "match_falsa_info",
            {
                "query_embedding": query_embedding,
                "match_count": match_count,
                "match_threshold": 0.0,
            },
        ).execute()
        return _response_data(response) or []

    async def search_trips_by_vector(
        self,
        *,
        query_embedding: list[float],
        departure: str | None = None,
        destination: str | None = None,
        departure_date: date | None = None,
        departure_time: str | None = None,
        requested_time: time | None = None,
        seats: int = 1,
        vehicle_type: str | None = None,
        match_count: int = 10,
    ) -> list[dict[str, Any]]:
        try:
            response = await self.client.rpc(
                "match_active_trips",
                {
                    "query_embedding": query_embedding,
                    "match_count": match_count,
                    "match_threshold": 0.0,
                    "filter_departure": departure,
                    "filter_destination": destination,
                    "filter_departure_date": (
                        departure_date.isoformat() if departure_date else None
                    ),
                    "filter_departure_time": departure_time,
                    "filter_requested_time": (
                        requested_time.isoformat(timespec="minutes") if requested_time else None
                    ),
                    "filter_seats": seats,
                    "filter_vehicle_type": vehicle_type,
                },
            ).execute()
            return _response_data(response) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Supabase match_active_trips RPC failed; falling back to regular active trips search: %s",
                exc,
            )
            return []

    async def upsert_info_chunks(self, chunks: list[dict[str, Any]]) -> int:
        if not chunks:
            return 0
        response = await self.client.table("falsa_info_chunks").upsert(chunks).execute()
        data = _response_data(response)
        return len(data) if isinstance(data, list) else len(chunks)

    async def upsert_trip_embeddings(self, trip_embeddings: list[dict[str, Any]]) -> int:
        if not trip_embeddings:
            return 0
        response = await (
            self.client.table("driver_trip_embeddings")
            .upsert(trip_embeddings, on_conflict="trip_id")
            .execute()
        )
        data = _response_data(response)
        return len(data) if isinstance(data, list) else len(trip_embeddings)

    async def delete_trip_embedding(self, trip_id: str) -> None:
        await (
            self.client.table("driver_trip_embeddings")
            .delete()
            .eq("trip_id", trip_id)
            .execute()
        )

    def _apply_departure_request_filter(
        self,
        query: Any,
        departure_request: DepartureRequest | None,
    ) -> Any:
        if not departure_request:
            return self._apply_not_departed_filter(query)

        today, remaining_buckets = not_departed_bucket_filter()
        if departure_request.departure_date:
            query = query.eq("departure_date", departure_request.departure_date.isoformat())
            if departure_request.departure_time:
                return query.eq("departure_time", departure_request.departure_time)
            if departure_request.departure_date == today:
                return query.in_("departure_time", list(remaining_buckets))
            return query

        query = self._apply_not_departed_filter(query)
        if departure_request.departure_time:
            return query.eq("departure_time", departure_request.departure_time)
        return query

    def _apply_not_departed_filter(self, query: Any) -> Any:
        today, remaining_buckets = not_departed_bucket_filter()
        bucket_list = ",".join(remaining_buckets)
        return query.or_(
            f"departure_date.gt.{today.isoformat()},"
            f"and(departure_date.eq.{today.isoformat()},departure_time.in.({bucket_list}))"
        )

    async def get_trip_by_id(self, trip_id: str) -> dict[str, Any] | None:
        response = await (
            self.client.table("driver_trips")
            .select("*, drivers(*, customers(*)), driver_cars(*)")
            .eq("id", trip_id)
            .maybe_single()
            .execute()
        )
        return _response_data(response)

    async def get_driver_by_remoteJid(self, remote_jid: str) -> dict[str, Any] | None:
        response = await (
            self.client.table("drivers")
            .select("*, customers!inner(*)")
            .eq("customers.remoteJid", remote_jid)
            .maybe_single()
            .execute()
        )
        return _response_data(response)

    async def create_driver(self, *, customer_id: str) -> dict[str, Any]:
        response = await (
            self.client.table("drivers")
            .insert({"customer_id": customer_id, "status": "active"})
            .execute()
        )
        data = _response_data(response)
        driver = data[0] if isinstance(data, list) else data
        await (
            self.client.table("driver_wallet")
            .insert({"driver_id": driver["id"], "balance": 0})
            .execute()
        )
        driver_response = await (
            self.client.table("drivers")
            .select("*, customers(*)")
            .eq("id", str(driver["id"]))
            .maybe_single()
            .execute()
        )
        return _response_data(driver_response)

    async def get_driver_latest_trip(self, driver_id: str) -> dict[str, Any] | None:
        response = await (
            self.client.table("driver_trips")
            .select("*, driver_cars(*)")
            .eq("driver_id", driver_id)
            .order("created_at", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        return _response_data(response)

    async def get_driver_trip_by_datetime(
        self,
        driver_id: str,
        departure_date: date,
        departure_time: str,
    ) -> dict[str, Any] | None:
        response = await (
            self.client.table("driver_trips")
            .select("id, departure, destination, departure_date, departure_time")
            .eq("driver_id", driver_id)
            .eq("status", "active")
            .eq("departure_date", departure_date.isoformat())
            .eq("departure_time", departure_time)
            .maybe_single()
            .execute()
        )
        return _response_data(response)

    async def list_driver_cars(self, driver_id: str) -> list[dict[str, Any]]:
        response = await (
            self.client.table("driver_cars")
            .select("*")
            .eq("driver_id", driver_id)
            .execute()
        )
        return _response_data(response) or []

    async def list_driver_trips(self, driver_id: str) -> list[dict[str, Any]]:
        query = (
            self.client.table("driver_trips")
            .select("*, driver_cars(*)")
            .eq("driver_id", driver_id)
            .eq("status", "active")
        )
        query = self._apply_not_departed_filter(query).order("departure_date").order("departure_time")
        response = await query.execute()
        return _response_data(response) or []

    async def create_driver_car(
        self,
        *,
        driver_id: str,
        car_type: str,
        plate_number: str | None = None,
        seat_count: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "driver_id": driver_id,
            "car_type": car_type,
        }
        if plate_number is not None:
            payload["plate_number"] = plate_number
        if seat_count is not None:
            payload["seat_count"] = seat_count

        response = await self.client.table("driver_cars").insert(payload).execute()
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def create_driver_trip(
        self,
        *,
        driver_id: str,
        car_id: str | None,
        departure: str,
        destination: str,
        departure_date: date,
        departure_time: str,
        available_seats: int,
        total_seats: int,
        price: float,
    ) -> dict[str, Any]:
        payload = {
            "driver_id": driver_id,
            "car_id": car_id,
            "departure": departure,
            "destination": destination,
            "departure_date": departure_date.isoformat(),
            "departure_time": departure_time,
            "available_seats": available_seats,
            "total_seats": total_seats,
            "price": price,
            "status": "active",
        }
        response = await self.client.table("driver_trips").insert(payload).execute()
        data = _response_data(response)
        trip = data[0] if isinstance(data, list) else data
        return await self.get_trip_by_id(str(trip["id"])) or trip

    async def update_driver_trip(
        self,
        trip_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        if not updates:
            raise ValueError("updates must not be empty")
        response = await (
            self.client.table("driver_trips")
            .update(updates)
            .eq("id", trip_id)
            .execute()
        )
        data = _response_data(response)
        updated = data[0] if isinstance(data, list) else data
        return await self.get_trip_by_id(str(updated.get("id") or trip_id)) or updated

    async def cancel_driver_trip(self, trip_id: str) -> dict[str, Any]:
        response = await (
            self.client.table("driver_trips")
            .update({"status": "cancelled"})
            .eq("id", trip_id)
            .execute()
        )
        data = _response_data(response)
        updated = data[0] if isinstance(data, list) else data
        return await self.get_trip_by_id(str(updated.get("id") or trip_id)) or updated

    async def create_booking_lead(
        self,
        *,
        customer_id: str,
        trip_id: str,
        requested_seats: int,
        notes: str | None,
    ) -> dict[str, Any]:
        payload = {
            "customer_id": customer_id,
            "trip_id": trip_id,
            "requested_seats": requested_seats,
            "status": "pending",
            "notes": notes,
            "driver_notification_status": "not_sent",
        }
        response = await self.client.table("booking_leads").insert(payload).execute()
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data

    async def update_booking_lead_notification(
        self,
        *,
        lead_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"driver_notification_status": status}
        if metadata is not None:
            payload["metadata"] = metadata
        response = (
            await self.client.table("booking_leads").update(payload).eq("id", lead_id).execute()
        )
        data = _response_data(response)
        return data[0] if isinstance(data, list) else data
