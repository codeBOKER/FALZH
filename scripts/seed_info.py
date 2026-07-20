import asyncio
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.config import get_settings
from app.database.supabase import SupabaseRepository, create_supabase_client
from app.services.admin_service import AdminService
from app.services.embedding_service import JinaEmbeddingService


async def main() -> None:
    settings = get_settings()
    repository = SupabaseRepository(await create_supabase_client(settings))
    service = AdminService(
        repository=repository,
        embeddings=JinaEmbeddingService(settings),
        settings=settings,
    )
    indexed = await service.seed_info()
    print(f"Indexed {indexed} FALZH info chunks")


if __name__ == "__main__":
    asyncio.run(main())
