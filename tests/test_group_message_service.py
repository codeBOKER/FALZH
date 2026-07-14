import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.models.domain import AIProviderResponse, ExtractedTrip, WhatsAppInboundMessage
from app.services.group_message_service import GroupMessageService
from tests.conftest import FakeRepository, FakeEmbeddings


class FakeProvider:
    def __init__(self, response_content: str | None = None) -> None:
        self.response_content = response_content
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = 0.2,
    ) -> AIProviderResponse:
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        return AIProviderResponse(content=self.response_content)


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


def _make_inbound(
    text: str,
    message_id: str = "wamid.group.1",
) -> WhatsAppInboundMessage:
    return WhatsAppInboundMessage(
        message_id=message_id,
        remoteJid="12345@g.us",
        text=text,
        timestamp="1790000000",
        is_group=True,
    )


def _trip_ad_response(
    *,
    departure: str = "صنعاء",
    destination: str = "عدن",
    departure_date: str = "2026-07-15",
    departure_time: str = "morning",
    price: float = 5000,
    driver_phone: str = "967712345678",
    driver_name: str | None = "أحمد",
    car_type: str | None = "سيارة",
    available_seats: int | None = 4,
    total_seats: int | None = 4,
) -> str:
    return json.dumps({
        "is_trip_ad": True,
        "departure": departure,
        "destination": destination,
        "departure_date": departure_date,
        "departure_time": departure_time,
        "available_seats": available_seats,
        "total_seats": total_seats,
        "price": price,
        "car_type": car_type,
        "driver_name": driver_name,
        "driver_phone": driver_phone,
    })


@pytest.mark.asyncio
async def test_non_trip_message_is_ignored(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content=json.dumps({"is_trip_ad": False}))

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="مرحبا بالجميع")
    await service.handle_group_message(inbound)

    assert len(repo.customers_by_remote_jid) == 0
    assert len(repo.created_drivers) == 0
    assert len(repo.created_trips) == 0


@pytest.mark.asyncio
async def test_trip_ad_from_new_driver_creates_all_entities(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content=_trip_ad_response())

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة من صنعاء إلى عدن بكرة الصباح 5000 ريال 967712345678")
    await service.handle_group_message(inbound)

    assert len(repo.customers_by_remote_jid) == 1
    customer = list(repo.customers_by_remote_jid.values())[0]
    assert customer["registered"] is False
    assert customer["phone_number"] == "967712345678"

    assert len(repo.created_drivers) == 1
    assert len(repo.created_trips) == 1

    trip = repo.created_trips[0]
    assert trip["departure"] == "صنعاء"
    assert trip["destination"] == "عدن"


@pytest.mark.asyncio
async def test_trip_ad_from_existing_customer_is_discarded(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content=_trip_ad_response())

    existing_customer = await repo.upsert_customer(
        remote_jid="967712345678",
        name="أحمد",
        phone_number="967712345678",
        registered=True,
    )

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة من صنعاء إلى عدن 967712345678")
    await service.handle_group_message(inbound)

    assert len(repo.created_drivers) == 0
    assert len(repo.created_trips) == 0


@pytest.mark.asyncio
async def test_existing_unregistered_driver_adds_new_trip(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content=_trip_ad_response())

    await repo.upsert_customer(
        remote_jid="967712345678",
        name="أحمد",
        phone_number="967712345678",
        registered=False,
    )

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة من صنعاء إلى عدن")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 1
    assert repo.created_trips[0]["departure"] == "صنعاء"


@pytest.mark.asyncio
async def test_existing_unregistered_driver_duplicate_trip_is_skipped(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content=_trip_ad_response())

    await repo.upsert_customer(
        remote_jid="967712345678",
        name="أحمد",
        phone_number="967712345678",
        registered=False,
    )
    driver = await repo.create_driver(
        customer_id=list(repo.customers_by_remote_jid.values())[0]["id"]
    )
    await repo.create_driver_trip(
        driver_id=str(driver["id"]),
        car_id=None,
        departure="صنعاء",
        destination="عدن",
        departure_date=date(2026, 7, 15),
        departure_time="morning",
        available_seats=4,
        total_seats=4,
        price=5000,
    )

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    initial_count = len(repo.created_trips)
    inbound = _make_inbound(text="رحلة من صنعاء إلى عدن")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == initial_count


@pytest.mark.asyncio
async def test_incomplete_fields_cause_skip(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    response = json.dumps({
        "is_trip_ad": True,
        "departure": "صنعاء",
        "destination": None,
        "departure_date": "2026-07-15",
        "departure_time": "morning",
        "price": 5000,
        "driver_phone": "967712345678",
    })
    provider = FakeProvider(response_content=response)

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة من صنعاء")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 0


@pytest.mark.asyncio
async def test_missing_phone_cause_skip(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    response = json.dumps({
        "is_trip_ad": True,
        "departure": "صنعاء",
        "destination": "عدن",
        "departure_date": "2026-07-15",
        "departure_time": "morning",
        "price": 5000,
        "driver_phone": None,
    })
    provider = FakeProvider(response_content=response)

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة من صنعاء إلى عدن")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 0


@pytest.mark.asyncio
async def test_duplicate_group_message_is_deduplicated(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content=_trip_ad_response())

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة", message_id="wamid.dup.1")
    await service.handle_group_message(inbound)
    assert len(repo.created_trips) == 1

    await service.handle_group_message(inbound)
    assert len(repo.created_trips) == 1


@pytest.mark.asyncio
async def test_phone_normalization(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(
        response_content=_trip_ad_response(driver_phone="+967-71-234-5678")
    )

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة")
    await service.handle_group_message(inbound)

    assert len(repo.customers_by_remote_jid) == 1
    customer = list(repo.customers_by_remote_jid.values())[0]
    assert customer["phone_number"] == "967712345678"


@pytest.mark.asyncio
async def test_invalid_json_response_cause_skip(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(response_content="this is not json")

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 0


@pytest.mark.asyncio
async def test_markdown_fenced_json_is_parsed(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    fenced = f"```json\n{_trip_ad_response()}\n```"
    provider = FakeProvider(response_content=fenced)

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 1


@pytest.mark.asyncio
async def test_trip_ad_with_missing_car_type_uses_unknown(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(
        response_content=_trip_ad_response(car_type=None)
    )

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 1
    cars = list(repo.driver_cars_by_driver.values())[0]
    assert cars[0]["car_type"] == "غير معروف"


@pytest.mark.asyncio
async def test_trip_ad_with_missing_name_uses_none(settings: Settings) -> None:
    repo = FakeRepository()
    embeddings = FakeEmbeddings()
    provider = FakeProvider(
        response_content=_trip_ad_response(driver_name=None)
    )

    service = GroupMessageService(
        repository=repo,
        embeddings=embeddings,
        ai=provider,
        settings=settings,
    )

    inbound = _make_inbound(text="رحلة")
    await service.handle_group_message(inbound)

    assert len(repo.created_trips) == 1
    customer = list(repo.customers_by_remote_jid.values())[0]
    assert customer["name"] is None
