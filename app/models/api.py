from pydantic import BaseModel, Field
from typing import Any


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "FALZH"


class SeedInfoResponse(BaseModel):
    indexed_chunks: int


class SyncTripsResponse(BaseModel):
    indexed_trips: int


class WebhookAcceptedResponse(BaseModel):
    status: str = "accepted"
    messages: int = Field(ge=0)


class WebhookDebugResponse(BaseModel):
    status: str = "accepted"
    messages: int = Field(ge=0)
    replies: list[str] = Field(default_factory=list)


class JinaEmbeddingRequest(BaseModel):
    text: str = Field(min_length=1)


class JinaEmbeddingResponse(BaseModel):
    status: str = "ok"
    text: str
    embedding: list[float]
    dimensions: int


class LLMToolCallRequest(BaseModel):
    message: str = Field(min_length=1)


class DriverDebugRequest(BaseModel):
    message: str = Field(min_length=1)
    client_number: str = Field(min_length=1)


class LLMToolCallResponse(BaseModel):
    status: str = "ok"
    llm_response: str | None = None
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
