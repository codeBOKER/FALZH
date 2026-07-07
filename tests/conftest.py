import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.main import create_app
from app.models.domain import ToolResult


@pytest.fixture
def settings() -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="supabase-secret",
        jina_api_key="jina-secret",
        groq_api_key="groq-secret",
        groq_model="groq-tool-model",
        hf_token="hf-secret",
        hf_model="hf-tool-model",
        whatsapp_verify_token="verify-token",
        whatsapp_app_secret="app-secret",
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="123",
        admin_api_key="admin-secret",
    )


def signed_body(payload: dict[str, Any], secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, signature


def whatsapp_payload(message_id: str = "wamid.1", text: str = "Hello") -> dict[str, Any]:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "123"},
                            "contacts": [
                                {
                                    "wa_id": "967700000001",
                                    "profile": {"name": "Test Customer"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "967700000001",
                                    "id": message_id,
                                    "timestamp": "1790000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


class DummyConversation:
    def __init__(self) -> None:
        self.calls = []

    async def handle_inbound_message(self, inbound: Any) -> str:
        self.calls.append(inbound)
        return "ok"


class DummyAdmin:
    async def seed_info(self) -> int:
        return 2

    async def sync_trips(self) -> int:
        return 3


@pytest.fixture
def test_app(settings: Settings) -> Any:
    container = SimpleNamespace(
        settings=settings,
        conversation=DummyConversation(),
        admin=DummyAdmin(),
    )
    app = create_app(settings=settings, container=container)
    app.state.test_container = container
    return app


class FakeRepository:
    def __init__(self) -> None:
        self.customers_by_remote_jid: dict[str, dict[str, Any]] = {}
        self.drivers_by_remote_jid: dict[str, dict[str, Any]] = {}
        self.driver_cars_by_driver: dict[str, list[dict[str, Any]]] = {}
        self.latest_trips_by_driver: dict[str, dict[str, Any]] = {}
        self.created_drivers: list[dict[str, Any]] = []
        self.created_trips: list[dict[str, Any]] = []
        self.trip_embeddings: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.trip_selections: list[dict[str, Any]] = []
        self.notification_updates: list[dict[str, Any]] = []
        self.trips_by_id: dict[str, dict[str, Any]] = {}
        self.active_search_results: list[dict[str, Any]] = []
        self.info_search_results: list[dict[str, Any]] = []
        self.trip_vector_search_results: list[dict[str, Any]] = []
        self.vector_trip_search_calls: list[dict[str, Any]] = []

    async def upsert_customer(
        self,
        *,
        remote_jid: str | None = None,
        name: str | None = None,
        preferred_language: str | None = None,
        phone_number: str | None = None,
    ) -> dict[str, Any]:
        jid = remote_jid or phone_number
        if not jid:
            raise ValueError("remote_jid or phone_number is required")
        customer = self.customers_by_remote_jid.get(jid)
        if customer is None:
            customer = {
                "id": f"cust-{len(self.customers_by_remote_jid) + 1}",
                "remoteJid": jid,
                "name": name,
                "preferred_language": preferred_language,
                "phone_number": phone_number,
                "user_mode": None,
                "session_data": {},
            }
            self.customers_by_remote_jid[jid] = customer
        elif name:
            customer["name"] = name
        if phone_number:
            customer["phone_number"] = phone_number
        return customer

    async def update_customer_user_mode(
        self,
        *,
        customer_id: str,
        user_mode: str,
    ) -> dict[str, Any]:
        for customer in self.customers_by_remote_jid.values():
            if customer["id"] == customer_id:
                customer["user_mode"] = user_mode
                return customer
        raise KeyError(customer_id)

    async def update_customer_name(
        self,
        *,
        customer_id: str,
        name: str,
    ) -> dict[str, Any]:
        for customer in self.customers_by_remote_jid.values():
            if customer["id"] == customer_id:
                customer["name"] = name
                return customer
        raise KeyError(customer_id)

    async def get_customer_session(self, customer_id: str) -> dict[str, Any]:
        for customer in self.customers_by_phone.values():
            if customer["id"] == customer_id:
                return dict(customer.get("session_data") or {})
        return {}

    async def update_customer_session(
        self,
        *,
        customer_id: str,
        session_data: dict[str, Any],
    ) -> dict[str, Any]:
        for customer in self.customers_by_phone.values():
            if customer["id"] == customer_id:
                customer["session_data"] = session_data
                return customer
        raise KeyError(customer_id)

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
        return any(
            message.get("whatsapp_message_id") == whatsapp_message_id
            for message in self.messages
        )

    async def create_message(
        self,
        *,
        customer_id: str,
        sender_type: str,
        message: str,
        whatsapp_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": f"msg-{len(self.messages) + 1}",
            "customer_id": customer_id,
            "sender_type": sender_type,
            "message": message,
            "whatsapp_message_id": whatsapp_message_id,
            "metadata": metadata or {},
            "created_at": (
                datetime(2026, 5, 21, tzinfo=UTC) + timedelta(seconds=len(self.messages))
            ).isoformat(),
        }
        self.messages.append(row)
        return row

    async def get_recent_context_messages(
        self,
        *,
        customer_id: str,
        current_message_id: str,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        current_index = next(
            index for index, row in enumerate(self.messages) if row["id"] == current_message_id
        )
        prior = [
            row
            for row in self.messages[:current_index]
            if row["customer_id"] == customer_id
        ][-limit:]
        return prior + [self.messages[current_index]]

    async def get_trips_by_ids(self, trip_ids: list[str]) -> list[dict[str, Any]]:
        return [self.trips_by_id[trip_id] for trip_id in trip_ids if trip_id in self.trips_by_id]

    async def search_active_trips(self, **_: Any) -> list[dict[str, Any]]:
        return self.active_search_results

    async def search_info_chunks_by_vector(
        self,
        *,
        query_embedding: list[float],
        match_count: int = 5,
    ) -> list[dict[str, Any]]:
        return self.info_search_results[:match_count]

    async def search_trips_by_vector(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.vector_trip_search_calls.append(kwargs)
        match_count = int(kwargs.get("match_count") or 10)
        return self.trip_vector_search_results[:match_count]

    async def create_trip_selection(
        self,
        *,
        customer_id: str,
        trip_id: str,
        requested_seats: int,
        notes: str | None,
    ) -> dict[str, Any]:
        selection = {
            "id": f"sel-{len(self.trip_selections) + 1}",
            "customer_id": customer_id,
            "trip_id": trip_id,
            "requested_seats": requested_seats,
            "notes": notes,
        }
        self.trip_selections.append(selection)
        return selection

    async def update_selection_notification(
        self,
        *,
        selection_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        update = {"selection_id": selection_id, "status": status, "metadata": metadata}
        self.notification_updates.append(update)
        return update

    async def count_trip_selections(self, trip_id: str) -> int:
        return sum(1 for s in self.trip_selections if s["trip_id"] == trip_id)

    async def get_driver_by_phone(self, remote_jid: str) -> dict[str, Any] | None:
        return self.drivers_by_remote_jid.get(remote_jid)

    async def create_driver(self, *, customer_id: str) -> dict[str, Any]:
        customer = next(
            (customer for customer in self.customers_by_remote_jid.values() if customer["id"] == customer_id),
            None,
        )
        if customer is None:
            raise KeyError(customer_id)
        driver = {
            "id": f"driver-{len(self.drivers_by_remote_jid) + 1}",
            "customer_id": customer_id,
            "status": "active",
            "customers": customer,
        }
        self.drivers_by_remote_jid[customer["remoteJid"]] = driver
        self.created_drivers.append(driver)
        return driver

    async def get_driver_latest_trip(self, driver_id: str) -> dict[str, Any] | None:
        return self.latest_trips_by_driver.get(driver_id)

    async def list_driver_cars(self, driver_id: str) -> list[dict[str, Any]]:
        return self.driver_cars_by_driver.get(driver_id, [])

    async def list_driver_trips(self, driver_id: str) -> list[dict[str, Any]]:
        trips = [
            trip
            for trip in self.trips_by_id.values()
            if str(trip.get("driver_id")) == driver_id and trip.get("status") == "active"
        ]
        return sorted(
            trips,
            key=lambda trip: (
                str(trip.get("departure_date") or ""),
                {"morning": 0, "noon": 1, "night": 2}.get(str(trip.get("departure_time") or ""), 99),
            ),
        )

    async def create_driver_car(
        self,
        *,
        driver_id: str,
        car_type: str,
        plate_number: str | None = None,
        seat_count: int | None = None,
    ) -> dict[str, Any]:
        car_id = f"car-{sum(len(cars) for cars in self.driver_cars_by_driver.values()) + 1}"
        car = {
            "id": car_id,
            "driver_id": driver_id,
            "car_type": car_type,
            "plate_number": plate_number,
            "seat_count": seat_count,
        }
        self.driver_cars_by_driver.setdefault(driver_id, []).append(car)
        return car

    async def create_driver_trip(self, **kwargs: Any) -> dict[str, Any]:
        trip_id = f"trip-{len(self.created_trips) + 1}"
        driver_id = str(kwargs["driver_id"])
        car_id = kwargs.get("car_id")
        cars = self.driver_cars_by_driver.get(driver_id, [])
        driver = next(
            (row for row in self.drivers_by_remote_jid.values() if row["id"] == driver_id),
            {"name": "Driver"},
        )
        matched_car = next(
            (car for car in cars if str(car["id"]) == str(car_id)),
            {"car_type": "SUV"},
        )
        trip = {
            "id": trip_id,
            "status": "active",
            "drivers": driver,
            "driver_cars": matched_car,
            **kwargs,
        }
        self.created_trips.append(trip)
        self.trips_by_id[trip_id] = trip
        self.latest_trips_by_driver[driver_id] = trip
        return trip

    async def get_trip_by_id(self, trip_id: str) -> dict[str, Any] | None:
        return self.trips_by_id.get(trip_id)

    async def update_driver_trip(
        self,
        trip_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        trip = self.trips_by_id[trip_id]
        trip.update(updates)
        return trip

    async def cancel_driver_trip(self, trip_id: str) -> dict[str, Any]:
        trip = self.trips_by_id[trip_id]
        trip["status"] = "cancelled"
        return trip

    async def delete_trip_embedding(self, trip_id: str) -> None:
        self.trip_embeddings = [
            row for row in self.trip_embeddings if row.get("trip_id") != trip_id
        ]

    async def upsert_trip_embeddings(self, trip_embeddings: list[dict[str, Any]]) -> int:
        self.trip_embeddings.extend(trip_embeddings)
        return len(trip_embeddings)


class FakeEmbeddings:
    def __init__(self) -> None:
        self.query_texts: list[str] = []
        self.passage_texts: list[list[str]] = []
        self.query_embedding = [0.1, 0.2, 0.3]

    async def embed_query(self, text: str) -> list[float]:
        self.query_texts.append(text)
        return self.query_embedding

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self.passage_texts.append(texts)
        return [self.query_embedding for _ in texts]


class FakeWhatsApp:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []
        self.interactive_lists: list[tuple[str, dict[str, Any]]] = []

    async def send_text(self, to_phone: str, text: str) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((to_phone, text))
        return {"messages": [{"id": "sent"}]}

    async def send_interactive_list(
        self,
        to_phone: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("send failed")
        self.interactive_lists.append((to_phone, interactive))
        return {"messages": [{"id": "sent-interactive"}]}


class FakeAI:
    def __init__(self, reply: str = "Here is your reply") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def generate_reply(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        registry: Any,
    ) -> str:
        self.calls.append({"messages": messages, "tools": tools, "registry": registry})
        return self.reply


async def ok_tool(_: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, data={"value": 42})
