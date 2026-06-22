import logging
from pathlib import Path
from typing import Any

from app.ai.orchestrator import AIOrchestrator
from app.ai.tool_schemas import get_tool_schemas
from app.config import Settings
from app.database.supabase import SupabaseRepository
from app.models.domain import UserMode, WhatsAppInboundMessage
from app.services.embedding_service import JinaEmbeddingService
from app.tools.handlers import FalsaToolHandlers
from app.tools.registry import ToolRegistry
from app.utils.time import now_in_timezone
from app.whatsapp.client import WhatsAppClient

logger = logging.getLogger(__name__)

_PROMPT_PATHS: dict[UserMode, Path] = {
    "new_user": Path("prompts/system_new_user.md"),
    "driver": Path("prompts/system_driver.md"),
    "passenger": Path("prompts/system_passenger.md"),
}

_TOOLS_BY_MODE: dict[UserMode, list[str]] = {
    "new_user": [
        "about_falsa",
        "create_driver_account",
        "switch_to_driver",
        "switch_to_passenger",
    ],
    "driver": [
        "about_falsa",
        "check_driver_info",
        "check_driver_trips",
        "add_driver_car",
        "add_trip_by_driver",
        "switch_to_passenger",
    ],
    "passenger": [
        "about_falsa",
        "search_trips",
        "create_booking_lead",
        "create_driver_account",
        "switch_to_driver",
    ],
}


class ConversationService:
    def __init__(
        self,
        *,
        repository: SupabaseRepository,
        embeddings: JinaEmbeddingService,
        whatsapp: WhatsAppClient,
        ai: AIOrchestrator,
        settings: Settings,
        system_prompt_path: Path | None = None,
    ) -> None:
        self.repository = repository
        self.embeddings = embeddings
        self.whatsapp = whatsapp
        self.ai = ai
        self.settings = settings
        self.system_prompt_path = system_prompt_path

    async def handle_inbound_message(self, inbound: WhatsAppInboundMessage) -> str | None:
        if await self.repository.message_exists(inbound.message_id):
            logger.info("Skipping duplicate WhatsApp message %s", inbound.message_id)
            return None

        customer = await self.repository.upsert_customer(
            remote_jid=inbound.remoteJid,
            name=inbound.profile_name,
        )
        current_message = await self.repository.create_message(
            customer_id=str(customer["id"]),
            sender_type="customer",
            message=inbound.text,
            whatsapp_message_id=inbound.message_id,
            metadata={"whatsapp": inbound.raw, "timestamp": inbound.timestamp},
        )

        context = await self.repository.get_recent_context_messages(
            customer_id=str(customer["id"]),
            current_message_id=str(current_message["id"]),
            limit=4,
        )

        user_mode = _resolve_user_mode(customer)
        registry = self._tool_registry(customer, remoteJid=inbound.remoteJid, user_mode=user_mode)
        reply = await self.ai.generate_reply(
            messages=self._ai_messages(context, user_mode=user_mode),
            tools=get_tool_schemas(user_mode),
            registry=registry,
        )

        await self.repository.create_message(
            customer_id=str(customer["id"]),
            sender_type="assistant",
            message=reply,
            metadata={"provider_flow": "groq_primary_hf_fallback", "user_mode": user_mode},
        )
        await self.whatsapp.send_text(inbound.remoteJid, reply)
        return reply

    def _tool_registry(
        self,
        customer: dict[str, Any],
        *,
        remoteJid: str,
        user_mode: UserMode,
    ) -> ToolRegistry:
        handlers = FalsaToolHandlers(
            repository=self.repository,
            embeddings=self.embeddings,
            whatsapp=self.whatsapp,
            customer=customer,
            remoteJid=remoteJid,
            embedding_model=self.settings.jina_embedding_model,
        )
        registry = ToolRegistry()
        for tool_name in _TOOLS_BY_MODE[user_mode]:
            registry.register(tool_name, getattr(handlers, tool_name))
        return registry

    def _ai_messages(
        self,
        context: list[dict[str, Any]],
        *,
        user_mode: UserMode,
    ) -> list[dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": self._system_prompt(user_mode),
            }
        ]
        for row in context:
            role = _sender_to_ai_role(row.get("sender_type"))
            messages.append({"role": role, "content": row.get("message") or ""})
        return messages

    def _system_prompt(self, user_mode: UserMode) -> str:
        if self.system_prompt_path is not None:
            template = self.system_prompt_path.read_text(encoding="utf-8")
        else:
            template = _PROMPT_PATHS[user_mode].read_text(encoding="utf-8")
        current_datetime = now_in_timezone(self.settings.app_timezone).isoformat()
        return template.format(
            current_datetime=current_datetime,
            timezone=self.settings.app_timezone,
        )


def _resolve_user_mode(customer: dict[str, Any]) -> UserMode:
    mode = customer.get("user_mode")
    if mode == "driver":
        return "driver"
    if mode == "passenger":
        return "passenger"
    return "new_user"


def _sender_to_ai_role(sender_type: str | None) -> str:
    if sender_type == "assistant":
        return "assistant"
    if sender_type == "customer":
        return "user"
    return "system"
