import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from app.ai.orchestrator import AIOrchestrator
from app.config import Settings
from app.database.supabase import SupabaseRepository
from app.models.domain import ExtractedTrip, WhatsAppInboundMessage
from app.services.embedding_service import JinaEmbeddingService
from app.services.trip_indexing import index_trip
from app.utils.departure import normalize_departure_bucket, _parse_date_value
from app.utils.time import now_in_timezone

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT_PATH = Path("prompts/group_trip_extraction.md")


class GroupMessageService:
    def __init__(
        self,
        *,
        repository: SupabaseRepository,
        embeddings: JinaEmbeddingService,
        ai: AIOrchestrator,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.embeddings = embeddings
        self.ai = ai
        self.settings = settings

    async def handle_group_message(
        self,
        inbound: WhatsAppInboundMessage,
    ) -> None:
        if await self.repository.message_exists(inbound.message_id):
            logger.info("Skipping duplicate group message %s", inbound.message_id)
            return

        extracted = await self._extract_trip_from_text(inbound.text)
        if not extracted or not extracted.is_trip_ad:
            logger.info("Group message %s is not a trip advertisement", inbound.message_id)
            return

        if not self._validate_extracted_trip(extracted):
            logger.warning(
                "Group message %s has incomplete trip data: %s",
                inbound.message_id,
                extracted,
            )
            return

        departure_time = normalize_departure_bucket(extracted.departure_time)
        if not departure_time:
            logger.warning(
                "Group message %s: could not normalize departure_time '%s'",
                inbound.message_id,
                extracted.departure_time,
            )
            return

        departure_date = _parse_date_value(extracted.departure_date)
        if not departure_date:
            logger.warning(
                "Group message %s: invalid departure_date '%s'",
                inbound.message_id,
                extracted.departure_date,
            )
            return

        phone = self._normalize_phone(extracted.driver_phone)
        if not phone:
            logger.warning(
                "Group message %s: no valid phone number extracted",
                inbound.message_id,
            )
            return

        existing_customer = await self.repository.get_customer_by_phone_number(phone)
        if existing_customer:
            if existing_customer.get("registered"):
                logger.info(
                    "Driver phone %s is registered (customer %s); discarding group message %s",
                    phone,
                    existing_customer["id"],
                    inbound.message_id,
                )
                return

            driver = await self.repository.get_driver_by_remoteJid(phone)
            if driver:
                existing_trip = await self.repository.get_driver_trip_by_datetime(
                    driver_id=str(driver["id"]),
                    departure_date=departure_date,
                    departure_time=departure_time,
                )
                if existing_trip:
                    logger.info(
                        "Driver %s already has trip at %s %s; discarding group message %s",
                        phone,
                        departure_date,
                        departure_time,
                        inbound.message_id,
                    )
                    return

            trip = await self.repository.create_unregistered_driver_trip(
                driver_id=str(driver["id"]) if driver else None,
                phone_number=phone,
                driver_name=extracted.driver_name,
                car_type=extracted.car_type,
                departure=extracted.departure,
                destination=extracted.destination,
                departure_date=departure_date,
                departure_time=departure_time,
                available_seats=extracted.available_seats or 1,
                total_seats=extracted.total_seats or extracted.available_seats or 1,
                price=extracted.price or 0,
            )
        else:
            trip = await self.repository.create_unregistered_driver_entities(
                phone_number=phone,
                driver_name=extracted.driver_name,
                car_type=extracted.car_type,
                departure=extracted.departure,
                destination=extracted.destination,
                departure_date=departure_date,
                departure_time=departure_time,
                available_seats=extracted.available_seats or 1,
                total_seats=extracted.total_seats or extracted.available_seats or 1,
                price=extracted.price or 0,
            )

        await index_trip(
            repository=self.repository,
            embeddings=self.embeddings,
            embedding_model=self.settings.jina_embedding_model,
            trip=trip,
        )

        logger.info(
            "Created trip %s from group message (unregistered driver %s)",
            trip.get("id"),
            phone,
        )

    async def _extract_trip_from_text(self, text: str) -> ExtractedTrip | None:
        prompt_template = _EXTRACTION_PROMPT_PATH.read_text(encoding="utf-8")
        dt = now_in_timezone(self.settings.app_timezone)
        prompt = prompt_template.format(current_datetime=dt.isoformat())

        try:
            response = await self.ai.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                tools=None,
                tool_choice=None,
                temperature=0.1,
            )
        except Exception as exc:
            logger.warning("LLM extraction failed for group message: %s", exc)
            return None

        content = (response.content or "").strip()
        if not content:
            return None

        return self._parse_extracted_json(content)

    def _parse_extracted_json(self, content: str) -> ExtractedTrip | None:
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
            content = content.strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM extraction JSON: %s", content[:200])
            return None

        if not isinstance(data, dict):
            return None

        return ExtractedTrip(
            is_trip_ad=bool(data.get("is_trip_ad", False)),
            departure=data.get("departure"),
            destination=data.get("destination"),
            departure_date=data.get("departure_date"),
            departure_time=data.get("departure_time"),
            available_seats=_safe_int(data.get("available_seats")),
            total_seats=_safe_int(data.get("total_seats")),
            price=_safe_float(data.get("price")),
            car_type=data.get("car_type"),
            driver_name=data.get("driver_name"),
            driver_phone=data.get("driver_phone"),
        )

    def _validate_extracted_trip(self, trip: ExtractedTrip) -> bool:
        return all([
            trip.departure,
            trip.destination,
            trip.departure_date,
            trip.departure_time,
            trip.driver_phone,
        ])

    @staticmethod
    def _normalize_phone(phone: str | None) -> str | None:
        if not phone:
            return None
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) < 7:
            return None
        digits = digits.lstrip("0")
        return digits if digits else None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
