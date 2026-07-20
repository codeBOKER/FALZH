from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


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
    gemini_api_key: str = Field(min_length=1)
    gemini_model: str = Field(min_length=1)
    ai_temperature: float = 0.2
    ai_max_tool_iterations: int = 3
    request_timeout_seconds: float = 20.0

    whatsapp_graph_url: str = "https://graph.facebook.com"
    whatsapp_api_version: str = "v20.0"
    whatsapp_verify_token: str = Field(min_length=1)
    whatsapp_app_secret: str = Field(min_length=1)
    whatsapp_access_token: str = Field(min_length=1)
    whatsapp_phone_number_id: str = Field(min_length=1)

    admin_api_key: str = Field(min_length=1)

    default_country_code: str = "967"


@lru_cache
def get_settings() -> Settings:
    return Settings()
