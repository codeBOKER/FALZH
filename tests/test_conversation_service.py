import pytest

from app.models.domain import WhatsAppInboundMessage
from app.services.conversation_service import ConversationService
from tests.conftest import FakeAI, FakeEmbeddings, FakeRepository, FakeWhatsApp


@pytest.mark.asyncio
async def test_conversation_stores_messages_uses_last_four_context_and_sends_reply(settings):
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000001", name="Customer")
    for index in range(6):
        await repository.create_message(
            customer_id=customer["id"],
            sender_type="customer" if index % 2 == 0 else "assistant",
            message=f"prior-{index}",
        )
    ai = FakeAI(reply="Found trips for you")
    whatsapp = FakeWhatsApp()
    service = ConversationService(
        repository=repository,
        embeddings=FakeEmbeddings(),
        whatsapp=whatsapp,
        ai=ai,
        settings=settings,
    )

    reply = await service.handle_inbound_message(
        WhatsAppInboundMessage(
            message_id="wamid.new",
            from_phone="967700000001",
            text="Aden to Mukalla tomorrow",
            profile_name="Customer",
        )
    )

    assert reply == "Found trips for you"
    assert repository.messages[-2]["message"] == "Aden to Mukalla tomorrow"
    assert repository.messages[-1]["sender_type"] == "assistant"

    ai_messages = ai.calls[0]["messages"]
    assert ai_messages[0]["role"] == "system"
    assert "not chosen a role yet" in ai_messages[0]["content"]
    assert [message["content"] for message in ai_messages[1:]] == [
        "prior-2",
        "prior-3",
        "prior-4",
        "prior-5",
        "Aden to Mukalla tomorrow",
    ]
    tool_names = {tool["function"]["name"] for tool in ai.calls[0]["tools"]}
    assert tool_names == {
        "about_falsa",
        "create_driver_account",
        "switch_to_driver",
        "switch_to_passenger",
    }


@pytest.mark.asyncio
async def test_conversation_skips_duplicate_whatsapp_message(settings):
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000001")
    await repository.create_message(
        customer_id=customer["id"],
        sender_type="customer",
        message="already handled",
        whatsapp_message_id="wamid.duplicate",
    )
    ai = FakeAI()
    whatsapp = FakeWhatsApp()
    service = ConversationService(
        repository=repository,
        embeddings=FakeEmbeddings(),
        whatsapp=whatsapp,
        ai=ai,
        settings=settings,
    )

    result = await service.handle_inbound_message(
        WhatsAppInboundMessage(
            message_id="wamid.duplicate",
            from_phone="967700000001",
            text="same message",
        )
    )

    assert result is None
    assert ai.calls == []
    assert whatsapp.sent == []


@pytest.mark.asyncio
async def test_conversation_uses_passenger_tools_when_user_mode_is_passenger(settings):
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000001", name="Customer")
    customer["user_mode"] = "passenger"
    ai = FakeAI(reply="Passenger reply")
    service = ConversationService(
        repository=repository,
        embeddings=FakeEmbeddings(),
        whatsapp=FakeWhatsApp(),
        ai=ai,
        settings=settings,
    )

    await service.handle_inbound_message(
        WhatsAppInboundMessage(
            message_id="wamid.passenger",
            from_phone="967700000001",
            text="Aden to Mukalla tomorrow",
            profile_name="Customer",
        )
    )

    tool_names = {tool["function"]["name"] for tool in ai.calls[0]["tools"]}
    assert tool_names == {
        "about_falsa",
        "search_trips",
        "create_booking_lead",
        "create_driver_account",
        "switch_to_driver",
    }
    assert "travel booking assistant" in ai.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_conversation_uses_driver_tools_when_user_mode_is_driver(settings):
    repository = FakeRepository()
    customer = await repository.upsert_customer(remote_jid="967700000010", name="Ali")
    customer["user_mode"] = "driver"
    ai = FakeAI(reply="Driver reply")
    whatsapp = FakeWhatsApp()
    service = ConversationService(
        repository=repository,
        embeddings=FakeEmbeddings(),
        whatsapp=whatsapp,
        ai=ai,
        settings=settings,
    )

    await service.handle_inbound_message(
        WhatsAppInboundMessage(
            message_id="wamid.driver",
            from_phone="967700000010",
            text="Check my trips",
            profile_name="Ali",
        )
    )

    tool_names = {tool["function"]["name"] for tool in ai.calls[0]["tools"]}
    assert tool_names == {
        "about_falsa",
        "check_driver_info",
        "check_driver_trips",
        "add_driver_car",
        "add_trip_by_driver",
        "delete_trip_by_number",
        "modify_trip_by_number",
        "initiate_trip_action",
        "update_trip_field",
        "switch_to_passenger",
    }


@pytest.mark.asyncio
async def test_conversation_handles_delete_interactive_reply(settings):
    repository = FakeRepository()
    customer = await repository.upsert_customer(phone_number="967700000010", name="Ali")
    customer["user_mode"] = "driver"
    repository.drivers_by_phone["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "phone_number": "967700000010",
    }
    repository.trips_by_id["trip-1"] = {
        "id": "trip-1",
        "driver_id": "driver-1",
        "departure": "Aden",
        "destination": "Mukalla",
        "departure_date": "2026-12-01",
        "departure_time": "morning",
        "status": "active",
        "driver_cars": {"car_type": "SUV"},
        "drivers": {"name": "Ali"},
    }
    repository.trip_embeddings.append({"trip_id": "trip-1"})
    ai = FakeAI()
    service = ConversationService(
        repository=repository,
        embeddings=FakeEmbeddings(),
        whatsapp=FakeWhatsApp(),
        ai=ai,
        settings=settings,
    )

    reply = await service.handle_inbound_message(
        WhatsAppInboundMessage(
            message_id="wamid.delete",
            from_phone="967700000010",
            text="Noon - Mukalla",
            message_type="interactive",
            interactive_reply_id="DELETE_trip-1",
        )
    )

    assert reply == "Success! Your trip has been canceled."
    assert repository.trips_by_id["trip-1"]["status"] == "cancelled"
    assert repository.trip_embeddings == []
    assert ai.calls == []


@pytest.mark.asyncio
async def test_conversation_handles_modify_interactive_reply(settings):
    repository = FakeRepository()
    customer = await repository.upsert_customer(phone_number="967700000010", name="Ali")
    customer["user_mode"] = "driver"
    repository.drivers_by_phone["967700000010"] = {
        "id": "driver-1",
        "name": "Ali",
        "phone_number": "967700000010",
    }
    repository.trips_by_id["trip-1"] = {
        "id": "trip-1",
        "driver_id": "driver-1",
        "departure": "Aden",
        "destination": "Mukalla",
        "departure_date": "2026-12-01",
        "departure_time": "morning",
        "status": "active",
        "driver_cars": {"car_type": "SUV"},
        "drivers": {"name": "Ali"},
    }
    ai = FakeAI(reply="What would you like to change?")
    service = ConversationService(
        repository=repository,
        embeddings=FakeEmbeddings(),
        whatsapp=FakeWhatsApp(),
        ai=ai,
        settings=settings,
    )

    reply = await service.handle_inbound_message(
        WhatsAppInboundMessage(
            message_id="wamid.modify",
            from_phone="967700000010",
            text="Morning - Mukalla",
            message_type="interactive",
            interactive_reply_id="MODIFY_trip-1",
        )
    )

    assert reply == "What would you like to change?"
    session = await repository.get_customer_session(customer["id"])
    assert session["active_edit_trip_id"] == "trip-1"
    assert ai.calls[0]["messages"][-1]["content"].startswith("SYSTEM: Driver selected trip trip-1")
