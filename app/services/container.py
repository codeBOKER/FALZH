from dataclasses import dataclass

from app.ai.orchestrator import AIOrchestrator
from app.ai.providers import GroqChatProvider, HuggingFaceChatProvider
from app.config import Settings
from app.database.supabase import SupabaseRepository, create_supabase_client
from app.services.admin_service import AdminService
from app.services.conversation_service import ConversationService
from app.services.embedding_service import JinaEmbeddingService
from app.services.group_message_service import GroupMessageService
from app.whatsapp.client import WhatsAppClient


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    repository: SupabaseRepository
    embeddings: JinaEmbeddingService
    whatsapp: WhatsAppClient
    ai: AIOrchestrator
    conversation: ConversationService
    group_message: GroupMessageService
    admin: AdminService

    @classmethod
    async def from_settings(cls, settings: Settings) -> "ServiceContainer":
        supabase_client = await create_supabase_client(settings)
        repository = SupabaseRepository(supabase_client)
        embeddings = JinaEmbeddingService(settings)
        whatsapp = WhatsAppClient(settings)
        ai = AIOrchestrator(
            primary=GroqChatProvider(settings),
            fallback=HuggingFaceChatProvider(settings),
            temperature=settings.ai_temperature,
            max_tool_iterations=settings.ai_max_tool_iterations,
        )
        conversation = ConversationService(
            repository=repository,
            embeddings=embeddings,
            whatsapp=whatsapp,
            ai=ai,
            settings=settings,
        )
        group_message = GroupMessageService(
            repository=repository,
            embeddings=embeddings,
            ai=ai,
            settings=settings,
        )
        admin = AdminService(repository=repository, embeddings=embeddings, settings=settings)
        return cls(
            settings=settings,
            repository=repository,
            embeddings=embeddings,
            whatsapp=whatsapp,
            ai=ai,
            conversation=conversation,
            group_message=group_message,
            admin=admin,
        )
