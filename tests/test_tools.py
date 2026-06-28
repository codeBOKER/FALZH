import pytest

from app.tools.handlers import FalsaToolHandlers
from tests.conftest import FakeEmbeddings, FakeRepository, FakeWhatsApp


def make_handlers(
    *,
    repository: FakeRepository | None = None,
    embeddings: FakeEmbeddings | None = None,
    whatsapp: FakeWhatsApp | None = None,
    customer: dict | None = None,
    sender_phone: str = "967700000001",
) -> FalsaToolHandlers:
    return FalsaToolHandlers(
        repository=repository or FakeRepository(),
        embeddings=embeddings or FakeEmbeddings(),
        whatsapp=whatsapp or FakeWhatsApp(),
        customer=customer or {"id": "cust-1", "remoteJid": sender_phone},
        sender_phone=sender_phone,
        embedding_model="jina-embeddings-v5-text-small",
    )


def trip(
    *,
    trip_id="trip-1",
    departure="Aden",
    destination="Mukalla",
    departure_date="2026-12-01",
    departure_time="morning",
    available_seats=3,
    status="active",
    car_type="SUV",
):
    return {
        "id": trip_id,
        "departure": departure,
        "destination": destination,
        "departure_date": departure_date,
        "departure_time": departure_time,
        "available_seats": available_seats,
        "total_seats": 4,
        "price": "50.00",
        "status": status,
        "drivers": {"name": "Ali", "remoteJid": "967700000009"},
        "driver_cars": {"car_type": car_type},
    }


@pytest.mark.asyncio
async def test_about_falsa_returns_retrieved_context():
    repository = FakeRepository()
    repository.info_search_results = [
        {
            "similarity": 0.91,
            "chunk_text": "FALSA creates pending booking leads.",
            "source": "prompts/falsa_info.md",
        }
    ]
    embeddings = FakeEmbeddings()
    handlers = make_handlers(repository=repository, embeddings=embeddings)

    result = await handlers.about_falsa({"query": "What is FALSA?", "language": "en"})

    assert result.ok is True
    assert result.data["answer_context"][0]["text"] == "FALSA creates pending booking leads."
    assert embeddings.query_texts == ["What is FALSA?"]


@pytest.mark.asyncio
async def test_search_trips_uses_supabase_vector_matches_and_validates_business_rules():
    repository = FakeRepository()
    repository.trip_vector_search_results = [
        trip(trip_id="trip-1", available_seats=2),
        trip(trip_id="trip-2", status="cancelled"),
        trip(trip_id="trip-3", available_seats=0),
    ]
    embeddings = FakeEmbeddings()
    handlers = make_handlers(repository=repository, embeddings=embeddings)

    result = await handlers.search_trips(
        {"departure": "Aden", "destination": "Mukalla", "seats": 2, "vehicle_type": "SUV"}
    )

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["matches"][0]["trip_id"] == "trip-1"
    assert result.data["matches"][0]["departure_time_type"] == "morning"
    assert embeddings.query_texts == ["Aden Mukalla 2 seats SUV"]
    assert repository.vector_trip_search_calls[0]["departure"] == "Aden"
    assert repository.vector_trip_search_calls[0]["destination"] == "Mukalla"


@pytest.mark.asyncio
async def test_search_trips_filters_requested_datetime_to_departure_bucket():
    repository = FakeRepository()
    repository.trip_vector_search_results = [
        trip(trip_id="trip-1", departure_date="2026-12-02", departure_time="morning"),
        trip(trip_id="trip-2", departure_date="2026-12-02", departure_time="night"),
    ]
    handlers = make_handlers(repository=repository)

    result = await handlers.search_trips(
        {
            "departure": "Aden",
            "destination": "Mukalla",
            "travel_datetime": "2026-12-02T19:30:00+03:00",
        }
    )

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["matches"][0]["trip_id"] == "trip-2"
    assert repository.vector_trip_search_calls[0]["departure_time"] == "night"
    assert repository.vector_trip_search_calls[0]["requested_time"].hour == 19


@pytest.mark.asyncio
async def test_search_trips_reports_no_matches():
    handlers = make_handlers()

    result = await handlers.search_trips({"departure": "Aden", "destination": "Sana'a"})

    assert result.ok is True
    assert result.data["matches"] == []
    assert "No active" in result.data["note"]


@pytest.mark.asyncio
async def test_search_trips_includes_alternate_alert_when_first_result_is_over_one_hour():
    repository = FakeRepository()
    repository.trip_vector_search_results = [
        {
            **trip(trip_id="trip-1", departure_time="morning"),
            "time_difference_minutes": 90,
        }
    ]
    handlers = make_handlers(repository=repository)

    result = await handlers.search_trips(
        {
            "departure": "Aden",
            "destination": "Mukalla",
            "travel_time": "morning",
            "travel_time_exact": "04:30",
            "vector_query_text": "Aden to Mukalla tomorrow morning at 04:30",
        }
    )

    assert result.ok is True
    assert result.data["count"] == 1
    assert "more than 60 minutes" in result.data["alternate_alert"]
    assert result.data["matches"][0]["time_difference_minutes"] == 90


@pytest.mark.asyncio
async def test_create_booking_lead_notifies_driver():
    repository = FakeRepository()
    repository.trips_by_id["trip-1"] = trip()
    whatsapp = FakeWhatsApp()
    handlers = make_handlers(
        repository=repository,
        whatsapp=whatsapp,
        customer={"id": "cust-1", "remoteJid": "967700000001", "name": "Mona"},
    )

    result = await handlers.create_booking_lead(
        {"trip_id": "trip-1", "requested_seats": 2, "notes": "Window seat"}
    )

    assert result.ok is True
    assert result.data["driver_notification_status"] == "sent"
    assert repository.booking_leads[0]["requested_seats"] == 2
    assert whatsapp.sent[0][0] == "967700000009"


@pytest.mark.asyncio
async def test_create_booking_lead_keeps_pending_when_driver_notification_fails():
    repository = FakeRepository()
    repository.trips_by_id["trip-1"] = trip()
    handlers = make_handlers(
        repository=repository,
        whatsapp=FakeWhatsApp(fail=True),
    )

    result = await handlers.create_booking_lead({"trip_id": "trip-1", "requested_seats": 1})

    assert result.ok is True
    assert result.data["status"] == "pending"
    assert result.data["driver_notification_status"] == "failed"
    assert repository.notification_updates[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_create_booking_lead_rejects_insufficient_seats():
    repository = FakeRepository()
    repository.trips_by_id["trip-1"] = trip(available_seats=1)
    handlers = make_handlers(repository=repository)

    result = await handlers.create_booking_lead({"trip_id": "trip-1", "requested_seats": 2})

    assert result.ok is False
    assert result.error == "Not enough available seats"
    assert repository.booking_leads == []


@pytest.mark.asyncio
async def test_create_driver_account_uses_sender_phone():
    repository = FakeRepository()
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.create_driver_account({"name": "Ali Driver"})

    assert result.ok is True
    assert repository.created_drivers[0]["customers"]["remoteJid"] == "967700000010"
    assert repository.drivers_by_remote_jid["967700000010"]["name"] == "Ali Driver"


@pytest.mark.asyncio
async def test_create_driver_account_rejects_duplicate():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
    }
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.create_driver_account({"name": "Ali Driver"})

    assert result.ok is False
    assert "already exists" in (result.error or "")
    assert repository.created_drivers == []


@pytest.mark.asyncio
async def test_add_trip_by_driver_requires_account():
    handlers = make_handlers(sender_phone="967700000010")

    result = await handlers.add_trip_by_driver(
        {
            "departure": "عدن",
            "destination": "المكلا",
            "departure_date": "2026-06-10",
            "departure_time": "morning",
        }
    )

    assert result.ok is False
    assert result.data.get("action") == "create_driver_account"
    assert "create_driver_account" in (result.error or "")


@pytest.mark.asyncio
async def test_add_driver_car_accepts_name_only():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
    }
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.add_driver_car({"name": "سيارة"})

    assert result.ok is True
    assert result.data["name"] == "سيارة"
    assert result.data["plate_number"] is None
    assert result.data["seat_count"] is None
    assert repository.driver_cars_by_driver["driver-1"][0]["car_type"] == "سيارة"


@pytest.mark.asyncio
async def test_check_driver_info_returns_account_summary():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
        "status": "active",
    }
    repository.driver_cars_by_driver["driver-1"] = [
        {"id": "car-1", "car_type": "SUV", "plate_number": "1234", "seat_count": 4}
    ]
    repository.trips_by_id["trip-1"] = {
        "id": "trip-1",
        "driver_id": "driver-1",
        "departure": "عدن",
        "destination": "المكلا",
        "departure_date": "2026-12-01",
        "departure_time": "morning",
        "available_seats": 2,
        "total_seats": 4,
        "price": "80.00",
        "status": "active",
        "driver_cars": {"car_type": "SUV"},
        "drivers": {"name": "Ali"},
    }
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.check_driver_info({})

    assert result.ok is True
    assert result.data["driver_id"] == "driver-1"
    assert result.data["vehicle_count"] == 1
    assert result.data["active_trip_count"] == 1
    assert result.data["vehicles"][0]["name"] == "SUV"
    assert result.data["active_trips"][0]["trip_id"] == "trip-1"


@pytest.mark.asyncio
async def test_check_driver_trips_returns_upcoming_trips():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
    }
    repository.trips_by_id["trip-1"] = {
        "id": "trip-1",
        "driver_id": "driver-1",
        "departure": "عدن",
        "destination": "المكلا",
        "departure_date": "2026-12-01",
        "departure_time": "morning",
        "available_seats": 2,
        "total_seats": 4,
        "price": "80.00",
        "status": "active",
        "driver_cars": {"car_type": "SUV"},
        "drivers": {"name": "Ali"},
    }
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.check_driver_trips({})

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["upcoming_trips"][0]["trip_id"] == "trip-1"


@pytest.mark.asyncio
async def test_check_driver_trips_requests_driver_registration_if_unregistered():
    handlers = make_handlers(sender_phone="967700000010")

    result = await handlers.check_driver_trips({})

    assert result.ok is False
    assert result.data["action"] == "create_driver_account"
    assert "create_driver_account" in (result.error or "")


@pytest.mark.asyncio
async def test_check_driver_info_requests_driver_registration_if_unregistered():
    handlers = make_handlers(sender_phone="967700000010")

    result = await handlers.check_driver_info({})

    assert result.ok is False
    assert result.data["action"] == "create_driver_account"
    assert "create_driver_account" in (result.error or "")


@pytest.mark.asyncio
async def test_add_trip_by_driver_uses_latest_trip_defaults():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
    }
    repository.driver_cars_by_driver["driver-1"] = [
        {"id": "car-1", "car_type": "SUV", "seat_count": 4},
    ]
    repository.latest_trips_by_driver["driver-1"] = {
        "car_id": "car-1",
        "available_seats": 3,
        "total_seats": 4,
        "price": "75.00",
    }
    embeddings = FakeEmbeddings()
    handlers = make_handlers(
        repository=repository,
        embeddings=embeddings,
        sender_phone="967700000010",
    )

    result = await handlers.add_trip_by_driver(
        {
            "departure": "عدن",
            "destination": "صنعاء",
            "departure_date": "2026-06-10",
            "departure_time": "noon",
        }
    )

    assert result.ok is True
    assert result.data["trip_id"] == "trip-1"
    assert repository.created_trips[0]["car_id"] == "car-1"
    assert repository.created_trips[0]["available_seats"] == 3
    assert repository.created_trips[0]["price"] == 75.0
    assert repository.trip_embeddings[0]["trip_id"] == "trip-1"
    assert embeddings.passage_texts == [["Trip trip-1: عدن to صنعاء on 2026-06-10 during noon. Available seats: 3 of 4. Vehicle: SUV. Driver: Ali. Price: 75.0. Status: active."]]


@pytest.mark.asyncio
async def test_add_trip_by_driver_resolves_vehicle_type_by_name():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
    }
    repository.driver_cars_by_driver["driver-1"] = [
        {"id": "car-1", "car_type": "باص", "plate_number": "1234", "seat_count": 14},
        {"id": "car-2", "car_type": "سيارة", "plate_number": "5678", "seat_count": 4},
    ]
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.add_trip_by_driver(
        {
            "departure": "عدن",
            "destination": "المكلا",
            "departure_date": "2026-06-10",
            "departure_time": "morning",
            "vehicle_type": "باص",
            "available_seats": 10,
            "total_seats": 14,
            "price": 120,
        }
    )

    assert result.ok is True
    assert repository.created_trips[0]["car_id"] == "car-1"


@pytest.mark.asyncio
async def test_add_trip_by_driver_indexes_new_trip():
    repository = FakeRepository()
    repository.drivers_by_remote_jid["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000010",
    }
    repository.driver_cars_by_driver["driver-1"] = [
        {"id": "car-1", "car_type": "SUV", "seat_count": 4},
    ]
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.add_trip_by_driver(
        {
            "departure": "عدن",
            "destination": "المكلا",
            "departure_date": "2026-06-10",
            "departure_time": "morning",
            "vehicle_type": "SUV",
            "available_seats": 2,
            "total_seats": 4,
            "price": 50,
        }
    )

    assert result.ok is True
    assert result.data["indexed"] is True
    assert len(repository.trip_embeddings) == 1


@pytest.mark.asyncio
async def test_switch_to_driver_requires_existing_driver_account():
    repository = FakeRepository()
    customer = {"id": "cust-1", "remoteJid": "967700000001"}
    handlers = make_handlers(repository=repository, customer=customer)

    result = await handlers.switch_to_driver({})

    assert result.ok is False
    assert result.data["action"] == "create_driver_account"


@pytest.mark.asyncio
async def test_switch_to_driver_updates_customer_mode():
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000001")
    repository.drivers_by_remote_jid["967700000001"] = {
        "id": "driver-1",
        "name": "Ali",
        "remoteJid": "967700000001",
    }
    handlers = make_handlers(repository=repository, customer=customer)

    result = await handlers.switch_to_driver({})

    assert result.ok is True
    assert result.data["user_mode"] == "driver"
    assert customer["user_mode"] == "driver"


@pytest.mark.asyncio
async def test_switch_to_passenger_updates_mode_and_optional_name():
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000001")
    handlers = make_handlers(repository=repository, customer=customer)

    result = await handlers.switch_to_passenger({"name": "Sara"})

    assert result.ok is True
    assert result.data["user_mode"] == "passenger"
    assert customer["user_mode"] == "passenger"
    assert customer["name"] == "Sara"


@pytest.mark.asyncio
async def test_switch_to_passenger_without_name():
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000001", name="Existing")
    handlers = make_handlers(repository=repository, customer=customer)

    result = await handlers.switch_to_passenger({})

    assert result.ok is True
    assert customer["user_mode"] == "passenger"
    assert customer["name"] == "Existing"


def _driver_setup(repository: FakeRepository, *, phone: str = "967700000010") -> None:
    repository.drivers_by_phone[phone] = {
        "id": "driver-1",
        "name": "Ali",
        "phone_number": phone,
    }
    repository.trips_by_id["trip-1"] = {
        "id": "trip-1",
        "driver_id": "driver-1",
        "departure": "عدن",
        "destination": "المكلا",
        "departure_date": "2026-12-01",
        "departure_time": "morning",
        "available_seats": 2,
        "total_seats": 4,
        "price": "80.00",
        "status": "active",
        "driver_cars": {"car_type": "SUV"},
        "drivers": {"name": "Ali"},
    }


@pytest.mark.asyncio
async def test_initiate_trip_action_sends_whatsapp_list():
    repository = FakeRepository()
    whatsapp = FakeWhatsApp()
    _driver_setup(repository)
    handlers = make_handlers(
        repository=repository,
        whatsapp=whatsapp,
        sender_phone="967700000010",
    )

    result = await handlers.initiate_trip_action({"action_type": "DELETE"})

    assert result.ok is True
    assert result.data["count"] == 1
    assert "Pausing for response" in result.data["message"]
    assert len(whatsapp.interactive_lists) == 1
    _, interactive = whatsapp.interactive_lists[0]
    assert interactive["action"]["sections"][0]["rows"][0]["id"] == "DELETE_trip-1"


@pytest.mark.asyncio
async def test_initiate_trip_action_returns_no_trips_message():
    repository = FakeRepository()
    _driver_setup(repository)
    repository.trips_by_id.clear()
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.initiate_trip_action({"action_type": "MODIFY"})

    assert result.ok is True
    assert result.data["count"] == 0
    assert "No trips found" in result.data["message"]


@pytest.mark.asyncio
async def test_delete_trip_by_number_cancels_trip():
    repository = FakeRepository()
    _driver_setup(repository)
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.delete_trip_by_number({"trip_number": 1})

    assert result.ok is True
    assert result.data["trip_number"] == 1
    assert result.data["trip_id"] == "trip-1"
    assert repository.trips_by_id["trip-1"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_modify_trip_by_number_updates_trip_field():
    repository = FakeRepository()
    _driver_setup(repository)
    handlers = make_handlers(repository=repository, sender_phone="967700000010")

    result = await handlers.modify_trip_by_number(
        {"trip_number": 1, "field": "departure", "value": "تعز"}
    )

    assert result.ok is True
    assert result.data["trip_number"] == 1
    assert result.data["field"] == "departure"
    assert result.data["value"] == "تعز"
    assert repository.trips_by_id["trip-1"]["departure"] == "تعز"


@pytest.mark.asyncio
async def test_update_trip_field_uses_active_session_and_clears_it():
    repository = FakeRepository()
    customer = await repository.upsert_customer(phone_number="967700000010")
    await repository.set_customer_session_field(
        customer_id=customer["id"],
        key="active_edit_trip_id",
        value="trip-1",
    )
    _driver_setup(repository)
    handlers = make_handlers(
        repository=repository,
        customer=customer,
        sender_phone="967700000010",
    )

    result = await handlers.update_trip_field(
        {"field": "pickup_time", "value": "15:00"},
    )

    assert result.ok is True
    assert repository.trips_by_id["trip-1"]["departure_time"] == "noon"
    assert await repository.get_customer_session(customer["id"]) == {}
    assert len(repository.trip_embeddings) == 1
