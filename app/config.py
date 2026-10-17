from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class WhatsAppOutboxLevel(BaseModel):
    max_age_seconds: int | None
    retry_interval_seconds: int | None
    order: Literal["oldest", "newest", "discard"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FALZH"
    environment: str = "development"
    app_timezone: str = "Asia/Aden"
    log_level: str = "INFO"

    supabase_url: HttpUrl
    supabase_service_role_key: str = Field(min_length=1)

    jina_api_key: str = Field(min_length=1)
    jina_embedding_model: str = "jina-embeddings-v5-text-small"
    jina_embedding_dimensions: int = 1024
    jina_embedding_endpoint: str = "https://api.jina.ai/v1/embeddings"
    jina_query_task: str = "retrieval.query"
    jina_passage_task: str = "retrieval.passage"

    groq_api_key: str = Field(min_length=1)
    groq_model: str = Field(min_length=1)
    groq_api_key_2: str | None = None
    groq_model_2: str | None = None
    google_ai_api_key: str | None = None
    google_ai_model: str | None = None
    openrouter_api_key: str = Field(min_length=1)
    openrouter_model: str = Field(min_length=1)
    ai_temperature: float = 0.2
    ai_max_tool_iterations: int = 3
    request_timeout_seconds: float = 20.0

    whatsapp_graph_url: str = "https://graph.facebook.com"
    whatsapp_api_version: str = "v20.0"
    whatsapp_verify_token: str = Field(min_length=1)
    whatsapp_app_secret: str = Field(min_length=1)
    whatsapp_access_token: str = Field(min_length=1)
    whatsapp_phone_number_id: str = Field(min_length=1)
    whatsapp_bridge_secret: str | None = Field(default=None, min_length=1)
    whatsapp_outbox_levels: list[WhatsAppOutboxLevel] = Field(
        default_factory=lambda: [
            WhatsAppOutboxLevel(
                max_age_seconds=120,
                retry_interval_seconds=10,
                order="oldest",
            ),
            WhatsAppOutboxLevel(
                max_age_seconds=36_000,
                retry_interval_seconds=60,
                order="newest",
            ),
            WhatsAppOutboxLevel(
                max_age_seconds=None,
                retry_interval_seconds=None,
                order="discard",
            ),
        ]
    )

    admin_api_key: str = Field(min_length=1)

    default_country_code: str = "967"

    @model_validator(mode="after")
    def validate_whatsapp_outbox_levels(self) -> "Settings":
        levels = self.whatsapp_outbox_levels
        if len(levels) < 2:
            raise ValueError("WhatsApp outbox needs at least one retry level and one discard level")

        previous_max_age = 0
        for level in levels[:-1]:
            if level.max_age_seconds is None or level.max_age_seconds <= previous_max_age:
                raise ValueError("Retry level age limits must be positive and strictly increasing")
            if level.retry_interval_seconds is None or level.retry_interval_seconds <= 0:
                raise ValueError("Retry levels need a positive retry interval")
            if level.order == "discard":
                raise ValueError("Only the final WhatsApp outbox level can discard messages")
            previous_max_age = level.max_age_seconds

        final_level = levels[-1]
        if (
            final_level.max_age_seconds is not None
            or final_level.retry_interval_seconds is not None
            or final_level.order != "discard"
        ):
            raise ValueError("The final WhatsApp outbox level must be an unbounded discard level")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
