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
            remoteJid="967700000001",
            text="Aden to Mukalla tomorrow",
            profile_name="Customer",
        )
    )

    assert reply == "Found trips for you"
    assert repository.messages[-2]["message"] == "Aden to Mukalla tomorrow"
    assert repository.messages[-1]["sender_type"] == "assistant"

    ai_messages = ai.calls[0]["messages"]
    assert ai_messages[0]["role"] == "system"
    assert "travel as passenger" in ai_messages[0]["content"]
    context_contents = [message["content"] for message in ai_messages[1:]]
    assert context_contents[-1] == "Aden to Mukalla tomorrow"
    assert len(context_contents) <= 9
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
            remoteJid="967700000001",
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
            remoteJid="967700000001",
            text="Aden to Mukalla tomorrow",
            profile_name="Customer",
        )
    )

    tool_names = {tool["function"]["name"] for tool in ai.calls[0]["tools"]}
    assert tool_names == {
        "about_falsa",
        "search_trips",
        "select_trip",
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
            remoteJid="967700000010",
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
        "initiate_trip_action",
        "update_trip_field",
        "switch_to_passenger",
    }



