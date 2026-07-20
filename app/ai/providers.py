from typing import Any, Protocol

from app.config import Settings
from app.models.domain import AIProviderResponse, ToolCall


class ProviderError(RuntimeError):
    pass


class RetryableProviderError(ProviderError):
    pass


class InvalidToolCallGenerationError(RetryableProviderError):
    pass


class ChatProvider(Protocol):
    name: str

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.2,
    ) -> AIProviderResponse:
        ...


class OpenAICompatibleChatProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float,
        name: str,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.name = name
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.2,
    ) -> AIProviderResponse:
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = tool_choice

            completion = await self.client.chat.completions.create(**kwargs)
            message = completion.choices[0].message
            return _normalize_openai_message(message)
        except Exception as exc:  # noqa: BLE001
            raise _provider_error_from_exception(exc, self.name) from exc


class GroqChatProvider(OpenAICompatibleChatProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            model=settings.groq_model,
            timeout=settings.request_timeout_seconds,
            name="groq",
        )


class OpenRouterChatProvider(OpenAICompatibleChatProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            model=settings.openrouter_model,
            timeout=settings.request_timeout_seconds,
            name="openrouter",
        )


def _normalize_openai_message(message: Any) -> AIProviderResponse:
    raw_message: dict[str, Any]
    if hasattr(message, "model_dump"):
        raw_message = message.model_dump(exclude_none=True)
    elif isinstance(message, dict):
        raw_message = message
    else:
        raw_message = {}

    tool_calls = []
    for tool_call in raw_message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        tool_calls.append(
            ToolCall(
                id=tool_call.get("id") or function.get("name", "tool-call"),
                name=function.get("name", ""),
                arguments=function.get("arguments") or "{}",
            )
        )

    return AIProviderResponse(
        content=raw_message.get("content"),
        tool_calls=tool_calls,
        raw_message=raw_message,
    )


def _provider_error_from_exception(exc: Exception, provider_name: str) -> ProviderError:
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    message = str(exc)
    if body:
        message = f"{message} {body}"

    if status_code == 400 and "failed_generation" in message:
        return InvalidToolCallGenerationError(f"{provider_name} generated an invalid tool call")

    if status_code in {408, 409, 429} or (isinstance(status_code, int) and status_code >= 500):
        return RetryableProviderError(f"{provider_name} retryable failure: {message}")

    if exc.__class__.__name__ in {"APITimeoutError", "APIConnectionError", "RateLimitError"}:
        return RetryableProviderError(f"{provider_name} network/rate failure: {message}")

    return ProviderError(f"{provider_name} failure: {message}")
