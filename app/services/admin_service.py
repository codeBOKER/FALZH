import hashlib
from pathlib import Path

from app.config import Settings
from app.database.supabase import SupabaseRepository
from app.services.embedding_service import JinaEmbeddingService
from app.services.trip_indexing import build_trip_embedding_record


class AdminService:
    def __init__(
        self,
        *,
        repository: SupabaseRepository,
        embeddings: JinaEmbeddingService,
        settings: Settings,
        info_path: Path | None = None,
    ) -> None:
        self.repository = repository
        self.embeddings = embeddings
        self.settings = settings
        self.info_path = info_path or Path("prompts/falzh_info.md")

    async def seed_info(self) -> int:
        text = self.info_path.read_text(encoding="utf-8")
        chunk_texts = _chunk_markdown(text)
        embeddings = await self.embeddings.embed_passages(chunk_texts)
        chunks = []
        for chunk, embedding in zip(chunk_texts, embeddings, strict=True):
            digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:24]
            chunks.append(
                {
                    "id": f"info-{digest}",
                    "chunk_text": chunk,
                    "source": str(self.info_path),
                    "embedding": embedding,
                    "embedding_model": self.settings.jina_embedding_model,
                }
            )
        return await self.repository.upsert_info_chunks(chunks)

    async def sync_trips(self) -> int:
        trips = await self.repository.list_active_trips()
        trip_records = [
            build_trip_embedding_record(trip, self.settings.jina_embedding_model)
            for trip in trips
        ]
        embeddings = await self.embeddings.embed_passages(
            [record["chunk_text"] for record in trip_records]
        )
        for record, embedding in zip(trip_records, embeddings, strict=True):
            record["embedding"] = embedding
        return await self.repository.upsert_trip_embeddings(trip_records)


def _chunk_markdown(text: str, *, max_chars: int = 1200) -> list[str]:
    sections = [section.strip() for section in text.split("\n## ") if section.strip()]
    chunks: list[str] = []
    for index, section in enumerate(sections):
        content = section if index == 0 else f"## {section}"
        if len(content) <= max_chars:
            chunks.append(content)
            continue
        for start in range(0, len(content), max_chars):
            chunks.append(content[start : start + max_chars].strip())
    return chunks
