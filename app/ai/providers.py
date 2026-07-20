import json
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


class GeminiChatProvider:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model
        self.timeout = settings.request_timeout_seconds
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.2,
    ) -> AIProviderResponse:
        from google.genai import types

        try:
            contents = _convert_messages_to_gemini(messages)
            config_kwargs: dict[str, Any] = {"temperature": temperature}
            if tools:
                config_kwargs["tools"] = _convert_tools_to_gemini(tools)
                if tool_choice and tool_choice != "none":
                    config_kwargs["tool_config"] = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfig.Mode.AUTO
                            if tool_choice == "auto"
                            else types.FunctionCallingConfig.Mode.ANY,
                        )
                    )
            config = types.GenerateContentConfig(**config_kwargs)
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            return _parse_gemini_response(response)
        except Exception as exc:  # noqa: BLE001
            raise _provider_error_from_exception(exc, self.name) from exc


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


def _convert_tools_to_gemini(tools: list[dict[str, Any]]) -> list[Any]:
    from google.genai import types

    declarations = []
    for tool in tools:
        func = tool.get("function", {})
        params = func.get("parameters", {})
        declarations.append(
            types.FunctionDeclaration(
                name=func.get("name", ""),
                description=func.get("description", ""),
                parameters=params if params else None,
            )
        )
    return [types.Tool(function_declarations=declarations)]


def _convert_messages_to_gemini(messages: list[dict[str, Any]]) -> list[Any]:
    from google.genai import types

    contents = []
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    for msg in messages:
        role = msg.get("role", "user")

        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            if tool_call_id in pending_tool_calls:
                tc = pending_tool_calls.pop(tool_call_id)
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=tc["name"],
                                response=json.loads(msg.get("content", "{}")),
                            )
                        ],
                    )
                )
            continue

        parts: list[Any] = []
        content = msg.get("content")
        if content:
            parts.append(types.Part(text=content))

        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            try:
                parsed_args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {}
            parts.append(
                types.Part.from_function_call(
                    name=func.get("name", ""),
                    args=parsed_args,
                )
            )
            pending_tool_calls[tc.get("id", "")] = {
                "name": func.get("name", ""),
            }

        if parts:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append(types.Content(role=gemini_role, parts=parts))

    return contents


def _parse_gemini_response(response: Any) -> AIProviderResponse:
    text_content = ""
    tool_calls: list[ToolCall] = []
    raw_message: dict[str, Any] = {}

    if not response.candidates:
        return AIProviderResponse(
            content=text_content, tool_calls=tool_calls, raw_message=raw_message
        )

    candidate = response.candidates[0]
    parts = candidate.content.parts if candidate.content else []

    for part in parts:
        if part.text:
            text_content += part.text
        elif part.function_call:
            fc = part.function_call
            args = dict(fc.args) if fc.args else {}
            tool_calls.append(
                ToolCall(
                    id=fc.name,
                    name=fc.name,
                    arguments=json.dumps(args, ensure_ascii=False),
                )
            )

    raw_message["role"] = "assistant"
    if text_content:
        raw_message["content"] = text_content
    if tool_calls:
        raw_message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in tool_calls
        ]

    return AIProviderResponse(content=text_content, tool_calls=tool_calls, raw_message=raw_message)


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
