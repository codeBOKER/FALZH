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
    assert "FALZH" in ai_messages[0]["content"]
    context_contents = [message["content"] for message in ai_messages[1:]]
    assert context_contents[-1] == "Aden to Mukalla tomorrow"
    assert len(context_contents) <= 9
    tool_names = {tool["function"]["name"] for tool in ai.calls[0]["tools"]}
    assert tool_names == {
        "about_falzh",
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
        "about_falzh",
        "search_trips",
        "select_trip",
        "create_driver_account",
        "switch_to_driver",
    }
    assert "travel booking assistant" in ai.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_returning_driver_gets_welcome_and_upgraded_to_driver(settings):
    """An unregistered driver saved from group trips who later DMs the bot
    should get a personalized welcome system note, use driver tools, and
    have their user_mode upgraded to 'driver'."""
    repository = FakeRepository()

    # Step 1: Simulate group-trip extraction creating an unregistered driver
    customer = await repository.upsert_customer(
        remote_jid=None,
        name="فهد",
        phone_number="967712345678",
        registered=False,
    )
    await repository.create_driver(customer_id=customer["id"])

    assert customer["user_mode"] is None
    assert customer["remoteJid"] is None

    # Step 2: Same driver sends a DM
    ai = FakeAI(reply="مرحباً فهد! نتابع رحلاتك...")
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
            message_id="wamid.returning",
            remoteJid="967712345678",
            text="مرحبا، أبي أضيف رحلة",
            phone_number="967712345678",
            profile_name="فهد",
        )
    )

    assert reply == "مرحباً فهد! نتابع رحلاتك..."

    # Should use driver tools (not new_user tools)
    tool_names = {tool["function"]["name"] for tool in ai.calls[0]["tools"]}
    assert tool_names == {
        "about_falzh",
        "check_driver_info",
        "check_driver_trips",
        "add_driver_car",
        "add_trip_by_driver",
        "initiate_trip_action",
        "update_trip_field",
        "switch_to_passenger",
    }

    # System note should be appended with returning-driver context
    ai_messages = ai.calls[0]["messages"]
    system_contents = [m["content"] for m in ai_messages if m["role"] == "system"]
    assert any("فهد" in msg for msg in system_contents)
    assert any("group" in msg.lower() or "مجموعات" in msg for msg in system_contents)

    # user_mode should be upgraded to 'driver'
    assert customer["user_mode"] == "driver"

    # WhatsApp message was sent
    assert len(whatsapp.sent) == 1


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
        "about_falzh",
        "check_driver_info",
        "check_driver_trips",
        "add_driver_car",
        "add_trip_by_driver",
        "initiate_trip_action",
        "update_trip_field",
        "switch_to_passenger",
    }



