import logging
from pathlib import Path
from typing import Any

from app.ai.orchestrator import AIOrchestrator
from app.ai.tool_schemas import _TOOLS_BY_MODE, get_tool_schemas
from app.config import Settings
from app.database.supabase import SupabaseRepository
from app.models.domain import UserMode, WhatsAppInboundMessage
from app.services.embedding_service import JinaEmbeddingService
from app.services.trip_indexing import unindex_trip
from app.tools.handlers import FalsaToolHandlers, _trip_summary
from app.tools.registry import ToolRegistry
from app.utils.time import now_in_timezone
from app.whatsapp.client import WhatsAppClient
from app.whatsapp.trip_selection import parse_trip_action_reply

logger = logging.getLogger(__name__)

_PROMPT_PATHS: dict[UserMode, Path] = {
    "new_user": Path("prompts/system_new_user.md"),
    "driver": Path("prompts/system_driver.md"),
    "passenger": Path("prompts/system_passenger.md"),
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
            phone_number=inbound.phone_number,
        )

        metadata: dict[str, Any] = {
            "whatsapp": inbound.raw,
            "timestamp": inbound.timestamp,
        }
        if inbound.context_message_id:
            metadata["context_message_id"] = inbound.context_message_id

        current_message = await self.repository.create_message(
            customer_id=str(customer["id"]),
            sender_type="customer",
            message=inbound.text,
            whatsapp_message_id=inbound.message_id,
            metadata=metadata,
        )

        context = await self.repository.get_recent_context_messages(
            customer_id=str(customer["id"]),
            current_message_id=str(current_message["id"]),
            limit=8,
        )

        user_mode = _resolve_user_mode(customer)
        registry = self._tool_registry(
            customer,
            remoteJid=inbound.remoteJid,
            user_mode=user_mode,
            current_message=current_message,
        )
        reply = await self.ai.generate_reply(
            messages=self._ai_messages(context, user_mode=user_mode),
            tools=get_tool_schemas(user_mode),
            registry=registry,
        )

        if not reply:
            return reply

        await self.whatsapp.send_text(inbound.remoteJid, reply)

        await self.repository.create_message(
            customer_id=str(customer["id"]),
            sender_type="assistant",
            message=reply,
            metadata={"provider_flow": "groq_primary_hf_fallback", "user_mode": user_mode},
        )

        return reply

    async def _handle_trip_interactive_reply(
        self,
        inbound: WhatsAppInboundMessage,
        customer: dict[str, Any],
        *,
        action: str,
        trip_id: str,
    ) -> str | None:
        current_message = await self.repository.create_message(
            customer_id=str(customer["id"]),
            sender_type="customer",
            message=inbound.text,
            whatsapp_message_id=inbound.message_id,
            metadata={
                "whatsapp": inbound.raw,
                "timestamp": inbound.timestamp,
                "interactive_reply_id": inbound.interactive_reply_id,
            },
        )

        driver = await self.repository.get_driver_by_remoteJid(inbound.remoteJid)
        if not driver:
            reply = (
                "لا يوجد حساب سائق مرتبط بهذا الرفم"
                "من فضللك, سجل كحساب سائق اولا"
            )
            await self._store_and_send_assistant_reply(
                customer,
                inbound.remoteJid,
                reply,
                user_mode=_resolve_user_mode(customer),
            )
            return reply

        trip = await self.repository.get_trip_by_id(trip_id)
        if not trip or str(trip.get("driver_id")) != str(driver["id"]):
            reply = "That trip could not be found for your account."
            await self._store_and_send_assistant_reply(
                customer,
                inbound.remoteJid,
                reply,
                user_mode=_resolve_user_mode(customer),
            )
            return reply

        if action == "DELETE":
            await self.repository.cancel_driver_trip(trip_id)
            await unindex_trip(repository=self.repository, trip_id=trip_id)
            reply = "Success! Your trip has been canceled."
            await self._store_and_send_assistant_reply(
                customer,
                inbound.remoteJid,
                reply,
                user_mode="driver",
            )
            return reply

        if action == "MODIFY":
            await self.repository.set_customer_session_field(
                customer_id=str(customer["id"]),
                key="active_edit_trip_id",
                value=trip_id,
            )
            summary = _trip_summary(trip)
            route = f"{summary.get('departure')} -> {summary.get('destination')}"
            time_label = summary.get("departure_time") or summary.get("departure_time_type")
            system_note = (
                f"SYSTEM: Driver selected trip {trip_id} ({route}, {time_label}) to modify. "
                "Ask them what details they want to change."
            )
            user_mode = "driver"
            registry = self._tool_registry(
                customer,
                remoteJid=inbound.remoteJid,
                user_mode=user_mode,
                current_message=current_message,
            )
            context = await self.repository.get_recent_context_messages(
                customer_id=str(customer["id"]),
                current_message_id=str(current_message["id"]),
                limit=8,
            )
            messages = self._ai_messages(context, user_mode=user_mode)
            messages.append({"role": "system", "content": system_note})
            reply = await self.ai.generate_reply(
                messages=messages,
                tools=get_tool_schemas(user_mode),
                registry=registry,
            )
            await self._store_and_send_assistant_reply(
                customer,
                inbound.remoteJid,
                reply,
                user_mode=user_mode,
            )
            return reply

        return None

    async def _store_and_send_assistant_reply(
        self,
        customer: dict[str, Any],
        remoteJid: str,
        reply: str,
        *,
        user_mode: UserMode,
    ) -> None:
        await self.repository.create_message(
            customer_id=str(customer["id"]),
            sender_type="assistant",
            message=reply,
            metadata={"provider_flow": "trip_interactive_reply", "user_mode": user_mode},
        )
        await self.whatsapp.send_text(remoteJid, reply)

    def _tool_registry(
        self,
        customer: dict[str, Any],
        *,
        remoteJid: str,
        user_mode: UserMode,
        current_message: dict[str, Any] | None = None,
    ) -> ToolRegistry:
        handlers = FalsaToolHandlers(
            repository=self.repository,
            embeddings=self.embeddings,
            whatsapp=self.whatsapp,
            customer=customer,
            remoteJid=remoteJid,
            embedding_model=self.settings.jina_embedding_model,
            current_message=current_message,
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
        base_template = Path("prompts/system.md").read_text(encoding="utf-8")
        if self.system_prompt_path is not None:
            mode_template = self.system_prompt_path.read_text(encoding="utf-8")
        else:
            mode_template = _PROMPT_PATHS[user_mode].read_text(encoding="utf-8")
        template = base_template + "\n\n" + mode_template
        dt = now_in_timezone(self.settings.app_timezone)
        current_datetime = dt.isoformat()
        day_name = dt.strftime("%A")
        return template.format(
            current_datetime=current_datetime,
            day_name=day_name,
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
