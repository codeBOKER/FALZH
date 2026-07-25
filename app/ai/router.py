import json
import logging
from typing import Any

import litellm

from app.models.domain import AIProviderResponse, ToolCall
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class LiteLLMOrchestration:
    """Provides AI orchestration using LiteLLM Router for automatic API management"""

    def __init__(
        self,
        groq_api_key: str,
        groq_model: str,
        hf_api_key: str,
        hf_api_key_2: str,
        hf_model: str,
        openrouter_api_key: str,
        openrouter_model: str,
        max_tool_iterations: int = 3,
        temperature: float = 0.2,
    ) -> None:
        self.name = "litellm-orchestration"
        self.groq_api_key = groq_api_key
        self.groq_model = groq_model
        self.hf_api_key = hf_api_key
        self.hf_api_key_2 = hf_api_key_2
        self.hf_model = hf_model
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_model = openrouter_model
        self.max_tool_iterations = max_tool_iterations
        self.temperature = temperature
        self._initialized = False

    @staticmethod
    def _litellm_model(model: str, provider: str) -> str:
        """Ensure model name has the litellm provider prefix.

        litellm strips the provider prefix before sending to the API,
        so 'groq/openai/gpt-oss-120b' sends 'openai/gpt-oss-120b' to Groq.
        """
        if model.startswith(f"{provider}/"):
            return model
        return f"{provider}/{model}"

    async def _ensure_initialized(self):
        if not self._initialized:
            self.router = litellm.Router(
                model_list=[
                    {
                        "model_name": "groq",
                        "litellm_params": {
                            "model": self._litellm_model(self.groq_model, "groq"),
                            "api_key": self.groq_api_key,
                            "api_base": "https://api.groq.com/openai/v1",
                        },
                    },
                    {
                        "model_name": "hf1",
                        "litellm_params": {
                            "model": self._litellm_model(self.hf_model, "huggingface"),
                            "api_key": self.hf_api_key,
                            "api_base": "https://router.huggingface.co/v1",
                        },
                    },
                    {
                        "model_name": "hf2",
                        "litellm_params": {
                            "model": self._litellm_model(self.hf_model, "huggingface"),
                            "api_key": self.hf_api_key_2,
                            "api_base": "https://router.huggingface.co/v1",
                        },
                    },
                    {
                        "model_name": "openrouter",
                        "litellm_params": {
                            "model": self._litellm_model(self.openrouter_model, "openrouter"),
                            "api_key": self.openrouter_api_key,
                            "api_base": "https://openrouter.ai/api/v1",
                        },
                    },
                ],
                fallbacks=[{"groq": ["hf1", "hf2", "openrouter"]}],
                num_retries=0,
                retry_policy={
                    "TimeoutErrorRetries": 0,
                    "RateLimitErrorRetries": 0,
                    "InternalServerErrorRetries": 0,
                },
                routing_strategy="latency-based-routing",
                set_verbose=False,
            )
            self._initialized = True

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        tool_choice: str | None = "auto",
    ) -> AIProviderResponse:
        if temperature is None:
            temperature = self.temperature

        await self._ensure_initialized()

        kwargs = {
            "messages": messages,
            "model": "groq",
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            response = await self.router.acompletion(**kwargs)
            return self._adapt_response(response)
        except Exception as exc:
            logger.error("LiteLLM Router error: %s", exc)
            raise RuntimeError(f"AI request failed: {exc}") from exc

    async def generate_reply(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        registry: ToolRegistry,
    ) -> str:
        working_messages = [dict(msg) for msg in messages]

        for _ in range(self.max_tool_iterations + 1):
            response = await self.chat(
                messages=working_messages,
                tools=tools,
                temperature=self.temperature,
            )

            if not response.tool_calls:
                content = (response.content or "").strip()
                if content:
                    return content
                continue

            working_messages.append(self._assistant_tool_message(response))
            for tool_call in response.tool_calls:
                result = await self._execute_tool_call(registry, tool_call)
                if result.suppress_llm_reply:
                    return result.error or ""
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(result.to_payload(), ensure_ascii=False),
                    }
                )

        return (
            "I found that this request needs extra checking. "
            "A support team member will follow up with you shortly."
        )

    def _adapt_response(self, response: Any) -> AIProviderResponse:
        raw_message: dict[str, Any] = {}

        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            if hasattr(choice, "message") and choice.message:
                if hasattr(choice.message, "model_dump"):
                    raw_message = choice.message.model_dump(exclude_none=True)
                elif isinstance(choice.message, dict):
                    raw_message = choice.message
                else:
                    raw_message = {
                        "content": getattr(choice.message, "content", None),
                        "tool_calls": getattr(choice.message, "tool_calls", None),
                    }

        tool_calls = []
        for tool_call in raw_message.get("tool_calls") or []:
            if hasattr(tool_call, "function"):
                function = tool_call.function
                name = function.name
                arguments = function.arguments or "{}"
                id_val = getattr(tool_call, "id", None) or f"tool-call-{name}"
            else:
                function = tool_call.get("function") or {}
                name = function.get("name", "")
                arguments = function.get("arguments") or "{}"
                id_val = tool_call.get("id") or f"tool-call-{name}"

            tool_calls.append(
                ToolCall(
                    id=id_val,
                    name=name,
                    arguments=arguments,
                )
            )

        return AIProviderResponse(
            content=raw_message.get("content"),
            tool_calls=tool_calls,
            raw_message=raw_message,
        )

    @staticmethod
    def _assistant_tool_message(response: AIProviderResponse) -> dict[str, Any]:
        if response.raw_message:
            return response.raw_message
        return {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.name, "arguments": tool_call.arguments},
                }
                for tool_call in response.tool_calls
            ],
        }

    @staticmethod
    async def _execute_tool_call(
        registry: ToolRegistry, tool_call: ToolCall
    ) -> Any:
        try:
            arguments = json.loads(tool_call.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            from app.models.domain import ToolResult

            return ToolResult(
                ok=False, data={}, error=f"Invalid tool arguments: {exc}"
            )

        return await registry.execute(tool_call.name, arguments)
